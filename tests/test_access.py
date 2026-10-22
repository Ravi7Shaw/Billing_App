"""Exercise application permissions with real SQLite and offscreen Qt widgets."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / os.environ.get('BILLING_APP_DIR', 'app')))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from access import AccessSession, RoleDatabase, verify_admin_password
from database import Database
from billing_tab import BillingTab
import main
import widgets


class AccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Database(str(Path(folder.name) / 'test.db'))
        category = self.db.get_categories()[0]
        self.iid = self.db.add_item('Shirt', category['id'], None, '123', '', '', 100, 10)
        self.category = category['name']
        self.session = AccessSession()
        self.access = RoleDatabase(self.db, self.session)
        self.errors = []
        old_hook = sys.excepthook
        sys.excepthook = lambda *args: self.errors.append(args[1])
        self.addCleanup(setattr, sys, 'excepthook', old_hook)
        for method in ('warning', 'critical', 'information'):
            mocked = patch.object(QMessageBox, method)
            mocked.start()
            self.addCleanup(mocked.stop)

    def tearDown(self):
        self.app.processEvents()
        self.assertEqual(self.errors, [])

    def tab(self):
        tab = BillingTab(self.access)
        self.addCleanup(tab.close)
        tab._add_to_cart(self.iid, 'Shirt', self.category, 1, 100, 5)
        return tab

    def window(self):
        with patch.object(main, 'Database', return_value=self.db):
            window = main.MainWindow()
        self.addCleanup(window.close)
        return window

    def payload(self):
        return dict(customer_id=None, items=[dict(item_id=self.iid, name='Shirt', category=self.category,
                    quantity=1, rate=100, subtotal=100, gst_rate=5, gst_amount=5)],
                    subtotal=100, discount_percent=0, discount_amount=0, total=105,
                    taxable_amount=100, gst_rate=5, gst_amount=5, payment_mode='Cash',
                    initial_payment_amount=105)

    def test_password_and_default_employee_session(self):
        self.assertFalse(self.session.is_admin)
        for password in ('', '1852', '1852J', ' 1852j'):
            self.assertFalse(self.session.login(password))
            self.assertFalse(self.session.is_admin)
        self.assertTrue(self.session.login('1852j'))
        self.assertTrue(self.session.is_admin)
        self.session.logout()
        self.assertFalse(self.session.is_admin)

    def test_login_cancel_failure_success_and_logout_clear_dashboard(self):
        window = self.window()
        self.assertEqual(window.tabs.count(), 1)
        self.assertIsNone(window.inventory_tab)
        for answer in [('1852j', False), ('wrong', True)]:
            with patch.object(main.QInputDialog, 'getText', return_value=answer):
                window._login_admin()
            self.assertFalse(window.session.is_admin)
            self.assertEqual(window.tabs.count(), 1)
        with patch.object(main.QInputDialog, 'getText', return_value=('1852j', True)):
            window._login_admin()
        self.assertTrue(window.session.is_admin)
        self.assertGreaterEqual(window.tabs.count(), 7)
        tab = window.billing_tab
        tab._add_to_cart(self.iid, 'Shirt', self.category, 1, 100)
        tab.cart[0]['discount'] = 10
        tab._recalculate_totals()
        tab.payment_amount_input.setValue(0)
        window._logout_admin()
        self.assertEqual(window.tabs.count(), 1)
        self.assertFalse(window.session.is_admin)
        self.assertEqual(window.billing_tab.cart, [])
        self.assertIsNone(window.sales_tab)
        self.assertTrue(window.billing_tab.discount_arrow_btn.isHidden())
        self.assertEqual(self.db.search_bills(), [])

    def test_cancel_switch_preserves_employee_bill(self):
        window = self.window()
        window.billing_tab._add_to_cart(self.iid, 'Shirt', self.category, 1, 100)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No), patch.object(main.QInputDialog, 'getText') as prompt:
            window._login_admin()
        prompt.assert_not_called()
        self.assertFalse(window.session.is_admin)
        self.assertEqual(len(window.billing_tab.cart), 1)

    def test_employee_full_payment_saves_gst_stock_and_receipt(self):
        tab = self.tab()
        self.assertEqual(tab.payment_amount_input.value(), 105)
        self.assertFalse(tab.payment_amount_input.isEnabled())
        self.assertTrue(tab.rate_input.isReadOnly())
        self.assertFalse(tab.cart_table.item(0, 3).flags() & Qt.ItemIsEditable)
        with patch('billing_tab.ReceiptDialog') as receipt:
            tab._complete_bill()
        receipt.assert_called_once()
        bill = self.db.search_bills()[0]
        saved, lines = self.db.get_bill(bill['id'])
        self.assertEqual(saved['total'], 105)
        self.assertEqual(saved['gst_amount'], 5)
        self.assertEqual(saved['discount_amount'], 0)
        self.assertEqual(self.db.get_bill_balance(bill['id']), 0)
        self.assertEqual(self.db.get_item_by_id(self.iid)['stock_qty'], 9)
        self.assertEqual(len(tab.cart), 0)
        self.assertEqual(lines[0]['gst_amount'], 5)

    def test_employee_cannot_edit_discount_or_price_in_table(self):
        tab = self.tab()
        tab._toggle_discount_visibility()
        self.assertFalse(tab.discount_visible)
        tab.cart_table.item(0, 3).setText('1')
        tab.cart_table.item(0, 5).setText('99')
        self.assertEqual(tab.cart[0]['rate'], 100)
        self.assertEqual(tab.cart[0]['discount'], 0)
        self.assertEqual(tab._grand_total(), 105)

    def test_employee_rejects_partial_credit_and_tampered_cart_before_customer_write(self):
        tab = self.tab()
        baseline = copy.deepcopy(tab.cart)
        for field, value in [('discount', 10), ('rate', 1), ('gst_rate', 0), ('amount', 1)]:
            tab.cart = copy.deepcopy(baseline)
            tab.cart[0][field] = value
            tab.customer_name_input.setText('Should not be saved')
            with patch('billing_tab.ReceiptDialog') as receipt:
                tab._complete_bill()
            receipt.assert_not_called()
        tab.cart = baseline
        for paid in (0, 50):
            tab.payment_amount_input.setValue(paid)
            tab._complete_bill()
        self.assertEqual(self.db.search_bills(), [])
        self.assertEqual(self.db.search_customers(''), [])

    def test_employee_quantity_changes_keep_full_payment(self):
        tab = self.tab()
        tab.cart_table.item(0, 2).setText('2')
        self.assertEqual(tab.payment_amount_input.value(), 210)
        self.assertEqual(tab._grand_total(), 210)

    def test_employee_manual_catalog_selection_and_barcode(self):
        tab = BillingTab(self.access)
        self.addCleanup(tab.close)
        tab.category_combo.setCurrentIndex(tab.category_combo.findText(self.category))
        tab.item_name_combo.setEditText('New unauthorized item')
        tab.rate_input.setValue(1)
        tab._add_manual_item()
        self.assertEqual(tab.cart, [])
        self.assertEqual(len(self.db.get_items()), 1)
        tab.item_name_combo.setEditText('Shirt')
        tab.rate_input.setValue(1)  # disabled input cannot override catalog price
        tab._add_manual_item()
        self.assertEqual(tab.cart[0]['rate'], 100)
        tab.barcode_input.setText('123')
        tab._on_barcode_scanned()
        self.assertEqual(tab.cart[0]['qty'], 2)

    def test_employee_cannot_modify_returning_customer_or_read_spending(self):
        cid = self.db.add_customer('Original', '123', 'Address', 'Private notes')
        tab = self.tab()
        tab.phone_input.setText('123')
        tab._lookup_customer()
        self.assertEqual(tab.customer_info_label.text(), 'Returning customer')
        tab.customer_name_input.setText('Changed')
        with patch('billing_tab.ReceiptDialog'):
            tab._complete_bill()
        self.assertEqual(self.db.get_customer_by_phone('123')['name'], 'Original')
        self.assertEqual(self.db.search_bills()[0]['customer_id'], cid)

    def test_employee_phone_suggestions_select_and_save_existing_customer(self):
        cid = self.db.add_customer('Ravi', '910884773', 'Ravi address', 'Private notes')
        self.db.add_customer('Ravi', '910885555', 'Other address')
        self.db.add_customer('No phone', '')
        tab = self.tab()
        self.assertFalse(self.session.is_admin)
        tab.show()
        tab.phone_input.setFocus()
        QTest.keyClicks(tab.phone_input, '910884')
        self.app.processEvents()
        completer = tab.phone_input.completer()
        self.assertEqual(completer.completionCount(), 1)
        index = completer.completionModel().index(0, 0)
        self.assertEqual(index.data(Qt.DisplayRole), '910884773 — Ravi')
        self.assertEqual(completer.currentCompletion(), '910884773')
        popup = completer.popup()
        popup.setCurrentIndex(index)
        QTest.keyClick(popup, Qt.Key_Return)
        self.assertEqual(tab.phone_input.text(), '910884773')
        self.assertEqual(tab.customer_name_input.text(), 'Ravi')
        self.assertEqual(tab.customer_address_input.text(), 'Ravi address')
        self.assertEqual(tab.matched_customer['id'], cid)
        self.assertEqual(tab.customer_info_label.text(), 'Returning customer')
        with patch('billing_tab.ReceiptDialog'):
            tab._complete_bill()
        self.assertEqual(self.db.search_bills()[0]['customer_id'], cid)
        self.assertEqual(len(self.db.search_customers()), 3)
        completer = tab.phone_input.completer()
        completer.setCompletionPrefix('91088')
        self.assertEqual(completer.completionCount(), 2)

    def test_employee_phone_suggestions_refresh_and_keep_directory_restricted(self):
        self.db.add_customer('Ravi', '910884773', 'Address', 'Private notes')
        self.db.add_customer('No phone', '')
        rows = self.access.get_customer_phone_suggestions()
        self.assertEqual([dict(r) for r in rows], [dict(name='Ravi', phone='910884773')])
        for method, args in [('search_customers', ()), ('get_customer_total_spent', (1,)),
                             ('get_wishlist', (1,))]:
            with self.subTest(method=method), self.assertRaises(PermissionError):
                getattr(self.access, method)(*args)
        tab = self.tab()
        self.db.add_customer('New customer', '910889999')
        tab.refresh_catalog()
        completer = tab.phone_input.completer()
        completer.setCompletionPrefix('91088')
        self.assertEqual(completer.completionCount(), 2)
        self.assertTrue(self.session.login('1852j'))
        tab.refresh_catalog()
        self.session.logout()
        tab.refresh_catalog()
        completer = tab.phone_input.completer()
        completer.setCompletionPrefix('910889')
        self.assertEqual(completer.currentCompletion(), '910889999')
        self.assertEqual(completer.completionModel().index(0, 0).data(Qt.DisplayRole),
                         '910889999 — New customer')

    def test_command_boundary_blocks_management_and_stale_admin_callback(self):
        for method, args in [('add_expense', ('Test', 1)), ('search_bills', ()),
                             ('get_customer_balance', (1,)), ('add_payment', (1, 1)),
                             ('delete_bill', (1,)), ('update_customer', (1, 'X', ''))]:
            with self.subTest(method=method), self.assertRaises(PermissionError):
                getattr(self.access, method)(*args)
        self.session.login('1852j')
        saved_admin_action = self.access.add_expense
        self.session.logout()
        with self.assertRaises(PermissionError):
            saved_admin_action('Should be blocked', 1)
        self.assertEqual(self.db.get_expenses(), [])

    def test_save_command_rejects_discount_credit_price_tax_and_total_bypass(self):
        for field, value in [('discount_amount', 1), ('discount_percent', 1),
                             ('initial_payment_amount', 0), ('initial_payment_amount', 50),
                             ('total', 100), ('gst_amount', 0), ('subtotal', 99),
                             ('gst_rate', 0), ('payment_mode', 'Credit'),
                             ('bill_date', '2020-01-01')]:
            values = self.payload()
            values[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(PermissionError):
                self.access.save_bill(**values)
        values = self.payload()
        values['items'][0].update(rate=1, subtotal=1)
        with self.assertRaises(PermissionError):
            self.access.save_bill(**values)
        self.assertEqual(self.db.search_bills(), [])

    def test_catalog_changes_are_checked_again_before_save(self):
        values = self.payload()
        self.db.update_item_gst_rate(self.iid, 12)
        with self.assertRaises(PermissionError):
            self.access.save_bill(**values)
        self.db.delete_item(self.iid)
        with self.assertRaises(PermissionError):
            self.access.save_bill(**values)
        self.assertEqual(self.db.search_bills(), [])

    def test_employee_fractional_catalog_price_uses_displayed_cents(self):
        row = self.db.get_item_by_id(self.iid)
        self.db.update_item(self.iid, row['name'], row['category_id'], None, '123', '', '', 1.3755, 10)
        tab = BillingTab(self.access)
        self.addCleanup(tab.close)
        tab._add_to_cart(self.iid, 'Shirt', self.category, 3, 1.3755, 5)
        self.assertEqual(tab.cart[0]['rate'], 1.38)
        self.assertEqual(tab.cart[0]['amount'], 4.14)
        with patch('billing_tab.ReceiptDialog'):
            tab._complete_bill()
        self.assertEqual(self.db.search_bills()[0]['total'], 4.35)

    def test_employee_mixed_gst_rates_and_optional_customer_fields(self):
        row = self.db.get_item_by_id(self.iid)
        other = self.db.add_item('Other', row['category_id'], None, '', '', '', 10, 10)
        self.db.update_item_gst_rate(other, 12)
        tab = self.tab()
        tab._add_to_cart(other, 'Other', self.category, 1, 10, 12)
        tab.customer_name_input.setText('Name only')
        with patch('billing_tab.ReceiptDialog'):
            tab._complete_bill()
        bill = self.db.search_bills()[0]
        saved, lines = self.db.get_bill(bill['id'])
        self.assertEqual(saved['total'], 116.2)
        self.assertEqual(saved['gst_rate'], 0)
        self.assertEqual(saved['gst_amount'], 6.2)
        self.assertEqual(self.db.get_bill_balance(bill['id']), 0)
        self.assertEqual(len(lines), 2)

    def test_employee_receipt_scope_and_admin_historical_access(self):
        bid, _ = self.access.save_bill(**self.payload())
        self.assertEqual(self.access.get_bill(bid)[0]['total'], 105)
        self.session.logout()
        with self.assertRaises(PermissionError):
            self.access.get_bill(bid)
        self.session.login('1852j')
        self.assertEqual(self.access.get_bill(bid)[0]['total'], 105)

    def test_admin_can_discount_take_partial_payment_and_credit(self):
        self.session.login('1852j')
        for paid in (0, 50):
            tab = self.tab()
            tab._toggle_discount_visibility()
            self.assertTrue(tab.discount_visible)
            tab.cart_table.item(0, 5).setText('10')
            tab.payment_amount_input.setValue(paid)
            with patch('billing_tab.ReceiptDialog'):
                tab._complete_bill()
        bills = self.db.search_bills()
        self.assertEqual(len(bills), 2)
        self.assertEqual(sorted(self.db.get_bill_balance(b['id']) for b in bills), [44.5, 94.5])
        self.assertTrue(all(b['discount_amount'] == 10 for b in bills))

    def test_delete_confirmation_uses_admin_password(self):
        for password, expected in [('1852', False), ('1852j', True)]:
            with patch.object(widgets.QInputDialog, 'getText', return_value=(password, True)):
                self.assertEqual(widgets.confirm_delete_password(None, 'delete'), expected)


if __name__ == '__main__':
    unittest.main()
