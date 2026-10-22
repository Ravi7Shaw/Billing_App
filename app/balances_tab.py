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
    QDialog, QFormLayout, QDoubleSpinBox, QComboBox, QDateEdit, QTextEdit, QSplitter, QTabWidget
)
from PySide6.QtCore import Qt, QDate

from widgets import rupees, make_heading
from money import sum_money
from period_filter import PeriodFilter
from paging import Pager
from report_export import export_csv
from receipt import ReceiptDialog


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
    def __init__(self, db, autoload=True):
        super().__init__()
        self.db = db
        self.selected_customer_id = None
        self._customer_ids = []
        self._bill_ids = []
        self._bills_cache = []
        self._payments_cache = []
        self._build_ui()
        if autoload:
            self.refresh()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.addWidget(make_heading('Customer Balances', 'Click a bill or payment row to open its receipt'))
        self.period = PeriodFilter('All time')
        self.period.changed.connect(self.refresh)
        outer.addWidget(self.period)
        search = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('Search customer, phone or bill number…')
        self.search_input.textChanged.connect(self.refresh)
        search.addWidget(self.search_input, 1)
        export = QPushButton('Export matched bills CSV')
        export.clicked.connect(self._export_csv)
        search.addWidget(export)
        outer.addLayout(search)
        summary = QHBoxLayout()
        self.total_billed_label = self._make_summary_card(summary, 'Billed in period')
        self.total_paid_label = self._make_summary_card(summary, 'Paid toward these bills')
        self.total_outstanding_label = self._make_summary_card(summary, 'Still outstanding')
        outer.addLayout(summary)
        note = QLabel('Bills use bill dates. Paid / outstanding include all payments to date. Payment history uses payment dates.')
        note.setWordWrap(True)
        outer.addWidget(note)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter, 1)
        customers = QGroupBox('Customers / walk-in sales')
        customers.setMinimumWidth(240)
        left = QVBoxLayout(customers)
        self.customer_table = self._table(['Customer', 'Phone', 'Billed', 'Balance'])
        self.customer_table.itemSelectionChanged.connect(self._on_customer_selected)
        left.addWidget(self.customer_table)
        self.customer_pager = Pager()
        self.customer_pager.changed.connect(self.refresh)
        left.addWidget(self.customer_pager)
        self.splitter.addWidget(customers)
        self.detail_tabs = QTabWidget()
        self.splitter.addWidget(self.detail_tabs)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes([400, 850])
        ledger = QGroupBox('Bills issued in selected period')
        ledger.setMinimumHeight(210)
        right = QVBoxLayout(ledger)
        self.customer_heading = QLabel('Select a customer')
        self.customer_heading.setWordWrap(True)
        self.customer_summary = QLabel()
        self.customer_summary.setWordWrap(True)
        right.addWidget(self.customer_heading)
        right.addWidget(self.customer_summary)
        self.bill_table = self._table(['Bill No', 'Date', 'Total', 'Paid', 'Balance', 'Status', 'Action'])
        self.bill_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Fixed)
        self.bill_table.setColumnWidth(6, 140)
        self.bill_table.cellClicked.connect(self._open_bill)
        right.addWidget(self.bill_table, 1)
        self.bill_pager = Pager()
        self.bill_pager.changed.connect(lambda: self._load_customer(self.selected_customer_id))
        right.addWidget(self.bill_pager)
        self.detail_tabs.addTab(ledger, 'Bills')
        history = QGroupBox('Payments received in selected period')
        history.setMinimumHeight(160)
        history_layout = QVBoxLayout(history)
        export_payments = QPushButton('Export customer payments CSV')
        export_payments.clicked.connect(self._export_payments)
        history_layout.addWidget(export_payments)
        self.payment_table = self._table(['Date', 'Bill No', 'Amount', 'Mode', 'Notes'], stretch=4)
        self.payment_table.cellClicked.connect(self._open_payment)
        history_layout.addWidget(self.payment_table)
        self.payment_pager = Pager()
        self.payment_pager.changed.connect(lambda: self._load_customer(self.selected_customer_id))
        history_layout.addWidget(self.payment_pager)
        self.detail_tabs.addTab(history, 'Payment history')

    @staticmethod
    def _table(headers, stretch=0):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(42)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
        table.horizontalHeader().setMinimumSectionSize(90)
        return table

    @staticmethod
    def _make_summary_card(layout, title):
        box = QGroupBox(title)
        column = QVBoxLayout(box)
        value = QLabel(rupees(0))
        value.setProperty('role', 'total')
        column.addWidget(value)
        layout.addWidget(box)
        return value

    def refresh(self):
        try:
            start, end = self.period.bounds()
        except ValueError as exc:
            self._bills_cache = self._payments_cache = []
            self._groups = {}
            self._customer_ids = []
            self.customer_pager.set_total(0)
            self.customer_table.setRowCount(0)
            self._clear_detail()
            for label in (self.total_billed_label, self.total_paid_label, self.total_outstanding_label):
                label.setText(rupees(0))
            self.customer_heading.setText(str(exc))
            return False
        text = self.search_input.text().strip()
        self._groups = self.db.balance_summary(start, end, text)
        for label, field in ((self.total_billed_label, 'billed'), (self.total_paid_label, 'paid'),
                             (self.total_outstanding_label, 'balance')):
            label.setText(rupees(sum_money(g[field] for g in self._groups.values())))
        ids = sorted(self._groups, key=lambda k: (self._groups[k]['name'].casefold(), k))
        self.customer_pager.filter((start, end, text))
        self.customer_pager.set_total(len(ids))
        self._customer_ids = ids[self.customer_pager.offset:self.customer_pager.offset+self.customer_pager.size]
        self.customer_table.blockSignals(True)
        self.customer_table.setRowCount(len(self._customer_ids))
        for row, key in enumerate(self._customer_ids):
            group = self._groups[key]
            values = [group['name'], group['phone'] or '-',
                      rupees(group['billed']), rupees(group['balance'])]
            for col, value in enumerate(values):
                self.customer_table.setItem(row, col, QTableWidgetItem(value))
        if self.selected_customer_id not in self._customer_ids:
            self.selected_customer_id = self._customer_ids[0] if self._customer_ids else None
        if self.selected_customer_id is not None:
            self.customer_table.selectRow(self._customer_ids.index(self.selected_customer_id))
        self.customer_table.blockSignals(False)
        if self.selected_customer_id is not None:
            self._load_customer(self.selected_customer_id)
        else:
            self._clear_detail()
        return True

    def _clear_detail(self):
        self.selected_customer_id = None
        self._bill_ids = []
        self._shown_payments = []
        self.bill_table.setRowCount(0)
        self.payment_table.setRowCount(0)
        self.bill_pager.set_total(0)
        self.payment_pager.set_total(0)
        self.customer_heading.setText('No matching records')
        self.customer_summary.clear()

    def _on_customer_selected(self):
        rows = self.customer_table.selectionModel().selectedRows()
        if rows and rows[0].row() < len(self._customer_ids):
            self.selected_customer_id = self._customer_ids[rows[0].row()]
            self._load_customer(self.selected_customer_id)

    def _load_customer(self, key):
        group = self._groups.get(key)
        if group is None:
            self._clear_detail()
            return
        start, end = self.period.bounds()
        search = self.search_input.text().strip()
        for pager in (self.bill_pager, self.payment_pager):
            pager.filter((key, start, end, search))
        result = self.db.customer_ledger_page(key, start, end, search,
                    self.bill_pager.size, self.bill_pager.offset, self.payment_pager.offset)
        self.bill_pager.set_total(result['bill_count'])
        self.payment_pager.set_total(result['payment_count'])
        bills = result['bills']
        self._bills_cache = bills
        self.customer_heading.setText(group['name'])
        self.customer_summary.setText(f"Phone: {group['phone'] or '-'} · {result['bill_count']} bill(s) · "
                                      f"Outstanding: {rupees(group['balance'])}")
        self._bill_ids = [b['id'] for b in bills]
        self.bill_table.setRowCount(len(bills))
        for row, bill in enumerate(bills):
            values = [bill['bill_no'], bill['bill_date'][:10], rupees(bill['total']),
                      rupees(bill['paid']), rupees(bill['balance']),
                      'Paid' if bill['balance'] == 0 else 'Outstanding']
            for col, value in enumerate(values):
                self.bill_table.setItem(row, col, QTableWidgetItem(value))
            if bill['balance'] > 0:
                button = QPushButton('Receive payment')
                button.setProperty('compact', 'true')
                button.setMinimumHeight(32)
                button.clicked.connect(lambda _, b=bill: self._receive_payment(b))
                self.bill_table.setCellWidget(row, 6, button)
            else:
                label = QLabel('Paid')
                label.setAlignment(Qt.AlignCenter)
                self.bill_table.setCellWidget(row, 6, label)
        self._shown_payments = result['payments']
        self.payment_table.setRowCount(len(self._shown_payments))
        for row, payment in enumerate(self._shown_payments):
            for col, value in enumerate([payment['payment_date'][:10], payment['bill_no'],
                                         rupees(payment['amount']), payment['payment_mode'], payment['notes'] or '']):
                self.payment_table.setItem(row, col, QTableWidgetItem(value))

    def _show_receipt(self, bill_id):
        bill, items = self.db.get_bill(bill_id)
        if bill:
            ReceiptDialog(bill, items, self).exec()

    def _open_bill(self, row, column):
        if column != 6 and 0 <= row < len(self._bill_ids):
            self._show_receipt(self._bill_ids[row])

    def _open_payment(self, row, column):
        if 0 <= row < len(self._shown_payments):
            self._show_receipt(self._shown_payments[row]['bill_id'])

    def _receive_payment(self, bill):
        if self.db.get_bill_balance(bill['id']) <= 0:
            QMessageBox.information(self, 'Already paid', 'This bill is already fully paid.')
        elif ReceivePaymentDialog(self.db, bill, self).exec() == QDialog.Accepted:
            QMessageBox.information(self, 'Payment received', 'Payment was recorded successfully.')
        self.refresh()

    def _export_csv(self):
        if not self.refresh():
            return
        export_csv(self, 'Export balances', 'balances_export.csv',
                   ['Bill No', 'Bill Date', 'Customer', 'Phone', 'Billed', 'Paid to date', 'Outstanding'],
                   ([b['bill_no'], b['bill_date'], b['customer_name'] or 'Walk-in', b['customer_phone'] or '',
                     b['total'], b['paid'], b['balance']] for b in self.db.export_bill_rows(*self.period.bounds(), self.search_input.text().strip())))

    def _export_payments(self):
        if not self.refresh():
            return
        if self.selected_customer_id is None:
            return
        export_csv(self, 'Export customer payments', 'payments_export.csv',
                   ['Date', 'Bill No', 'Amount', 'Mode', 'Notes'],
                   ([p['payment_date'], p['bill_no'], p['amount'], p['payment_mode'], p['notes'] or '']
                    for p in self.db.export_payment_rows(*self.period.bounds(), self.search_input.text().strip(), customer=self.selected_customer_id)))
