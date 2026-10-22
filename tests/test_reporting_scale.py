"""Report accuracy across paging, historical money, snapshots and deferred screens."""
import csv
from decimal import Decimal
from datetime import date, timedelta
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / os.environ.get('BILLING_APP_DIR', 'app')))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QMessageBox, QLabel
from database import Database
from sales_tab import SalesTab
from balances_tab import BalancesTab
from expenses_tab import ExpensesTab
from customers_tab import CustomersTab
from stats_tab import StatsTab
from report_export import export_csv
import main


class ReportingScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db = Database(str(self.folder/'scale.db'))
        self.cid = self.db.add_customer('Synthetic Customer', '123')
        with self.db._conn() as conn:
            conn.executemany('''INSERT INTO bills(id,bill_no,customer_id,bill_date,subtotal,total)
                VALUES (?, ?, ?, '2024-02-29 23:59:59', 1.01, 1.01)''',
                [(i, f'TEST-{i:04}', self.cid) for i in range(1,406)])
            conn.executemany('''INSERT INTO bill_items(bill_id,item_name_snapshot,category_snapshot,
                quantity,rate,subtotal,gst_amount) VALUES (?, 'Test product', 'Test', 1, 1.01, 1.01, 0)''',
                [(i,) for i in range(1,406)])
            conn.executemany('''INSERT INTO payments(bill_id,customer_id,payment_date,amount,payment_mode)
                VALUES (?, ?, '2024-03-01', .50, 'Cash')''',[(i,self.cid) for i in range(1,406)])
            conn.executemany("INSERT INTO expenses(category,amount,expense_date) VALUES ('Other', .01, '2024-02-29')",[()]*405)
        self.errors = []
        original = sys.excepthook
        sys.excepthook = lambda *args: self.errors.append(args[1])
        self.addCleanup(setattr,sys,'excepthook',original)
        for method in ('warning','information','critical'):
            mock = patch.object(QMessageBox,method)
            mock.start()
            self.addCleanup(mock.stop)

    def tearDown(self):
        self.app.processEvents()
        self.assertEqual(self.errors,[])

    def tab(self, cls):
        tab = cls(self.db)
        if hasattr(tab,'period'):
            tab.period.preset.setCurrentText('All time')
        self.addCleanup(tab.close)
        return tab

    def export(self, tab, method='_export_csv'):
        path = self.folder/'export.csv'
        with patch('report_export.QFileDialog.getSaveFileName',return_value=(str(path),'CSV')):
            getattr(tab,method)()
        with path.open(encoding='utf-8-sig',newline='') as stream:
            return list(csv.reader(stream))

    def test_paging_full_totals_stable_ties_and_export_every_row(self):
        tab = self.tab(SalesTab)
        seen = []
        for size in (200,200,5):
            self.assertEqual(tab.bills_table.rowCount(),size)
            self.assertIn('405 bill(s)',tab.summary_label.text())
            seen.extend(b['id'] for b in tab._bills_cache)
            if size == 200: tab.pager.next.click()
        self.assertEqual(seen,list(range(405,0,-1)))
        self.assertEqual(len(self.export(tab)),406)
        self.assertEqual(self.db.sales_page()['summary'],dict(count=405,revenue=409.05,pieces=405))
        tab.search_input.setText('TEST-0405')
        tab.refresh()
        self.assertEqual(tab.pager.page,0)
        self.assertEqual(len(self.export(tab)),2)

    def test_deleted_last_page_clamps_to_remaining_rows(self):
        tab = self.tab(SalesTab)
        tab.pager.next.click(); tab.pager.next.click()
        with self.db._conn() as conn:
            conn.execute('DELETE FROM bills WHERE id<=5')
        tab.refresh()
        self.assertEqual(tab.pager.page,1)
        self.assertEqual(tab.bills_table.rowCount(),200)
        self.assertEqual(tab._bills_cache[-1]['id'],6)

    def test_balance_pages_payments_receipts_and_complete_exports(self):
        tab = self.tab(BalancesTab)
        self.assertEqual(tab.bill_table.rowCount(),200)
        self.assertEqual(tab.payment_table.rowCount(),200)
        self.assertIn('206.55',tab.total_outstanding_label.text())
        tab.bill_pager.next.click(); tab.payment_pager.next.click()
        self.assertEqual(tab._bill_ids[0],205)
        with patch('balances_tab.ReceiptDialog') as receipt:
            tab._open_bill(0,0)
            self.assertEqual(receipt.call_args.args[0]['id'],205)
            tab._open_payment(0,0)
            self.assertEqual(receipt.call_args.args[0]['id'],205)
        self.assertEqual(len(self.export(tab)),406)
        self.assertEqual(len(self.export(tab,'_export_payments')),406)
        # A payment-only month must still include the customer and all collections.
        groups = self.db.balance_summary('2024-03-01','2024-03-31')
        self.assertEqual(groups[self.cid]['billed'],0)
        ledger = self.db.customer_ledger_page(self.cid,'2024-03-01','2024-03-31')
        self.assertEqual(ledger['bill_count'],0)
        self.assertEqual(ledger['payment_count'],405)

    def test_expense_page_summary_and_export_are_not_truncated(self):
        tab = self.tab(ExpensesTab)
        self.assertEqual(tab.expense_table.rowCount(),200)
        self.assertIn('405 expense(s)',tab.summary_label.text())
        self.assertIn('4.05',tab.summary_label.text())
        tab.pager.next.click(); tab.pager.next.click()
        self.assertEqual(tab.expense_table.rowCount(),5)
        self.assertEqual(len(self.export(tab)),406)

    def test_customer_history_and_selection_remain_correct_after_filter(self):
        tab = self.tab(CustomersTab)
        tab.customer_table.selectRow(0)
        self.assertEqual(tab.history_table.rowCount(),200)
        tab.history_pager.next.click()
        with patch('customers_tab.ReceiptDialog') as receipt:
            tab._open_history_receipt(0,0)
            self.assertEqual(receipt.call_args.args[0]['id'],205)
        tab.search_input.setText('No such customer')
        self.assertIsNone(tab.selected_customer_id)
        self.assertEqual(tab.history_table.rowCount(),0)
        self.assertEqual(tab.name_input.text(),'')

    def test_customer_page_totals_and_paging_do_not_mix_profiles(self):
        with self.db._conn() as conn:
            conn.executemany('INSERT INTO customers(name) VALUES (?)',[(f'A Customer {i:04}',) for i in range(205)])
        tab = self.tab(CustomersTab)
        self.assertEqual(tab.customer_table.rowCount(),200)
        tab.customer_pager.next.click()
        self.assertEqual(tab.customer_table.rowCount(),6)
        tab.customer_table.selectRow(5)
        self.assertEqual(tab.selected_customer_id,self.cid)
        self.assertIn('409.05',tab.summary_label.text())

    def test_fractional_imported_money_rounds_sum_once_and_charts_reconcile(self):
        with self.db._conn() as conn:
            conn.execute('DELETE FROM payments')
            conn.execute('DELETE FROM expenses')
            conn.executemany("INSERT INTO payments(bill_id,customer_id,payment_date,amount) VALUES (1,?,'2024-03-01',?)",[(self.cid,.005)]*3)
            conn.executemany("INSERT INTO expenses(expense_date,category,amount) VALUES ('2024-03-01','Other',?)",[(.005,)]*3)
            conn.execute('UPDATE bill_items SET subtotal=.333333, gst_amount=0 WHERE bill_id=1')
        report = self.db.report_statistics()
        self.assertEqual(report['totals']['payments_collected'],.02)
        self.assertEqual(report['totals']['expenses'],.02)
        self.assertEqual(report['totals']['outstanding'],409.03)
        self.assertEqual(sum(Decimal(str(p['revenue'])) for p in report['products']),Decimal('409.05'))
        self.assertEqual(self.db.balance_summary()[self.cid]['balance'],409.03)

    def test_stream_export_has_one_snapshot_and_releases_it(self):
        rows = self.db.export_bill_rows()
        first = next(rows)
        with self.db._conn() as conn:
            conn.execute('UPDATE bills SET total=2 WHERE id=1')
        rest = list(rows)
        self.assertEqual(first['id'],405)
        self.assertEqual(rest[-1]['total'],1.01)
        self.assertEqual(list(self.db.export_bill_rows())[-1]['total'],2)

    def test_failed_export_preserves_previous_file_and_cleans_temporary(self):
        path = self.folder/'existing.csv'
        path.write_text('previous complete export')
        def rows():
            yield ['first row']
            raise sqlite3.OperationalError('synthetic read failure')
        with patch('report_export.QFileDialog.getSaveFileName',return_value=(str(path),'CSV')), \
             patch('report_export.QMessageBox.warning') as warning, \
             patch('report_export.QMessageBox.information') as success:
            export_csv(None,'Export','test.csv',['Name'],rows())
        warning.assert_called_once(); success.assert_not_called()
        self.assertEqual(path.read_text(),'previous complete export')
        self.assertFalse(list(self.folder.glob('.billing-export-*')))

    def test_chart_controls_do_not_requery_and_removed_note_is_absent(self):
        tab = self.tab(StatsTab)
        with patch.object(self.db,'report_statistics',side_effect=AssertionError('unexpected query')):
            tab.chart_group.setCurrentText('Categories')
            tab.chart_group.setCurrentText('Brands / Styles')
            tab.top_items_sort_combo.setCurrentText('Revenue')
        self.assertFalse(any('Revenue includes GST' in label.text() for label in tab.findChildren(QLabel)))

    def test_hidden_reports_defer_until_open_and_refresh_on_return(self):
        with patch.object(main,'Database',return_value=self.db):
            window = main.MainWindow()
        self.addCleanup(window.close)
        window.session.login('1852j')
        with patch.object(self.db,'report_statistics',wraps=self.db.report_statistics) as stats:
            window._build_dashboard()
            stats.assert_not_called()
            window._on_bill_saved()
            stats.assert_not_called()
            window.stats_tab.period.blockSignals(True)
            window.stats_tab.period.preset.setCurrentText('All time')
            window.stats_tab.period.blockSignals(False)
            window.tabs.setCurrentWidget(window.stats_tab)
            self.assertEqual(stats.call_count,1)
            window.tabs.setCurrentWidget(window.billing_tab)
            with self.db._conn() as conn:
                conn.execute('UPDATE bills SET total=2 WHERE id=1')
            window.tabs.setCurrentWidget(window.stats_tab)
            self.assertEqual(window.stats_tab._report['totals']['revenue'],410.04)
            self.assertEqual(stats.call_count,2)

    def test_long_trend_aggregates_bars_but_retains_daily_metrics_and_export(self):
        with self.db._conn() as conn:
            conn.executemany('UPDATE bills SET bill_date=? WHERE id=?',
                [((date(2024,1,1)+timedelta(days=i)).isoformat(),i) for i in range(1,406)])
        tab = self.tab(StatsTab)
        self.assertEqual(tab._report['totals']['active_days'],405)
        self.assertEqual(tab._report['totals']['mean_daily'],1.01)
        ax = tab.trend_figure.axes[0]
        self.assertEqual(len(ax.containers[0]),len(tab._report['monthly']))
        self.assertIn('Monthly',ax.get_xlabel())
        self.assertTrue(any('Monthly mean' in t.get_text() for t in ax.get_legend().get_texts()))
        exported = self.export(tab)
        self.assertEqual(sum(row[0]=='daily' for row in exported),405)

    def test_legacy_zero_weight_lines_still_allocate_every_cent(self):
        with self.db._conn() as conn:
            conn.execute('UPDATE bill_items SET quantity=0,subtotal=0,gst_amount=0 WHERE bill_id=1')
        report = self.db.report_statistics()
        self.assertEqual(report['totals']['pieces'],404)
        self.assertEqual(sum(Decimal(str(p['revenue'])) for p in report['products']),Decimal('409.05'))

    def test_date_queries_use_indexes_and_writes_keep_full_durability(self):
        with self.db._conn() as conn:
            self.assertEqual(conn.execute('PRAGMA synchronous').fetchone()[0],2)
            self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0],1)
            for table,column in [('bills','bill_date'),('payments','payment_date'),('expenses','expense_date')]:
                plan = list(conn.execute(f"EXPLAIN QUERY PLAN SELECT id FROM {table} WHERE date({column})>=date(?) AND date({column})<=date(?)",('2024-02-01','2024-02-29')))
                self.assertTrue(any('SEARCH' in r[3] and f'idx_{table}_day' in r[3] for r in plan),plan)


if __name__ == '__main__':
    unittest.main()
