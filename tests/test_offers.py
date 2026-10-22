"""Configurable offers: money, eligibility, roles, saving, GUI and indexed lookup."""
import copy
from datetime import date
import os
import random
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/os.environ.get('BILLING_APP_DIR','app')))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QMessageBox
from database import Database
from access import AccessSession,RoleDatabase
from billing_tab import BillingTab
from offers_tab import OffersTab
from offers import OFFER_LOOKUP
from money import sum_money
from receipt import build_receipt_html,build_receipt_text
import main


class OfferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = Database(str(Path(self.folder.name)/'offers.db'))
        self.categories = []
        self.brands = []
        self.items = []
        for category in ('Test Jackets','Test Sweatshirts','Test Trousers'):
            self.db.add_category(category)
            cid = next(r['id'] for r in self.db.get_categories() if r['name']==category)
            self.db.add_subtype(cid,'Free Soul')
            brand = next(r['id'] for r in self.db.get_subtypes(cid) if r['name']=='Free Soul')
            self.categories.append(cid); self.brands.append(brand)
            self.items.append(self.db.add_item(category,cid,brand,'','','',800,100))
        self.session = AccessSession()
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

    def offer(self, quantity=3, price=2000, scopes=None, name='Free Soul bundle'):
        return self.db.save_offer(name,quantity,price,scopes or list(zip(self.categories[:2],self.brands[:2])))

    def row(self,index,qty=1,rate=800):
        return dict(item_id=self.items[index],qty=qty,rate=rate)

    def savings(self,cart):
        return sum_money(r['offer_discount'] for r in self.db.quote_offers(cart))

    def tab(self,admin=False):
        if admin:self.session.login('1852j')
        tab = BillingTab(self.access)
        self.addCleanup(tab.close)
        return tab

    def add(self,tab,index,qty=1,rate=800):
        item = self.db.get_item_by_id(self.items[index])
        tab._add_to_cart(item['id'],item['name'],item['category_name'],qty,rate,item['gst_rate'])

    def payload(self,tab):
        with patch.object(tab.db,'save_bill',side_effect=RuntimeError('capture')) as save:
            tab._complete_bill()
        args,kwargs = save.call_args
        return args,kwargs

    def test_empty_database_has_no_sample_offer_and_no_discount(self):
        self.assertEqual(self.db.offer_page()[0],0)
        self.assertEqual(self.savings([self.row(0,3)]),0)

    def test_single_threshold_repeated_bundles_and_regular_price_leftovers(self):
        self.offer()
        for quantity,saving in [(1,0),(2,0),(3,400),(4,400),(5,400),(6,800),(7,800)]:
            with self.subTest(quantity=quantity):
                self.assertEqual(self.savings([self.row(0,quantity)]),saving)
        tab = self.tab()
        self.add(tab,0,3)
        self.assertEqual(tab._grand_total(),2100)
        self.assertEqual(tab.payment_amount_input.value(),2100)
        tab.cart_table.item(0,2).setText('4')
        self.assertEqual(tab._grand_total(),2940)
        tab.cart_table.item(0,2).setText('2')
        self.assertEqual(tab._grand_total(),1680)

    def test_mixed_categories_share_bundle_and_other_brands_are_excluded(self):
        self.offer()
        self.assertEqual(self.savings([self.row(0,2),self.row(1)]),400)
        self.assertEqual(self.savings([self.row(0,2),self.row(2)]),0)
        other = self.db.add_item('Other brand',self.categories[0],None,'','','',800,10)
        self.assertEqual(self.savings([self.row(0,2),dict(item_id=other,qty=1,rate=800)]),0)

    def test_brand_specific_precedence_and_scope_conflicts_are_atomic(self):
        broad = self.offer(price=1800,scopes=[(self.categories[0],0)],name='All brands')
        exact = self.offer(scopes=[(self.categories[0],self.brands[0])])
        self.assertEqual(self.savings([self.row(0,3)]),400)
        with self.assertRaises(ValueError):self.offer(scopes=[(self.categories[0],self.brands[0])])
        self.assertEqual(self.db.offer_page()[0],2)
        self.db.set_offer_active(exact,False)
        self.assertEqual(self.savings([self.row(0,3)]),600)
        self.db.save_offer('Replacement',3,1900,[(self.categories[0],self.brands[0])])
        with self.assertRaises(ValueError):self.db.set_offer_active(exact,True)
        self.assertEqual(self.db.get_offer(exact)['active'],0)
        with self.assertRaises(ValueError):
            self.db.save_offer('Should rollback',5,1000,[(self.categories[0],self.brands[0])],broad)
        self.assertEqual(self.db.get_offer(broad)['name'],'All brands')

    def test_cheaper_extra_pieces_do_not_cancel_profitable_bundles(self):
        self.offer(scopes=[(self.categories[0],self.brands[0])])
        low = self.db.add_item('Cheap',self.categories[0],self.brands[0],'','','',100,10)
        cart = [self.row(0,3),dict(item_id=low,qty=3,rate=100)]
        self.assertEqual(self.savings(cart),400)
        quote = self.db.quote_offers(cart)
        self.assertEqual(quote[1]['offer_discount'],0)
        self.assertEqual(self.savings([dict(item_id=low,qty=3,rate=100)]),0)

    def test_large_quantity_does_not_expand_into_individual_pieces(self):
        self.offer()
        self.assertEqual(self.savings([self.row(0,999999)]),133333200)

    def test_invalid_offer_fields_and_brand_category_pair_are_rejected(self):
        for quantity,price,scopes in [(3,0,[(self.categories[0],0)]),(1,100,[(self.categories[0],0)]),(3,-1,[(self.categories[0],0)]),
                                    (3,float('nan'),[(self.categories[0],0)]),(3,100,[]),
                                    (3,100,[(self.categories[0],self.brands[1])])]:
            with self.assertRaises(ValueError):self.db.save_offer('Invalid',quantity,price,scopes)
        self.assertEqual(self.db.offer_page()[0],0)

    def test_employee_save_snapshots_offer_and_reports_agree(self):
        offer = self.offer()
        tab = self.tab();self.add(tab,0,2);self.add(tab,1)
        with patch('billing_tab.ReceiptDialog'):tab._complete_bill()
        bill = self.db.search_bills()[0]
        saved,items = self.db.get_bill(bill['id'])
        self.assertEqual((saved['subtotal'],saved['discount_amount'],saved['taxable_amount'],saved['gst_amount'],saved['total']),
                         (2400,400,2000,100,2100))
        self.assertEqual(sum_money(i['subtotal'] for i in items),2000)
        self.assertEqual(sum_money(i['offer_discount'] for i in items),400)
        self.assertEqual(self.db.get_bill_balance(bill['id']),0)
        self.db.save_offer('Edited future offer',3,1800,list(zip(self.categories[:2],self.brands[:2])),offer)
        saved,items = self.db.get_bill(bill['id'])
        self.assertIn('Free Soul bundle',build_receipt_html(saved,items))
        self.assertIn('Free Soul bundle',build_receipt_text(saved,items))
        self.assertNotIn('Edited future offer',build_receipt_html(saved,items))
        report = self.db.report_statistics()
        self.assertEqual(report['totals']['revenue'],2100)
        self.assertEqual(sum_money(r['revenue'] for r in report['products']),2100)
        self.db.delete_bill(bill['id'])
        self.assertEqual(self.db.get_item_by_id(self.items[0])['stock_qty'],100)

    def test_bundle_gst_does_not_lose_a_cent_across_three_products(self):
        self.offer(scopes=list(zip(self.categories,self.brands)))
        tab = self.tab()
        for index in range(3):self.add(tab,index)
        self.assertEqual(tab._gst_amount(),100)
        self.assertEqual(tab._grand_total(),2100)
        with patch('billing_tab.ReceiptDialog'):tab._complete_bill()
        self.assertEqual(len(self.db.search_bills()),1)

    def test_mixed_gst_rates_use_discounted_line_amounts(self):
        self.offer()
        self.db.update_item_gst_rate(self.items[1],12)
        tab = self.tab();self.add(tab,0,2);self.add(tab,1)
        self.assertEqual(tab._taxable_amount(),2000)
        self.assertEqual(tab._gst_amount(),146.67)
        self.assertEqual(tab._grand_total(),2146.67)
        with patch('billing_tab.ReceiptDialog'):tab._complete_bill()
        self.assertEqual(len(self.db.search_bills()),1)

    def test_employee_offer_management_and_forged_pricing_are_rejected(self):
        self.offer()
        for method,args in [('save_offer',('Bad',3,1,[(self.categories[0],0)])),('offer_page',()),
                            ('get_offer',(1,)),('set_offer_active',(1,False))]:
            with self.assertRaises(PermissionError):getattr(self.access,method)(*args)
        tab = self.tab();self.add(tab,0,3)
        args,kwargs = self.payload(tab)
        for field,value in [('offer_discount',500),('offer_id_snapshot',999),('subtotal',1)]:
            changed = list(copy.deepcopy(args));changed[1][0][field]=value
            with self.assertRaises(PermissionError):self.access.save_bill(*changed,employee_pricing=False,**kwargs)
        self.assertEqual(self.db.search_bills(),[])
        self.assertEqual(self.db.get_item_by_id(self.items[0])['stock_qty'],100)

    def test_changed_offer_is_rejected_inside_save_transaction(self):
        offer = self.offer()
        tab = self.tab();self.add(tab,0,3)
        args,kwargs = self.payload(tab)
        self.db.set_offer_active(offer,False)
        with self.assertRaises(PermissionError):self.access.save_bill(*args,**kwargs)
        self.assertEqual(self.db.search_bills(),[])
        self.assertEqual(self.db.get_item_by_id(self.items[0])['stock_qty'],100)
        with self.db._conn() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM payments').fetchone()[0],0)
        tab._refresh_offers()
        self.assertEqual(tab._grand_total(),2520)
        with patch('billing_tab.ReceiptDialog'):tab._complete_bill()
        self.assertEqual(len(self.db.search_bills()),1)

    def test_admin_manual_discount_and_custom_price_remain_available(self):
        self.offer()
        tab = self.tab(admin=True);self.add(tab,0,3)
        tab.cart_table.item(0,5).setText('100')
        self.assertEqual(tab._grand_total(),1995)
        with patch('billing_tab.ReceiptDialog'):tab._complete_bill()
        self.assertEqual(self.db.search_bills()[0]['discount_amount'],500)
        self.add(tab,0,3,700)
        self.assertEqual(tab.cart[0]['offer_discount'],0)
        self.assertEqual(tab._grand_total(),2205)

    def test_gui_create_edit_disable_and_employee_dashboard(self):
        self.session.login('1852j')
        changed = []
        tab = OffersTab(self.access,on_changed=lambda:changed.append(True))
        self.addCleanup(tab.close)
        tab.name.setText('GUI bundle');tab.price.setValue(2000)
        for cid,brand in zip(self.categories[:2],self.brands[:2]):
            tab.category.setCurrentIndex(tab.category.findData(cid))
            tab.brand.setCurrentIndex(tab.brand.findData(brand));tab._add_scope()
        self.assertEqual(tab.scope_table.rowCount(),2)
        tab._save()
        self.assertEqual(tab.table.rowCount(),1)
        self.assertEqual(self.savings([self.row(0,2),self.row(1)]),400)
        tab.table.selectRow(0);tab.price.setValue(1900);tab._save()
        self.assertEqual(self.savings([self.row(0,3)]),500)
        tab.active.setChecked(False);tab._save()
        self.assertEqual(self.savings([self.row(0,3)]),0)
        self.assertEqual(len(changed),3)
        tab.status.setCurrentText('Disabled');self.assertEqual(tab.table.rowCount(),1)
        self.session.logout()
        with self.assertRaises(PermissionError):tab.db.save_offer('Bypass',3,1,[(self.categories[0],0)])
        with patch.object(main,'Database',return_value=self.db):window=main.MainWindow()
        self.addCleanup(window.close)
        self.assertEqual(window.tabs.count(),1)
        self.assertIsNone(window.offers_tab)
        window.session.login('1852j');window._build_dashboard()
        self.assertIsNotNone(window.offers_tab)

    def test_offer_list_prefix_search_escapes_wildcards_and_pages(self):
        with self.db._conn() as conn:
            conn.executemany('INSERT INTO offers(name,bundle_quantity,bundle_cents,active) VALUES (?,3,200000,0)',
                             [(f'History {i:04}',) for i in range(405)]+[('Special_%',)])
        total,rows = self.db.offer_page(False,200,200,'History')
        self.assertEqual((total,len(rows)),(405,200))
        self.assertEqual(rows[0]['name'],'History 0200')
        self.assertEqual(self.db.offer_page(False,200,0,'Special_%')[0],1)
        self.assertEqual(self.db.offer_page(False,200,0,'SpecialX')[0],0)

    def test_varied_prices_match_simple_expanded_reference(self):
        rng = random.Random(419)
        offer = self.offer(scopes=[(self.categories[0],0)])
        ids = [self.db.add_item(f'Variant {i}',self.categories[0],None,'','','',price,100)
               for i,price in enumerate([100,350,800,1000])]
        for _ in range(100):
            quantity = rng.randint(2,7)
            bundle = rng.randint(100,2500)
            self.db.save_offer('Variable offer',quantity,bundle,[(self.categories[0],0)],offer)
            cart = [dict(item_id=i,qty=rng.randint(1,5),rate=rate) for i,rate in zip(ids,[100,350,800,1000])]
            pieces = sorted([r['rate'] for r in cart for _ in range(r['qty'])],reverse=True)
            saving = sum(max(0,sum(pieces[i:i+quantity])-bundle)
                         for i in range(0,len(pieces)-quantity+1,quantity))
            self.assertEqual(self.savings(cart),saving)
            self.assertEqual(self.savings(list(reversed(cart))),saving)

    def test_removed_brand_offer_can_be_disabled_but_not_reenabled(self):
        offer = self.offer(scopes=[(self.categories[0],self.brands[0])])
        self.db.delete_subtype(self.brands[0])
        self.db.save_offer('Retired',3,2000,[(self.categories[0],self.brands[0])],offer,False)
        self.assertEqual(self.db.get_offer(offer)['active'],0)
        with self.assertRaises(ValueError):self.db.set_offer_active(offer,True)
        self.assertEqual(self.savings([self.row(0,3)]),0)

    def test_lookup_plan_uses_unique_active_scope_index(self):
        self.offer()
        with self.db._conn() as conn:
            plan = [r[3] for r in conn.execute('EXPLAIN QUERY PLAN '+OFFER_LOOKUP,(self.categories[0],self.brands[0]))]
        self.assertTrue(any('idx_offer_scope_active' in r and 'SEARCH' in r for r in plan),plan)
        self.assertFalse(any('SCAN' in r for r in plan),plan)

    def test_schema_upgrade_is_repeatable_and_preserves_historical_rows(self):
        bid,_ = self.db.save_bill(None,[dict(name='Old',quantity=1,rate=10,subtotal=10)],10,0,0,10,'Cash')
        with self.db._conn() as conn:
            for column in ('offer_id_snapshot','offer_name_snapshot','offer_discount'):
                conn.execute(f'ALTER TABLE bill_items DROP COLUMN {column}')
        for _ in range(2):self.db=Database(self.db.path)
        bill,items = self.db.get_bill(bid)
        self.assertEqual(bill['total'],10)
        self.assertEqual(items[0]['subtotal'],10)
        self.assertEqual(items[0]['offer_discount'],0)
        self.assertEqual(self.db.offer_page()[0],0)


if __name__=='__main__':unittest.main()
