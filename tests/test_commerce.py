"""Real transactions: purchasing, valuation, accounts, permissions and migrations."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/os.environ.get('BILLING_APP_DIR','app')))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from database import Database
from access import AccessSession,RoleDatabase
from commerce_ui import PurchasesTab,ChartAccountsTab,purchase_html
from receipt import ReceiptDialog
from PySide6.QtWidgets import QApplication
from PySide6.QtPrintSupport import QPrinter


class CommerceFixture:
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.db=Database(str(Path(self.folder.name)/'test.db'))
        self.item=self.db.add_item('Denim jeans',self.db.get_categories()[0]['id'],None,'JEANS','','',500,0)
        group=next(r['id'] for r in self.db.get_account_groups() if r['name']=='Suppliers')
        self.supplier=self.db.save_account_ledger('Supplier',group,'Supplier','9000000000')
        self.seller=dict(name='Supplier',phone='9000000000',gst='29ABCDE1234F1Z5')
        self.buyer=dict(name='Shop',phone='9111111111',gst='29ABCDE1234F1Z5')
        self.seq=0

    def purchase(self,qty=10,rate=100,gst=False,paid=0,location=1,unit=1,discount=0):
        self.seq+=1
        return self.db.save_purchase(self.supplier,self.seller,self.buyer,'SUP-'+str(self.seq),
            [dict(item_id=self.item,unit_id=unit,quantity=qty,rate=rate,discount=discount,gst_rate=18)],gst,
            location,paid,purchase_date='2026-10-22')

    def sale(self,qty=1,location=1,channel='Retail',paid=0,customer=None):
        return self.db.save_bill(customer,[dict(item_id=self.item,name='Jeans',quantity=qty,rate=500,subtotal=qty*500,gst_rate=0,gst_amount=0)],
            qty*500,0,0,qty*500,'Cash',bill_date='2026-10-22',initial_payment_amount=paid,godown_id=location,sale_channel=channel)[0]

    def report(self,location=None):
        return self.db.commerce_report('2026-10-01','2026-10-31',location)


class CommerceTests(CommerceFixture, unittest.TestCase):
    def test_purchase_gst_optional_details_unit_conversion_and_supplier_balance(self):
        unit=self.db.save_master('Unit','Bundle',50)
        self.seller.pop('gst');self.buyer.pop('gst')
        pid=self.purchase(qty=2,rate=25000,paid=25000,unit=unit)
        p,lines=self.db.get_purchase(pid)
        self.assertEqual((p['net_cents'],p['gst_cents'],p['paid_cents']),(5000000,0,2500000))
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],100)
        self.assertEqual(self.db.account_statement('ledger:'+str(self.supplier))['balance_cents'],-2500000)
        self.db.save_master('Unit','Bundle',60,unit)
        self.assertEqual(self.db.get_purchase(pid)[1][0]['unit_pieces'],50)
        self.assertEqual(lines[0]['unit_name'],'Bundle')
        with self.assertRaises(ValueError):self.purchase(gst=True)

    def test_gst_rounding_and_purchase_payment_boundaries(self):
        pid=self.purchase(qty=3,rate=10.01,gst=True,paid=10,discount=.02)
        p,_=self.db.get_purchase(pid)
        self.assertEqual((p['net_cents'],p['gst_cents'],p['total_cents']),(3001,540,3541))
        self.db.add_purchase_payment(pid,25.41)
        with self.assertRaises(ValueError):self.db.add_purchase_payment(pid,.01)
        self.assertEqual(self.db.account_statement('ledger:'+str(self.supplier))['balance_cents'],0)

    def test_atomic_purchase_failure_and_duplicate_invoice(self):
        self.purchase()
        before=self.db.get_item_by_id(self.item)['stock_qty']
        self.seq=0
        with self.assertRaises(sqlite3.IntegrityError):self.purchase()
        with self.db._conn() as c:
            c.execute("CREATE TRIGGER fail_purchase BEFORE INSERT ON purchase_lines BEGIN SELECT RAISE(ABORT,'forced'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.purchase()
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],before)
        self.assertEqual(len(self.report()['purchases']),1)

    def test_weighted_average_sale_profit_and_delete_reversal(self):
        self.purchase(qty=10,rate=100);self.purchase(qty=10,rate=200)
        bid=self.sale(qty=4,paid=1000,channel='Wholesale')
        self.assertEqual(self.db.get_bill(bid)[1][0]['cost_cents'],60000)
        self.db.add_expense('Rent',100,expense_date='2026-10-22')
        r=self.report()
        self.assertEqual(r['profit_cents'],130000)
        self.assertEqual(r['sales'][0]['sale_channel'],'Wholesale')
        self.assertEqual(self.db.stock_report()[0]['quantity'],16)
        self.db.delete_bill(bid)
        self.assertEqual(self.db.stock_report()[0]['value_cents'],300000)
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],20)
        self.assertEqual(len(self.report()['sales']),0)

    def test_godown_transfer_and_sale_use_only_selected_location(self):
        second=self.db.save_master('Godown','Shop 2')
        self.purchase(qty=10,rate=100)
        self.db.transfer_stock(self.item,1,second,4)
        self.sale(qty=2,location=second)
        self.assertEqual(self.db.stock_report(1)[0]['quantity'],6)
        self.assertEqual(self.db.stock_report(second)[0]['quantity'],2)
        self.assertEqual(self.db.stock_report()[0]['quantity'],8)
        self.assertEqual(self.report(second)['cost_cents'],20000)
        self.assertEqual(self.report(1)['net_sales_cents'],0)
        with self.assertRaises(ValueError):self.db.transfer_stock(self.item,1,second,7)
        self.assertEqual(self.db.stock_report()[0]['quantity'],8)

    def test_opening_cost_and_unknown_history_never_invent_profit(self):
        self.db.adjust_stock(self.item,10)
        self.assertIsNone(self.db.stock_report()[0]['value_cents'])
        self.db.set_opening_stock(self.item,1,10,150)
        self.sale(2)
        self.assertEqual(self.report()['cost_cents'],30000)
        with self.assertRaises(ValueError):self.db.set_opening_stock(self.item,1,8,100)
        self.sale(20)
        self.assertIsNone(self.report()['profit_cents'])
        self.assertEqual(self.db.stock_report()[0]['quantity'],0)

    def test_supplier_and_customer_advances_allocate_without_double_cash(self):
        v=self.db.save_voucher('Payment','ledger:'+str(self.supplier),'cash',200,'Supplier token',voucher_date='2026-10-20')
        pid=self.purchase()
        self.db.apply_advance(v,'Purchase',pid,200,'2026-10-22')
        self.assertEqual(self.db.get_purchase(pid)[0]['paid_cents'],20000)
        self.assertEqual(self.db.account_statement('cash')['balance_cents'],-20000)
        self.assertEqual(self.db.account_statement('ledger:'+str(self.supplier))['balance_cents'],-80000)
        with self.assertRaises(ValueError):self.db.apply_advance(v,'Purchase',pid,1,'2026-10-22')
        cid=self.db.add_customer('Ravi','900001')
        v=self.db.save_voucher('Receipt','cash','customer:'+str(cid),100,'Customer token',voucher_date='2026-10-20')
        bid=self.sale(customer=cid)
        self.db.apply_advance(v,'Sale',bid,100,'2026-10-22')
        self.assertEqual(self.db.get_bill_balance(bid),400)
        self.assertEqual(self.db.account_statement('customer:'+str(cid))['balance_cents'],40000)
        self.assertEqual(self.db.account_statement('cash')['balance_cents'],-10000)

    def test_journal_balances_and_period_opening(self):
        self.db.save_voucher('Payment','ledger:'+str(self.supplier),'cash',20,'Advance',voucher_date='2026-09-20')
        self.purchase(paid=50)
        result=self.db.account_statement('ledger:'+str(self.supplier),'2026-10-01','2026-10-31')
        self.assertEqual(result['opening_cents'],2000)
        self.assertEqual(result['balance_cents'],-93000)
        balances=sum(self.db.account_statement(r['key'])['balance_cents'] for r in self.db.get_account_ledgers())
        self.assertEqual(balances,0)
        with self.assertRaises(ValueError):self.db.save_voucher('Journal','cash','cash',10,'Wrong')
        with self.assertRaises(ValueError):self.db.save_voucher('Payment','cash','inventory',10,'Wrong')

    def test_reopening_database_preserves_godown_allocation_and_legacy_total(self):
        self.purchase();second=self.db.save_master('Godown','Second');self.db.transfer_stock(self.item,1,second,3)
        again=Database(self.db.path)
        self.assertEqual(again.stock_report(1)[0]['quantity'],7)
        self.assertEqual(again.stock_report(second)[0]['quantity'],3)
        row=again.get_item_by_id(self.item)
        again.update_item(self.item,row['name'],row['category_id'],None,'JEANS','','',500,12)
        self.assertEqual(again.stock_report(1)[0]['quantity'],9)
        self.assertEqual(again.stock_report(second)[0]['quantity'],3)
        with self.assertRaises(ValueError):again.adjust_stock(self.item,-10)
        self.assertEqual(again.get_item_by_id(self.item)['stock_qty'],12)

    def test_admin_boundary_and_purchase_receipt_pdf(self):
        session=AccessSession();role=RoleDatabase(self.db,session)
        for name,args in [('get_suppliers',()),('stock_report',()),('save_master',('Godown','Forbidden')),('commerce_report',('2026-10-01','2026-10-31'))]:
            with self.assertRaises(PermissionError):getattr(role,name)(*args)
        session.login('1852j');p=PurchasesTab(role);accounts=ChartAccountsTab(role)
        self.addCleanup(p.close);self.addCleanup(accounts.close)
        pid=self.purchase(gst=True,paid=100)
        data,lines=self.db.get_purchase(pid)
        html=purchase_html(data,lines)
        self.assertIn('Balance payable',html)
        receipt=ReceiptDialog(dict(bill_no='P-1',total=0),[],html=html)
        self.addCleanup(receipt.close)
        printer=QPrinter();receipt._configure_printer(printer);printer.setOutputFormat(QPrinter.PdfFormat)
        path=Path(self.folder.name)/'purchase.pdf';printer.setOutputFileName(str(path));receipt._print_document(printer)
        self.assertTrue(path.read_bytes().startswith(b'%PDF'))
        session.logout()
        with self.assertRaises(PermissionError):role.get_purchase(pid)


class CommerceBoundaryTests(CommerceFixture, unittest.TestCase):
    def test_exchange_restores_original_cost_and_shop(self):
        shop=self.db.save_master('Godown','Exchange shop')
        self.purchase(qty=10,rate=100,location=shop)
        bid=self.sale(qty=2,location=shop,paid=1000,channel='Wholesale')
        original=self.db.get_bill(bid)[1][0]
        replacement=dict(item_id=self.item,name='Jeans',quantity=3,rate=500,subtotal=1500,gst_rate=0,gst_amount=0)
        eid,_=self.db.save_exchange(bid,{original['id']:1},[replacement],500,1000,'Cash','cost-exchange')
        bill,lines=self.db.get_bill(eid)
        self.assertEqual((bill['godown_id'],bill['sale_channel']),(shop,'Wholesale'))
        self.assertEqual([r['cost_cents'] for r in lines],[-10000,30000])
        self.assertEqual(self.db.stock_report(shop)[0]['quantity'],6)
        self.assertEqual(self.report(shop)['cost_cents'],40000)

    def test_expenses_are_allocated_to_the_selected_shop(self):
        shop=self.db.save_master('Godown','Rent shop')
        self.db.add_expense('Rent',100,expense_date='2026-10-22',godown_id=shop)
        self.assertEqual(self.report(1)['expenses_cents'],0)
        self.assertEqual(self.report(shop)['expenses_cents'],10000)
        self.assertEqual(self.report()['expenses_cents'],10000)

    def test_employee_cannot_forge_godown_or_wholesale(self):
        session=AccessSession();role=RoleDatabase(self.db,session)
        self.purchase()
        item=self.db.get_item_by_id(self.item)
        for kwargs in (dict(godown_id=2),dict(sale_channel='Wholesale')):
            with self.assertRaises((PermissionError,ValueError)):
                role.save_bill(None,[dict(item_id=self.item,name=item['name'],quantity=1,rate=500,subtotal=500,gst_rate=5,gst_amount=25)],
                    500,0,0,525,'Cash',gst_rate=5,gst_amount=25,initial_payment_amount=525,**kwargs)
        self.assertEqual(self.db.search_bills(),[])

    def test_purchase_screen_saves_exact_preview_and_clears_only_after_commit(self):
        from PySide6.QtWidgets import QMessageBox
        session=AccessSession();session.login('1852j');tab=PurchasesTab(RoleDatabase(self.db,session));self.addCleanup(tab.close)
        tab.invoice.setText('UI-1');tab.rate.setValue(500);tab.qty.setValue(100);tab._add();tab.paid.setValue(25000)
        with patch('commerce_ui.show_purchase') as receipt,patch.object(QMessageBox,'information'):
            tab._save()
        receipt.assert_called_once()
        p,_=self.db.get_purchase(tab.saved_id)
        self.assertEqual((p['total_cents'],p['paid_cents']),(5000000,2500000))
        self.assertEqual(tab.lines,[])
        self.assertTrue(tab.receipt_button.isEnabled())

    def test_legacy_bill_without_lines_is_not_claimed_as_pure_profit(self):
        with self.db._conn() as c:
            c.execute("INSERT INTO bills(bill_no,bill_date,subtotal,total) VALUES('LEGACY','2026-10-22',100,100)")
        self.assertIsNone(self.report()['profit_cents'])

    def test_backup_contains_accounting_and_godown_state(self):
        self.purchase();self.db.save_master('Godown','Backup shop')
        backup=self.db.backup_database(self.folder.name)
        with sqlite3.connect(backup) as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM purchases').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM godowns').fetchone()[0],2)

if __name__=='__main__':unittest.main()
