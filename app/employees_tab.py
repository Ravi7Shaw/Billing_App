"""Admin employee directory, monthly attendance sheet and expense history."""
from datetime import date, timedelta
from PySide6.QtCore import Qt,QDate
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,
    QTableWidget,QTableWidgetItem,QHeaderView,QSplitter,QDateEdit,QComboBox,QTabWidget,
    QDialog,QFormLayout,QCheckBox,QDialogButtonBox,QDoubleSpinBox,QMessageBox)
from employees import ATTENDANCE_STATUSES
from widgets import make_heading,rupees,confirm_delete_password
from paging import Pager
from report_export import export_csv


class EmployeesTab(QWidget):
    def __init__(self,db,autoload=True):
        super().__init__()
        self.db = db
        self.employee_id = None
        self._employees = []
        self._report = None
        self._days = []
        self._build_ui()
        if autoload:
            self.refresh()

    @staticmethod
    def _table(headers,stretch):
        table = QTableWidget(0,len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(stretch,QHeaderView.Stretch)
        return table

    @staticmethod
    def _fill(table,rows):
        table.setRowCount(len(rows))
        for r,values in enumerate(rows):
            for c,value in enumerate(values):
                table.setItem(r,c,QTableWidgetItem(str(value)))

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.addWidget(make_heading('Employees','Monthly attendance and payments recorded by the admin'))
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search employee name (starts with…)')
        self.search.returnPressed.connect(self.refresh)
        filters.addWidget(self.search,1)
        self.show_archived = QCheckBox('Include archived')
        self.show_archived.toggled.connect(self.refresh)
        filters.addWidget(self.show_archived)
        find = QPushButton('Search / Refresh');find.clicked.connect(self.refresh)
        filters.addWidget(find)
        filters.addWidget(QLabel('Month:'))
        self.month = QDateEdit(calendarPopup=True)
        self.month.setDisplayFormat('MMMM yyyy')
        self.month.setDate(QDate.currentDate())
        self.month.dateChanged.connect(self._month_changed)
        filters.addWidget(self.month)
        outer.addLayout(filters)
        split = QSplitter(Qt.Horizontal);outer.addWidget(split,1)
        directory = QWidget();left = QVBoxLayout(directory)
        buttons = QHBoxLayout()
        add = QPushButton('Add employee');add.clicked.connect(lambda:self._edit_employee(False))
        buttons.addWidget(add)
        self.edit_button = QPushButton('Edit / Archive');self.edit_button.clicked.connect(lambda:self._edit_employee(True))
        buttons.addWidget(self.edit_button);left.addLayout(buttons)
        self.employee_table = self._table(['ID','Name','Phone','Status'],1)
        self.employee_table.itemSelectionChanged.connect(self._select_employee)
        left.addWidget(self.employee_table,1)
        self.pager = Pager();self.pager.size = 100;self.pager.changed.connect(self.refresh)
        left.addWidget(self.pager);split.addWidget(directory)
        detail = QWidget();right = QVBoxLayout(detail)
        self.employee_label = QLabel('Select an employee')
        self.employee_label.setTextFormat(Qt.PlainText)
        self.employee_label.setStyleSheet('font-size:18px;font-weight:700;')
        self.employee_label.setWordWrap(True);right.addWidget(self.employee_label)
        self.attendance_summary = QLabel();self.attendance_summary.setWordWrap(True);right.addWidget(self.attendance_summary)
        self.expense_summary = QLabel();self.expense_summary.setWordWrap(True);self.expense_summary.setTextFormat(Qt.PlainText)
        right.addWidget(self.expense_summary)
        self.details = QTabWidget();right.addWidget(self.details,1)
        sheet = QWidget();sheet_layout = QVBoxLayout(sheet)
        self.attendance_table = self._table(['Date','Day','Attendance','Note'],3)
        self.attendance_table.itemSelectionChanged.connect(self._select_day)
        sheet_layout.addWidget(self.attendance_table,1)
        form = QHBoxLayout()
        self.day_input = QDateEdit(calendarPopup=True);self.day_input.setDisplayFormat('dd MMM yyyy')
        self.day_input.setMaximumDate(QDate.currentDate());self.day_input.setDate(QDate.currentDate())
        self.day_input.dateChanged.connect(self._load_day)
        form.addWidget(self.day_input)
        self.status_input = QComboBox();self.status_input.addItems(['Not marked',*ATTENDANCE_STATUSES]);form.addWidget(self.status_input)
        self.note_input = QLineEdit();self.note_input.setPlaceholderText('Optional attendance note');self.note_input.setMaxLength(1000)
        form.addWidget(self.note_input,1);sheet_layout.addLayout(form)
        actions = QHBoxLayout()
        self.save_attendance = QPushButton('Save attendance');self.save_attendance.clicked.connect(self._save_attendance)
        actions.addWidget(self.save_attendance)
        attendance_csv = QPushButton('Export attendance CSV');attendance_csv.clicked.connect(self._export_attendance)
        actions.addWidget(attendance_csv);sheet_layout.addLayout(actions)
        self.details.addTab(sheet,'Attendance sheet')
        costs = QWidget();cost_layout = QVBoxLayout(costs)
        self.expense_table = self._table(['Date','Category','Amount','Payment','Description'],4)
        cost_layout.addWidget(self.expense_table,1)
        self.expense_pager = Pager();self.expense_pager.size = 100;self.expense_pager.changed.connect(self._refresh_detail)
        cost_layout.addWidget(self.expense_pager)
        actions = QHBoxLayout()
        self.add_expense = QPushButton('Add employee expense');self.add_expense.clicked.connect(self._add_expense)
        actions.addWidget(self.add_expense)
        delete = QPushButton('Delete selected expense');delete.clicked.connect(self._delete_expense)
        actions.addWidget(delete)
        expense_csv = QPushButton('Export expenses CSV');expense_csv.clicked.connect(self._export_expenses)
        actions.addWidget(expense_csv);cost_layout.addLayout(actions)
        self.details.addTab(costs,'Employee expenses')
        split.addWidget(detail);split.setSizes([400,850])
        self._clear_detail()

    def _month_key(self):
        return self.month.date().toString('yyyy-MM')

    def refresh(self,*_):
        self.pager.filter((self.search.text().strip(),self.show_archived.isChecked()))
        total,rows = self.db.employee_page(self.search.text(),self.show_archived.isChecked(),self.pager.size,self.pager.offset)
        self.pager.set_total(total)
        selected = self.employee_id
        self._employees = rows
        self.employee_table.blockSignals(True)
        self.employee_table.clearSelection()
        self._fill(self.employee_table,[[r['id'],r['name'],r['phone'] or '', 'Active' if r['active'] else 'Archived'] for r in rows])
        self.employee_table.blockSignals(False)
        self._clear_detail()
        if rows:
            index = next((i for i,r in enumerate(rows) if r['id']==selected),0)
            self.employee_table.selectRow(index)

    def _clear_detail(self):
        self.employee_id = None
        self._report = None
        self._days = []
        self.employee_label.setText('Select an employee')
        self.attendance_summary.clear();self.expense_summary.clear()
        self.attendance_table.setRowCount(0);self.expense_table.setRowCount(0)
        self.edit_button.setEnabled(False);self.details.setEnabled(False)
        self.expense_pager.set_total(0)

    def _select_employee(self):
        rows = self.employee_table.selectionModel().selectedRows()
        if not rows:
            self._clear_detail();return
        self.employee_id = self._employees[rows[0].row()]['id']
        self._refresh_detail()

    def _month_changed(self,*_):
        self._refresh_detail()

    def _refresh_detail(self,*_):
        if self.employee_id is None:
            return
        self.expense_pager.filter((self.employee_id,self._month_key()))
        report = self.db.employee_month(self.employee_id,self._month_key(),self.expense_pager.size,self.expense_pager.offset)
        self._report = report
        employee = report['employee'];counts = report['counts']
        self.employee_label.setText(f"{employee['name']} · Employee #{employee['id']}"+(' · Archived' if not employee['active'] else ''))
        self.attendance_summary.setText(f"Days attended: {report['days_attended']}  •  Full days: {counts['Full Day']}  •  Half days: {counts['Half Day']}\n"
            f"Absent: {counts['Absent']}  •  Not marked (through today): {report['unmarked']}")
        categories = ' · '.join(f"{r['category']}: {rupees(r['total'])}" for r in report['expense_categories'][:4])
        if len(report['expense_categories'])>4:
            categories += f" · +{len(report['expense_categories'])-4} more categories"
        self.expense_summary.setText(f"Expenses paid: {rupees(report['expense_total'])}  •  {report['expense_count']} records"+(f'\n{categories}' if categories else ''))
        self.details.setEnabled(True);self.edit_button.setEnabled(True)
        self._days = self._attendance_rows(report)
        self.attendance_table.blockSignals(True)
        self.attendance_table.clearSelection()
        self._fill(self.attendance_table,self._days)
        self.attendance_table.blockSignals(False)
        self._fill(self.expense_table,[[r['expense_date'],r['category'],rupees(r['amount']),r['payment_mode'] or '',r['description'] or ''] for r in report['expenses']])
        self.expense_pager.set_total(report['expense_count'])
        start = QDate.fromString(report['start'],'yyyy-MM-dd')
        end = min(QDate.fromString(report['end'],'yyyy-MM-dd'),QDate.currentDate())
        writable = bool(employee['active']) and start<=end
        self.save_attendance.setEnabled(writable);self.add_expense.setEnabled(writable)
        self.day_input.blockSignals(True)
        self.day_input.setEnabled(writable)
        if start<=end:
            self.day_input.setDateRange(start,end)
        self.day_input.blockSignals(False)
        self._load_day()

    @staticmethod
    def _attendance_rows(report):
        records = {r['attendance_date']:r for r in report['attendance']}
        day = date.fromisoformat(report['start']);end = date.fromisoformat(report['end'])
        rows = []
        while day<=end:
            record = records.get(day.isoformat(),{})
            rows.append([day.isoformat(),day.strftime('%a'),record.get('status','Upcoming' if day>date.today() else 'Not marked'),record.get('notes') or ''])
            day += timedelta(days=1)
        return rows

    def _select_day(self):
        rows = self.attendance_table.selectionModel().selectedRows()
        if rows:
            day = QDate.fromString(self._days[rows[0].row()][0],'yyyy-MM-dd')
            if day<=QDate.currentDate():
                self.day_input.setDate(day)
                self._load_day()
            else:
                self.save_attendance.setEnabled(False)
                self.status_input.setCurrentText('Not marked')
                self.note_input.clear()

    def _load_day(self,*_):
        if self._report:
            day = self.day_input.date().toString('yyyy-MM-dd')
            self.save_attendance.setEnabled(bool(self._report['employee']['active']) and
                self._report['start']<=day<=min(self._report['end'],date.today().isoformat()))
        record = next((r for r in (self._report or {}).get('attendance',[]) if r['attendance_date']==self.day_input.date().toString('yyyy-MM-dd')),None)
        self.status_input.setCurrentText(record['status'] if record else 'Not marked')
        self.note_input.setText((record['notes'] or '') if record else '')

    def _save_attendance(self):
        if self.employee_id is None:
            return
        try:
            self.db.set_employee_attendance(self.employee_id,self.day_input.date().toString('yyyy-MM-dd'),self.status_input.currentText(),self.note_input.text())
        except Exception as exc:
            QMessageBox.warning(self,'Attendance not saved',str(exc));return
        self._refresh_detail()

    def _edit_employee(self,editing):
        employee = self._report['employee'] if editing and self._report else None
        if editing and not employee:
            return
        dialog = QDialog(self);dialog.setWindowTitle('Edit employee' if editing else 'Add employee')
        form = QFormLayout(dialog)
        name = QLineEdit(employee['name'] if employee else '');name.setMaxLength(150);form.addRow('Name:',name)
        phone = QLineEdit(employee['phone'] or '' if employee else '');phone.setPlaceholderText('Optional');phone.setMaxLength(40);form.addRow('Phone:',phone)
        active = QCheckBox('Active employee');active.setChecked(bool(employee['active']) if employee else True);form.addRow('',active)
        buttons = QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)
        saved = False
        def save():
            nonlocal saved
            if saved:
                return
            try:
                eid = self.db.save_employee(name.text(),phone.text(),employee['id'] if employee else None,active.isChecked())
            except Exception as exc:
                QMessageBox.warning(dialog,'Employee not saved',str(exc));return
            self.employee_id = eid
            saved = True
            buttons.setEnabled(False)
            dialog.accept()
        buttons.accepted.connect(save)
        if dialog.exec()==QDialog.Accepted:
            self.search.setText(name.text())
            self.show_archived.blockSignals(True);self.show_archived.setChecked(not active.isChecked());self.show_archived.blockSignals(False)
            self.refresh()

    def _add_expense(self):
        if self._report is None:
            return
        employee = self._report['employee']
        dialog = QDialog(self);dialog.setWindowTitle(f"Expense — {employee['name']}");form = QFormLayout(dialog)
        day = QDateEdit(calendarPopup=True);day.setDisplayFormat('dd MMM yyyy')
        day.setDateRange(QDate.fromString(self._report['start'],'yyyy-MM-dd'),min(QDate.fromString(self._report['end'],'yyyy-MM-dd'),QDate.currentDate()))
        day.setDate(min(QDate.currentDate(),day.maximumDate()));form.addRow('Paid on:',day)
        category = QComboBox();category.setEditable(True);category.addItems(['Salary','Salary advance','Travel','Food','Other'])
        form.addRow('Category:',category)
        amount = QDoubleSpinBox();amount.setRange(0,999999999.99);amount.setDecimals(2);amount.setPrefix('Rs. ');form.addRow('Amount paid:',amount)
        mode = QComboBox();mode.addItems(['Cash','Card','UPI','Bank Transfer','Other']);form.addRow('Payment:',mode)
        note = QLineEdit();note.setPlaceholderText('Optional description');form.addRow('Note:',note)
        hint = QLabel('Record money paid now. For salary after an advance, enter only the remaining payment.');hint.setWordWrap(True);form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)
        saved = False
        def save():
            nonlocal saved
            if saved:
                return
            try:
                self.db.add_expense(category.currentText(),amount.value(),mode.currentText(),note.text(),day.date().toString('yyyy-MM-dd'),employee_id=employee['id'])
            except Exception as exc:
                QMessageBox.warning(dialog,'Expense not saved',str(exc));return
            saved = True
            buttons.setEnabled(False)
            dialog.accept()
        buttons.accepted.connect(save)
        if dialog.exec()==QDialog.Accepted:
            self._refresh_detail()

    def _delete_expense(self):
        rows = self.expense_table.selectionModel().selectedRows()
        if self._report is None or not rows:
            return
        expense = self._report['expenses'][rows[0].row()]
        if not confirm_delete_password(self,'delete this employee expense'):
            return
        try:
            self.db.delete_expense(expense['id'])
        except Exception as exc:
            QMessageBox.warning(self,'Expense not deleted',str(exc));return
        self._refresh_detail()

    def _export_attendance(self):
        if self.employee_id is None:
            return
        self._refresh_detail()
        employee = self._report['employee']
        export_csv(self,'Export employee attendance',f'attendance_{employee["id"]}_{self._month_key()}.csv',
            ['Employee ID','Employee','Date','Day','Attendance','Note'],
            ([employee['id'],employee['name'],*r] for r in self._days))

    def _export_expenses(self):
        if self.employee_id is None:
            return
        rows = self.db.export_employee_expenses(self.employee_id,self._month_key())
        export_csv(self,'Export employee expenses',f'employee_expenses_{self.employee_id}_{self._month_key()}.csv',
            ['Employee ID','Employee','Date','Category','Amount','Payment','Description'],
            ([r['employee_id'],r['employee_name'],r['expense_date'],r['category'],r['amount'],r['payment_mode'],r['description']] for r in rows))
