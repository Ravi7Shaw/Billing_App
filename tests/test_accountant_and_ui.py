"""Accountant role, purchases linked to stock, and the consolidated UI fixes."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / os.environ.get('BILLING_APP_DIR', 'app')))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QPushButton

from database import Database
from access import (AccessSession, RoleDatabase, make_accountant_credential,
                    verify_accountant_password)
from commerce_ui import ChartAccountsTab
from customers_tab import CustomersTab
from exchanges_tab import ExchangesTab, ExchangeCart
from expenses_tab import ExpensesTab
from receipt import ReceiptDialog

ACCOUNTANT_PASSWORD = 'acct-pass-1'


def _db(folder):
    return Database(str(Path(folder) / 'ui.db'))


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = _db(self.folder.name)
        self.category = self.db.get_categories()[0]['id']
        self.item = self.db.add_item('Denim jeans', self.category, None, 'JEANS', '', '', 500, 0)
        group = next(r['id'] for r in self.db.get_account_groups() if r['name'] == 'Suppliers')
        self.supplier = self.db.save_account_ledger('Supplier', group, 'Supplier', '9000000000')
        self.seller = dict(name='Supplier', phone='9000000000', gst='')
        self.buyer = dict(name='Shop', phone='9111111111', gst='')

    def paid_bill(self, qty=2, customer=None, godown=1):
        total = qty * 500
        return self.db.save_bill(customer, [dict(item_id=self.item, name='Denim jeans', quantity=qty,
                                                 rate=500, subtotal=total, gst_rate=0, gst_amount=0)],
                                 total, 0, 0, total, 'Cash', bill_date='2026-10-22',
                                 initial_payment_amount=total, godown_id=godown)[0]


class AccountantRoleTests(_Base):
    def test_password_record_is_salted_and_verified(self):
        record = make_accountant_credential(ACCOUNTANT_PASSWORD)
        self.assertTrue(record.startswith('pbkdf2_sha256$'))
        self.assertNotEqual(record, make_accountant_credential(ACCOUNTANT_PASSWORD))
        self.assertTrue(verify_accountant_password(ACCOUNTANT_PASSWORD, record))
        self.assertFalse(verify_accountant_password('wrong', record))
        self.assertFalse(verify_accountant_password(ACCOUNTANT_PASSWORD, None))

    def test_credential_storage_rejects_plain_text(self):
        with self.assertRaises(ValueError):
            self.db.set_accountant_credential('plain-text-password')
        self.assertIsNone(self.db.get_accountant_credential())

    def test_login_gate_and_role_separation(self):
        self.db.set_accountant_credential(make_accountant_credential(ACCOUNTANT_PASSWORD))
        stored = self.db.get_accountant_credential()
        session = AccessSession()
        self.assertFalse(session.login_accountant('bad', stored))
        self.assertEqual(session.role, 'employee')
        self.assertTrue(session.login_accountant(ACCOUNTANT_PASSWORD, stored))
        self.assertTrue(session.is_accountant)
        self.assertFalse(session.is_admin)
        session.logout()
        self.assertEqual(session.role, 'employee')

    def test_accountant_posts_purchase_that_reaches_inventory(self):
        session = AccessSession()
        session.login_accountant(ACCOUNTANT_PASSWORD,
                                 self._stored_credential())
        role = RoleDatabase(self.db, session)
        pid = role.save_purchase(self.supplier, self.seller, self.buyer, 'A-1',
                                 [dict(item_id=self.item, unit_id=1, quantity=5, rate=100,
                                       discount=0, gst_rate=0)],
                                 False, 1, 0, purchase_date='2026-10-22')
        self.assertTrue(pid)
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'], 5)
        stock = next(r for r in role.stock_report() if r['id'] == self.item)
        self.assertEqual(stock['quantity'], 5)

    def test_accountant_is_refused_selling_catalog_and_expense_actions(self):
        role = self._accountant_role()
        bid = self.paid_bill()
        blocked = [
            lambda: role.save_bill(None, [], 0, 0, 0, 0, 'Cash', bill_date='2026-10-22'),
            lambda: role.get_bill(bid),
            lambda: role.add_item('X', self.category, None, '', '', '', 10, 1),
            lambda: role.set_opening_stock(self.item, 1, 1, 10),
            lambda: role.add_expense('Rent', 10, expense_date='2026-10-22'),
            lambda: role.delete_bill(bid),
            lambda: role.add_category('New category'),
        ]
        for call in blocked:
            with self.assertRaises(PermissionError):
                call()

    def test_accountant_cannot_read_non_callable_database_state(self):
        role = self._accountant_role()
        with self.assertRaises(PermissionError):
            role.path

    def test_employee_and_admin_permissions_are_unchanged(self):
        employee = RoleDatabase(self.db, AccessSession())
        with self.assertRaises(PermissionError):
            employee.save_purchase(self.supplier, self.seller, self.buyer, 'E-1', [], False)
        admin_session = AccessSession()
        self.assertTrue(admin_session.login('1852j'))
        self.assertEqual(admin_session.role, 'admin')
        self.assertFalse(admin_session.is_accountant)
        admin = RoleDatabase(self.db, admin_session)
        self.assertTrue(admin.get_godowns())

    def _stored_credential(self):
        self.db.set_accountant_credential(make_accountant_credential(ACCOUNTANT_PASSWORD))
        return self.db.get_accountant_credential()

    def _accountant_role(self):
        session = AccessSession()
        self.assertTrue(session.login_accountant(ACCOUNTANT_PASSWORD, self._stored_credential()))
        return RoleDatabase(self.db, session)


class ChartOfAccountsAccessTests(_Base):
    def test_accountant_chart_hides_admin_only_catalog_buttons(self):
        session = AccessSession()
        session.login_accountant(ACCOUNTANT_PASSWORD, self._stored_credential())
        tab = ChartAccountsTab(RoleDatabase(self.db, session), can_edit_catalog=False)
        self.addCleanup(tab.close)
        hidden = {b.text() for b in tab.findChildren(QPushButton) if b.isHidden()}
        self.assertIn('Add item', hidden)
        self.assertIn('Stock categories', hidden)
        self.assertIn('Opening stock / cost', hidden)
        self.assertNotIn('Transfer stock', hidden)

    def _stored_credential(self):
        self.db.set_accountant_credential(make_accountant_credential(ACCOUNTANT_PASSWORD))
        return self.db.get_accountant_credential()


class ExchangeAsTabTests(_Base):
    def test_exchange_opens_as_tab_and_closes_when_cancelled(self):
        cid = self.db.add_customer('Example Customer', '9876543210')
        bid = self.paid_bill(qty=2, customer=cid)
        opened, closed = [], []

        def open_tab(widget, title):
            opened.append((widget, title))
            return widget

        tab = ExchangesTab(self.db, autoload=False, open_tab=open_tab, close_tab=closed.append)
        self.addCleanup(tab.close)
        tab.search_mode.setCurrentIndex(1)
        tab.search_input.setText(self.db.get_bill(bid)[0]['bill_no'])
        tab.refresh()
        self.assertTrue(tab._quantities, 'the bill lines should be selectable')
        line_id, spin = next(iter(tab._quantities.items()))
        spin.setValue(1)
        tab._start_exchange()

        self.assertEqual(len(opened), 1, 'exchange must open as a tab, not a dialog')
        cart, title = opened[0]
        self.assertIsInstance(cart, ExchangeCart)
        self.assertTrue(title.startswith('Exchange '))
        cart._cancel()
        self.assertEqual(closed, [cart])

    def test_exchange_tab_requires_main_window_hook(self):
        bid = self.paid_bill(qty=1)
        tab = ExchangesTab(self.db, autoload=False)
        self.addCleanup(tab.close)
        tab.search_mode.setCurrentIndex(1)
        tab.search_input.setText(self.db.get_bill(bid)[0]['bill_no'])
        tab.refresh()
        next(iter(tab._quantities.values())).setValue(1)
        with self.assertRaises(RuntimeError):
            tab._start_exchange()


class LayoutVisibilityTests(_Base):
    def test_expense_history_is_visible_without_scrolling(self):
        for day in range(5):
            self.db.add_expense(expense_date='2026-10-20', godown_id=1, category='Rent',
                                amount=1000 + day, payment_mode='Cash', description='rent')
        tab = ExpensesTab(self.db)
        self.addCleanup(tab.close)
        tab.resize(1300, 820)
        tab.show()
        self.app.processEvents()
        table = tab.expense_table
        self.assertGreaterEqual(table.height(), 200)
        bottom = table.mapTo(tab, QPoint(0, table.height())).y()
        self.assertLessEqual(bottom, tab.height())
        self.assertEqual(table.rowCount(), 5)

    def test_receipt_action_buttons_stay_inside_dialog(self):
        bid = self.paid_bill(qty=2)
        bill, lines = self.db.get_bill(bid)
        dialog = ReceiptDialog(bill, lines)
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        for button in (dialog.print_button, dialog.pdf_button, dialog.close_button):
            self.assertTrue(dialog.rect().contains(button.geometry()),
                            f'{button.text()} is clipped by the receipt window')

    def test_customer_bill_row_opens_the_receipt(self):
        cid = self.db.add_customer('Ravi', '910884773')
        self.paid_bill(qty=2, customer=cid)
        tab = CustomersTab(self.db, autoload=False)
        self.addCleanup(tab.close)
        tab._load_customer(cid)
        self.assertEqual(tab.history_table.rowCount(), 1)
        with patch('customers_tab.ReceiptDialog') as receipt:
            tab._open_history_receipt(0, 0)
        receipt.assert_called_once()
        receipt.return_value.exec.assert_called_once()


class AccountantDashboardTests(_Base):
    def test_accountant_dashboard_shows_only_accounting_screens(self):
        from main import MainWindow
        self.db.set_accountant_credential(make_accountant_credential(ACCOUNTANT_PASSWORD))
        with patch('main.Database', return_value=self.db):
            window = MainWindow()
        self.addCleanup(window.close)
        window.session.login_accountant(ACCOUNTANT_PASSWORD, self.db.get_accountant_credential())
        window._build_dashboard()
        names = [window.tabs.tabText(i) for i in range(window.tabs.count())]
        self.assertEqual(names, ['Purchases', 'Chart of Accounts'])
        self.assertIsNone(window.billing_tab)
        self.assertTrue(window.accountant_button.isHidden())
        self.assertFalse(window.logout_button.isHidden())
        self.assertTrue(window.accountant_password_button.isHidden())

    def test_accountant_cannot_open_while_no_password_exists(self):
        from main import MainWindow
        with patch('main.Database', return_value=self.db), \
                patch('main.QMessageBox.information') as info, \
                patch('main.QInputDialog.getText') as prompt:
            window = MainWindow()
            self.addCleanup(window.close)
            window._login_accountant()
        info.assert_called_once()
        prompt.assert_not_called()
        self.assertFalse(window.session.is_accountant)


if __name__ == '__main__':
    unittest.main()
