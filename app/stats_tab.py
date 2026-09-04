"""
stats_tab.py
------------
Business insights: top-selling items, daily sales trend with mean/std-dev,
category breakdown, and customer preferences (top spenders + open "wants
next" requests across all customers).
"""

import statistics
from datetime import date

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QDateEdit,
    QGridLayout, QScrollArea
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QColor

from widgets import rupees, make_heading, make_stat_card
from theme import COLORS


class StatsTab(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        outer_scroll = QScrollArea()
        outer_scroll.setWidgetResizable(True)
        outer_scroll.setStyleSheet("QScrollArea { border: none; }")
        container = QWidget()
        outer_scroll.setWidget(container)

        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.addWidget(outer_scroll)

        outer = QVBoxLayout(container)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)

        header_row = QHBoxLayout()
        header_row.addWidget(make_heading("Statistics", "Sales trends, best sellers, and what customers want"))
        header_row.addStretch()

        self.quick_range_combo = QComboBox()
        self.quick_range_combo.addItems(["Last 30 days", "Last 7 days", "This month", "All time"])
        self.quick_range_combo.currentTextChanged.connect(self.refresh)
        header_row.addWidget(QLabel("Period:"))
        header_row.addWidget(self.quick_range_combo)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("role", "secondary")
        refresh_btn.clicked.connect(self.refresh)
        header_row.addWidget(refresh_btn)
        outer.addLayout(header_row)

        # ---------------- KPI cards ----------------
        self.kpi_grid = QGridLayout()
        self.kpi_grid.setSpacing(10)
        outer.addLayout(self.kpi_grid)

        # ---------------- charts row ----------------
        charts_row = QHBoxLayout()
        charts_row.setSpacing(12)
        outer.addLayout(charts_row)

        trend_box = QGroupBox("Daily Sales Trend (with mean & std. dev.)")
        trend_v = QVBoxLayout(trend_box)
        self.trend_figure = Figure(figsize=(6, 3.6), constrained_layout=True)
        self.trend_canvas = FigureCanvas(self.trend_figure)
        self.trend_canvas.setMinimumHeight(300)
        trend_v.addWidget(self.trend_canvas)
        charts_row.addWidget(trend_box, 2)

        category_box = QGroupBox("Sales by Category")
        category_v = QVBoxLayout(category_box)
        self.category_figure = Figure(figsize=(4.2, 3.6), constrained_layout=True)
        self.category_canvas = FigureCanvas(self.category_figure)
        self.category_canvas.setMinimumHeight(300)
        category_v.addWidget(self.category_canvas)
        charts_row.addWidget(category_box, 1)

        # ---------------- monthly sales (always full history, independent
        # of the Period filter above -- a month-by-month view is naturally
        # a longer-range picture than "last 7/30 days") ----------------
        monthly_box = QGroupBox("Monthly Sales")
        monthly_v = QVBoxLayout(monthly_box)
        self.monthly_figure = Figure(figsize=(10, 3.2), constrained_layout=True)
        self.monthly_canvas = FigureCanvas(self.monthly_figure)
        self.monthly_canvas.setMinimumHeight(260)
        monthly_v.addWidget(self.monthly_canvas)
        outer.addWidget(monthly_box)

        # ---------------- top items ----------------
        top_items_box = QGroupBox("Best Selling Items")
        top_items_v = QVBoxLayout(top_items_box)

        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Rank by:"))
        self.top_items_sort_combo = QComboBox()
        self.top_items_sort_combo.addItems(["Quantity sold", "Revenue"])
        self.top_items_sort_combo.currentTextChanged.connect(self.refresh)
        sort_row.addWidget(self.top_items_sort_combo)
        sort_row.addStretch()
        top_items_v.addLayout(sort_row)

        self.top_items_table = QTableWidget(0, 4)
        self.top_items_table.setHorizontalHeaderLabels(["Item", "Category", "Qty Sold", "Revenue"])
        self.top_items_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.top_items_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.top_items_table.setMinimumHeight(260)
        top_items_v.addWidget(self.top_items_table)
        outer.addWidget(top_items_box)

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
        today = QDate.currentDate()
        label = self.quick_range_combo.currentText()
        if label == "Last 7 days":
            frm = today.addDays(-6)
        elif label == "This month":
            frm = QDate(today.year(), today.month(), 1)
        elif label == "All time":
            frm = QDate(2000, 1, 1)
        else:  # Last 30 days
            frm = today.addDays(-29)
        return frm.toString("yyyy-MM-dd"), today.toString("yyyy-MM-dd")

    def refresh(self):
        date_from, date_to = self._date_range()
        self._refresh_kpis(date_from, date_to)
        self._refresh_trend_chart(date_from, date_to)
        self._refresh_category_chart(date_from, date_to)
        self._refresh_monthly_chart()
        self._refresh_top_items(date_from, date_to)
        self._refresh_top_customers(date_from, date_to)
        self._refresh_wishlist()

    def _clear_grid(self):
        while self.kpi_grid.count():
            child = self.kpi_grid.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _refresh_kpis(self, date_from, date_to):
        self._clear_grid()
        totals = self.db.stat_totals(date_from, date_to)
        daily = self.db.stat_daily_sales(date_from, date_to)
        revenues = [d["revenue"] for d in daily]
        mean_daily = statistics.mean(revenues) if revenues else 0
        std_daily = statistics.pstdev(revenues) if len(revenues) > 1 else 0

        cards = [
            make_stat_card("Total Revenue", rupees(totals["revenue"]), f"{totals['bill_count']} bill(s)"),
            make_stat_card("Pieces Sold", str(totals["pieces"]), ""),
            make_stat_card("Avg Bill Value", rupees(totals["avg_bill"]), ""),
            make_stat_card("Mean Daily Sales", rupees(mean_daily), f"over {len(revenues)} active day(s)"),
            make_stat_card("Std. Dev. (daily)", rupees(std_daily), "day-to-day variation"),
        ]
        for col, card in enumerate(cards):
            self.kpi_grid.addWidget(card, 0, col)

    def _refresh_trend_chart(self, date_from, date_to):
        daily = self.db.stat_daily_sales(date_from, date_to)
        self.trend_figure.clear()
        ax = self.trend_figure.add_subplot(111)
        if daily:
            dates = [d["d"] for d in daily]
            revenues = [d["revenue"] for d in daily]
            mean_val = statistics.mean(revenues)
            std_val = statistics.pstdev(revenues) if len(revenues) > 1 else 0

            ax.bar(dates, revenues, color=COLORS["primary"], alpha=0.85, label="Daily revenue")
            ax.axhline(mean_val, color=COLORS["accent"], linestyle="--", linewidth=1.5,
                       label=f"Mean: {rupees(mean_val)}")
            if std_val:
                ax.axhspan(max(mean_val - std_val, 0), mean_val + std_val,
                           color=COLORS["accent"], alpha=0.12, label=f"\u00b1 1 std dev ({rupees(std_val)})")
            ax.legend(fontsize=8, loc="upper left")
            ax.set_ylabel("Revenue (Rs.)")
            step = max(1, len(dates) // 10)
            ax.set_xticks(dates[::step])
            ax.tick_params(axis="x", rotation=45, labelsize=7)
            ax.tick_params(axis="y", labelsize=8)
        else:
            ax.text(0.5, 0.5, "No sales in this period", ha="center", va="center", color=COLORS["muted"])
            ax.set_xticks([])
            ax.set_yticks([])
        self.trend_canvas.draw()

    def _refresh_category_chart(self, date_from, date_to):
        rows = self.db.stat_category_sales(date_from, date_to)
        self.category_figure.clear()
        ax = self.category_figure.add_subplot(111)
        if rows:
            labels = [r["category"] or "Uncategorised" for r in rows]
            values = [r["revenue"] for r in rows]
            palette = [COLORS["primary"], COLORS["accent"], "#4a90a4", "#8e6c88", "#c4a35a", "#7a8b69"]
            colors = [palette[i % len(palette)] for i in range(len(labels))]
            # Percentage stays on the wedge; category names move to a side
            # legend instead of wedge labels, which overlap badly when one
            # category dominates (e.g. a single 100% slice).
            wedges, _texts, _autotexts = ax.pie(
                values, autopct="%1.0f%%", colors=colors,
                textprops={"fontsize": 8, "color": "white", "fontweight": "bold"},
                pctdistance=0.75,
            )
            ax.legend(
                wedges, labels, loc="center left", bbox_to_anchor=(1.0, 0.5),
                fontsize=8, frameon=False,
            )
        else:
            ax.text(0.5, 0.5, "No sales in this period", ha="center", va="center", color=COLORS["muted"])
            ax.set_xticks([])
            ax.set_yticks([])
        self.category_canvas.draw()

    @staticmethod
    def _month_add(year_month, delta):
        """'2026-01' + 1 -> '2026-02'. Works across year boundaries."""
        y, m = int(year_month[:4]), int(year_month[5:7])
        idx = y * 12 + (m - 1) + delta
        return f"{idx // 12:04d}-{idx % 12 + 1:02d}"

    @staticmethod
    def _month_label(year_month):
        y, m = int(year_month[:4]), int(year_month[5:7])
        return date(y, m, 1).strftime("%b %Y")

    def _refresh_monthly_chart(self):
        """Shows a trailing month-by-month view anchored to the current
        month, always at least MIN_MONTHS wide even if the shop has far
        less sales history than that -- otherwise a new shop with only a
        day or two of sales gets a single bar stretched across the whole
        chart, which doesn't read as a trend graph at all. Extends further
        back automatically to fit all real sales history, capped at
        MAX_MONTHS so the chart doesn't grow unbounded over the years."""
        MIN_MONTHS = 6
        MAX_MONTHS = 12

        rows = self.db.stat_monthly_sales()
        data = {r["month"]: r["revenue"] for r in rows} if rows else {}

        self.monthly_figure.clear()
        ax = self.monthly_figure.add_subplot(111)

        today_month = date.today().strftime("%Y-%m")
        earliest_with_sales = min(data.keys()) if data else today_month

        start = min(self._month_add(today_month, -(MIN_MONTHS - 1)), earliest_with_sales)
        earliest_allowed = self._month_add(today_month, -(MAX_MONTHS - 1))
        if start < earliest_allowed:
            start = earliest_allowed

        full_range = []
        cur = start
        while cur <= today_month:
            full_range.append(cur)
            cur = self._month_add(cur, 1)

        revenues = [data.get(m, 0) for m in full_range]
        labels = [self._month_label(m) for m in full_range]
        max_rev = max(revenues) if revenues else 0

        bars = ax.bar(labels, revenues, color=COLORS["primary"], width=0.55, zorder=3)
        ax.set_ylabel("Revenue (Rs.)")
        ax.set_xlim(-0.7, len(full_range) - 0.3)
        ax.set_ylim(0, max_rev * 1.25 if max_rev > 0 else 100)
        ax.tick_params(axis="x", rotation=35, labelsize=8)
        ax.tick_params(axis="y", labelsize=8)
        ax.grid(axis="y", color=COLORS["border"], linewidth=0.7, zorder=0)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

        for bar, rev in zip(bars, revenues):
            if rev > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2, bar.get_height() + max(max_rev * 0.03, 3),
                    rupees(rev), ha="center", va="bottom", fontsize=7, color=COLORS["text"],
                )

        if not data:
            ax.text(
                0.5, 0.5, "No sales recorded yet", ha="center", va="center",
                color=COLORS["muted"], transform=ax.transAxes,
            )

        self.monthly_canvas.draw()

    def _refresh_top_items(self, date_from, date_to):
        by = "quantity" if self.top_items_sort_combo.currentText() == "Quantity sold" else "revenue"
        rows = self.db.stat_top_items(date_from, date_to, limit=10, by=by)
        self.top_items_table.setRowCount(len(rows))
        for row_idx, r in enumerate(rows):
            self.top_items_table.setItem(row_idx, 0, QTableWidgetItem(r["name"]))
            self.top_items_table.setItem(row_idx, 1, QTableWidgetItem(r["category"] or "-"))
            self.top_items_table.setItem(row_idx, 2, QTableWidgetItem(str(r["total_qty"])))
            self.top_items_table.setItem(row_idx, 3, QTableWidgetItem(rupees(r["total_revenue"])))
            if row_idx == 0:
                for col in range(4):
                    self.top_items_table.item(row_idx, col).setBackground(QColor("#fff3cd"))

    def _refresh_top_customers(self, date_from, date_to):
        rows = self.db.stat_top_customers(date_from, date_to, limit=10)
        self.top_customers_table.setRowCount(len(rows))
        for row_idx, r in enumerate(rows):
            self.top_customers_table.setItem(row_idx, 0, QTableWidgetItem(r["name"]))
            self.top_customers_table.setItem(row_idx, 1, QTableWidgetItem(str(r["visits"])))
            self.top_customers_table.setItem(row_idx, 2, QTableWidgetItem(rupees(r["total_spent"])))

    def _refresh_wishlist(self):
        rows = self.db.all_open_wishlist()
        self.wishlist_table.setRowCount(len(rows))
        for row_idx, r in enumerate(rows):
            self.wishlist_table.setItem(row_idx, 0, QTableWidgetItem(r["customer_name"]))
            self.wishlist_table.setItem(row_idx, 1, QTableWidgetItem(r["item_description"]))
            self.wishlist_table.setItem(row_idx, 2, QTableWidgetItem(r["date_added"][:10]))
