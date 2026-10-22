"""
stats_tab.py
------------
Business insights: top-selling items, daily sales trend with mean/std-dev,
category breakdown, and customer preferences (top spenders + open "wants
next" requests across all customers).
"""

import statistics
import textwrap

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QDateEdit,
    QGridLayout, QScrollArea, QLayout
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QColor

from widgets import rupees, make_heading, make_stat_card
from theme import COLORS
from period_filter import PeriodFilter
from report_export import export_csv


class StatsTab(QWidget):
    def __init__(self, db, autoload=True):
        super().__init__()
        self.db = db
        self._build_ui()
        if autoload:
            self.refresh()

    def _build_ui(self):
        outer_scroll = QScrollArea()
        outer_scroll.setWidgetResizable(True)
        outer_scroll.setStyleSheet("QScrollArea { border: none; }")
        container = QWidget()
        container.setObjectName("statisticsPage")
        container.setStyleSheet(f"QWidget#statisticsPage {{ background: {COLORS['bg']}; }}")
        outer_scroll.setWidget(container)

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.addWidget(outer_scroll)

        outer = QVBoxLayout(container)
        outer.setSizeConstraint(QLayout.SetMinimumSize)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)

        header_row = QHBoxLayout()
        header_row.addWidget(make_heading("Statistics", "Sales trends, best sellers, and what customers want"))
        header_row.addStretch()

        export = QPushButton("Export CSV")
        export.clicked.connect(self._export_csv)
        header_row.addWidget(export)
        outer.addLayout(header_row)
        self.period = PeriodFilter()
        self.quick_range_combo = self.period.preset
        self.period.changed.connect(self.refresh)
        outer.addWidget(self.period)
        # ---------------- KPI cards ----------------
        self.kpi_grid = QGridLayout()
        self.kpi_grid.setSpacing(10)
        outer.addLayout(self.kpi_grid)

        # ---------------- charts row ----------------

        trend_box = QGroupBox("Sales Trend (with mean & std. dev.)")
        trend_box.setMinimumHeight(390)
        trend_v = QVBoxLayout(trend_box)
        self.trend_figure = Figure(figsize=(6, 3.6), constrained_layout=True)
        self.trend_canvas = FigureCanvas(self.trend_figure)
        self.trend_canvas.setMinimumHeight(340)
        trend_v.addWidget(self.trend_canvas)
        outer.addWidget(trend_box)

        category_box = QGroupBox("Share of Sales")
        category_box.setMinimumHeight(460)
        category_v = QVBoxLayout(category_box)
        self.chart_group = QComboBox()
        self.chart_group.addItems(["Products", "Categories", "Brands / Styles"])
        self.chart_group.currentTextChanged.connect(self._change_chart_group)
        category_v.addWidget(self.chart_group)
        self.category_figure = Figure(figsize=(4.2, 3.6), constrained_layout=True)
        self.category_canvas = FigureCanvas(self.category_figure)
        self.category_canvas.setMinimumHeight(360)
        category_v.addWidget(self.category_canvas)
        outer.addWidget(category_box)

        monthly_box = QGroupBox("Monthly Sales — selected period")
        monthly_box.setMinimumHeight(390)
        monthly_v = QVBoxLayout(monthly_box)
        self.monthly_figure = Figure(figsize=(10, 3.6), constrained_layout=True)
        self.monthly_canvas = FigureCanvas(self.monthly_figure)
        self.monthly_canvas.setMinimumHeight(340)
        monthly_v.addWidget(self.monthly_canvas)
        outer.addWidget(monthly_box)

        # ---------------- top items ----------------
        top_items_box = QGroupBox("Best Selling Items")
        top_items_v = QVBoxLayout(top_items_box)

        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Rank by:"))
        self.top_items_sort_combo = QComboBox()
        self.top_items_sort_combo.addItems(["Quantity sold", "Revenue"])
        self.top_items_sort_combo.currentTextChanged.connect(self._change_item_sort)
        sort_row.addWidget(self.top_items_sort_combo)
        sort_row.addStretch()
        top_items_v.addLayout(sort_row)

        self.top_items_table = QTableWidget(0, 5)
        self.top_items_table.setHorizontalHeaderLabels(["Item", "Category", "Brand / Style", "Qty Sold", "Revenue"])
        self.top_items_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3, 4):
            self.top_items_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.top_items_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.top_items_table.setMinimumHeight(260)
        top_items_v.addWidget(self.top_items_table)
        outer.addWidget(top_items_box)

        top_brands_box = QGroupBox("Best Selling Brands / Styles")
        brands_v = QVBoxLayout(top_brands_box)
        brands_v.addWidget(QLabel("Uses the same Rank by selection as Best Selling Items."))
        self.top_brands_table = QTableWidget(0, 4)
        self.top_brands_table.setHorizontalHeaderLabels(["Category", "Brand / Style", "Qty Sold", "Revenue"])
        self.top_brands_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for column in (0, 2, 3):
            self.top_brands_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.top_brands_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.top_brands_table.setMinimumHeight(260)
        brands_v.addWidget(self.top_brands_table)
        outer.addWidget(top_brands_box)

        # ---------------- customer preferences ----------------
        pref_row = QHBoxLayout()
        pref_row.setSpacing(12)
        outer.addLayout(pref_row)

        top_customers_box = QGroupBox("Top Customers")
        tc_v = QVBoxLayout(top_customers_box)
        self.top_customers_table = QTableWidget(0, 3)
        self.top_customers_table.setHorizontalHeaderLabels(["Name", "Visits", "Total Spent"])
        self.top_customers_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.top_customers_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.top_customers_table.setMinimumHeight(220)
        tc_v.addWidget(self.top_customers_table)
        pref_row.addWidget(top_customers_box, 1)

        wishlist_box = QGroupBox("What Customers Are Asking For (open requests)")
        wl_v = QVBoxLayout(wishlist_box)
        self.wishlist_table = QTableWidget(0, 3)
        self.wishlist_table.setHorizontalHeaderLabels(["Customer", "Wants", "Since"])
        self.wishlist_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.wishlist_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.wishlist_table.setMinimumHeight(220)
        wl_v.addWidget(self.wishlist_table)
        pref_row.addWidget(wishlist_box, 1)

    # ------------------------------------------------------------- helpers
    def _date_range(self):
        return self.period.bounds()

    def refresh(self):
        try:
            date_from, date_to = self._date_range()
        except ValueError as exc:
            self._report = None
            self._clear_grid()
            for figure, canvas in ((self.trend_figure, self.trend_canvas),
                                   (self.category_figure, self.category_canvas),
                                   (self.monthly_figure, self.monthly_canvas)):
                figure.clear()
                canvas.draw()
            for table in (self.top_items_table, self.top_brands_table, self.top_customers_table, self.wishlist_table):
                table.setRowCount(0)
            return False
        self._report = self.db.report_statistics(date_from, date_to)
        self._refresh_kpis(date_from, date_to)
        self._refresh_trend_chart(date_from, date_to)
        self._refresh_category_chart(date_from, date_to)
        self._refresh_monthly_chart()
        self._refresh_top_items(date_from, date_to)
        self._refresh_top_customers(date_from, date_to)
        self._refresh_wishlist()
        return True

    def _change_chart_group(self, *_):
        if getattr(self, '_report', None) is not None:
            self._refresh_category_chart(*self._date_range())

    def _change_item_sort(self, *_):
        if getattr(self, '_report', None) is not None:
            self._refresh_top_items(*self._date_range())

    def _clear_grid(self):
        while self.kpi_grid.count():
            child = self.kpi_grid.takeAt(0)
            if child.widget():
                child.widget().hide()
                child.widget().deleteLater()

    def _refresh_kpis(self, date_from, date_to):
        self._clear_grid()
        totals = self._report['totals']
        cards = [
            ('Total Revenue', rupees(totals['revenue']), 'GST included'),
            ('Total Expenses', rupees(totals['expenses']), 'Expense dates in period'),
            ('Sales less expenses', rupees(totals['net_profit']), 'For cost-based profit, open Chart of Accounts → Reports'),
            ('Payments Collected', rupees(totals['payments_collected']), 'Payment dates in period'),
            ('Outstanding Credit', rupees(totals['outstanding']), 'Selected bills, balance to date'),
            ('Total Bills', str(totals['bill_count']), 'Bill dates in period'),
            ('Pieces Sold', str(totals['pieces']), ''),
            ('Average Bill Value', rupees(totals['avg_bill']), 'Revenue / total bills'),
            ('Mean Daily Sales', rupees(totals['mean_daily']), f"{totals['active_days']} active day(s)"),
            ('Median Daily Sales', rupees(totals['median_daily']), 'Middle active-day revenue'),
            ('Std. Dev. (daily)', rupees(totals['std_daily']), 'Population, active sales days'),
        ]
        for index, (title, value, note) in enumerate(cards):
            self.kpi_grid.addWidget(make_stat_card(title, value, note), index // 3, index % 3)

    def _refresh_trend_chart(self, date_from, date_to):
        daily = self._report['daily']
        monthly = len(daily) > 180
        plotted = self._report['monthly'] if monthly else daily
        self.trend_figure.clear()
        ax = self.trend_figure.add_subplot(111)
        if daily:
            dates = [d["month" if monthly else "date"] for d in plotted]
            revenues = [d["revenue"] for d in plotted]
            mean_val = statistics.mean(revenues)
            std_val = statistics.pstdev(revenues) if len(revenues) > 1 else 0

            ax.bar(dates, revenues, color=COLORS["primary"], alpha=0.85, label="Monthly revenue" if monthly else "Daily revenue")
            ax.axhline(mean_val, color=COLORS["accent"], linestyle="--", linewidth=1.5,
                       label=f"{'Monthly' if monthly else 'Daily'} mean: {rupees(mean_val)}")
            if std_val:
                ax.axhspan(max(mean_val - std_val, 0), mean_val + std_val,
                           color=COLORS["accent"], alpha=0.12, label=f"{'Monthly' if monthly else 'Daily'} ± 1 std dev ({rupees(std_val)})")
            ax.legend(fontsize=8, loc="upper left")
            ax.set_title(f"Total sales: {rupees(self._report['totals']['revenue'])} · {self._report['totals']['bill_count']} bills")
            ax.set_ylabel("Revenue (Rs.)")
            ax.set_xlabel("Monthly bars for long periods" if monthly else "Date")
            step = max(1, len(dates) // 10)
            ax.set_xticks(dates[::step])
            ax.tick_params(axis="x", rotation=45, labelsize=7)
            ax.tick_params(axis="y", labelsize=8)
        else:
            ax.text(0.5, 0.5, "No sales in this period", ha="center", va="center", color=COLORS["muted"])
            ax.set_xticks([])
            ax.set_yticks([])
        self.trend_canvas.draw_idle()

    def _refresh_category_chart(self, date_from, date_to):
        group = self.chart_group.currentText()
        report_key, title = {'Products': ('products', 'product'),
                             'Categories': ('categories', 'category'),
                             'Brands / Styles': ('brands', 'brand / style')}[group]
        rows = self._report[report_key]
        def label(row):
            if report_key == 'products':
                return f"{row['category']} → {row['brand']} → {row['name']}"
            if report_key == 'brands':
                return f"{row['category']} → {row['brand']}"
            return row['category']
        if any(r['revenue'] < 0 for r in rows):
            ranked = sorted(rows, key=lambda r: abs(r['revenue']), reverse=True)
            names = [label(r) for r in ranked[:8]]
            values = [r['revenue'] for r in ranked[:8]]
            if len(ranked)>8:
                names.append('Other (net)')
                values.append(sum(r['revenue'] for r in ranked[8:]))
            self.category_figure.clear()
            ax = self.category_figure.add_subplot(111)
            ax.barh(names, values, color=[COLORS['primary'] if value>=0 else COLORS['accent'] for value in values])
            ax.axvline(0, color=COLORS['muted'], linewidth=1)
            ax.set_title('Net sales after exchanges — ' + title)
            ax.set_xlabel('Revenue (Rs.)')
            self.category_canvas.draw_idle()
            return
        rows = [r for r in rows if r['revenue'] > 0]
        names = [label(r) for r in rows]
        values = [r['revenue'] for r in rows]
        if len(values) > 8:
            names, values = names[:8] + ['Other'], values[:8] + [sum(values[8:])]
        self.category_figure.clear()
        ax = self.category_figure.add_subplot(111)
        if values:
            total = sum(values)
            palette = [COLORS['primary'], COLORS['accent'], '#4a90a4', '#8e6c88', '#c4a35a', '#7a8b69', '#5972a5', '#876e63', '#315b8a']
            wedges, _, _ = ax.pie(values, autopct=lambda pct: f'{pct:.0f}%' if pct >= 3 else '',
                                 colors=palette[:len(values)], startangle=90, pctdistance=.78,
                                 wedgeprops=dict(width=.42, edgecolor='white'),
                                 textprops=dict(fontsize=9, color='white', fontweight='bold'))
            labels = [textwrap.fill(f'{name} — {rupees(value)} ({value/total:.1%})', 45)
                      for name, value in zip(names, values)]
            ax.legend(wedges, labels, loc='center left', bbox_to_anchor=(1, .5), fontsize=8, frameon=False)
            ax.set_title('Sales by ' + title)
        else:
            ax.text(.5, .5, 'No positive sales in this period', ha='center', va='center')
            ax.set_axis_off()
        self.category_canvas.draw_idle()

    def _refresh_monthly_chart(self):
        rows = self._report['monthly']
        self.monthly_figure.clear()
        ax = self.monthly_figure.add_subplot(111)
        if rows:
            bars = ax.bar([r['month'] for r in rows], [r['revenue'] for r in rows], color=COLORS['primary'])
            step = max(1, (len(rows) + 11) // 12)
            for index, (bar, row) in enumerate(zip(bars, rows)):
                if index % step == 0:
                    ax.annotate(f"{rupees(row['revenue'])}\n{row['bill_count']} bills",
                                (bar.get_x()+bar.get_width()/2, bar.get_height()),
                                xytext=(0, 4), textcoords='offset points', ha='center', fontsize=8)
            ax.set_xticks(range(0, len(rows), step), [r['month'] for r in rows[::step]])
            ax.tick_params(axis='x', rotation=45, labelsize=8)
            ax.margins(y=.22)
            totals = self._report['totals']
            ax.set_title(f"Total sales: {rupees(totals['revenue'])} · {totals['bill_count']} bills")
            ax.set_ylabel('Revenue (Rs.)')
        else:
            ax.text(.5, .5, 'No sales in this period', ha='center', va='center')
            ax.set_axis_off()
        self.monthly_canvas.draw_idle()

    def _refresh_top_items(self, date_from, date_to):
        key = 'quantity' if self.top_items_sort_combo.currentText() == 'Quantity sold' else 'revenue'
        rows = sorted(self._report['products'], key=lambda r: r[key], reverse=True)[:10]
        self.top_items_table.setRowCount(len(rows))
        for row_idx, r in enumerate(rows):
            for col, value in enumerate([r['name'], r['category'], r['brand'], str(r['quantity']), rupees(r['revenue'])]):
                self.top_items_table.setItem(row_idx, col, QTableWidgetItem(value))

        brands = sorted(self._report['brands'], key=lambda r: r[key], reverse=True)[:10]
        self.top_brands_table.setRowCount(len(brands))
        for row_idx, r in enumerate(brands):
            for col, value in enumerate([r['category'], r['brand'], str(r['quantity']), rupees(r['revenue'])]):
                self.top_brands_table.setItem(row_idx, col, QTableWidgetItem(value))

    def _refresh_top_customers(self, date_from, date_to):
        rows = self.db.stat_top_customers(date_from, date_to, limit=10)
        self.top_customers_table.setRowCount(len(rows))
        for row_idx, r in enumerate(rows):
            self.top_customers_table.setItem(row_idx, 0, QTableWidgetItem(r["name"]))
            self.top_customers_table.setItem(row_idx, 1, QTableWidgetItem(str(r["visits"])))
            self.top_customers_table.setItem(row_idx, 2, QTableWidgetItem(rupees(r["total_spent"])))

    def _refresh_wishlist(self):
        rows = self.db.all_open_wishlist()
        start, end = self.period.bounds()
        rows = [r for r in rows if (not start or r['date_added'][:10] >= start)
                and (not end or r['date_added'][:10] <= end)]
        self.wishlist_table.setRowCount(len(rows))
        for row_idx, r in enumerate(rows):
            self.wishlist_table.setItem(row_idx, 0, QTableWidgetItem(r["customer_name"]))
            self.wishlist_table.setItem(row_idx, 1, QTableWidgetItem(r["item_description"]))
            self.wishlist_table.setItem(row_idx, 2, QTableWidgetItem(r["date_added"][:10]))

    def _export_csv(self):
        if not self.refresh():
            return
        start, end = self.period.bounds()
        rows = [['period', 'from', start or 'All time', '', '', '', ''],
                ['period', 'to', end or 'All time', '', '', '', '']]
        rows += [['metric', key, '', '', '', '', value] for key, value in self._report['totals'].items()]
        for kind, label in [('daily', 'date'), ('monthly', 'month')]:
            rows += [[kind, r[label], '', r['bill_count'], '', r['revenue'], ''] for r in self._report[kind]]
        rows += [['product', r['name'], r['category'], '', r['quantity'], r['revenue'], '', r['brand']] for r in self._report['products']]
        rows += [['category', r['category'], '', '', r['quantity'], r['revenue'], ''] for r in self._report['categories']]
        rows = [row + [''] if len(row) == 7 else row for row in rows]
        rows += [['brand', r['brand'], r['category'], '', r['quantity'], r['revenue'], '', r['brand']] for r in self._report['brands']]
        export_csv(self, 'Export Statistics', 'statistics_export.csv',
                   ['Section', 'Metric / Date / Name', 'Category / Period', 'Bills', 'Pieces', 'Revenue', 'Value', 'Brand / Style'], rows)
