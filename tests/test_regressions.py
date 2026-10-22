"""Run against either desktop copy with BILLING_APP_DIR (see tests/README.md)."""
import csv
import importlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / os.environ.get('BILLING_APP_DIR', 'app')
sys.path.insert(0, str(APP_DIR))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog, QDialog
from database import Database, SCHEMA
from billing_tab import BillingTab
from access import AccessSession
from expenses_tab import ExpensesTab
from balances_tab import ReceivePaymentDialog
from receipt import ReceiptDialog, _paid_amount, _balance_amount, _make_payment_qr_image
from money import money, sum_money, tax_amount
import main


class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='billing-regressions-')
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.db = Database(str(self.path / 'shop.db'))
        self.session = AccessSession()
        self.session.login("1852j")
        self.slot_errors = []
        old_hook = sys.excepthook
        sys.excepthook = lambda *args: self.slot_errors.append(args[1])
        self.addCleanup(setattr, sys, 'excepthook', old_hook)
        self.dialog_patches = [patch.object(QMessageBox, method) for method in ('information', 'warning', 'critical')]
        for patched in self.dialog_patches:
            patched.start()
            self.addCleanup(patched.stop)

    def tearDown(self):
        self.app.processEvents()
        self.assertEqual(self.slot_errors, [])

    def item(self, stock=10, name='Test shirt', size='', color='', barcode=''):
        category = self.db.get_categories()[0]['id']
        return self.db.add_item(name, category, None, barcode, size, color, 100, stock)

    def sale(self, paid=105, total=105, item_id=None, qty=1, customer_id=None, subtotal=100, **kwargs):
        return self.db.save_bill(
            customer_id, [{'item_id':item_id, 'name':'Test shirt', 'category':'Shirts',
                           'quantity':qty, 'rate':subtotal/qty, 'subtotal':subtotal,
                           'gst_rate':5, 'gst_amount':total-subtotal}],
            subtotal, 0, 0, total, 'Cash', initial_payment_amount=paid,
            taxable_amount=subtotal, gst_rate=5, gst_amount=total-subtotal, **kwargs)

    def test_unpaid_bill_survives_repeated_restarts(self):
        cid = self.db.add_customer('Customer', '')
        bid, _ = self.sale(paid=0, customer_id=cid)
        for _ in range(3):
            self.db = Database(self.db.path)
            self.assertEqual(self.db.get_bill_balance(bid), 105)
            self.assertEqual(self.db.get_bill_payment_history(bid), [])

    def test_existing_credit_database_without_new_migrations_is_preserved(self):
        # Reproduce an existing payment-enabled database before this patch.
        bid, _ = self.sale(paid=0)
        with sqlite3.connect(self.db.path) as conn:
            conn.execute('DROP TABLE bill_sequences')
        self.db = Database(self.db.path)
        self.assertEqual(self.db.get_bill_balance(bid), 105)

    def test_legacy_paid_bills_backfilled_once(self):
        legacy = str(self.path / 'legacy.db')
        with sqlite3.connect(legacy) as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO bills(bill_no,subtotal,total) VALUES ('INV-20260101-009',100,100)")
            conn.execute("CREATE TABLE expenses (id INTEGER PRIMARY KEY, expense_date TEXT, category TEXT, amount REAL, payment_mode TEXT, description TEXT)")
            conn.execute("INSERT INTO expenses VALUES (1,'2026-01-01','Rent',10,'Cash','')")
        db = Database(legacy)
        self.assertEqual(db.get_bill_paid_amount(1), 100)
        self.assertIsNone(db.get_expenses()[0]['created_at'])
        self.assertEqual(db.next_bill_no('2026-01-01'), 'INV-20260101-010')
        self.assertEqual(len(Database(legacy).get_bill_payment_history(1)), 1)

    def test_populated_expenses_open_and_have_timestamps(self):
        self.db.add_expense('Rent', 10)
        self.db = Database(self.db.path)
        self.assertTrue(self.db.get_expenses()[0]['created_at'])
        with patch.object(main, 'Database', return_value=self.db):
            window = main.MainWindow()
            window.session.login("1852j")
            window._build_dashboard()
        for i in range(window.tabs.count()):
            window.tabs.setCurrentIndex(i)
            self.app.processEvents()
        self.assertTrue(window.close())

    def test_expense_delete_button_confirmation_and_cancel(self):
        eid = self.db.add_expense('Rent', 10)
        tab = ExpensesTab(self.db)
        with patch('expenses_tab.confirm_delete_password', return_value=False) as confirm:
            tab.expense_table.cellWidget(0, 7).click()
            confirm.assert_called_once_with(tab, 'delete this expense')
        self.assertEqual(len(self.db.get_expenses()), 1)
        with patch('expenses_tab.confirm_delete_password', return_value=True):
            tab.expense_table.cellWidget(0, 7).click()
        self.assertEqual(self.db.get_expenses(), [])

    def test_receipts_use_actual_payments_on_initial_print_and_reprint(self):
        bid, _ = self.sale(paid=25)
        bill, lines = self.db.get_bill(bid)
        self.assertEqual((_paid_amount(bill), _balance_amount(bill)), (25, 80))
        dialog = ReceiptDialog(bill, lines)
        self.assertIn('80.00', dialog.text_edit.toPlainText())
        self.db.add_payment(bid, 80)
        bill, _ = self.db.get_bill(bid)
        self.assertEqual((_paid_amount(bill), _balance_amount(bill)), (105, 0))

    def test_pdf_and_print_action_generate_real_pdf(self):
        bill, lines = self.db.get_bill(self.sale()[0])
        dialog = ReceiptDialog(bill, lines)
        for action in ('save_pdf', 'print_receipt'):
            target = self.path / (action + '.pdf')
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(target), 'PDF')):
                if action == 'print_receipt':
                    def accept_print(printer, parent):
                        from PySide6.QtPrintSupport import QPrinter
                        printer.setOutputFormat(QPrinter.PdfFormat)
                        printer.setOutputFileName(str(target))
                        result = unittest.mock.Mock()
                        result.exec.return_value = QDialog.Accepted
                        return result
                    with patch('receipt.PrinterDialog', side_effect=accept_print):
                        dialog.print_receipt()
                else:
                    dialog.save_pdf()
            self.assertTrue(target.read_bytes().startswith(b'%PDF-'))
            self.assertGreater(target.stat().st_size, 1000)

    def test_receipt_qr_is_present(self):
        self.assertFalse(_make_payment_qr_image({'bill_no':'TEST'}, 100).isNull())

    def test_receipt_qr_contains_upi_recipient_and_invoice_amount(self):
        import receipt
        bill_no = 'INV-TEST & 1'
        for amount in (1, 105.50, 2100.01):
            with self.subTest(amount=amount):
                qr = receipt.qrcode.QRCode()
                with patch('receipt.qrcode.QRCode', return_value=qr):
                    image = _make_payment_qr_image({'bill_no': bill_no}, amount)
                self.assertFalse(image.isNull())
                payload = b''.join(part.data for part in qr.data_list).decode('utf-8')
                uri = urlsplit(payload)
                self.assertEqual((uri.scheme, uri.netloc), ('upi', 'pay'))
                self.assertEqual(parse_qs(uri.query), {
                    'pa': ['9148783935.ibz@icici'],
                    'pn': [receipt.SHOP_NAME],
                    'am': [f'{amount:.2f}'],
                    'cu': ['INR'],
                    'tn': [f'Invoice {bill_no}'],
                })
        bill, lines = self.db.get_bill(self.sale()[0])
        dialog = ReceiptDialog(bill, lines)
        self.addCleanup(dialog.close)
        self.assertIn('9148783935.ibz@icici', dialog.text_edit.toPlainText())

    def test_admin_discounted_receipt_qr_uses_paid_now(self):
        import receipt
        for paid_now in (200, 700, 0):
            with self.subTest(paid_now=paid_now):
                tab = BillingTab(self.db, session=self.session)
                self.addCleanup(tab.close)
                tab._add_to_cart(None, 'Discounted item', 'Shirts', 1, 800, 0)
                tab.cart_table.item(0, 5).setText('100')
                tab.payment_amount_input.setValue(paid_now)
                self.assertEqual(tab._grand_total(), 700)
                codes = []
                factory = receipt.qrcode.QRCode

                def capture_qr(*args, **kwargs):
                    code = factory(*args, **kwargs)
                    codes.append(code)
                    return code

                with patch('receipt.qrcode.QRCode', side_effect=capture_qr), \
                     patch.object(ReceiptDialog, 'exec', lambda self: QDialog.Accepted):
                    tab._complete_bill()
                dialog = tab.findChild(ReceiptDialog)
                self.assertIsNotNone(dialog)
                bill = dialog.bill_row
                self.assertEqual((bill['total'], bill['paid_amount'], bill['balance']),
                                 (700, paid_now, 700 - paid_now))
                html = receipt.build_receipt_html(bill, dialog.bill_items)
                if paid_now:
                    self.assertEqual(len(codes), 1)
                    payload = b''.join(part.data for part in codes[0].data_list).decode('utf-8')
                    params = parse_qs(urlsplit(payload).query)
                    self.assertEqual(params['am'], [f'{paid_now:.2f}'])
                    self.assertEqual(params['pa'], ['9148783935.ibz@icici'])
                    self.assertIn(f'<div class="qr-amount">₹{paid_now:.2f}</div>', html)
                else:
                    self.assertEqual(codes, [])
                    self.assertNotIn('qr://invoice-payment', html)
                    self.assertNotIn('SCAN TO PAY', dialog.text_edit.toPlainText())

    def test_reprinted_receipt_qr_matches_recorded_payments(self):
        import receipt
        bid, _ = self.sale(paid=200, total=700, subtotal=700)
        for expected, extra in ((200, 0), (350, 150)):
            if extra:
                self.db.add_payment(bid, extra)
            bill, lines = self.db.get_bill(bid)
            with patch('receipt._make_payment_qr_image', wraps=_make_payment_qr_image) as qr:
                dialog = ReceiptDialog(bill, lines)
            self.addCleanup(dialog.close)
            qr.assert_called_once_with(bill, expected)
            self.assertIn(f'<div class="qr-amount">₹{expected:.2f}</div>',
                          receipt.build_receipt_html(bill, lines))

    def test_partial_payment_and_rounding_agree(self):
        tab = BillingTab(self.db, session=self.session)
        tab._add_to_cart(None, 'Small item', 'Shirts', 1, 1.31)
        self.assertEqual(tab._grand_total(), 1.38)
        tab.customer_name_input.setText('Customer')
        tab.payment_amount_input.setValue(1)
        with patch('billing_tab.ReceiptDialog'):
            tab._complete_bill()
        bill = self.db.search_bills()[0]
        self.assertEqual(bill['total'], 1.38)
        self.assertEqual(self.db.get_bill_balance(bill['id']), 0.38)
        receive = ReceivePaymentDialog(self.db, bill)
        self.assertEqual(receive.amount_input.value(), 0.38)
        receive._save()
        self.assertEqual(self.db.get_bill_balance(bill['id']), 0)

    def test_legacy_fractional_balance_can_be_settled(self):
        bid, _ = self.sale(paid=1, total=1.38, subtotal=1.31)
        with sqlite3.connect(self.db.path) as conn:
            conn.execute('UPDATE bills SET total=1.3755 WHERE id=?', (bid,))
        self.assertEqual(self.db.get_bill_balance(bid), 0.38)
        self.db.add_payment(bid, 0.38)
        self.assertEqual(self.db.get_bill_balance(bid), 0)
        self.assertEqual(self.db.get_bill(bid)[0]['balance'], 0)

    def test_customer_balance_agrees_with_settled_legacy_bills(self):
        customer = self.db.add_customer('Customer', '')
        for _ in range(2):
            bid, _ = self.sale(total=1.37, paid=0, subtotal=1.30, customer_id=customer)
            with sqlite3.connect(self.db.path) as conn:
                conn.execute('UPDATE bills SET total=1.3745 WHERE id=?', (bid,))
            self.db.add_payment(bid, 1.37)
        self.assertEqual(self.db.get_customer_balance(customer)['balance'], 0)
        self.assertEqual(self.db.get_customer_balances()[0]['balance'], 0)

    def test_tax_rounding_matches_lines_and_stored_totals(self):
        tab = BillingTab(self.db, session=self.session)
        tab._add_to_cart(None, 'A', 'Shirts', 1, 0.10)
        tab._add_to_cart(None, 'B', 'Shirts', 1, 0.10)
        self.assertEqual(tab._gst_amount(), 0.02)
        with patch('billing_tab.ReceiptDialog'):
            tab._complete_bill()
        bill, lines = self.db.get_bill(self.db.search_bills()[0]['id'])
        self.assertEqual(sum_money(line['gst_amount'] for line in lines), bill['gst_amount'])
        self.assertEqual(bill['total'], 0.22)
        for invalid in ('nan', 'inf', '-inf'):
            with self.assertRaises(ValueError): money(invalid)

    def test_concurrent_payment_waits_then_rechecks_balance(self):
        bid, _ = self.sale(paid=0, total=100, subtotal=100)
        original = self.db._conn
        first_read = threading.Event()
        second_attempted = threading.Event()
        local = threading.local()
        class Cursor:
            def __init__(self, cursor): self.cursor = cursor
            def fetchone(self):
                row = self.cursor.fetchone()
                first_read.set()
                if not second_attempted.wait(5): raise AssertionError('second payment did not start')
                return row
        class Connection:
            def __init__(self, conn): self.conn = conn
            def execute(self, sql, *args):
                if sql == 'BEGIN IMMEDIATE' and local.role == 1:
                    second_attempted.set()
                cursor = self.conn.execute(sql, *args)
                return Cursor(cursor) if 'FROM payments' in sql and 'AS paid' in sql and local.role == 0 else cursor
        @contextmanager
        def instrumented():
            with original() as conn:
                yield Connection(conn)
        def pay(role):
            local.role = role
            try:
                self.db.add_payment(bid, 100)
                return 'paid'
            except ValueError:
                return 'rejected'
        with patch.object(self.db, '_conn', instrumented):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(pay, 0)
                self.assertTrue(first_read.wait(5))
                second = pool.submit(pay, 1)
                self.assertEqual(first.result(10), 'paid')
                self.assertEqual(second.result(10), 'rejected')
        self.assertEqual(self.db.get_bill_paid_amount(bid), 100)

    def test_invoice_numbers_survive_delete_and_reopen(self):
        bid, first = self.sale(bill_date='2026-01-01')
        self.db.delete_bill(bid)
        self.db = Database(self.db.path)
        _, second = self.sale(bill_date='2026-01-01')
        self.assertEqual(first, 'INV-20260101-001')
        self.assertEqual(second, 'INV-20260101-002')
        self.assertEqual(self.db.next_bill_no('2026-01-02'), 'INV-20260102-001')

    def test_stock_reversal_uses_actual_deduction_once(self):
        for stock in (0, 2, 10):
            iid = self.item(stock)
            bid, _ = self.sale(item_id=iid, qty=5)
            self.assertEqual(self.db.get_item_by_id(iid)['stock_qty'], max(stock-5, 0))
            self.db.delete_bill(bid)
            self.db.delete_bill(bid)
            self.assertEqual(self.db.get_item_by_id(iid)['stock_qty'], stock)

    def test_repeated_product_lines_record_separate_deductions(self):
        iid = self.item(3)
        lines = [{'item_id':iid,'name':'A','quantity':2,'rate':10,'subtotal':20},
                 {'item_id':iid,'name':'A','quantity':2,'rate':20,'subtotal':40}]
        bid, _ = self.db.save_bill(None, lines, 60, 0, 0, 60, 'Cash')
        self.assertEqual([r['stock_deducted'] for r in self.db.get_bill(bid)[1]], [2, 1])
        self.db.delete_bill(bid)
        self.assertEqual(self.db.get_item_by_id(iid)['stock_qty'], 3)

    def test_failed_bill_rolls_back_stock_payment_and_number(self):
        iid = self.item(10)
        lines = [{'item_id':iid,'name':'A','quantity':1,'rate':100,'subtotal':100},
                 {'name':'bad','quantity':0,'rate':100,'subtotal':100}]
        before = self.db.next_bill_no()
        with self.assertRaises(ValueError):
            self.db.save_bill(None, lines, 200, 0, 0, 200, 'Cash')
        self.assertEqual(self.db.search_bills(), [])
        self.assertEqual(self.db.get_item_by_id(iid)['stock_qty'], 10)
        self.assertEqual(self.db.next_bill_no(), before)

    def test_variant_creation_does_not_overwrite_original(self):
        iid = self.item(name='Classic', size='M', color='Blue', barcode='M')
        original = dict(self.db.get_item_by_id(iid))
        tab = BillingTab(self.db, session=self.session)
        tab.item_name_combo.setEditText('Classic')
        tab.size_input.setText('L')
        tab.color_input.setText('Red')
        tab.new_barcode_input.setText('L')
        tab.rate_input.setValue(200)
        tab._add_manual_item()
        self.assertEqual(dict(self.db.get_item_by_id(iid)), original)
        self.assertEqual(len(self.db.get_items()), 2)
        variant = self.db.get_item_by_barcode('L')
        self.assertEqual((variant['size'], variant['color']), ('L', 'Red'))
        labels = list(tab.current_items_by_name)
        self.assertEqual(len(labels), 2)
        self.assertTrue(all('classic' in label for label in labels))

    def test_matching_variant_uses_sale_rate_without_catalogue_edit(self):
        iid = self.item(name='Classic', size='M', color='Blue', barcode='M')
        tab = BillingTab(self.db, session=self.session)
        for price in (100, 200):
            tab.item_name_combo.setEditText('Classic')
            tab.rate_input.setValue(price)
            tab._add_manual_item()
        self.assertEqual(len(self.db.get_items()), 1)
        self.assertEqual(len(tab.cart), 2)
        self.assertEqual(tab._subtotal(), 300)
        self.assertEqual(self.db.get_item_by_id(iid)['rate'], 100)
        tab._add_to_cart(iid, 'Classic', 'Shirts', 1, 200)
        self.assertEqual(tab._subtotal(), 500)
        self.assertEqual(len(tab.cart), 2)

    def test_explicit_variant_selection_needs_no_barcode(self):
        first = self.item(name='Same')
        second = self.item(name='Same')
        tab = BillingTab(self.db, session=self.session)
        label = next(label for label, row in tab.current_items_by_name.items() if row['id'] == second)
        tab.item_name_combo.setEditText(label)
        tab._add_manual_item()
        self.assertEqual(len(tab.cart), 1)
        self.assertEqual(tab.cart[0]['item_id'], second)
        self.assertEqual(len(self.db.get_items()), 2)

    def test_optional_variant_fields_still_allow_manual_entry(self):
        tab = BillingTab(self.db, session=self.session)
        tab.item_name_combo.setEditText('No barcode or size')
        tab.rate_input.setValue(10)
        tab._add_manual_item()
        self.assertEqual(len(tab.cart), 1)
        row = self.db.get_item_by_id(tab.cart[0]['item_id'])
        self.assertIsNone(row['barcode'])
        self.assertIsNone(row['size'])
        self.assertIsNone(row['color'])

    def test_failed_manual_add_is_reported_without_changing_catalogue(self):
        self.item(name='Original', barcode='USED')
        tab = BillingTab(self.db, session=self.session)
        tab.item_name_combo.setEditText('Other')
        tab.rate_input.setValue(100)
        tab.new_barcode_input.setText('USED')
        tab._add_manual_item()
        self.assertEqual(tab.cart, [])
        self.assertEqual(len(self.db.get_items()), 1)
        QMessageBox.warning.assert_called()

    def test_customer_switch_clears_autofilled_identity(self):
        self.db.add_customer('A', '111', 'Address A')
        tab = BillingTab(self.db, session=self.session)
        tab.phone_input.setText('111')
        tab._lookup_customer()
        tab.phone_input.setText('222')
        tab._lookup_customer()
        self.assertEqual(tab.customer_name_input.text(), '')
        self.assertEqual(tab.customer_address_input.text(), '')
        tab.customer_name_input.setText('B')
        tab._add_to_cart(None, 'Test', 'Shirts', 1, 100)
        with patch('billing_tab.ReceiptDialog'):
            tab._complete_bill()
        self.assertEqual(self.db.get_customer_by_phone('222')['name'], 'B')
        self.assertEqual(self.db.get_customer_by_phone('111')['name'], 'A')

    def test_phone_suggestions_show_customer_name_and_save_selected_identity(self):
        cid = self.db.add_customer('Ravi', '910884773', 'Address Ravi')
        self.db.add_customer('Ravi', '910885555', 'Other address')
        self.db.add_customer('No phone', '')
        tab = BillingTab(self.db, session=self.session)
        self.addCleanup(tab.close)
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
        self.assertEqual(tab.customer_address_input.text(), 'Address Ravi')
        self.assertEqual(tab.matched_customer['id'], cid)
        tab._add_to_cart(None, 'Test', 'Shirts', 1, 100)
        with patch('billing_tab.ReceiptDialog'):
            tab._complete_bill()
        self.assertEqual(self.db.search_bills()[0]['customer_id'], cid)
        self.assertEqual(len(self.db.search_customers('')), 3)
        refreshed = tab.phone_input.completer()
        refreshed.setCompletionPrefix('91088')
        self.assertEqual(refreshed.completionCount(), 2)

    def test_blank_phone_transition_and_new_manual_name(self):
        self.db.add_customer('A', '111', 'Address A')
        tab = BillingTab(self.db, session=self.session)
        tab.phone_input.setText('111')
        tab._lookup_customer()
        tab.phone_input.setText('')
        tab.phone_input.textEdited.emit('')
        self.assertEqual(tab.customer_name_input.text(), '')
        tab.customer_name_input.setText('Name only')
        tab._lookup_customer()
        self.assertEqual(tab.customer_name_input.text(), 'Name only')

    def test_post_commit_failures_never_leave_a_resavable_cart(self):
        for failure in ('wishlist', 'receipt', 'refresh'):
            with self.subTest(failure=failure):
                refresh = unittest.mock.Mock()
                tab = BillingTab(self.db, refresh, session=self.session)
                tab.customer_name_input.setText('Customer')
                tab.wishlist_input.setText('Request')
                tab._add_to_cart(None, 'Test', 'Shirts', 1, 100)
                before = len(self.db.search_bills())
                with patch('billing_tab.ReceiptDialog') as receipt, patch.object(self.db, 'add_wishlist') as wishlist:
                    if failure == 'wishlist': wishlist.side_effect = sqlite3.OperationalError('locked')
                    if failure == 'receipt': receipt.side_effect = RuntimeError('render error')
                    if failure == 'refresh': refresh.side_effect = RuntimeError('refresh error')
                    tab._complete_bill()
                    self.assertEqual(tab.cart, [])
                    tab._complete_bill()
                self.assertEqual(len(self.db.search_bills()), before+1)

    def test_pre_commit_failure_keeps_cart_for_safe_retry(self):
        tab = BillingTab(self.db, session=self.session)
        tab._add_to_cart(None, 'Test', 'Shirts', 1, 100)
        with patch.object(self.db, 'save_bill', side_effect=sqlite3.OperationalError('locked')):
            tab._complete_bill()
        self.assertEqual(len(tab.cart), 1)
        self.assertEqual(len(self.db.search_bills()), 0)
        with patch('billing_tab.ReceiptDialog'):
            tab._complete_bill()
        self.assertEqual(len(self.db.search_bills()), 1)

    def test_inventory_refreshes_after_sale_and_bill_deletion(self):
        iid = self.item(10)
        with patch.object(main, 'Database', return_value=self.db):
            window = main.MainWindow()
            window.session.login("1852j")
            window._build_dashboard()
        window.billing_tab._add_to_cart(iid, 'Test', 'Shirts', 1, 100)
        with patch('billing_tab.ReceiptDialog'):
            window.billing_tab._complete_bill()
        window.tabs.setCurrentWidget(window.inventory_tab)
        self.assertEqual(window.inventory_tab.items_table.item(0, 4).text(), '9')
        window.tabs.setCurrentWidget(window.sales_tab)
        self.db.delete_bill(self.db.search_bills()[0]['id'])
        window.tabs.setCurrentWidget(window.inventory_tab)
        self.assertEqual(window.inventory_tab.items_table.item(0, 4).text(), '10')
        window.close()

    def test_category_removal_archives_links_and_history(self):
        iid = self.item(10)
        category = self.db.get_item_by_id(iid)['category_id']
        category_name = self.db.get_item_by_id(iid)['category_name']
        bid, _ = self.sale(item_id=iid)
        with self.assertRaises(ValueError): self.db.delete_category(category)
        self.db.delete_item(iid)
        self.db.delete_category(category)
        self.assertNotIn(category, [c['id'] for c in self.db.get_categories()])
        self.assertEqual(self.db.get_bill(bid)[1][0]['item_id'], iid)
        self.db = Database(self.db.path)
        self.assertNotIn(category, [c['id'] for c in self.db.get_categories()])
        self.db.delete_bill(bid)
        self.assertEqual(self.db.get_item_by_id(iid)['stock_qty'], 10)
        self.db.add_category(category_name)
        self.assertIn(category, [c['id'] for c in self.db.get_categories()])

    def test_archived_category_rejects_stale_item_form(self):
        iid = self.item()
        row = self.db.get_item_by_id(iid)
        category = row['category_id']
        self.db.delete_item(iid)
        self.db.delete_category(category)
        with self.assertRaises(ValueError):
            self.db.add_item('Stale', category, None, '', '', '', 10)
        with self.assertRaises(ValueError):
            self.db.update_item(iid, 'Stale', category, None, '', '', '', 10, 1)
        self.assertEqual(self.db.get_item_by_id(iid)['active'], 0)

    def test_active_duplicate_category_still_reports_conflict(self):
        name = self.db.get_categories()[0]['name']
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_category(name)

    def test_backup_and_csv_include_new_tables_and_optional_values(self):
        if not hasattr(self.db, 'export_all_tables_csv'):
            self.skipTest('Backup/export UI exists only in nested desktop copy')
        self.sale(paid=0)
        backup = self.db.backup_database()
        with sqlite3.connect(backup) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM bill_sequences').fetchone()[0], 1)
        files = self.db.export_all_tables_csv(str(self.path / 'csv'))
        self.assertIn('bill_sequences.csv', [Path(f).name for f in files])
        with open(self.path / 'csv' / 'bill_items.csv') as stream:
            self.assertIn('stock_deducted', next(csv.reader(stream)))


if __name__ == '__main__':
    unittest.main()
