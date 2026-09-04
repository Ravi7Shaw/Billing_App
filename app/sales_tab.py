"""
sales_tab.py
------------
A record of everything sold: filter by date range or search, view any
bill's line items, reprint, or export to CSV.
"""

import csv
from datetime import date, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QDateEdit,
    QComboBox, QFileDialog, QMessageBox, QDialog
)
from PySide6.QtCore import Qt, QDate

from widgets import rupees, make_heading, confirm_delete_password
from receipt import ReceiptDialog


class SalesTab(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self._bills_cache = []
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)
        outer.addWidget(make_heading("Sales History", "Every bill, what sold, and when"))

        filter_box = QGroupBox("Filters")
        filter_row = QHBoxLayout(filter_box)

        filter_row.addWidget(QLabel("From:"))
        self.date_from = QDateEdit(calendarPopup=True)
        self.date_from.setDisplayFormat("dd-MM-yyyy")
        self.date_from.setDate(QDate.currentDate().addMonths(-1))
        filter_row.addWidget(self.date_from)

        filter_row.addWidget(QLabel("To:"))
        self.date_to = QDateEdit(calendarPopup=True)
        self.date_to.setDisplayFormat("dd-MM-yyyy")
        self.date_to.setDate(QDate.currentDate())
        filter_row.addWidget(self.date_to)

        self.quick_range_combo = QComboBox()
        self.quick_range_combo.addItems(["Custom", "Today", "Last 7 days", "Last 30 days", "This month", "All time"])
        self.quick_range_combo.currentTextChanged.connect(self._apply_quick_range)
        filter_row.addWidget(self.quick_range_combo)

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
        self.bills_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.bills_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Fixed)
        self.bills_table.setColumnWidth(7, 90)
        self.bills_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.bills_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.bills_table.doubleClicked.connect(self._view_bill)
        outer.addWidget(self.bills_table, 1)

        hint = QLabel("Double-click a bill to view / reprint it.")
        hint.setStyleSheet("color: #6c757d; font-size: 11px;")
        outer.addWidget(hint)

    def _apply_quick_range(self, label):
        today = QDate.currentDate()
        if label == "Today":
            self.date_from.setDate(today)
            self.date_to.setDate(today)
        elif label == "Last 7 days":
            self.date_from.setDate(today.addDays(-6))
            self.date_to.setDate(today)
        elif label == "Last 30 days":
            self.date_from.setDate(today.addDays(-29))
            self.date_to.setDate(today)
        elif label == "This month":
            self.date_from.setDate(QDate(today.year(), today.month(), 1))
            self.date_to.setDate(today)
        elif label == "All time":
            self.date_from.setDate(QDate(2000, 1, 1))
            self.date_to.setDate(today)
        else:
            return
        self.refresh()

    def refresh(self):
        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to = self.date_to.date().toString("yyyy-MM-dd")
        search = self.search_input.text().strip() or None
        bills = self.db.search_bills(date_from=date_from, date_to=date_to, search_text=search)
        self._bills_cache = bills

        self.bills_table.setRowCount(len(bills))
        total_revenue = 0
        total_pieces = 0
        for row_idx, b in enumerate(bills):
            self.bills_table.setItem(row_idx, 0, QTableWidgetItem(b["bill_no"]))
            self.bills_table.setItem(row_idx, 1, QTableWidgetItem(b["bill_date"]))
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

            total_revenue += b["total"]
            total_pieces += b["piece_count"]
        self.bills_table.resizeRowsToContents()

        self.summary_label.setText(
            f"{len(bills)} bill(s) \u2022 {total_pieces} piece(s) sold \u2022 {rupees(total_revenue)} total"
        )

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
        self.db.delete_bill(bill_id)
        self.refresh()

    def _export_csv(self):
        if not self._bills_cache:
            QMessageBox.information(self, "Nothing to export", "No bills match the current filter.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Sales to CSV", "sales_export.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Bill No", "Date", "Customer", "Phone", "Pieces", "Subtotal", "Discount", "Total"])
            for b in self._bills_cache:
                writer.writerow([
                    b["bill_no"], b["bill_date"], b["customer_name"] or "Walk-in",
                    b["customer_phone"] or "", b["piece_count"], b["subtotal"],
                    b["discount_amount"], b["total"],
                ])
        QMessageBox.information(self, "Exported", f"Saved to:\n{path}")
