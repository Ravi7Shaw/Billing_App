"""Atomic admin exchanges: original paid value, stock, money, roles and GUI."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/os.environ.get('BILLING_APP_DIR','app')))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QMessageBox
from money import sum_money
from database import Database
from access import AccessSession,RoleDatabase
from exchanges_tab import ExchangeCart,ExchangesTab
from receipt import build_receipt_html,build_receipt_text
from stats_tab import StatsTab
from money import money,tax_amount
import main


class ExchangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = Database(str(Path(self.folder.name)/'exchanges.db'))
        self.cid = self.db.add_customer('Example Customer','9876543210')
        self.categories = self.db.get_categories()[:2]
        self.old = self.db.add_item('Original jacket',self.categories[0]['id'],None,'','','',800,10)
        self.new = self.db.add_item('New purchase',self.categories[1]['id'],None,'','','',1000,10)
        self.session = AccessSession()
        self.session.login('1852j')
        self.access = RoleDatabase(self.db,self.session)
        self.errors = []
        hook = sys.excepthook
        sys.excepthook = lambda *args:self.errors.append(args[1])
        self.addCleanup(setattr,sys,'excepthook',hook)
        for method in ('warning','critical','information'):
            mock = patch.object(QMessageBox,method)
            mock.start();self.addCleanup(mock.stop)

    def tearDown(self):
        self.app.processEvents()
        self.assertEqual(self.errors,[])

    def line(self,iid=None,qty=1,rate=None,net=None,gst=5):
        iid = self.new if iid is None else iid
        item = self.db.get_item_by_id(iid)
        rate = item['rate'] if rate is None else rate
        net = money(qty*rate) if net is None else net
        return dict(item_id=iid,name=item['name'],category=item['category_name'],quantity=qty,
                    rate=rate,subtotal=net,gst_rate=gst,gst_amount=tax_amount(net,gst))

    def sale(self,qty=1,net=None,gst=5,paid=None,customer=True):
        row = self.line(self.old,qty,net=net,gst=gst)
        gross = money(qty*800)
        total = money(row['subtotal']+row['gst_amount'])
        bid,_ = self.db.save_bill(self.cid if customer else None,[row],gross,0,
            money(gross-row['subtotal']),total,'Cash','2024-01-01',
            taxable_amount=row['subtotal'],gst_rate=gst,gst_amount=row['gst_amount'],
            initial_payment_amount=total if paid is None else paid)
        return bid,self.db.get_bill(bid)[1][0]['id']

    def exchange(self,bid,lid,qty=1,items=None,key='test',paid=None,credit=None):
        items = items or [self.line()]
        quote = self.db.quote_exchange(bid,{lid:qty})
        credit = quote['credit_cents']/100 if credit is None else credit
        diff = money(sum(i['subtotal']+i['gst_amount'] for i in items)-credit)
        return self.db.save_exchange(bid,{lid:qty},items,credit,diff if paid is None else paid,'Cash',key)

    def snapshot(self):
        with self.db._conn() as conn:
            return {table:[tuple(r) for r in conn.execute('SELECT * FROM '+table+' ORDER BY 1')]
                    for table in ('bills','bill_items','payments','items','bill_sequences','exchanges','exchange_returns')}

    def test_success_original_immutable_stock_payment_receipt_and_reports(self):
        bid,lid = self.sale()
        original = tuple(self.db.get_bill(bid)[0])
        eid,_ = self.exchange(bid,lid)
        bill,lines = self.db.get_bill(eid)
        self.assertEqual(tuple(self.db.get_bill(bid)[0]),original)
        self.assertEqual((bill['total'],bill['paid_amount'],bill['balance']),(210,210,0))
        self.assertEqual(bill['customer_id'],self.cid)
        self.assertEqual([r['quantity'] for r in lines],[-1,1])
        self.assertEqual(sum(r['subtotal']+r['gst_amount'] for r in lines),210)
        self.assertEqual(self.db.get_item_by_id(self.old)['stock_qty'],10)
        self.assertEqual(self.db.get_item_by_id(self.new)['stock_qty'],9)
        for receipt in (build_receipt_html(bill,lines),build_receipt_text(bill,lines)):
            self.assertIn(self.db.get_bill(bid)[0]['bill_no'],receipt)
            self.assertIn('Returned value',receipt)
        self.assertIn('Returned item',build_receipt_html(bill,lines))
        today = date.today().isoformat()
        report = self.db.report_statistics(today,today)
        totals = report['totals']
        self.assertEqual((totals['revenue'],totals['payments_collected'],totals['outstanding'],totals['pieces'],totals['bill_count']),
                         (210,210,0,0,1))
        self.assertEqual({r['name']:r['revenue'] for r in report['products']},{'Original jacket':-840,'New purchase':1050})
        self.assertEqual(sum_money(r['revenue'] for r in report['brands']),210)
        self.assertEqual(sum(r['quantity'] for r in report['brands']),0)
        report = self.db.report_statistics()
        self.assertEqual((report['totals']['revenue'],report['totals']['pieces']),(1050,1))
        self.assertEqual(sum(r['revenue'] for r in report['products']),1050)
        tab = StatsTab(self.db)
        self.addCleanup(tab.close)
        tab._report = self.db.report_statistics(today,today)
        tab._refresh_category_chart(today,today)
        tab.chart_group.setCurrentText('Brands / Styles')
        tab._refresh_top_items(today,today)
        self.assertEqual(sum(float(tab.top_brands_table.item(row, 2).text())
                             for row in range(tab.top_brands_table.rowCount())),0)

    def test_strict_higher_value_and_full_difference_rollback(self):
        bid,lid = self.sale()
        before = self.snapshot()
        for rate in (700,800):
            with self.subTest(rate=rate), self.assertRaisesRegex(ValueError,'must cost more'):
                self.exchange(bid,lid,items=[self.line(rate=rate)])
            self.assertEqual(self.snapshot(),before)
        for paid in (0,209.99,210.01):
            with self.subTest(paid=paid), self.assertRaisesRegex(ValueError,'full difference'):
                self.exchange(bid,lid,paid=paid)
            self.assertEqual(self.snapshot(),before)
        with self.assertRaisesRegex(ValueError,'return value changed'):
            self.exchange(bid,lid,credit=839.99)
        self.assertEqual(self.snapshot(),before)

    def test_unpaid_source_must_be_settled(self):
        bid,lid = self.sale(paid=100)
        with self.assertRaisesRegex(ValueError,'outstanding balance'):
            self.db.quote_exchange(bid,{lid:1})
        self.db.add_payment(bid,740)
        self.assertEqual(self.db.quote_exchange(bid,{lid:1})['credit_cents'],84000)

    def test_returned_value_uses_discounted_snapshot_not_current_price(self):
        bid,lid = self.sale(qty=3,net=2000)
        with self.db._conn() as conn:
            conn.execute('UPDATE items SET rate=9000 WHERE id=?',(self.old,))
        quote = self.db.quote_exchange(bid,{lid:3})
        self.assertEqual(quote['credit_cents'],210000)
        eid,_ = self.exchange(bid,lid,qty=3,items=[self.line(qty=3)])
        self.assertEqual(self.db.get_bill(eid)[0]['total'],1050)

    def test_partial_returns_preserve_final_rounding_cent(self):
        bid,lid = self.sale(qty=3,net=100.01)
        credits = []
        for n in range(3):
            credits.append(self.db.quote_exchange(bid,{lid:1})['credit_cents'])
            self.exchange(bid,lid,key=str(n))
        self.assertEqual(credits,[3500,3500,3501])
        self.assertEqual(sum(credits),10501)
        with self.db._conn() as conn:
            totals = conn.execute('SELECT SUM(net_cents),SUM(gst_cents) FROM exchange_returns WHERE source_line_id=?',(lid,)).fetchone()
            self.assertEqual(tuple(totals),(10001,500))
        with self.assertRaises(ValueError):self.db.quote_exchange(bid,{lid:1})
        self.assertEqual(self.db.exchange_source(bid)['lines'][0]['available_qty'],0)

    def test_legacy_bill_level_discount_is_allocated_to_paid_total(self):
        bid,lid = self.sale(qty=2)
        with self.db._conn() as conn:
            conn.execute('UPDATE bills SET total=1050,gst_amount=50 WHERE id=?',(bid,))
            conn.execute('UPDATE payments SET amount=1050 WHERE bill_id=?',(bid,))
        self.assertEqual(self.db.quote_exchange(bid,{lid:1})['credit_cents'],52500)
        self.assertEqual(self.db.quote_exchange(bid,{lid:2})['credit_cents'],105000)

    def test_invalid_selection_cannot_write(self):
        bid,lid = self.sale()
        other,otherline = self.sale()
        for selections in ({},{otherline:1},{lid:0},{lid:-1},{lid:2},{lid:1.5},{lid:True}):
            with self.subTest(selections=selections),self.assertRaises(ValueError):
                self.db.quote_exchange(bid,selections)
        with self.assertRaises(ValueError):self.db.exchange_source(-1)

    def test_idempotent_retry_and_changed_payload_rejected(self):
        bid,lid = self.sale()
        items = [self.line()]
        first = self.db.save_exchange(bid,{lid:1},items,840,210,'Cash','retry')
        before = self.snapshot()
        self.assertEqual(self.db.save_exchange(bid,{lid:1},items,840,210,'Cash','retry'),first)
        self.assertEqual(self.snapshot(),before)
        with self.assertRaisesRegex(ValueError,'different bill'):
            self.db.save_exchange(bid,{lid:1},items,840,210,'UPI','retry')
        self.assertEqual(self.snapshot(),before)

    def test_concurrent_return_is_only_committed_once(self):
        bid,lid = self.sale()
        def save(key):
            try:return self.db.save_exchange(bid,{lid:1},[self.line()],840,210,'Cash',key)
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save,['first','second']))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.db.get_item_by_id(self.old)['stock_qty'],10)
        self.assertEqual(self.db.get_item_by_id(self.new)['stock_qty'],9)
        self.assertEqual(len(self.db.search_bills()),2)

    def test_exchange_replacement_again_uses_full_purchase_value(self):
        bid,lid = self.sale()
        eid,_ = self.exchange(bid,lid)
        positive = next(r for r in self.db.get_bill(eid)[1] if r['quantity']>0)
        self.assertEqual(self.db.quote_exchange(eid,{positive['id']:1})['credit_cents'],105000)
        third,_ = self.exchange(eid,positive['id'],items=[self.line(qty=2)],key='again')
        self.assertEqual(self.db.get_bill(third)[0]['total'],1050)
        self.assertEqual(self.db.report_statistics()['totals']['revenue'],2100)
        self.assertEqual(self.db.report_statistics()['totals']['pieces'],2)

    def test_mixed_tax_exchange_can_have_negative_tax_adjustment(self):
        bid,lid = self.sale(gst=18)
        eid,_ = self.exchange(bid,lid,items=[self.line(gst=0)])
        bill,lines = self.db.get_bill(eid)
        self.assertEqual((bill['total'],bill['taxable_amount'],bill['gst_amount']),(56,200,-144))
        self.assertEqual(sum(r['subtotal']+r['gst_amount'] for r in lines),56)
        self.assertEqual(self.db.report_statistics()['totals']['revenue'],1000)
        self.assertIn('-₹72.00',build_receipt_html(bill,lines))
        self.assertIn('CGST adjustment',build_receipt_html(bill,lines))

    def test_current_offers_checked_before_any_write(self):
        bid,lid = self.sale()
        items = [self.line(qty=3)]
        before = self.snapshot()
        self.db.save_offer('New bundle',3,2500,[(self.categories[1]['id'],0)])
        with self.assertRaisesRegex(PermissionError,'Offers changed'):
            self.exchange(bid,lid,items=items)
        self.assertEqual(self.snapshot(),before)

    def test_invalid_replacement_tax_or_missing_item_rolls_back(self):
        bid,lid = self.sale()
        before = self.snapshot()
        for row in (dict(self.line(),gst_amount=0),dict(self.line(),item_id=99999),dict(self.line(),quantity=-1)):
            with self.assertRaises((ValueError,PermissionError)):
                self.exchange(bid,lid,items=[row])
            self.assertEqual(self.snapshot(),before)

    def test_lookup_phone_case_insensitive_bill_walkin_and_paging(self):
        first,lid = self.sale()
        last,_ = self.sale()
        walkin,_ = self.sale(customer=False)
        self.assertEqual(self.db.find_exchange_bills('')[0],0)
        self.assertEqual(self.db.find_exchange_bills('9876543210')[0],2)
        count,rows = self.db.find_exchange_bills('9876543210',limit=1,offset=1)
        self.assertEqual((count,rows[0]['id']),(2,first))
        no = self.db.get_bill(walkin)[0]['bill_no']
        count,rows = self.db.find_exchange_bills(no.lower(),False)
        self.assertEqual((count,rows[0]['id']),(1,walkin))
        self.assertEqual(self.db.find_exchange_bills('987')[0],0)
        self.assertEqual(self.db.find_exchange_bills(no[:-1],False)[0],0)

    def test_lookup_plans_are_indexed(self):
        with self.db._conn() as conn:
            queries = ["SELECT b.id FROM bills b LEFT JOIN customers c ON c.id=b.customer_id WHERE c.phone=?",
                       "SELECT b.id FROM bills b WHERE b.bill_no=? COLLATE NOCASE"]
            for query in queries:
                plan = [r[3] for r in conn.execute('EXPLAIN QUERY PLAN '+query,('example',))]
                self.assertFalse(any('SCAN' in r for r in plan),plan)
                self.assertTrue(any('SEARCH' in r for r in plan),plan)

    def test_linked_bill_deletion_blocked_and_schema_repeatable(self):
        bid,lid = self.sale()
        eid,_ = self.exchange(bid,lid)
        before = self.snapshot()
        for target in (bid,eid):
            with self.assertRaisesRegex(ValueError,'cannot be deleted'):self.db.delete_bill(target)
        for _ in range(2):self.db = Database(self.db.path)
        self.assertEqual(self.snapshot(),before)
        unlinked,_ = self.sale()
        self.db.delete_bill(unlinked)
        self.assertEqual(self.snapshot()['bills'],before['bills'])

    def test_employee_and_stale_admin_callbacks_cannot_exchange(self):
        bid,lid = self.sale()
        methods = [(self.access.find_exchange_bills,('9876543210',)),
                   (self.access.exchange_source,(bid,)),(self.access.quote_exchange,(bid,{lid:1})),
                   (self.access.save_exchange,(bid,{lid:1},[self.line()],840,210,'Cash','denied'))]
        self.session.logout()
        for callback,args in methods:
            with self.assertRaises(PermissionError):callback(*args)
        with patch.object(main,'Database',return_value=self.db):window=main.MainWindow()
        self.addCleanup(window.close)
        self.assertIsNone(window.exchanges_tab)
        window.session.login('1852j');window._build_dashboard()
        self.assertIsNotNone(window.exchanges_tab)

    def test_gui_lookup_quantity_and_original_receipt(self):
        bid,lid = self.sale(qty=3,net=2000)
        tab = ExchangesTab(self.access)
        self.addCleanup(tab.close)
        tab.search_input.setText('9876543210');tab.refresh()
        self.assertEqual(tab.bill_table.rowCount(),1)
        self.assertEqual(tab.return_table.rowCount(),1)
        tab._quantities[lid].setValue(2)
        self.assertTrue(tab.start_button.isEnabled())
        self.assertFalse(tab.start_button.isHidden())
        self.assertEqual(tab.quote['credit_cents'],140000)
        with patch('exchanges_tab.ReceiptDialog') as receipt:
            tab._view_receipt()
            self.assertEqual(receipt.call_args.args[0]['id'],bid)

    def test_gui_cart_automatic_offers_collects_difference_once(self):
        bid,lid = self.sale()
        self.db.save_offer('Replacement bundle',3,2500,[(self.categories[1]['id'],0)])
        done = []
        with patch.object(self.db,'search_customers',side_effect=AssertionError('Unbounded customer search')):
            cart = ExchangeCart(self.access,bid,{lid:1},self.db.quote_exchange(bid,{lid:1}),lambda:done.append(True))
        self.addCleanup(cart.close)
        cart._add_to_cart(self.new,'New purchase',self.categories[1]['name'],3,1000,5)
        self.assertEqual(cart._grand_total(),2625)
        self.assertEqual(cart.payment_amount_input.value(),1785)
        self.assertFalse(cart.payment_amount_input.isEnabled())
        self.assertTrue(cart.payment_amount_input.isHidden())
        self.assertTrue(cart.payment_form.labelForField(cart.payment_amount_input).isHidden())
        self.assertTrue(cart.bill_date_input.isHidden())
        self.assertTrue(cart.phone_input.isReadOnly())
        with patch('exchanges_tab.ReceiptDialog'):
            cart._complete_bill();cart._complete_bill()
        self.assertEqual(done,[True])
        self.assertEqual(len(self.db.search_bills()),2)
        bill = self.db.search_bills()[0]
        self.assertEqual(bill['total'],1785)
        self.assertEqual(self.db.get_bill(bill['id'])[0]['customer_id'],self.cid)


if __name__=='__main__':unittest.main()
