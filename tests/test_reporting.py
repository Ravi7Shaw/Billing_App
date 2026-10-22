"""Known-value reporting fixtures, filter boundaries, CSV and receipt navigation."""
import csv
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / os.environ.get('BILLING_APP_DIR', 'app')))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea
from PySide6.QtTest import QTest
from database import Database
from period_filter import PeriodFilter
from sales_tab import SalesTab
from expenses_tab import ExpensesTab
from balances_tab import BalancesTab
from customers_tab import CustomersTab
from stats_tab import StatsTab
from money import sum_money


class ReportingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.db = Database(str(self.path/'report.db'))
        self.cid = self.db.add_customer('Sample Customer', '123')
        self.category = self.db.get_categories()[0]
        self.iid = self.db.add_item('Shirt', self.category['id'], None, '', '', '', 100, 100)
        self.old = self.sale('1999-12-31', 10, 10, customer=None)
        self.jan = self.sale('2024-01-31', 105, 25)
        self.feb = self.sale('2024-02-01', 210, 0, qty=2)
        self.leap = self.sale('2024-02-29', 105, 105)
        self.mar = self.sale('2024-03-01', 50, 50)
        self.db.add_payment(self.jan, 30, payment_date='2024-02-02')
        self.db.add_payment(self.feb, 50, payment_date='2024-02-05')
        self.db.add_payment(self.feb, 60, payment_date='2024-03-01')
        self.db.add_expense('Rent', 20, expense_date='2024-02-10')
        self.db.add_expense('Other', 9, expense_date='2024-01-31')
        with self.db._conn() as conn:
            conn.execute("UPDATE bills SET bill_date='2024-01-31 23:59:59' WHERE id=?", (self.jan,))
            conn.execute("UPDATE bills SET bill_date='2024-02-29 23:59:59' WHERE id=?", (self.leap,))
        self.errors = []
        original = sys.excepthook
        sys.excepthook = lambda *args: self.errors.append(args[1])
        self.addCleanup(setattr, sys, 'excepthook', original)
        for name in ('warning', 'critical', 'information'):
            mock = patch.object(QMessageBox, name)
            mock.start()
            self.addCleanup(mock.stop)

    def tearDown(self):
        self.app.processEvents()
        self.assertEqual(self.errors, [])

    def sale(self, day, total, paid, qty=1, customer='default'):
        cid = self.cid if customer == 'default' else customer
        return self.db.save_bill(cid, [dict(item_id=self.iid, name='Shirt', category=self.category['name'],
            quantity=qty, rate=total/qty, subtotal=total, gst_rate=0, gst_amount=0)],
            total, 0, 0, total, 'Cash', day, initial_payment_amount=paid)[0]

    def tab(self, cls):
        tab = cls(self.db)
        self.addCleanup(tab.close)
        return tab

    def month(self, tab, value=QDate(2024, 2, 1)):
        tab.period.blockSignals(True)
        tab.period.month_from.setDate(value)
        tab.period.preset.setCurrentText('Selected month')
        tab.period.blockSignals(False)
        self.assertTrue(tab.refresh())

    def export(self, tab, method='_export_csv'):
        path = self.path/'export.csv'
        with patch('report_export.QFileDialog.getSaveFileName', return_value=(str(path), 'CSV')):
            getattr(tab, method)()
        with path.open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.reader(stream))

    def test_period_presets_inclusive_days_months_leap_and_all_time(self):
        period = PeriodFilter()
        self.addCleanup(period.close)
        today = QDate.currentDate()
        for label, start in [('Today', today), ('Last 7 days', today.addDays(-6)),
                             ('Last 30 days', today.addDays(-29)),
                             ('This month', QDate(today.year(), today.month(), 1))]:
            period.preset.setCurrentText(label)
            self.assertEqual(period.bounds(), (start.toString('yyyy-MM-dd'), today.toString('yyyy-MM-dd')))
        period.month_from.setDate(QDate(2024, 2, 10))
        period.preset.setCurrentText('Selected month')
        self.assertEqual(period.bounds(), ('2024-02-01', '2024-02-29'))
        period.preset.setCurrentText('Month range')
        period.month_from.setDate(QDate(2023, 12, 10))
        period.month_to.setDate(QDate(2024, 2, 10))
        self.assertEqual(period.bounds(), ('2023-12-01', '2024-02-29'))
        period.month_to.setDate(QDate(2023, 11, 1))
        with self.assertRaises(ValueError): period.bounds()
        period.preset.setCurrentText('Custom dates')
        period.date_from.setDate(QDate(2024, 2, 29))
        period.date_to.setDate(QDate(2024, 2, 29))
        self.assertEqual(period.bounds(), ('2024-02-29', '2024-02-29'))
        period.date_to.setDate(QDate(2024, 2, 28))
        with self.assertRaises(ValueError): period.bounds()
        period.preset.setCurrentText('All time')
        self.assertEqual(period.bounds(), (None, None))

    def test_statistics_known_values_payments_and_no_join_multiplication(self):
        report = self.db.report_statistics('2024-02-01', '2024-02-29')
        values = report['totals']
        expected = dict(revenue=315, expenses=20, net_profit=295, payments_collected=185,
                        outstanding=100, bill_count=2, pieces=3, avg_bill=157.5,
                        mean_daily=157.5, median_daily=157.5, std_daily=52.5, active_days=2)
        self.assertEqual(values, expected)
        self.assertEqual(report['monthly'], [dict(month='2024-02', revenue=315, bill_count=2)])
        self.assertEqual(sum_money(r['revenue'] for r in report['products']), 315)
        self.assertEqual(sum_money(r['revenue'] for r in report['categories']), 315)
        bills = self.db.report_bills('2024-02-01', '2024-02-29')
        self.assertEqual({b['id'] for b in bills}, {self.feb, self.leap})
        self.assertEqual(sum_money(b['paid'] for b in bills), 215)
        self.assertEqual(sum_money(b['balance'] for b in bills), 100)

    def test_all_time_has_pre_2000_data_and_monthly_filter_is_honored(self):
        self.assertEqual(self.db.report_statistics()['totals']['revenue'], 480)
        self.assertIn(self.old, [b['id'] for b in self.db.report_bills()])
        self.assertEqual(self.db.stat_monthly_sales('2024-02-01','2024-02-29'),
                         [dict(month='2024-02', revenue=315, bill_count=2)])
        self.assertEqual(len(self.db.stat_monthly_sales()), 4)

    def test_daily_stats_use_days_with_sales_and_empty_period_is_zero(self):
        report = self.db.report_statistics('2024-01-01', '2024-03-31')
        days = [105, 210, 105, 50]
        self.assertEqual(report['totals']['median_daily'], statistics.median(days))
        self.assertEqual(report['totals']['std_daily'], statistics.pstdev(days))
        empty = self.db.report_statistics('2020-01-01', '2020-01-02')
        self.assertTrue(all(v == 0 for v in empty['totals'].values()))
        self.assertEqual(empty['monthly'], [])

    def test_product_allocation_reconciles_legacy_bill_discount_and_rounding(self):
        items = [dict(name=name, category='Test', quantity=1, rate=1, subtotal=1) for name in ('A','B','C')]
        self.db.save_bill(None, items, 3, 33.3333, 1, 2, 'Cash', '2022-01-01')
        report = self.db.report_statistics('2022-01-01','2022-01-01')
        self.assertEqual(sum_money(r['revenue'] for r in report['products']), 2)
        self.assertEqual(sorted(r['revenue'] for r in report['products']), [.66,.67,.67])
        self.assertEqual(report['brands'], [dict(category='Test', brand='Unknown brand / style', quantity=3, revenue=2)])

    def test_legacy_bill_without_lines_still_reconciles_chart(self):
        with self.db._conn() as conn:
            conn.execute('DELETE FROM bill_items WHERE bill_id=?', (self.old,))
        report = self.db.report_statistics('1999-12-31', '1999-12-31')
        self.assertEqual(report['totals']['pieces'], 0)
        self.assertEqual(report['products'][0]['name'], 'Unspecified items')
        self.assertEqual(report['products'][0]['revenue'], 10)
        self.assertEqual(report['brands'], [dict(category='Uncategorised', brand='Unknown brand / style', quantity=0, revenue=10)])

    def brand_sales(self):
        self.db.add_category('Brand test shirts')
        self.db.add_category('Brand test trousers')
        categories = {r['name']: r['id'] for r in self.db.get_categories()}
        for category, brand, qty, total in (
            ('Brand test shirts', 'CK', 3, 30),
            ('Brand test shirts', 'Other', 1, 100),
            ('Brand test trousers', 'CK', 2, 40),
        ):
            category_id = categories[category]
            self.db.add_subtype(category_id, brand)
            brand_id = next(r['id'] for r in self.db.get_subtypes(category_id) if r['name'] == brand)
            iid = self.db.add_item('Same item name', category_id, brand_id, '', '', '', 100, 100)
            self.db.save_bill(None, [dict(item_id=iid, name='Same item name', category=category,
                quantity=qty, rate=total/qty, subtotal=total, gst_rate=0, gst_amount=0)],
                total, 0, 0, total, 'Cash', '2025-02-01')

    def test_brands_separate_same_named_items_and_categories_with_date_filters(self):
        self.brand_sales()
        report = self.db.report_statistics('2025-02-01', '2025-02-28')
        expected = {('Brand test shirts', 'CK'): (3, 30),
                    ('Brand test shirts', 'Other'): (1, 100),
                    ('Brand test trousers', 'CK'): (2, 40)}
        self.assertEqual(len(report['products']), 3)
        for kind in ('products', 'brands'):
            self.assertEqual({(r['category'], r['brand']): (r['quantity'], r['revenue'])
                              for r in report[kind]}, expected)
        for kind in ('products', 'brands', 'categories'):
            self.assertEqual(sum_money(r['revenue'] for r in report[kind]), 170)
            self.assertEqual(sum(r['quantity'] for r in report[kind]), 6)
        self.assertEqual(report['totals']['revenue'], 170)
        self.assertEqual(self.db.report_statistics('2025-03-01', '2025-03-31')['brands'], [])
        self.assertEqual(self.db.report_statistics('2024-02-01', '2024-02-29')['brands'][0]['brand'],
                         'No brand / style')

    def test_brand_tables_chart_cached_ranking_and_csv(self):
        self.brand_sales()
        tab = self.tab(StatsTab)
        self.month(tab, QDate(2025, 2, 1))
        self.assertEqual(tab.top_items_table.columnCount(), 5)
        self.assertEqual(tab.top_items_table.item(0, 2).text(), 'CK')
        self.assertEqual(tab.top_items_table.item(0, 3).text(), '3')
        self.assertEqual(tab.top_brands_table.rowCount(), 3)
        self.assertEqual(tab.top_brands_table.item(0, 1).text(), 'CK')
        with patch.object(self.db, 'report_statistics', side_effect=AssertionError('unexpected query')):
            tab.top_items_sort_combo.setCurrentText('Revenue')
            tab.chart_group.setCurrentText('Brands / Styles')
        self.assertEqual(tab.top_brands_table.item(0, 1).text(), 'Other')
        self.assertEqual(tab.top_items_table.item(0, 2).text(), 'Other')
        legend = tab.category_figure.axes[0].get_legend()
        self.assertTrue(any('Brand test shirts' in t.get_text() and 'Other' in t.get_text()
                            for t in legend.get_texts()))
        rows = self.export(tab)
        self.assertEqual(rows[0][-1], 'Brand / Style')
        self.assertTrue(all(len(r) == 8 for r in rows))
        for kind in ('product', 'brand'):
            selected = [r for r in rows if r[0] == kind]
            self.assertEqual(len(selected), 3)
            self.assertEqual(sum(float(r[5]) for r in selected), 170)
            self.assertEqual({(r[2], r[7]) for r in selected},
                             {('Brand test shirts', 'CK'), ('Brand test shirts', 'Other'),
                              ('Brand test trousers', 'CK')})
        self.month(tab, QDate(2025, 3, 1))
        self.assertEqual(tab.top_brands_table.rowCount(), 0)
        self.assertEqual(tab.top_items_table.rowCount(), 0)
        self.assertEqual(len(tab.category_figure.axes[0].patches), 0)
        self.month(tab, QDate(2025, 2, 1))
        tab.period.blockSignals(True)
        tab.period.preset.setCurrentText('Custom dates')
        tab.period.date_from.setDate(QDate(2025, 3, 1))
        tab.period.date_to.setDate(QDate(2025, 2, 1))
        tab.period.blockSignals(False)
        self.assertFalse(tab.refresh())
        self.assertEqual(tab.top_brands_table.rowCount(), 0)
        self.assertEqual(tab.top_items_table.rowCount(), 0)

    def test_brand_csv_includes_groups_beyond_top_ten(self):
        for index in range(12):
            self.db.add_subtype(self.category['id'], f'Brand {index:02}')
            brand_id = next(r['id'] for r in self.db.get_subtypes(self.category['id'])
                            if r['name'] == f'Brand {index:02}')
            iid = self.db.add_item(f'Item {index}', self.category['id'], brand_id, '', '', '', 100, 100)
            self.db.save_bill(None, [dict(item_id=iid, name=f'Item {index}', category=self.category['name'],
                quantity=1, rate=1, subtotal=1, gst_rate=0, gst_amount=0)],
                1, 0, 0, 1, 'Cash', '2025-02-01')
        tab = self.tab(StatsTab)
        self.month(tab, QDate(2025, 2, 1))
        self.assertEqual(tab.top_brands_table.rowCount(), 10)
        self.assertEqual(tab.top_items_table.rowCount(), 10)
        tab.chart_group.setCurrentText('Brands / Styles')
        self.assertEqual(len(tab.category_figure.axes[0].patches), 9)
        rows = self.export(tab)
        self.assertEqual(len([r for r in rows if r[0] == 'brand']), 12)
        self.assertEqual(len([r for r in rows if r[0] == 'product']), 12)

    def test_sales_filter_and_csv_share_month_custom_and_search(self):
        tab = self.tab(SalesTab)
        self.month(tab)
        rows = self.export(tab)
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(float(r[-1]) for r in rows[1:]), 315)
        tab.search_input.setText(self.db.get_bill(self.feb)[0]['bill_no'])
        rows = self.export(tab)
        self.assertEqual(len(rows), 2)
        self.assertEqual(float(rows[1][-1]), 210)
        tab.search_input.clear()
        tab.period.preset.setCurrentText('Custom dates')
        tab.period.date_from.setDate(QDate(2024, 2, 29))
        tab.period.date_to.setDate(QDate(2024, 2, 29))
        self.assertEqual(len(self.export(tab)), 2)
        tab.period.preset.setCurrentText('All time')
        self.assertEqual(len(self.export(tab)), 6)

    def test_expense_filter_and_csv_include_category(self):
        self.db.add_expense('Transport', 7, expense_date='2024-02-29')
        tab = self.tab(ExpensesTab)
        self.month(tab)
        self.assertEqual(sum(float(r[2]) for r in self.export(tab)[1:]), 27)
        tab.category_filter.setCurrentText('Transport')
        rows = self.export(tab)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], 'Transport')
        tab.category_filter.setCurrentText('All categories')
        tab.period.preset.setCurrentText('All time')
        self.assertEqual(sum(float(r[2]) for r in self.export(tab)[1:]), 36)

    def test_balance_period_receipts_payment_dates_export_and_selection(self):
        tab = self.tab(BalancesTab)
        self.month(tab)
        self.assertEqual(set(tab._bill_ids), {self.feb, self.leap})
        self.assertEqual(sum_money(p['amount'] for p in tab._shown_payments), 185)
        self.assertEqual(sum(float(r[-1]) for r in self.export(tab)[1:]), 100)
        self.assertEqual(sum(float(r[2]) for r in self.export(tab, '_export_payments')[1:]), 185)
        with patch('balances_tab.ReceiptDialog') as receipt:
            tab.bill_table.cellClicked.emit(0, 0)
            self.assertEqual(receipt.call_args.args[0]['id'], tab._bill_ids[0])
            receipt.reset_mock()
            tab.bill_table.cellClicked.emit(0, 6)
            receipt.assert_not_called()
            tab.payment_table.cellClicked.emit(0, 1)
            self.assertEqual(receipt.call_args.args[0]['id'], tab._shown_payments[0]['bill_id'])
        tab.search_input.setText('No match at all')
        self.assertEqual(tab.bill_table.rowCount(), 0)
        self.assertEqual(tab.payment_table.rowCount(), 0)
        self.assertIsNone(tab.selected_customer_id)

    def test_balances_include_walk_in_and_reflow_payment_button(self):
        tab = self.tab(BalancesTab)
        self.assertIn(0, tab._customer_ids)
        tab.resize(1280, 800)
        tab.show()
        self.app.processEvents()
        tab.selected_customer_id = self.cid
        tab._load_customer(self.cid)
        row = tab._bill_ids.index(self.feb)
        button = tab.bill_table.cellWidget(row, 6)
        self.assertGreaterEqual(button.height(), 32)
        self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance(button.text()) + 12)
        with patch('balances_tab.ReceiptDialog') as receipt:
            point = tab.bill_table.visualItemRect(tab.bill_table.item(row, 0)).center()
            QTest.mouseClick(tab.bill_table.viewport(), Qt.LeftButton, pos=point)
            self.assertEqual(receipt.call_args.args[0]['id'], self.feb)

    def test_customer_purchase_row_opens_correct_receipt(self):
        tab = self.tab(CustomersTab)
        tab._load_customer(self.cid)
        with patch('customers_tab.ReceiptDialog') as receipt:
            tab.history_table.cellClicked.emit(0, 0)
            self.assertEqual(receipt.call_args.args[0]['id'], tab._history_bill_ids[0])
            receipt.reset_mock()
            tab.history_table.cellClicked.emit(0, 4)
            receipt.assert_not_called()

    def test_statistics_cards_charts_and_csv_use_same_period(self):
        tab = self.tab(StatsTab)
        self.month(tab)
        self.assertEqual(tab.kpi_grid.count(), 11)
        self.assertEqual(tab._report['totals']['bill_count'], 2)
        self.assertEqual(len(tab.monthly_figure.axes[0].patches), 1)
        self.assertEqual(tab.monthly_figure.axes[0].patches[0].get_height(), 315)
        self.assertEqual(len(tab.category_figure.axes[0].patches), 1)
        rows = self.export(tab)
        metrics = {r[1]:float(r[6]) for r in rows[1:] if r[0]=='metric'}
        self.assertEqual(metrics['bill_count'], 2)
        self.assertEqual(metrics['revenue'], 315)
        self.assertEqual(sum(float(r[5]) for r in rows if r[0]=='monthly'), 315)
        tab.chart_group.setCurrentText('Categories')
        self.assertEqual(len(tab.category_figure.axes[0].patches), 1)

    def test_styled_statistics_scroll_preserves_chart_height_after_filter_changes(self):
        # Other GUI tests leave closed widgets/pending events in the shared app.
        # Run an actual event loop with the production theme in a fresh process.
        result = subprocess.run(
            [sys.executable, str(ROOT / 'tests/report_layout_probe.py'),
             str(ROOT / os.environ.get('BILLING_APP_DIR', 'app'))],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_zero_sales_pie_is_safe_and_invalid_filter_clears_export(self):
        self.sale('2020-01-01', 0, 0)
        tab = self.tab(StatsTab)
        self.month(tab, QDate(2020,1,1))
        self.assertEqual(len(tab.category_figure.axes[0].patches), 0)
        for cls in (SalesTab, ExpensesTab, BalancesTab, StatsTab):
            current = self.tab(cls)
            current.period.blockSignals(True)
            current.period.preset.setCurrentText('Custom dates')
            current.period.date_from.setDate(QDate(2024, 3, 1))
            current.period.date_to.setDate(QDate(2024, 2, 1))
            current.period.blockSignals(False)
            self.assertFalse(current.refresh())
            with patch('report_export.QFileDialog.getSaveFileName') as choose:
                current._export_csv()
            choose.assert_not_called()

    def test_exports_escape_customer_formulas_and_handle_write_failure(self):
        self.db.update_customer(self.cid, '=1+1', '123', '', '')
        tab = self.tab(SalesTab)
        self.month(tab)
        self.assertEqual(self.export(tab)[1][2], "'=1+1")
        with patch('report_export.QFileDialog.getSaveFileName', return_value=(str(self.path), 'CSV')), \
             patch('report_export.QMessageBox.warning') as warning:
            tab._export_csv()
        warning.assert_called_once()


if __name__ == '__main__':
    unittest.main()
