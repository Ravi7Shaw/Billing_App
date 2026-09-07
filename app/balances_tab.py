"""
balances_tab.py
---------------
Customer credit / balance ledger.

Shows:
- Every customer's total billed
- Total payments received
- Outstanding balance
- Bill-by-bill ledger
- Payment history
- Receive payment for outstanding bills
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QDialog, QFormLayout, QDoubleSpinBox, QComboBox, QDateEdit, QTextEdit
)
from PySide6.QtCore import Qt, QDate

from widgets import rupees, make_heading


class ReceivePaymentDialog(QDialog):
    def __init__(self, db, bill, parent=None):
        super().__init__(parent)
        self.db = db
        self.bill = bill
        self.setWindowTitle(f"Receive Payment - {bill['bill_no']}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        title = QLabel(
            f"<b>{bill['bill_no']}</b><br>"
            f"Bill total: {rupees(bill['total'])}<br>"
            f"Outstanding: <b>{rupees(db.get_bill_balance(bill['id']))}</b>"
        )
        title.setWordWrap(True)
        layout.addWidget(title)

        form = QFormLayout()

        self.amount_input = QDoubleSpinBox()
        self.amount_input.setRange(0.01, max(db.get_bill_balance(bill["id"]), 0.01))
        self.amount_input.setDecimals(2)
        self.amount_input.setPrefix("Rs. ")
        self.amount_input.setValue(db.get_bill_balance(bill["id"]))
        form.addRow("Amount received:", self.amount_input)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Cash", "Card", "UPI", "Other"])
        form.addRow("Payment mode:", self.mode_combo)

        self.date_input = QDateEdit(calendarPopup=True)
        self.date_input.setDate(QDate.currentDate())
        self.date_input.setMaximumDate(QDate.currentDate())
        self.date_input.setDisplayFormat("dd/MM/yyyy")
        form.addRow("Payment date:", self.date_input)

        self.notes_input = QTextEdit()
        self.notes_input.setPlaceholderText("Optional note...")
        self.notes_input.setMaximumHeight(80)
        form.addRow("Notes:", self.notes_input)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setProperty("role", "secondary")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        save = QPushButton("Receive Payment")
        save.clicked.connect(self._save)
        buttons.addWidget(save)

        layout.addLayout(buttons)

    def _save(self):
        amount = self.amount_input.value()
        mode = self.mode_combo.currentText()
        payment_date = self.date_input.date().toString("yyyy-MM-dd")
        notes = self.notes_input.toPlainText().strip()

        try:
            self.db.add_payment(
                self.bill["id"],
                amount,
                payment_mode=mode,
                payment_date=payment_date,
                notes=notes,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Payment failed", str(exc))
            return

        self.accept()


class BalancesTab(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.selected_customer_id = None
        self._customer_ids = []
        self._bill_ids = []

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        outer.addWidget(
            make_heading(
                "Customer Balances",
                "Track credit sales, outstanding balances, and receive later payments",
            )
        )

        # Summary cards
        summary = QHBoxLayout()
        summary.setSpacing(10)

        self.total_billed_label = self._make_summary_card(
            summary, "Total Billed", "Rs. 0.00"
        )
        self.total_paid_label = self._make_summary_card(
            summary, "Payments Received", "Rs. 0.00"
        )
        self.total_outstanding_label = self._make_summary_card(
            summary, "Outstanding", "Rs. 0.00"
        )

        outer.addLayout(summary)

        content = QHBoxLayout()
        content.setSpacing(14)
        outer.addLayout(content, 1)

        # ---------------- customers ----------------
        customers_box = QGroupBox("Customers With Balances")
        customers_box.setMinimumWidth(430)
        customers_v = QVBoxLayout(customers_box)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search customer name or phone...")
        self.search_input.textChanged.connect(self.refresh)
        search_row.addWidget(self.search_input)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("role", "secondary")
        refresh_btn.clicked.connect(self.refresh)
        search_row.addWidget(refresh_btn)
        customers_v.addLayout(search_row)

        self.customer_table = QTableWidget(0, 4)
        self.customer_table.setHorizontalHeaderLabels(
            ["Customer", "Phone", "Billed", "Balance"]
        )
        header = self.customer_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.customer_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.customer_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.customer_table.itemSelectionChanged.connect(self._on_customer_selected)
        customers_v.addWidget(self.customer_table)

        content.addWidget(customers_box)

        # ---------------- detail / ledger ----------------
        right = QVBoxLayout()
        right.setSpacing(12)
        content.addLayout(right, 1)

        details_box = QGroupBox("Customer Ledger")
        details_v = QVBoxLayout(details_box)

        self.customer_heading = QLabel("Select a customer")
        self.customer_heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        details_v.addWidget(self.customer_heading)

        self.customer_summary = QLabel("")
        self.customer_summary.setWordWrap(True)
        self.customer_summary.setStyleSheet("color: #6c757d; font-size: 12px;")
        details_v.addWidget(self.customer_summary)

        self.bill_table = QTableWidget(0, 7)
        self.bill_table.setHorizontalHeaderLabels(
            ["Bill No", "Date", "Total", "Paid", "Balance", "Status", "Action"]
        )
        bh = self.bill_table.horizontalHeader()
        bh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        bh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        bh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        bh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        bh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        bh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        bh.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.bill_table.setEditTriggers(QTableWidget.NoEditTriggers)
        details_v.addWidget(self.bill_table, 1)

        right.addWidget(details_box, 2)

        payments_box = QGroupBox("Payment History")
        payments_v = QVBoxLayout(payments_box)

        self.payment_table = QTableWidget(0, 5)
        self.payment_table.setHorizontalHeaderLabels(
            ["Date", "Bill No", "Amount", "Mode", "Notes"]
        )
        ph = self.payment_table.horizontalHeader()
        ph.setSectionResizeMode(4, QHeaderView.Stretch)
        self.payment_table.setEditTriggers(QTableWidget.NoEditTriggers)
        payments_v.addWidget(self.payment_table)

        right.addWidget(payments_box, 1)

    def _make_summary_card(self, parent_layout, title, value):
        box = QGroupBox(title)
        v = QVBoxLayout(box)
        label = QLabel(value)
        label.setProperty("role", "total")
        v.addWidget(label)
        parent_layout.addWidget(box)
        return label

    def refresh(self):
        text = self.search_input.text().strip().lower() if hasattr(self, "search_input") else ""

        rows = self.db.get_customer_balances()

        total_billed = sum(float(r["total_billed"] or 0) for r in rows)
        total_paid = sum(float(r["total_paid"] or 0) for r in rows)
        total_balance = max(total_billed - total_paid, 0)

        self.total_billed_label.setText(rupees(total_billed))
        self.total_paid_label.setText(rupees(total_paid))
        self.total_outstanding_label.setText(rupees(total_balance))

        filtered = []
        for row in rows:
            name = (row["name"] or "").lower()
            phone = (row["phone"] or "").lower()
            balance = max(float(row["total_billed"] or 0) - float(row["total_paid"] or 0), 0)

            if text and text not in name and text not in phone:
                continue

            # Keep customers visible even when fully paid; this is useful for
            # checking a customer's complete ledger.
            filtered.append((row, balance))

        self.customer_table.setRowCount(len(filtered))
        self._customer_ids = []

        for i, (row, balance) in enumerate(filtered):
            self._customer_ids.append(row["id"])
            self.customer_table.setItem(i, 0, QTableWidgetItem(row["name"]))
            self.customer_table.setItem(i, 1, QTableWidgetItem(row["phone"] or "-"))
            self.customer_table.setItem(i, 2, QTableWidgetItem(rupees(row["total_billed"] or 0)))
            self.customer_table.setItem(i, 3, QTableWidgetItem(rupees(balance)))

        if self.selected_customer_id:
            self._load_customer(self.selected_customer_id)

    def _on_customer_selected(self):
        rows = self.customer_table.selectionModel().selectedRows()
        if not rows:
            return

        row_idx = rows[0].row()
        if row_idx >= len(self._customer_ids):
            return

        self.selected_customer_id = self._customer_ids[row_idx]
        self._load_customer(self.selected_customer_id)

    def _load_customer(self, customer_id):
        customer = self.db.get_customer_by_id(customer_id)
        if not customer:
            self.selected_customer_id = None
            return

        balance_info = self.db.get_customer_balance(customer_id)
        billed = float(balance_info["total_billed"] or 0)
        paid = float(balance_info["total_paid"] or 0)
        balance = max(billed - paid, 0)

        self.customer_heading.setText(customer["name"])
        self.customer_summary.setText(
            f"Phone: {customer['phone'] or '-'}  •  "
            f"Total billed: {rupees(billed)}  •  "
            f"Paid: {rupees(paid)}  •  "
            f"Outstanding: {rupees(balance)}"
        )

        history = self.db.get_customer_purchase_history(customer_id)

        self.bill_table.setRowCount(len(history))
        self._bill_ids = []

        all_payments = []

        for i, bill in enumerate(history):
            bill_id = bill["id"]
            self._bill_ids.append(bill_id)

            bill_paid = float(self.db.get_bill_paid_amount(bill_id) or 0)
            bill_balance = max(float(bill["total"]) - bill_paid, 0)

            self.bill_table.setItem(i, 0, QTableWidgetItem(bill["bill_no"]))
            self.bill_table.setItem(i, 1, QTableWidgetItem(bill["bill_date"]))
            self.bill_table.setItem(i, 2, QTableWidgetItem(rupees(bill["total"])))
            self.bill_table.setItem(i, 3, QTableWidgetItem(rupees(bill_paid)))
            self.bill_table.setItem(i, 4, QTableWidgetItem(rupees(bill_balance)))

            status = "Paid" if bill_balance <= 0.005 else "Outstanding"
            self.bill_table.setItem(i, 5, QTableWidgetItem(status))

            if bill_balance > 0.005:
                receive_btn = QPushButton("Receive")
                receive_btn.clicked.connect(
                    lambda _, b=dict(bill): self._receive_payment(b)
                )
                self.bill_table.setCellWidget(i, 6, receive_btn)
            else:
                paid_label = QLabel("✓ Paid")
                paid_label.setAlignment(Qt.AlignCenter)
                self.bill_table.setCellWidget(i, 6, paid_label)

            for payment in self.db.get_bill_payment_history(bill_id):
                all_payments.append((payment, bill["bill_no"]))

        self.payment_table.setRowCount(len(all_payments))

        for i, (payment, bill_no) in enumerate(all_payments):
            self.payment_table.setItem(i, 0, QTableWidgetItem(payment["payment_date"]))
            self.payment_table.setItem(i, 1, QTableWidgetItem(bill_no))
            self.payment_table.setItem(i, 2, QTableWidgetItem(rupees(payment["amount"])))
            self.payment_table.setItem(i, 3, QTableWidgetItem(payment["payment_mode"]))
            self.payment_table.setItem(i, 4, QTableWidgetItem(payment["notes"] or ""))

    def _receive_payment(self, bill):
        current_balance = self.db.get_bill_balance(bill["id"])

        if current_balance <= 0.005:
            QMessageBox.information(self, "Already paid", "This bill is already fully paid.")
            self._load_customer(self.selected_customer_id)
            return

        dialog = ReceivePaymentDialog(self.db, bill, self)

        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            if self.selected_customer_id:
                self._load_customer(self.selected_customer_id)
            QMessageBox.information(self, "Payment received", "Payment was recorded successfully.")
