"""Labels: independent decoder, physical dimensions, idempotent stock and confirmations."""
import json
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
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPainter
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication,QMessageBox,QDialog
from database import Database
from access import AccessSession,RoleDatabase
from label_design import default_design,metadata_for,validate_design,paint_label,configure_label_printer,print_labels
from label_tab import LabelTab
from commerce_ui import ChartAccountsTab


class LabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.db=Database(str(Path(self.folder.name)/'test.db'))
        self.cat=self.db.get_categories()[0]['id']
        self.item=self.db.add_item('Denim jeans',self.cat,None,'CS-1234567890','M','Blue',500,0)
        self.metadata=metadata_for(dict(self.db.get_item_by_id(self.item)),'Default shop',{'fabric':'Cotton'})
        self.design=default_design()
        self.session=AccessSession();self.session.login('1852j');self.role=RoleDatabase(self.db,self.session)
        self.errors=[];hook=sys.excepthook;sys.excepthook=lambda *args:self.errors.append(str(args[1]));self.addCleanup(setattr,sys,'excepthook',hook)

    def tearDown(self):
        self.app.processEvents();self.assertEqual(self.errors,[])

    def prepare(self,key='request',add=True,quantity=10,location=1):
        return self.db.prepare_label_batch(key,self.item,None,location,quantity,add,100,self.design,self.metadata,'Test printer',203)

    def test_durable_batch_adds_stock_once_and_reprint_never_adds_stock(self):
        bid=self.prepare();self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],0)
        self.assertEqual(self.prepare(),bid)
        self.db.claim_label_batch(bid)
        with self.assertRaises(ValueError):self.db.claim_label_batch(bid)
        self.db.settle_label_batch(bid,True);self.db.settle_label_batch(bid,True)
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],10)
        self.assertEqual(self.db.stock_report()[0]['value_cents'],100000)
        self.assertEqual(self.db.account_statement('inventory')['balance_cents'],100000)
        row=self.db.get_item_by_id(self.item)
        self.db.update_item(self.item,row['name'],self.cat,None,row['barcode'],'M','Blue',600,10)
        retry=self.db.prepare_label_reprint(bid,'reprint',2,'Test printer')
        self.db.claim_label_batch(retry);self.db.settle_label_batch(retry,True)
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],10)
        self.assertEqual(json.loads(self.db.get_label_batch(retry)['metadata'])['rate'],'500.00')

    def test_uncertain_job_blocks_duplicate_stock_and_can_be_reconciled(self):
        bid=self.prepare();self.db.claim_label_batch(bid);self.db.mark_label_uncertain(bid,'Paper jam')
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],0)
        with self.assertRaises(ValueError):self.prepare('another')
        self.db.settle_label_batch(bid,False,'No output')
        fresh=self.prepare('another');self.db.claim_label_batch(fresh);self.db.settle_label_batch(fresh,True)
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],10)

    def test_stock_posting_failure_rolls_back_and_retry_is_once_only(self):
        bid=self.prepare();self.db.claim_label_batch(bid)
        with self.db._conn() as c:c.execute("CREATE TRIGGER stop_post BEFORE INSERT ON stock_movements BEGIN SELECT RAISE(ABORT,'forced'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.db.settle_label_batch(bid,True)
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],0)
        self.assertEqual(self.db.get_label_batch(bid)['status'],'sending')
        with self.db._conn() as c:c.execute('DROP TRIGGER stop_post')
        self.db.settle_label_batch(bid,True)
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],10)

    def test_metadata_barcode_resolves_and_cannot_be_assigned_to_another_item(self):
        self.design['elements']=[dict(kind='qr',x=5,y=5,w=40,h=40,text='{metadata}')]
        self.prepare()
        payload=self.metadata['metadata']
        self.assertEqual(self.db.get_item_by_barcode(payload)['id'],self.item)
        employee=RoleDatabase(self.db,AccessSession())
        self.assertEqual(employee.get_item_by_barcode(payload)['id'],self.item)
        with self.assertRaises(ValueError):self.db.add_item('Conflict',self.cat,None,payload,'','',1,0)
        other=self.db.add_item('Other',self.cat,None,'other','','',1,0)
        with self.assertRaises(ValueError):self.db.update_item(other,'Other',self.cat,None,payload,'','',1,0)

    def test_new_item_and_alias_creation_roll_back_on_collision(self):
        data=dict(name='New',category_id=self.cat,subtype_id=None,category_name=self.db.get_categories()[0]['name'],subtype_name='',barcode='NEW',size='',color='',rate=100)
        context=metadata_for(data,'Default shop');design=default_design();design['elements'][3]['text']='CS-1234567890'
        with self.assertRaises(ValueError):self.db.prepare_label_batch('new',None,data,1,5,True,10,design,context,'Printer',203)
        self.assertIsNone(self.db.get_item_by_barcode('NEW'))
        self.assertEqual(len(self.db.get_items()),1)

    def test_new_stock_belongs_only_to_selected_godown(self):
        second=self.db.save_master('Godown','Second')
        data=dict(name='New item',category_id=self.cat,subtype_id=None,category_name=self.db.get_categories()[0]['name'],subtype_name='',barcode='NEW1',size='',color='',rate=100)
        context=metadata_for(data,'Second')
        bid=self.db.prepare_label_batch('new',None,data,second,5,True,10,self.design,context,'Printer',203)
        self.db.claim_label_batch(bid);self.db.settle_label_batch(bid,True)
        iid=self.db.get_item_by_barcode('NEW1')['id']
        self.assertNotIn(iid,[r['id'] for r in self.db.stock_report(1)])
        self.assertEqual(next(r['quantity'] for r in self.db.stock_report(second) if r['id']==iid),5)

    def test_permissions_stale_callbacks_and_template_persistence(self):
        self.role.save_label_template('100x50',self.design)
        callback=self.role.prepare_label_batch;self.session.logout()
        for method,args in [('get_label_batches',()),('get_label_templates',()),('save_label_template',('x',self.design))]:
            with self.assertRaises(PermissionError):getattr(self.role,method)(*args)
        with self.assertRaises(PermissionError):callback('x',self.item,None,1,1,True,0,self.design,self.metadata,'p',203)
        again=Database(self.db.path)
        self.assertEqual(json.loads(again.get_label_templates()[0]['document']),self.design)

    def test_validation_rejects_clipping_tiny_barcodes_and_unknown_fields(self):
        validate_design(self.design,self.metadata)
        cases=[]
        for mutation in (lambda d:d['elements'][0].update(x=99),lambda d:d['elements'][3].update(w=5),
                         lambda d:d['elements'][0].update(text='{missing}'),lambda d:d['elements'][3].update(rotation=45),
                         lambda d:d['elements'][0].update(size=100),lambda d:d['elements'][4].update(y=30)):
            design=json.loads(json.dumps(self.design));mutation(design);cases.append(design)
        for design in cases:
            with self.assertRaises(ValueError):validate_design(design,self.metadata)

    def test_code128_and_unicode_qr_decode_independently_at_printer_dpi(self):
        try:import zxingcpp
        except ImportError:self.skipTest('Install tests/requirements-labels.txt for independent barcode decoding')
        from PIL import Image
        for kind,text in [('barcode','CS-1234567890'),('qr',json.dumps({'item':'ಜೀನ್ಸ್','size':'M','price':'500.00'},ensure_ascii=False))]:
            design=dict(version=1,width=100,height=50,elements=[dict(kind=kind,x=4,y=4,w=92 if kind=='barcode' else 40,h=25 if kind=='barcode' else 40,text='{payload}')])
            for dpi in (203,300):
                validate_design(design,{"payload":text},dpi)
                image=QImage(round(100*dpi/25.4),round(50*dpi/25.4),QImage.Format_RGB32);image.fill(Qt.white)
                painter=QPainter(image);paint_label(painter,design,{"payload":text},dpi);painter.end()
                path=Path(self.folder.name)/'label.png';image.save(str(path))
                decoded=zxingcpp.read_barcode(Image.open(path))
                self.assertIsNotNone(decoded);self.assertEqual(decoded.text,text)

    def test_full_metadata_preset_decodes_and_can_be_undone(self):
        try:import zxingcpp
        except ImportError:self.skipTest('Install tests/requirements-labels.txt for independent barcode decoding')
        from PIL import Image
        tab=LabelTab(self.role);self.addCleanup(tab.close)
        tab.item.setCurrentIndex(tab.item.findData(self.item))
        original=json.loads(json.dumps(tab.document))
        tab.preset.setCurrentIndex(tab.preset.findData('qr'));tab.use_preset()
        for dpi in (203,300):
            validate_design(tab.document,tab.context,dpi)
            image=QImage(round(100*dpi/25.4),round(50*dpi/25.4),QImage.Format_RGB32);image.fill(Qt.white)
            painter=QPainter(image);paint_label(painter,tab.document,tab.context,dpi);painter.end()
            path=Path(self.folder.name)/'preset.png';image.save(str(path))
            decoded=zxingcpp.read_barcode(Image.open(path))
            self.assertIsNotNone(decoded);self.assertEqual(decoded.text,tab.context['metadata'])
        tab.undo();self.assertEqual(tab.document,original)

    def test_double_click_text_opens_writing_box_and_saves_with_undo(self):
        from PySide6.QtCore import QTimer,QPointF
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPlainTextEdit
        tab=LabelTab(self.role);self.addCleanup(tab.close)
        tab.item.setCurrentIndex(tab.item.findData(self.item))
        tab.pages.setCurrentIndex(1);tab.resize(800,600);tab.show();QTest.qWait(30)
        original=tab.document['elements'][0]['text'];written='My shop\nDenim {size}'
        def type_and_save():
            dialog=QApplication.activeModalWidget()
            editor=dialog.findChild(QPlainTextEdit,'labelTextEditor')
            editor.selectAll();QTest.keyClicks(editor,'My shop');QTest.keyClick(editor,Qt.Key_Return)
            QTest.keyClicks(editor,'Denim {size}');dialog.accept()
        QTimer.singleShot(50,type_and_save)
        point=tab.view.mapFromScene(QPointF(10*96/25.4,6*96/25.4))
        QTest.mouseDClick(tab.view.viewport(),Qt.LeftButton,pos=point)
        self.assertEqual(tab.document['elements'][0]['text'],written)
        self.assertEqual(tab.text.toPlainText(),written)
        tab.undo();self.assertEqual(tab.document['elements'][0]['text'],original)
        tab.redo();self.assertEqual(tab.document['elements'][0]['text'],written)

    def test_edit_text_cancel_and_new_text_entry(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QPlainTextEdit
        tab=LabelTab(self.role);self.addCleanup(tab.close)
        tab.rebuild(0);original=json.loads(json.dumps(tab.document))
        def cancel():
            dialog=QApplication.activeModalWidget()
            dialog.findChild(QPlainTextEdit,'labelTextEditor').setPlainText('Do not save')
            dialog.reject()
        QTimer.singleShot(0,cancel);tab.edit_text_button.click()
        self.assertEqual(tab.document,original)
        def save():
            dialog=QApplication.activeModalWidget()
            dialog.findChild(QPlainTextEdit,'labelTextEditor').setPlainText('Custom label')
            dialog.accept()
        tab.kind.setCurrentIndex(tab.kind.findData('text'))
        QTimer.singleShot(0,save);tab.add_element()
        self.assertEqual(tab.document['elements'][-1]['text'],'Custom label')
        tab.rebuild(3);QTimer.singleShot(0,save);tab.edit_text()
        self.assertEqual(tab.document['elements'][3]['text'],'Custom label')

    def test_text_style_and_dimensions_save_cancel_undo_and_template(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QFontComboBox,QDoubleSpinBox,QCheckBox
        tab=LabelTab(self.role);self.addCleanup(tab.close);tab.rebuild(0)
        original=json.loads(json.dumps(tab.document))
        def style(accept):
            dialog=QApplication.activeModalWidget()
            font=dialog.findChild(QFontComboBox,'labelTextFont');font.setCurrentIndex(0)
            dialog.findChild(QDoubleSpinBox,'labelTextSize').setValue(18)
            dialog.findChild(QDoubleSpinBox,'labelTextW').setValue(80)
            dialog.findChild(QDoubleSpinBox,'labelTextH').setValue(16)
            for key in ('Bold','Italic','Underline'):dialog.findChild(QCheckBox,'labelText'+key).setChecked(True)
            self.chosen_font=font.currentFont().family()
            dialog.accept() if accept else dialog.reject()
        QTimer.singleShot(0,lambda:style(False));tab.edit_text();self.assertEqual(tab.document,original)
        QTimer.singleShot(0,lambda:style(True));tab.edit_text_button.click()
        element=tab.document['elements'][0]
        self.assertEqual((element['font'],element['size'],element['w'],element['h']),(self.chosen_font,18,80,16))
        self.assertTrue(all(element[k] for k in ('bold','italic','underline')))
        self.db.save_label_template('Styled',tab.document)
        self.assertEqual(json.loads(self.db.get_label_templates()[0]['document']),tab.document)
        tab.undo();self.assertEqual(tab.document,original)
        tab.redo();self.assertEqual(tab.document['elements'][0],element)

    def test_shape_tools_properties_and_rendered_geometry(self):
        tab=LabelTab(self.role);self.addCleanup(tab.close)
        for kind in ('rectangle','ellipse','triangle','line'):
            tab.kind.setCurrentIndex(tab.kind.findData(kind));tab.add_element()
            self.assertEqual(tab.editor_tabs.currentIndex(),1)
            self.assertEqual(tab.shape.currentData(),kind)
            tab.coords['w'].setValue(20);tab.coords['h'].setValue(20)
            tab.filled.setChecked(True);tab.ink='#000000';tab.fill_color='#000000'
            tab.apply_properties();element=tab.document['elements'][-1]
            document=dict(version=1,width=30,height=30,elements=[dict(element,x=5,y=5)])
            validate_design(document,{},203)
            image=QImage(300,300,QImage.Format_RGB32);image.fill(Qt.white)
            painter=QPainter(image);paint_label(painter,document,{},254);painter.end()
            self.assertLess(image.pixelColor(150,150).lightness(),50)
            if kind in ('ellipse','triangle'):self.assertGreater(image.pixelColor(55,55).lightness(),200)
            tab.shape.setCurrentIndex(tab.shape.findData('ellipse'));tab.apply_properties()
            self.assertEqual(tab.document['elements'][-1]['kind'],'ellipse')
            tab.undo();self.assertEqual(tab.document['elements'][-1]['kind'],kind)

    def test_pdf_has_exact_user_page_dimensions_and_label_count(self):
        try:from pypdf import PdfReader
        except ImportError:self.skipTest('Install tests/requirements-labels.txt for PDF page measurement')
        design=dict(version=1,width=75,height=35,elements=[dict(kind='barcode',x=3,y=8,w=69,h=15,text='{barcode}')])
        printer=QPrinter(QPrinter.HighResolution);printer.setOutputFormat(QPrinter.PdfFormat)
        path=Path(self.folder.name)/'labels.pdf';printer.setOutputFileName(str(path))
        configure_label_printer(printer,design,203);print_labels(printer,design,self.metadata,3)
        pages=PdfReader(path).pages;self.assertEqual(len(pages),3)
        for page in pages:
            self.assertAlmostEqual(float(page.mediabox.width)*25.4/72,75,delta=.3)
            self.assertAlmostEqual(float(page.mediabox.height)*25.4/72,35,delta=.3)
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],0)

    def test_two_confirmations_cancel_without_printing_or_stock_mutation(self):
        tab=LabelTab(self.role);self.addCleanup(tab.close);tab.item.setCurrentIndex(tab.item.findData(self.item));tab.stock_mode.setCurrentIndex(1)
        class Dialog:
            def __init__(self,parent):
                from PySide6.QtWidgets import QComboBox
                self.printer=QComboBox();self.printer.addItem('Test printer')
            def exec(self):return QDialog.Accepted
        with patch('label_tab.LabelPrinterDialog',Dialog),patch('label_tab.configure_label_printer',return_value=203),patch('label_tab.print_labels') as output:
            for responses in ([QMessageBox.No],[QMessageBox.Yes,QMessageBox.No]):
                with patch.object(QMessageBox,'question',side_effect=responses) as question:tab.start_print()
                self.assertEqual(question.call_count,len(responses))
            output.assert_not_called()
        self.assertEqual(self.db.get_label_batches(),[])

    def test_successful_ui_print_requires_two_yes_and_posts_exact_count(self):
        tab=LabelTab(self.role);self.addCleanup(tab.close);tab.item.setCurrentIndex(tab.item.findData(self.item));tab.stock_mode.setCurrentIndex(1);tab.quantity.setValue(7)
        class Dialog:
            def __init__(self,parent):
                from PySide6.QtWidgets import QComboBox
                self.printer=QComboBox();self.printer.addItem('Test printer')
            def exec(self):return QDialog.Accepted
        with patch('label_tab.LabelPrinterDialog',Dialog),patch('label_tab.configure_label_printer',return_value=203),patch('label_tab.print_labels') as output,patch.object(QMessageBox,'question',return_value=QMessageBox.Yes) as question,patch.object(QMessageBox,'information'):
            tab.start_print()
        self.assertEqual(question.call_count,2);self.assertEqual(output.call_args.args[3],7)
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],7)
        self.assertFalse(tab.stock_mode.currentData())

    def test_existing_history_visible_and_scope_shared_by_accounts_inventory_reports(self):
        # Earlier bills must be visible without rebuilding/re-entering the old data.
        self.db.save_bill(None,[dict(item_id=self.item,name='Jeans',quantity=1,rate=100,subtotal=100)],100,0,0,100,'Cash',bill_date='2020-01-01')
        second=self.db.save_master('Godown','Second')
        self.db.add_expense('Rent',10,expense_date='2020-01-01',godown_id=second)
        tab=ChartAccountsTab(self.role);self.addCleanup(tab.close)
        self.assertEqual(tab.period.preset.currentText(),'All time');self.assertEqual(tab.sales_table.rowCount(),1)
        tab.scope.setCurrentIndex(tab.scope.findData(second))
        self.assertEqual(tab.sales_table.rowCount(),0);self.assertEqual(tab.stock_table.rowCount(),0)
        self.assertEqual(tab.statement_table.rowCount(),1)
        self.assertIn('Expense',tab.statement_table.item(0,1).text())
        tab.scope.setCurrentIndex(0)
        self.assertEqual(tab.sales_table.rowCount(),1);self.assertEqual(tab.stock_table.rowCount(),1)

    def test_print_failure_stays_uncertain_and_does_not_credit_stock(self):
        tab=LabelTab(self.role);self.addCleanup(tab.close);tab.item.setCurrentIndex(tab.item.findData(self.item));tab.stock_mode.setCurrentIndex(1)
        class Dialog:
            def __init__(self,parent):
                from PySide6.QtWidgets import QComboBox
                self.printer=QComboBox();self.printer.addItem('Test printer')
            def exec(self):return QDialog.Accepted
        with patch('label_tab.LabelPrinterDialog',Dialog),patch('label_tab.configure_label_printer',return_value=203),patch('label_tab.print_labels',side_effect=ValueError('Paper jam')),patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(QMessageBox,'warning') as warning:
            tab.start_print()
        warning.assert_called_once()
        self.assertEqual(self.db.get_label_batches()[0]['status'],'uncertain')
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],0)

    def test_preview_uses_printer_dots_and_calibration_settings_survive_template(self):
        from label_tab import LabelElement
        tab=LabelTab(self.role);self.addCleanup(tab.close);tab.item.setCurrentIndex(tab.item.findData(self.item))
        tab.offset_x.setValue(1);tab.offset_y.setValue(-1);tab.dpi.setCurrentIndex(tab.dpi.findData(300))
        element=next(e for e in tab.scene.items() if isinstance(e,LabelElement) and e.data['kind']=='barcode')
        from label_design import PX_PER_MM
        self.assertAlmostEqual(element.pos().x(),(element.data['x']+1)*PX_PER_MM)
        image=QImage(500,200,QImage.Format_RGB32);painter=QPainter(image)
        with patch('label_tab.draw_element') as draw:element.paint(painter,None)
        painter.end();self.assertAlmostEqual(draw.call_args.args[3],300/25.4)
        self.db.save_label_template('Calibrated',tab.document)
        saved=json.loads(self.db.get_label_templates()[0]['document'])
        self.assertEqual(saved['settings'],dict(dpi=300,offset_x=1,offset_y=-1))

    def test_no_printer_state_and_changed_catalog_are_rejected(self):
        from label_tab import LabelPrinterDialog
        from PySide6.QtWidgets import QDialogButtonBox
        with patch('label_tab.QPrinterInfo.availablePrinterNames',return_value=[]):dialog=LabelPrinterDialog(None)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).isEnabled())
        row=self.db.get_item_by_id(self.item)
        self.db.update_item(self.item,row['name'],self.cat,None,row['barcode'],'M','Blue',700,0)
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.db.get_label_batches(),[])

if __name__=='__main__':unittest.main()
