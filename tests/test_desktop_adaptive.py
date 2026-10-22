"""Responsive windows, explicit printer selection and admin database backup."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/os.environ.get('BILLING_APP_DIR','app')))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox
from PySide6.QtPrintSupport import QPrinter
from database import Database
from receipt import ReceiptDialog, PrinterDialog
import main


class AdaptiveDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.db = Database(str(self.path/'test.db'))
        bid,_ = self.db.save_bill(None,[dict(name='Test',quantity=1,rate=100,subtotal=100)],100,0,0,100,'UPI')
        bill,lines = self.db.get_bill(bid)
        self.receipt = ReceiptDialog(bill,lines); self.addCleanup(self.receipt.close)

    def test_geometry_in_fresh_styled_process(self):
        result = subprocess.run([sys.executable,str(ROOT/'tests/desktop_layout_probe.py'),
                                 str(ROOT/os.environ.get('BILLING_APP_DIR','app'))],capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_print_button_opens_explicit_dialog_and_submits_real_document(self):
        target = self.path/'printed.pdf'
        def select(printer,parent):
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(str(target))
            return Mock(exec=Mock(return_value=QDialog.Accepted))
        with patch('receipt.PrinterDialog',side_effect=select) as chooser:
            self.receipt.print_button.click()
        chooser.assert_called_once()
        self.assertTrue(target.read_bytes().startswith(b'%PDF-'))
        self.assertGreater(target.stat().st_size,1000)

    def test_no_installed_printer_keeps_dialog_visible_with_instructions(self):
        with patch('receipt.available_printers',return_value=[]):
            dialog = PrinterDialog(QPrinter(),self.receipt)
        self.addCleanup(dialog.close)
        dialog.show();self.app.processEvents()
        buttons = dialog.findChild(QDialogButtonBox)
        self.assertFalse(buttons.button(QDialogButtonBox.Ok).isEnabled())
        self.assertTrue(dialog.isVisible())

    def test_print_failure_is_shown_and_document_size_is_restored(self):
        printer = QPrinter();printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(str(self.path/'failure.pdf'))
        doc = self.receipt.text_edit.document(); before = doc.pageSize()
        with patch.object(doc,'print_',side_effect=RuntimeError('Printer unavailable')):
            with self.assertRaisesRegex(RuntimeError,'Printer unavailable'):
                self.receipt._print_document(printer)
        self.assertEqual(doc.pageSize(),before)
        with patch('receipt.PrinterDialog',side_effect=RuntimeError('Driver unavailable')), \
             patch.object(QMessageBox,'warning') as warning:
            self.receipt.print_button.click()
        warning.assert_called_once()

    def test_compact_bill_view_accepts_repeated_barcode_scans(self):
        category = self.db.get_categories()[0]
        self.db.add_item('Barcode shirt', category['id'], None, 'CK123', '', '', 200, 20)
        with patch.object(main, 'Database', return_value=self.db): window = main.MainWindow()
        self.addCleanup(window.close)
        window.resize(800, 600); window.show(); self.app.processEvents()
        tab = window.billing_tab
        tab.compact_tabs.setCurrentWidget(tab.bill_panel)
        tab.quick_barcode_input.setFocus()
        for _ in range(2):
            QTest.keyClicks(tab.quick_barcode_input, 'CK123')
            QTest.keyClick(tab.quick_barcode_input, Qt.Key_Return)
            self.app.processEvents()
        self.assertEqual(len(tab.cart), 1)
        self.assertEqual(tab.cart[0]['qty'], 2)
        self.assertEqual(tab.quick_barcode_input.text(), '')
        self.assertIs(tab.compact_tabs.currentWidget(), tab.bill_panel)
        self.assertTrue(tab.quick_barcode_input.hasFocus())
        QTest.keyClicks(tab.quick_barcode_input, 'UNKNOWN')
        QTest.keyClick(tab.quick_barcode_input, Qt.Key_Return)
        self.assertIs(tab.compact_tabs.currentWidget(), tab.entry_scroll)
        self.assertFalse(tab.barcode_status.isHidden())

    def test_backup_button_saves_valid_database_and_is_admin_only(self):
        with patch.object(main,'Database',return_value=self.db):window=main.MainWindow()
        self.addCleanup(window.close)
        self.assertTrue(window.backup_button.isHidden())
        with self.assertRaises(PermissionError):window.db.backup_database(str(self.path/'blocked'))
        window.session.login('1852j');window._build_dashboard()
        destination = self.path/'backups';destination.mkdir()
        with patch('maintenance.QFileDialog.getExistingDirectory',return_value=str(destination)), \
             patch.object(QMessageBox,'information') as info:
            window.backup_button.click()
        info.assert_called_once()
        backups = list(destination.glob('*.db'));self.assertEqual(len(backups),1)
        with sqlite3.connect(backups[0]) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('SELECT total FROM bills').fetchone()[0],100)
        window._logout_admin();self.assertTrue(window.backup_button.isHidden())
