"""
sales_tab.py
------------
A record of everything sold: filter by date range or search, view any
bill's line items, reprint, or export to CSV.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)

from widgets import rupees, make_heading, confirm_delete_password
from receipt import ReceiptDialog
from period_filter import PeriodFilter
from report_export import export_csv
from paging import Pager


class SalesTab(QWidget):
    def __init__(self, db, autoload=True):
        super().__init__()
        self.db = db
        self._bills_cache = []
        self._build_ui()
        if autoload:
            self.refresh()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)
        outer.addWidget(make_heading("Sales History", "Every bill, what sold, and when"))

        self.period = PeriodFilter()
        self.period.changed.connect(self.refresh)
        outer.addWidget(self.period)
        self.date_from, self.date_to = self.period.date_from, self.period.date_to
        self.quick_range_combo = self.period.preset
        filter_box = QGroupBox("Search and export")
        filter_row = QHBoxLayout(filter_box)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search bill no / customer / phone...")
        filter_row.addWidget(self.search_input, 1)

        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self.refresh)
        filter_row.addWidget(search_btn)

        export_btn = QPushButton("Export CSV")
        export_btn.setProperty("role", "secondary")
        export_btn.clicked.connect(self._export_csv)
        filter_row.addWidget(export_btn)

        outer.addWidget(filter_box)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-weight: 600; color: #2f6f4f;")
        outer.addWidget(self.summary_label)

        self.bills_table = QTableWidget(0, 8)
        self.bills_table.verticalHeader().setDefaultSectionSize(40)
        self.bills_table.setHorizontalHeaderLabels(
            ["Bill No", "Date", "Customer", "Pieces", "Subtotal", "Discount", "Total", ""]
        )
        self.bills_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.bills_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.bills_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.bills_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Fixed)
        self.bills_table.setColumnWidth(7, 90)
        self.bills_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.bills_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.bills_table.doubleClicked.connect(self._view_bill)
        outer.addWidget(self.bills_table, 1)
        self.pager = Pager()
        self.pager.changed.connect(self.refresh)
        outer.addWidget(self.pager)

        hint = QLabel("Double-click a bill to view / reprint it.")
        hint.setStyleSheet("color: #6c757d; font-size: 11px;")
        outer.addWidget(hint)

    def refresh(self):
        try:
            date_from, date_to = self.period.bounds()
        except ValueError as exc:
            self.pager.set_total(0)
            self._bills_cache = []
            self.bills_table.setRowCount(0)
            self.summary_label.setText(str(exc))
            return False
        search = self.search_input.text().strip() or None
        self.pager.filter((date_from,date_to,search))
        result = self.db.sales_page(date_from,date_to,search, self.pager.size,self.pager.offset)
        self.pager.set_total(result['summary']['count'])
        bills = result['rows']
        self._bills_cache = bills

        self.bills_table.setRowCount(len(bills))
        total_revenue = result['summary']['revenue']
        total_pieces = result['summary']['pieces']
        for row_idx, b in enumerate(bills):
            self.bills_table.setItem(row_idx, 0, QTableWidgetItem(b["bill_no"]))
            self.bills_table.setItem(row_idx, 1, QTableWidgetItem(b["bill_date"][:10]))
            self.bills_table.setItem(row_idx, 2, QTableWidgetItem(b["customer_name"] or "Walk-in"))
            self.bills_table.setItem(row_idx, 3, QTableWidgetItem(str(b["piece_count"])))
            self.bills_table.setItem(row_idx, 4, QTableWidgetItem(rupees(b["subtotal"])))
            self.bills_table.setItem(row_idx, 5, QTableWidgetItem(rupees(b["discount_amount"])))
            self.bills_table.setItem(row_idx, 6, QTableWidgetItem(rupees(b["total"])))

            remove_btn = QPushButton("Remove")
            remove_btn.setProperty("role", "danger")
            remove_btn.setProperty("compact", "true")
            bill_id = b["id"]
            remove_btn.clicked.connect(lambda _, bid=bill_id, no=b["bill_no"]: self._delete_bill(bid, no))
            self.bills_table.setCellWidget(row_idx, 7, remove_btn)

        self.bills_table.resizeRowsToContents()

        self.summary_label.setText(
            f"{result['summary']['count']} bill(s) \u2022 {total_pieces} piece(s) sold \u2022 {rupees(total_revenue)} total"
        )

        return True

    def _view_bill(self):
        rows = self.bills_table.selectionModel().selectedRows()
        if not rows:
            return
        bill_id = self._bills_cache[rows[0].row()]["id"]
        bill_row, bill_items = self.db.get_bill(bill_id)
        dialog = ReceiptDialog(bill_row, bill_items, self)
        dialog.exec()

    def _delete_bill(self, bill_id, bill_no):
        confirm = QMessageBox.question(
            self, "Remove Bill",
            f"Remove bill '{bill_no}'? This cannot be undone."
        )
        if confirm != QMessageBox.Yes:
            return
        if not confirm_delete_password(self, f"remove bill '{bill_no}'"):
            return
        try:
            self.db.delete_bill(bill_id)
        except ValueError as exc:
            QMessageBox.warning(self, 'Cannot remove bill', str(exc))
            return
        self.refresh()

    def _export_csv(self):
        if not self.refresh():
            return
        bills = self.db.export_bill_rows(*self.period.bounds(), self.search_input.text().strip())
        export_csv(self, "Export Sales", "sales_export.csv",
                   ["Bill No", "Date", "Customer", "Phone", "Pieces", "Subtotal", "Discount", "Total"],
                   ([b["bill_no"], b["bill_date"], b["customer_name"] or "Walk-in",
                     b["customer_phone"] or "", b["piece_count"], b["subtotal"],
                     b["discount_amount"], b["total"]] for b in bills))
