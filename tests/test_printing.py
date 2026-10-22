"""Native Windows selection and CUPS submission without requiring a real printer."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/os.environ.get('BILLING_APP_DIR','app')))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QDialog,QMessageBox,QComboBox,QSpinBox
from PySide6.QtPrintSupport import QPrinter
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader=None
from database import Database
from access import AccessSession,RoleDatabase
from label_tab import LabelTab,LabelPrinterDialog
from receipt import ReceiptDialog,PrinterDialog
import printing


class PrintingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.pdf=Path(self.tmp.name)/'input.pdf';self.pdf.write_bytes(b'%PDF-test')

    def test_windows_never_uses_cups_even_if_commands_exist(self):
        with patch.object(printing.sys,'platform','win32'),patch.object(printing.shutil,'which',return_value='/usr/bin/lp'),patch.object(printing.QPrinterInfo,'availablePrinterNames',return_value=['Windows Printer']),patch.object(printing.subprocess,'run') as run:
            self.assertEqual(printing.printing_backend(),'qt')
            self.assertEqual(printing.available_printers(),['Windows Printer']);run.assert_not_called()
            self.assertIn('Windows',printing.printer_setup_help())

    def test_linux_can_discover_queues_without_qt_plugin(self):
        with patch.object(printing.sys,'platform','linux'),patch.object(printing.shutil,'which',return_value='/usr/bin/lp'),patch.object(printing.QPrinterInfo,'availablePrinterNames',return_value=[]),patch.object(printing,'_cups',return_value='Citizen accepting requests since today\nOffice not accepting requests since today\n'):
            self.assertEqual(printing.printing_backend(),'cups')
            self.assertEqual(printing.available_printers(),['Citizen','Office'])
            self.assertIn('pacman',printing.printer_setup_help())
        with patch.object(printing.shutil,'which',return_value=None):self.assertEqual(printing.printing_backend(),'qt')

    def test_exact_media_no_scaling_and_copies_with_job_acknowledgement(self):
        result=subprocess.CompletedProcess([],0,'request id is Citizen-42 (1 file(s))\n','')
        with patch.object(printing,'check_cups_queue') as ready,patch.object(printing.subprocess,'run',return_value=result) as run:
            self.assertEqual(printing.submit_pdf(self.pdf,'Citizen',100,50,203,2),'Citizen-42')
        ready.assert_called_once_with('Citizen')
        args,options=run.call_args;command=args[0]
        self.assertEqual(command[:5],['lp','-d','Citizen','-n','2'])
        self.assertIn('media=Custom.100x50mm',command)
        self.assertIn('print-scaling=none',command);self.assertIn('printer-resolution=203dpi',command)
        self.assertIn('job-sheets=none',command);self.assertNotIn('shell',options)
        self.assertEqual(options['env']['LC_ALL'],'C');self.assertEqual(command[-1],str(self.pdf.resolve()))

    def test_paused_or_rejecting_queue_blocks_submission(self):
        for responses in (['printer Citizen disabled since today'],['printer Citizen is idle.','Citizen not accepting requests since today']):
            with patch.object(printing,'_cups',side_effect=responses) as run:
                with self.assertRaises(printing.PrintError):printing.submit_pdf(self.pdf,'Citizen',100,50)
            self.assertTrue(all(call.args[0][0]=='lpstat' for call in run.call_args_list))

    def test_timeout_missing_job_id_and_nonzero_exit_never_retry(self):
        for response in (subprocess.TimeoutExpired(['lp'],30),subprocess.CompletedProcess([],0,'unknown response',''),subprocess.CompletedProcess([],1,'','filter failed')):
            with patch.object(printing,'check_cups_queue'),patch.object(printing.subprocess,'run') as run:
                if isinstance(response,Exception):run.side_effect=response
                else:run.return_value=response
                with self.assertRaises(printing.PrintError):printing.submit_pdf(self.pdf,'Citizen',100,50)
                run.assert_called_once()

    def test_queue_status_exposes_pending_driver_error(self):
        with patch.object(printing,'printing_backend',return_value='cups'),patch.object(printing,'_cups',side_effect=['printer Citizen is idle.\n','Citizen-42 user 1234\n Status: Filter failed\n']):
            result=printing.queue_status('Citizen')
        self.assertIn('Filter failed',result);self.assertIn('Citizen-42',result)

    def test_invalid_queue_and_non_pdf_never_submit(self):
        with patch.object(printing.subprocess,'run') as run:
            for queue in ('-bad','bad queue','x/y',''):
                with self.assertRaises(printing.PrintError):printing.submit_pdf(self.pdf,queue,100,50)
            self.pdf.write_bytes(b'bad')
            with self.assertRaises(printing.PrintError):printing.submit_pdf(self.pdf,'Citizen',100,50)
            run.assert_not_called()

    def test_both_printer_dialogs_refresh_from_empty_to_available(self):
        for target,constructor,field in [('label_tab',lambda:LabelPrinterDialog(None),'printer'),('receipt',lambda:PrinterDialog(QPrinter()),'printers')]:
            with patch(target+'.available_printers',side_effect=[[],['Citizen']]):
                dialog=constructor();self.addCleanup(dialog.close)
                self.assertEqual(getattr(dialog,field).count(),0)
                dialog.refresh_printers();self.assertEqual(getattr(dialog,field).currentText(),'Citizen')

    def test_printer_dialog_controls_fit_small_screen(self):
        from PySide6.QtCore import QRect,QPoint
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton
        from theme import STYLESHEET
        previous=self.app.styleSheet();self.app.setStyleSheet(STYLESHEET)
        try:
            for target,constructor in [('label_tab',lambda:LabelPrinterDialog(None)),('receipt',lambda:PrinterDialog(QPrinter()))]:
                with patch(target+'.available_printers',return_value=[]):dialog=constructor()
                self.addCleanup(dialog.close);dialog.show();dialog.resize(640,400);QTest.qWait(30)
                self.assertLessEqual(dialog.width(),640);self.assertLessEqual(dialog.height(),480)
                for button in dialog.findChildren(QPushButton):
                    self.assertTrue(dialog.rect().contains(QRect(button.mapTo(dialog,QPoint()),button.size())),button.text())
                dialog.close()
        finally:self.app.setStyleSheet(previous)

    @unittest.skipIf(PdfReader is None,'Install tests/requirements-labels.txt for PDF checks')
    def test_label_cups_submission_posts_stock_once_and_records_job(self):
        db=Database(str(Path(self.tmp.name)/'db'));cat=db.get_categories()[0]['id']
        iid=db.add_item('Shirt',cat,None,'SKU123','M','Blue',100,0)
        session=AccessSession();session.login('1852j');tab=LabelTab(RoleDatabase(db,session));self.addCleanup(tab.close)
        tab.item.setCurrentIndex(tab.item.findData(iid));tab.quantity.setValue(3);tab.stock_mode.setCurrentIndex(1)
        class Dialog:
            transport='cups'
            def __init__(self,parent):self.printer=QComboBox();self.printer.addItem('Citizen')
            def exec(self):return QDialog.Accepted
        def submit(path,queue,width,height,dpi,**kwargs):
            self.assertEqual((queue,width,height,dpi),('Citizen',100,50,203))
            reader=PdfReader(path);self.assertEqual(len(reader.pages),3)
            self.assertAlmostEqual(float(reader.pages[0].mediabox.width)*25.4/72,100,delta=.3)
            return 'Citizen-91'
        with patch('label_tab.LabelPrinterDialog',Dialog),patch('label_tab.check_cups_queue'),patch('label_tab.submit_pdf',side_effect=submit),patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(QMessageBox,'information'):
            tab.start_print()
        self.assertEqual(db.get_item_by_id(iid)['stock_qty'],3)
        batch=db.get_label_batches()[0];self.assertEqual(batch['status'],'submitted');self.assertIn('Citizen-91',batch['notes'])

    def test_cups_label_failure_is_uncertain_and_does_not_post_stock(self):
        db=Database(str(Path(self.tmp.name)/'db'));cat=db.get_categories()[0]['id']
        iid=db.add_item('Shirt',cat,None,'SKU123','','',100,0)
        session=AccessSession();session.login('1852j');tab=LabelTab(RoleDatabase(db,session));self.addCleanup(tab.close)
        tab.item.setCurrentIndex(tab.item.findData(iid));tab.stock_mode.setCurrentIndex(1)
        class Dialog:
            transport='cups'
            def __init__(self,parent):self.printer=QComboBox();self.printer.addItem('Citizen')
            def exec(self):return QDialog.Accepted
        with patch('label_tab.LabelPrinterDialog',Dialog),patch('label_tab.check_cups_queue'),patch('label_tab.submit_pdf',side_effect=printing.PrintError('Spool timeout')),patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(QMessageBox,'warning') as warning:
            tab.start_print()
        warning.assert_called_once();self.assertEqual(db.get_item_by_id(iid)['stock_qty'],0)
        self.assertEqual(db.get_label_batches()[0]['status'],'uncertain')

    @unittest.skipIf(PdfReader is None,'Install tests/requirements-labels.txt for PDF checks')
    def test_receipt_renders_a4_pdf_then_submits_requested_copies(self):
        db=Database(str(Path(self.tmp.name)/'db'))
        bid,_=db.save_bill(None,[dict(name='Test',quantity=1,rate=100,subtotal=100)],100,0,0,100,'Cash')
        row,lines=db.get_bill(bid);receipt=ReceiptDialog(row,lines);self.addCleanup(receipt.close)
        previous=receipt.text_edit.document().pageSize()
        def choose(printer,parent):
            copies=QSpinBox();copies.setValue(2)
            return Mock(transport='cups',queue='Office',copies=copies,exec=Mock(return_value=QDialog.Accepted))
        def submit(path,*args):
            page=PdfReader(path).pages[0]
            self.assertAlmostEqual(float(page.mediabox.width)*25.4/72,210,delta=.3)
            self.assertEqual(args[:5],('Office',210,297,300,2));return 'Office-92'
        with patch('receipt.PrinterDialog',side_effect=choose),patch('receipt.submit_pdf',side_effect=submit) as output,patch.object(QMessageBox,'information'):
            receipt.print_receipt()
        output.assert_called_once();self.assertEqual(receipt.text_edit.document().pageSize(),previous)


if __name__=='__main__':unittest.main()
