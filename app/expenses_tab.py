"""
expenses_tab.py
---------------
Expense management for the Cloth Shop Billing System.

Features:
- Add expense with date, category, amount, payment method and description
- View expense history
- Filter by date range/category
- Delete an expense
- Running total for the selected period
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QDoubleSpinBox,
    QComboBox,
    QDateEdit,
    QTextEdit,
    QFormLayout,
)
from PySide6.QtCore import QDate

from widgets import rupees, make_heading, confirm_delete_password
from period_filter import PeriodFilter
from report_export import export_csv
from money import sum_money
from paging import Pager


class ExpensesTab(QWidget):
    def __init__(self, db, autoload=True):
        super().__init__()
        self.db = db
        self._expense_ids = []

        self._build_ui()
        if autoload:
            self.refresh()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        outer.addWidget(
            make_heading(
                "Expenses",
                "Record shop expenses so profit statistics include real business costs",
            )
        )

        # ---------------- Add expense ----------------
        add_box = QGroupBox("Add Expense")
        form = QFormLayout(add_box)
        form.setSpacing(10)

        self.date_input = QDateEdit(calendarPopup=True)
        self.date_input.setDate(QDate.currentDate())
        self.date_input.setMaximumDate(QDate.currentDate())
        self.date_input.setDisplayFormat("dd/MM/yyyy")
        form.addRow("Date:", self.date_input)
        self.godown_input = QComboBox()
        for location in self.db.get_godowns():
            self.godown_input.addItem(location['name'], location['id'])
        form.addRow('Godown:', self.godown_input)

        self.category_input = QComboBox()
        self.category_input.setEditable(True)
        self.category_input.addItems(
            [
                "Rent",
                "Electricity",
                "Water",
                "Internet",
                "Salary",
                "Transport",
                "Packaging",
                "Repairs",
                "Supplies",
                "Other",
            ]
        )
        form.addRow("Category:", self.category_input)

        self.amount_input = QDoubleSpinBox()
        self.amount_input.setRange(0.01, 999999999.99)
        self.amount_input.setDecimals(2)
        self.amount_input.setPrefix("Rs. ")
        self.amount_input.setSingleStep(100)
        form.addRow("Amount:", self.amount_input)

        self.payment_mode_input = QComboBox()
        self.payment_mode_input.addItems(
            ["Cash", "Card", "UPI", "Bank Transfer", "Other"]
        )
        form.addRow("Payment mode:", self.payment_mode_input)

        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText(
            "Optional note, e.g. September shop rent"
        )
        self.description_input.setMaximumHeight(70)
        form.addRow("Description:", self.description_input)

        add_row = QHBoxLayout()
        add_row.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.setProperty("role", "secondary")
        clear_btn.clicked.connect(self._clear_form)
        add_row.addWidget(clear_btn)

        add_btn = QPushButton("Add Expense")
        add_btn.clicked.connect(self._add_expense)
        add_row.addWidget(add_btn)

        form.addRow("", add_row)

        outer.addWidget(add_box)

        # ---------------- Filters ----------------
        self.period = PeriodFilter()
        self.period.changed.connect(self.refresh)
        outer.addWidget(self.period)
        self.date_from, self.date_to = self.period.date_from, self.period.date_to
        filter_box = QGroupBox("Expense History")
        filter_row = QHBoxLayout(filter_box)

        self.category_filter = QComboBox()
        self.category_filter.addItem("All categories")
        self.category_filter.currentTextChanged.connect(self.refresh)
        filter_row.addWidget(self.category_filter)

        search_btn = QPushButton("Search")
        search_btn.setProperty("role", "secondary")
        search_btn.clicked.connect(self.refresh)
        filter_row.addWidget(search_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("role", "secondary")
        refresh_btn.clicked.connect(self.refresh)
        filter_row.addWidget(refresh_btn)

        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self._export_csv)
        filter_row.addWidget(export_btn)
        outer.addWidget(filter_box)

        # ---------------- Summary ----------------
        self.summary_label = QLabel("Total expenses: Rs. 0.00")
        self.summary_label.setStyleSheet(
            "font-weight: 700; color: #2f6f4f; padding: 4px;"
        )
        outer.addWidget(self.summary_label)

        # ---------------- Table ----------------
        self.expense_table = QTableWidget(0, 8)
        self.expense_table.setHorizontalHeaderLabels(
            [
                "Date",
                "Category",
                "Amount",
                "Payment",
                "Description",
                "Added",
                "Employee",
                "",
            ]
        )

        header = self.expense_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)

        self.expense_table.setEditTriggers(QTableWidget.NoEditTriggers)
        outer.addWidget(self.expense_table, 1)
        self.pager = Pager()
        self.pager.changed.connect(self.refresh)
        outer.addWidget(self.pager)

    def _add_expense(self):
        category = self.category_input.currentText().strip()
        amount = self.amount_input.value()
        payment_mode = self.payment_mode_input.currentText()
        expense_date = self.date_input.date().toString("yyyy-MM-dd")
        description = self.description_input.toPlainText().strip()

        if not category:
            QMessageBox.warning(self, "Missing category", "Enter an expense category.")
            self.category_input.setFocus()
            return

        if amount <= 0:
            QMessageBox.warning(
                self, "Invalid amount", "Expense amount must be greater than zero."
            )
            self.amount_input.setFocus()
            return

        try:
            self.db.add_expense(
                expense_date=expense_date,
                godown_id=self.godown_input.currentData() or 1,
                category=category,
                amount=amount,
                payment_mode=payment_mode,
                description=description,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Could not add expense", str(exc))
            return

        self._clear_form()
        self._refresh_category_filter()
        self.refresh()

    def _clear_form(self):
        self.date_input.setDate(QDate.currentDate())
        self.amount_input.setValue(0)
        self.description_input.clear()

    def _refresh_category_filter(self):
        current = self.category_filter.currentText()
        categories = self.db.expense_categories()

        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("All categories")
        self.category_filter.addItems(categories)

        index = self.category_filter.findText(current)
        if index >= 0:
            self.category_filter.setCurrentIndex(index)

        self.category_filter.blockSignals(False)

    def refresh(self):
        if not hasattr(self, "expense_table"):
            return

        self._refresh_category_filter()
        selected = self.godown_input.currentData()
        self.godown_input.clear()
        for location in self.db.get_godowns():
            self.godown_input.addItem(location['name'], location['id'])
        index = self.godown_input.findData(selected)
        if index >= 0:
            self.godown_input.setCurrentIndex(index)

        try:
            start_date, end_date = self.period.bounds()
        except ValueError as exc:
            self.summary_label.setText(str(exc))
            self.expense_table.setRowCount(0)
            self.pager.set_total(0)
            self._expenses_cache = []
            return False

        category = self.category_filter.currentText()

        category = None if category == 'All categories' else category
        self.pager.filter((start_date,end_date,category))
        result = self.db.expense_page(start_date,end_date,category,self.pager.size,self.pager.offset)
        self.pager.set_total(result['count'])
        rows = result['rows']
        self._expenses_cache = rows
        total = result['total']

        self.summary_label.setText(
            f"Total expenses: {rupees(total)}   •   {result['count']} expense(s)"
        )

        self.expense_table.setRowCount(len(rows))
        self._expense_ids = []

        for row_idx, expense in enumerate(rows):
            self._expense_ids.append(expense["id"])

            self.expense_table.setItem(
                row_idx, 0, QTableWidgetItem(expense["expense_date"])
            )
            self.expense_table.setItem(
                row_idx, 1, QTableWidgetItem(expense["category"])
            )
            self.expense_table.setItem(
                row_idx, 2, QTableWidgetItem(rupees(expense["amount"]))
            )
            self.expense_table.setItem(
                row_idx, 3, QTableWidgetItem(expense["payment_mode"] or "-")
            )
            self.expense_table.setItem(
                row_idx, 4, QTableWidgetItem(expense["description"] or "")
            )
            self.expense_table.setItem(
                row_idx, 5, QTableWidgetItem((expense["created_at"] or "")[:16])
            )

            delete_btn = QPushButton("Delete")
            delete_btn.setProperty("role", "danger")
            expense_id = expense["id"]
            delete_btn.clicked.connect(
                lambda _, eid=expense_id: self._delete_expense(eid)
            )
            self.expense_table.setItem(row_idx, 6, QTableWidgetItem(
                f"{expense['employee_name']} (#{expense['employee_id']})" if expense['employee_id'] is not None else ""))
            self.expense_table.setCellWidget(row_idx, 7, delete_btn)

        return True

    def _delete_expense(self, expense_id):
        if not confirm_delete_password(self, "delete this expense"):
            return

        try:
            self.db.delete_expense(expense_id)
        except Exception as exc:
            QMessageBox.warning(self, "Could not delete expense", str(exc))
            return

        self.refresh()

    def _export_csv(self):
        if not self.refresh():
            return
        category = self.category_filter.currentText()
        rows = self.db.export_expense_rows(*self.period.bounds(), None if category == 'All categories' else category)
        export_csv(self, "Export Expenses", "expenses_export.csv",
                   ["Date", "Category", "Amount", "Payment", "Description", "Added", "Employee ID", "Employee"],
                   ([r["expense_date"], r["category"], r["amount"], r["payment_mode"],
                     r["description"], r["created_at"], r["employee_id"], r["employee_name"]] for r in rows))
