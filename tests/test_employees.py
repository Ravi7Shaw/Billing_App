"""Monthly attendance, linked cash expenses, legacy preservation, roles and UI."""
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/os.environ.get('BILLING_APP_DIR','app')))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication,QMessageBox,QDialog,QLineEdit,QCheckBox,QDoubleSpinBox,QDialogButtonBox
from database import Database
from access import AccessSession,RoleDatabase
from employees import month_bounds
from employees_tab import EmployeesTab
from expenses_tab import ExpensesTab
import main


class EmployeeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.db = Database(str(Path(self.folder.name)/'employees.db'))
        self.eid = self.db.save_employee('Ravi Kumar','9000000001')
        self.other = self.db.save_employee('Ravi Kumar','9000000002')
        self.session = AccessSession();self.session.login('1852j')
        self.access = RoleDatabase(self.db,self.session)
        self.errors = [];hook = sys.excepthook
        sys.excepthook = lambda *args:self.errors.append(args[1])
        self.addCleanup(setattr,sys,'excepthook',hook)
        for name in ('warning','critical','information'):
            mock=patch.object(QMessageBox,name);mock.start();self.addCleanup(mock.stop)

    def tearDown(self):
        self.app.processEvents();self.assertEqual(self.errors,[])

    def tab(self):
        tab = EmployeesTab(self.access);self.addCleanup(tab.close)
        tab.month.setDate(QDate(2024,2,1))
        return tab

    def expense(self,amount,day='2024-02-29',category='Salary',employee=None):
        return self.db.add_expense(category,amount,expense_date=day,employee_id=self.eid if employee is None else employee)

    def test_month_bounds_leap_and_invalid_months(self):
        self.assertEqual(month_bounds('2024-02'),('2024-02-01','2024-02-29'))
        self.assertEqual(month_bounds('2023-02'),('2023-02-01','2023-02-28'))
        for invalid in ('2024-13','2024-2','bad','2024-02-03'):
            with self.assertRaises(ValueError):month_bounds(invalid)

    def test_unique_daily_marks_corrections_half_days_and_clear(self):
        for day,status in [('2024-01-31','Full Day'),('2024-02-01','Full Day'),('2024-02-02','Half Day'),('2024-02-29','Absent'),('2024-03-01','Full Day')]:
            self.db.set_employee_attendance(self.eid,day,status)
        self.db.set_employee_attendance(self.eid,'2024-02-01','Full Day','Updated')
        self.db.set_employee_attendance(self.other,'2024-02-01','Full Day')
        report = self.db.employee_month(self.eid,'2024-02')
        self.assertEqual((report['days_attended'],report['day_equivalents'],report['unmarked']),(2,1.5,26))
        self.assertEqual(report['counts'],{'Full Day':1,'Half Day':1,'Absent':1})
        self.assertEqual(len(report['attendance']),3)
        self.assertEqual(report['attendance'][0]['notes'],'Updated')
        self.db.save_attendance(self.eid,'2024-02-02','Full Day')
        self.assertEqual(self.db.employee_month(self.eid,'2024-02')['day_equivalents'],2)
        self.db.set_employee_attendance(self.eid,'2024-02-01','Not marked')
        self.assertEqual(self.db.employee_month(self.eid,'2024-02')['days_attended'],1)

    def test_future_invalid_and_unknown_employee_rejected(self):
        tomorrow=(date.today()+timedelta(days=1)).isoformat()
        for day in (tomorrow,'2024-02-30','20240201','2024-2-1'):
            with self.assertRaises(ValueError):self.db.set_employee_attendance(self.eid,day,'Full Day')
        with self.assertRaises(ValueError):self.db.set_employee_attendance(self.eid,'2024-02-01','Wrong')
        with self.assertRaises(ValueError):self.db.set_employee_attendance(-1,'2024-02-01','Full Day')
        self.assertEqual(self.db.employee_month(self.eid,'2099-01')['unmarked'],0)
        self.assertEqual(self.db.employee_month(self.eid,date.today().strftime('%Y-%m'))['unmarked'],date.today().day)

    def test_concurrent_upserts_never_count_one_date_twice(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda status:self.db.set_employee_attendance(self.eid,'2024-02-01',status),['Full Day','Half Day']))
        report = self.db.employee_month(self.eid,'2024-02')
        self.assertEqual(len(report['attendance']),1)
        self.assertEqual(report['days_attended'],1)

    def test_names_duplicates_unicode_literal_prefix_and_paging(self):
        self.db.save_employee('  Ana   María  ')
        self.db.save_employee('A_% Person')
        self.assertEqual(self.db.employee_page('ANA M')[1][0]['name'],'Ana María')
        self.assertEqual(self.db.employee_page('A_%')[0],1)
        count,rows = self.db.employee_page('ravi',limit=1,offset=1)
        self.assertEqual((count,rows[0]['id']),(2,self.other))
        self.assertEqual(self.db.employee_page('No match')[0],0)
        self.db.save_employee('Renamed','',self.eid)
        self.assertEqual(self.db.employee_page('ravi')[0],1)
        self.assertEqual(self.db.employee_page('renamed')[1][0]['id'],self.eid)
        for name in ('','   ','a'*151):
            with self.assertRaises(ValueError):self.db.save_employee(name)

    def test_legacy_employee_methods_keep_search_key_and_role(self):
        eid = self.db.add_employee('Legacy person','','Manager')
        self.assertEqual(self.db.employee_page('legacy')[1][0]['id'],eid)
        self.db.update_employee(eid,'New name','','Supervisor')
        self.assertEqual(self.db.employee_page('new name')[1][0]['role'],'Supervisor')
        self.db.save_employee('Newer name','',eid)
        self.assertEqual(self.db.employee_page('newer')[1][0]['role'],'Supervisor')

    def test_archiving_preserves_history_blocks_writes_and_restores(self):
        self.db.set_employee_attendance(self.eid,'2024-02-01','Full Day')
        self.expense(200)
        self.db.save_employee('Ravi Kumar','',self.eid,False)
        self.assertEqual(self.db.employee_page('ravi')[0],1)
        self.assertEqual(self.db.employee_page('ravi',True)[0],2)
        report=self.db.employee_month(self.eid,'2024-02')
        self.assertEqual((report['days_attended'],report['expense_total']),(1,200))
        with self.assertRaises(ValueError):self.db.set_employee_attendance(self.eid,'2024-02-02','Full Day')
        with self.assertRaises(ValueError):self.expense(100)
        self.db.save_employee('Ravi Kumar','',self.eid,True)
        self.expense(100)
        self.assertEqual(self.db.employee_month(self.eid,'2024-02')['expense_total'],300)

    def test_employee_costs_and_shop_expenses_are_same_rows(self):
        first=self.expense(1000,category='Salary advance')
        self.expense(4000,category='Salary')
        self.expense(100.01,category='Travel')
        self.expense(900,employee=self.other)
        self.expense(500,day='2024-03-01')
        self.db.add_expense('Rent',2000,expense_date='2024-02-01')
        report=self.db.employee_month(self.eid,'2024-02')
        self.assertEqual((report['expense_count'],report['expense_total']),(3,5100.01))
        self.assertEqual(sum(r['total'] for r in report['expense_categories']),5100.01)
        totals=self.db.report_statistics('2024-02-01','2024-02-29')['totals']
        self.assertEqual((totals['expenses'],totals['net_profit']),(8000.01,-8000.01))
        rows=self.db.expense_page('2024-02-01','2024-02-29')['rows']
        self.assertEqual(next(r['employee_name'] for r in rows if r['id']==first),'Ravi Kumar')
        self.db.update_expense(first,'Salary advance',800,expense_date='2024-02-01')
        self.assertEqual(self.db.employee_month(self.eid,'2024-02')['expense_total'],4900.01)
        self.db.delete_expense(first)
        self.assertEqual(self.db.employee_month(self.eid,'2024-02')['expense_total'],4100.01)
        with self.assertRaises(ValueError):self.expense(100,employee=99999)
        for amount in (0,-1,float('nan'),float('inf'),.001):
            with self.assertRaises(ValueError):self.expense(amount)

    def test_expense_totals_and_export_cover_all_pages(self):
        with self.db._conn() as conn:
            conn.executemany("INSERT INTO expenses(employee_id,expense_date,category,amount) VALUES (?,'2024-02-29','Food',.01)",[(self.eid,)]*205)
        report=self.db.employee_month(self.eid,'2024-02',100,200)
        self.assertEqual((report['expense_count'],len(report['expenses']),report['expense_total']),(205,5,2.05))
        self.assertEqual(len(list(self.db.export_employee_expenses(self.eid,'2024-02'))),205)
        tab=self.tab();tab.details.setCurrentIndex(1)
        tab.expense_pager.next.click();tab.expense_pager.next.click()
        self.assertEqual(tab.expense_table.rowCount(),5)
        self.assertIn('2.05',tab.expense_summary.text())
        rows=self.export(tab,'_export_expenses')
        self.assertEqual(len(rows),206)
        self.assertEqual(sum(round(float(r[4])*100) for r in rows[1:]),205)

    def test_additive_migration_preserves_existing_data_and_is_repeatable(self):
        self.expense(123)
        self.db.save_attendance(self.eid,'2024-02-29','Half Day','Historical')
        with self.db._conn() as conn:
            conn.execute('DROP INDEX idx_employee_name');conn.execute('ALTER TABLE employees DROP COLUMN name_key')
            conn.execute('DROP INDEX idx_employee_expenses');conn.execute('ALTER TABLE expenses DROP COLUMN employee_id')
            original=[tuple(r) for r in conn.execute('SELECT * FROM attendance')]
        for _ in range(2):self.db=Database(self.db.path)
        self.assertEqual(self.db.employee_page('ravi')[0],2)
        self.assertEqual(self.db.employee_month(self.eid,'2024-02')['day_equivalents'],.5)
        self.assertEqual(self.db.employee_month(self.eid,'2024-02')['expense_total'],0)
        self.assertEqual(self.db.report_statistics()['totals']['expenses'],123)
        with self.db._conn() as conn:
            self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM attendance')],original)
            self.assertIsNone(conn.execute('SELECT employee_id FROM expenses').fetchone()[0])

    def test_lookup_attendance_and_expense_query_plans_use_indexes(self):
        with self.db._conn() as conn:
            queries=[("SELECT id FROM employees WHERE name_key LIKE ? ESCAPE '\\' ORDER BY name_key COLLATE NOCASE,id",('ravi%',)),
                ('SELECT * FROM attendance WHERE employee_id=? AND date(attendance_date)>=? AND date(attendance_date)<=?',(self.eid,'2024-02-01','2024-02-29')),
                ('SELECT * FROM expenses WHERE employee_id=? AND date(expense_date)>=? AND date(expense_date)<=?',(self.eid,'2024-02-01','2024-02-29'))]
            for query,args in queries:
                plan=[r[3] for r in conn.execute('EXPLAIN QUERY PLAN '+query,args)]
                self.assertTrue(any('SEARCH' in p for p in plan),plan)
                self.assertFalse(any('SCAN' in p for p in plan),plan)

    def test_employees_and_stale_callbacks_are_denied(self):
        commands=[(self.access.save_employee,('Bypass',)),(self.access.employee_page,()),
                  (self.access.employee_month,(self.eid,'2024-02')),
                  (self.access.set_employee_attendance,(self.eid,'2024-02-01','Full Day')),
                  (self.access.save_attendance,(self.eid,'2024-02-01','Full Day')),
                  (self.access.export_employee_expenses,(self.eid,'2024-02')),
                  (self.access.add_expense,('Salary',100))]
        self.session.logout()
        for callback,args in commands:
            with self.assertRaises(PermissionError):callback(*args)
        with patch.object(main,'Database',return_value=self.db):window=main.MainWindow()
        self.addCleanup(window.close)
        self.assertIsNone(window.employees_tab)
        window.session.login('1852j');window._build_dashboard()
        self.assertIsNotNone(window.employees_tab)

    def export(self,tab,method):
        path=Path(self.folder.name)/'export.csv'
        with patch('report_export.QFileDialog.getSaveFileName',return_value=(str(path),'CSV')):
            getattr(tab,method)()
        with path.open(encoding='utf-8-sig',newline='') as stream:return list(csv.reader(stream))

    def test_gui_marks_month_search_and_csv(self):
        tab=self.tab()
        self.assertEqual(tab.employee_id,self.eid)
        tab.day_input.setDate(QDate(2024,2,29));tab.status_input.setCurrentText('Full Day');tab.note_input.setText('On time')
        tab.save_attendance.click();tab.save_attendance.click()
        self.assertEqual(tab._report['days_attended'],1)
        self.assertEqual(tab.attendance_table.rowCount(),29)
        self.assertEqual(tab.attendance_table.item(28,2).text(),'Full Day')
        rows=self.export(tab,'_export_attendance')
        self.assertEqual(len(rows),30)
        self.assertEqual(rows[-1][2:],['2024-02-29','Thu','Full Day','On time'])
        tab.month.setDate(QDate(2024,3,1));self.assertEqual(tab._report['days_attended'],0)
        tab.search.setText('not found');tab.refresh()
        self.assertIsNone(tab.employee_id);self.assertFalse(tab.details.isEnabled())
        tab.search.setText('ravi');tab.refresh();self.assertEqual(tab.employee_table.rowCount(),2)
        today=QDate.currentDate()
        if today.day()<today.daysInMonth():
            tab.month.setDate(today)
            tab.attendance_table.selectRow(today.day())
            self.assertFalse(tab.save_attendance.isEnabled())
            tab.attendance_table.selectRow(0)
            self.assertTrue(tab.save_attendance.isEnabled())
        tab.month.setDate(QDate(2099,1,1));self.assertFalse(tab.save_attendance.isEnabled())
        self.assertEqual(tab.attendance_table.item(0,2).text(),'Upcoming')

    def test_gui_add_edit_employee_and_add_delete_expense(self):
        tab=self.tab()
        def add_employee(dialog):
            fields=dialog.findChildren(QLineEdit);fields[0].setText('New person');fields[1].setText('123')
            dialog.findChild(QDialogButtonBox).accepted.emit();dialog.findChild(QDialogButtonBox).accepted.emit();return dialog.result()
        with patch.object(QDialog,'exec',add_employee):tab._edit_employee(False)
        self.assertEqual(tab._report['employee']['name'],'New person')
        eid=tab.employee_id
        def expense(dialog):
            dialog.findChild(QDoubleSpinBox).setValue(125.50)
            dialog.findChild(QDialogButtonBox).accepted.emit();dialog.findChild(QDialogButtonBox).accepted.emit();return dialog.result()
        with patch.object(QDialog,'exec',expense):tab._add_expense()
        self.assertEqual(tab._report['expense_total'],125.50)
        general=ExpensesTab(self.access);self.addCleanup(general.close)
        general.period.preset.setCurrentText('All time')
        self.assertIn('New person',general.expense_table.item(0,6).text())
        tab.expense_table.selectRow(0)
        with patch('employees_tab.confirm_delete_password',return_value=False):tab._delete_expense()
        self.assertEqual(tab._report['expense_count'],1)
        with patch('employees_tab.confirm_delete_password',return_value=True):tab._delete_expense()
        self.assertEqual(tab._report['expense_count'],0)
        def archive(dialog):
            dialog.findChild(QCheckBox).setChecked(False)
            dialog.findChild(QDialogButtonBox).accepted.emit();dialog.findChild(QDialogButtonBox).accepted.emit();return dialog.result()
        with patch.object(QDialog,'exec',archive):tab._edit_employee(True)
        self.assertEqual(tab.employee_id,eid)
        self.assertFalse(tab._report['employee']['active'])
        self.assertFalse(tab.save_attendance.isEnabled())


if __name__=='__main__':unittest.main()
