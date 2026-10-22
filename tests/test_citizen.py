"""Fixed Citizen ZPL, raw transports, setup decisions and durable form workflow."""
import ctypes
import json
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
from PySide6.QtWidgets import QApplication,QComboBox,QDialog,QMessageBox
from citizen_zpl import create_zpl,document,ean13,COMPANY
from citizen_tab import CitizenLabelTab
from database import Database
from access import AccessSession,RoleDatabase
from label_design import validate_design,paint_label
import printing
import printer_setup
import windows_raw

CODE='8905310168599'


class RawSpooler:
    def __init__(self,partial=False,fail=None):self.calls=[];self.data=b'';self.partial=partial;self.fail=fail;self.info=None
    def OpenPrinterW(self,name,handle,_):
        self.calls.append('open');handle._obj.value=123;return self.fail!='open'
    def StartDocPrinterW(self,handle,level,info):
        self.calls.append('start');self.info=(info._obj.pDocName,info._obj.pDatatype);return 0 if self.fail=='start' else 17
    def StartPagePrinter(self,*args):self.calls.append('page');return True
    def WritePrinter(self,handle,buffer,count,written):
        self.calls.append('write')
        if self.fail=='write':return False
        amount=max(1,count//2) if self.partial and not self.data else count
        written._obj.value=amount;self.data+=ctypes.string_at(buffer,amount);return True
    def EndPagePrinter(self,*args):self.calls.append('endpage');return True
    def EndDocPrinter(self,*args):self.calls.append('end');return self.fail!='end'
    def AbortPrinter(self,*args):self.calls.append('abort');return True
    def ClosePrinter(self,*args):self.calls.append('close');return True


class CitizenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Database(str(Path(self.tmp.name)/'test.db'));self.cat=self.db.get_categories()[0]['id']
        self.db.add_subtype(self.cat,'CK');self.brand=next(r['id'] for r in self.db.get_subtypes(self.cat) if r['name']=='CK')
        self.item=self.db.add_item('Shirt',self.cat,self.brand,CODE,'M','Blue',500,0)
        self.session=AccessSession();self.session.login('1852j');self.role=RoleDatabase(self.db,self.session)
        self.tab=CitizenLabelTab(self.role);self.addCleanup(self.tab.close)
        self.tab.item.setCurrentIndex(self.tab.item.findData(self.item))
        self.errors=[];previous=sys.excepthook;sys.excepthook=lambda *args:self.errors.append(str(args[1]));self.addCleanup(setattr,sys,'excepthook',previous)

    def tearDown(self):self.app.processEvents();self.assertEqual(self.errors,[])

    def payload(self,quantity=7):return create_zpl('CK','Shirt','M',850,500,CODE,quantity)

    def chooser(self):
        class Dialog:
            def __init__(self,parent,raw=False):self.printer=QComboBox();self.printer.addItem('Citizen_CL_S621II')
            def exec(self):return QDialog.Accepted
        return patch('citizen_tab.LabelPrinterDialog',Dialog)

    def test_fixed_label_company_geometry_ean13_and_exact_quantity(self):
        data=self.payload()
        self.assertIn(COMPANY.encode(),data);self.assertNotIn(b'OverSeas',data)
        self.assertIn(b'^PW812\n^LL406',data)
        self.assertIn(b'^BEN,105,Y,N^FD'+CODE.encode(),data)
        self.assertEqual(data.count(b'^XA'),1);self.assertEqual(data.count(b'^XZ'),1)
        self.assertEqual(data.count(b'^PQ7,0,1,Y'),1)
        self.assertIn(b'Rs. 850.00',data);self.assertIn(b'Rs.: 500.00',data)
        validate_design(document(),{'label_barcode':CODE},203)

    def test_invalid_ean13_prices_quantity_and_command_injection(self):
        for code in ('123','8905310168598','８９０５３１０１６８５９９'):
            with self.assertRaises(ValueError):ean13(code)
        for value in (-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):create_zpl('CK','Shirt','M',value,500,CODE,1)
        for value in (0,-1,True,1.5,10001):
            with self.assertRaises(ValueError):self.payload(value)
        data=create_zpl('^XZ~JA','Shirt','M',850,500,CODE,1)
        self.assertEqual(data.count(b'^XZ'),1);self.assertNotIn(b'~JA',data)
        self.assertIn(b'_5E_58_5A_7E_4A_41',data)

    def test_linux_raw_sends_exact_bytes_with_no_spooler_copy_multiplier(self):
        result=subprocess.CompletedProcess([],0,b'request id is Citizen-18 (1 file(s))\n',b'')
        payload=self.payload()
        with patch.object(printing.sys,'platform','linux'),patch.object(printing,'printing_backend',return_value='cups'),patch.object(printing,'check_cups_queue'),patch.object(printing.subprocess,'run',return_value=result) as run:
            self.assertEqual(printing.submit_raw('Citizen_CL_S621II',payload),'CUPS job Citizen-18')
        command=run.call_args.args[0];options=run.call_args.kwargs
        self.assertEqual(command[:5],['lp','-d','Citizen_CL_S621II','-o','raw'])
        self.assertEqual(command[command.index('-n')+1],'1');self.assertIn('job-sheets=none',command);self.assertEqual(options['input'],payload);self.assertNotIn('shell',options)

    def test_windows_raw_mode_partial_writes_and_cleanup(self):
        for partial in (False,True):
            spool=RawSpooler(partial=partial)
            with patch.object(windows_raw,'_load_spooler',return_value=spool):job=windows_raw.submit_windows_raw('Citizen Printer',self.payload())
            self.assertEqual(job,17);self.assertEqual(spool.data,self.payload());self.assertEqual(spool.info[1],'RAW')
            self.assertEqual(spool.calls[-2:],['end','close']);self.assertNotIn('abort',spool.calls)
        for failure in ('open','start','write','end'):
            spool=RawSpooler(fail=failure)
            with patch.object(windows_raw,'_load_spooler',return_value=spool),self.assertRaises(OSError):windows_raw.submit_windows_raw('Citizen',self.payload())
            if failure!='open':self.assertEqual(spool.calls[-1],'close')
            self.assertEqual('abort' in spool.calls,failure in ('write','end'))

    def test_default_raw_queue_matches_supplied_working_printer(self):
        from label_tab import LabelPrinterDialog
        with patch('label_tab.available_printers',return_value=['Citizen_Other','Citizen_CL_S621II']):dialog=LabelPrinterDialog(None,raw=True)
        self.addCleanup(dialog.close);self.assertEqual(dialog.printer.currentText(),'Citizen_CL_S621II')

    def test_windows_route_does_not_use_linux_commands(self):
        with patch.object(printing.sys,'platform','win32'),patch.object(windows_raw,'submit_windows_raw',return_value=12) as output,patch.object(printing.subprocess,'run') as run:
            self.assertEqual(printing.submit_raw('Citizen',self.payload()),'Windows job 12')
            output.assert_called_once_with('Citizen',self.payload());run.assert_not_called()

    def test_both_confirmations_cancel_without_stock_or_submission(self):
        self.tab.stock_mode.setCurrentIndex(1)
        for responses in ([QMessageBox.No],[QMessageBox.Yes,QMessageBox.No]):
            with self.chooser(),patch.object(QMessageBox,'question',side_effect=responses),patch('citizen_tab.submit_raw') as output:self.tab.start_print()
            output.assert_not_called()
        self.assertEqual(self.db.get_label_batches(),[]);self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],0)

    def test_print_stock_and_reprint_frozen_after_price_change(self):
        self.tab.stock_mode.setCurrentIndex(1);self.tab.quantity.setValue(7);sent=[]
        with self.chooser(),patch.object(QMessageBox,'question',return_value=QMessageBox.Yes) as question,patch.object(QMessageBox,'information'),patch('citizen_tab.submit_raw',side_effect=lambda q,p:(sent.append(p) or 'CUPS job Citizen-19')):
            self.tab.start_print()
        self.assertEqual(question.call_count,2);self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],7)
        self.assertIn(b'^PQ7,0,1,Y',sent[0]);batch=self.db.get_label_batches()[0]
        row=self.db.get_item_by_id(self.item)
        self.db.update_item(self.item,row['name'],self.cat,self.brand,CODE,'M','Blue',600,7)
        batch['quantity']=2
        with self.chooser(),patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(QMessageBox,'information'),patch('citizen_tab.submit_raw',side_effect=lambda q,p:(sent.append(p) or 'CUPS job Citizen-20')):
            self.tab.start_print(batch)
        self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],7)
        self.assertIn(b'Rs.: 500.00',sent[-1]);self.assertIn(b'^PQ2,0,1,Y',sent[-1])

    def test_raw_failure_stays_uncertain_without_stock_or_retry(self):
        self.tab.stock_mode.setCurrentIndex(1)
        with self.chooser(),patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(QMessageBox,'warning'),patch('citizen_tab.submit_raw',side_effect=OSError('Disconnected')) as output:
            self.tab.start_print()
        output.assert_called_once();self.assertEqual(self.db.get_item_by_id(self.item)['stock_qty'],0)
        self.assertEqual(self.db.get_label_batches()[0]['status'],'uncertain')

    def test_new_item_uses_selected_category_brand_godown_and_scan_alias(self):
        self.tab.item.setCurrentIndex(0);self.tab.category.setCurrentIndex(self.tab.category.findData(self.cat));self.tab.category_changed()
        self.tab.stock_brand.setCurrentIndex(self.tab.stock_brand.findData(self.brand))
        self.tab.product.setText('Jeans');self.tab.size.setText('L');self.tab.selling_price.setValue(900);self.tab.mrp.setValue(1000)
        code='4006381333931';self.tab.barcode.setText(code);self.tab.quantity.setValue(4)
        second=self.db.save_master('Godown','Second');self.tab.refresh();self.tab.location.setCurrentIndex(self.tab.location.findData(second))
        with self.chooser(),patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(QMessageBox,'information'),patch('citizen_tab.submit_raw',return_value='CUPS job Citizen-21'):self.tab.start_print()
        item=self.db.get_item_by_barcode(code);self.assertEqual(item['stock_qty'],4);self.assertEqual(item['category_id'],self.cat);self.assertEqual(item['subtype_id'],self.brand)
        self.assertEqual(self.db.stock_report(second)[0]['quantity'],4)

    def test_existing_alphanumeric_catalog_code_can_use_ean13_scan_alias(self):
        iid=self.db.add_item('Jeans',self.cat,self.brand,'CS-OLD123','L','Blue',500,0)
        self.tab.refresh();self.tab.item.setCurrentIndex(self.tab.item.findData(iid));self.tab.barcode.setText('4006381333931')
        with self.chooser(),patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(QMessageBox,'information'),patch('citizen_tab.submit_raw',return_value='Windows job 21'):
            self.tab.start_print()
        self.assertEqual(self.db.get_item_by_barcode('4006381333931')['id'],iid)
        self.assertEqual(self.db.get_item_by_barcode('CS-OLD123')['id'],iid)
        self.assertEqual(self.db.get_item_by_id(iid)['stock_qty'],0)

    def test_windows_setup_opens_os_driver_wizard(self):
        with patch.object(printer_setup.sys,'platform','win32'),patch.object(printer_setup.subprocess,'Popen') as launch,patch.object(QMessageBox,'information'):
            printer_setup.open_setup(self.tab)
        launch.assert_called_once_with(['rundll32.exe','printui.dll,PrintUIEntry','/il'])

    def test_typed_brand_for_new_item_is_created_only_after_confirmations(self):
        self.tab.item.setCurrentIndex(0);self.tab.brand.setText('New Brand');self.tab.product.setText('Jeans');self.tab.size.setText('L')
        self.tab.barcode.setText('4006381333931');self.tab.selling_price.setValue(900);self.tab.mrp.setValue(1000)
        with self.chooser(),patch.object(QMessageBox,'question',return_value=QMessageBox.No):self.tab.start_print()
        self.assertFalse(any(r['name']=='New Brand' for r in self.db.get_subtypes(self.cat)))
        with self.chooser(),patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(QMessageBox,'information'),patch('citizen_tab.submit_raw',return_value='CUPS job Citizen-22'):self.tab.start_print()
        row=self.db.get_item_by_barcode('4006381333931');self.assertEqual(row['subtype_name'],'New Brand');self.assertEqual(row['stock_qty'],1)

    def test_failed_new_item_rolls_back_new_brand(self):
        self.tab.item.setCurrentIndex(0);self.tab.brand.setText('Rollback Brand');self.tab.product.setText('Other shirt');self.tab.size.setText('L')
        self.tab.barcode.setText(CODE);self.tab.selling_price.setValue(900);self.tab.mrp.setValue(1000)
        iid,item,layout,metadata,quantity,_=self.tab.snapshot()
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):self.db.prepare_label_batch('collision',iid,item,self.tab.location.currentData(),quantity,True,None,layout,metadata,'Citizen',203)
        self.assertFalse(any(r['name']=='Rollback Brand' for r in self.db.get_subtypes(self.cat)))
        self.assertEqual(len(self.db.get_items()),1);self.assertEqual(self.db.get_label_batches(),[])

    def test_admin_rechecked_after_modal_confirmation_and_no_canvas(self):
        self.assertFalse(hasattr(self.tab,'scene'));self.assertEqual(self.tab.pages.count(),2)
        def confirm(*args,**kwargs):self.session.logout();return QMessageBox.Yes
        with self.chooser(),patch.object(QMessageBox,'question',side_effect=confirm),patch('citizen_tab.submit_raw') as output,self.assertRaises(PermissionError):self.tab.start_print()
        output.assert_not_called();self.assertEqual(self.db.get_label_batches(),[])

    def test_ean13_render_decodes_independently(self):
        try:import zxingcpp
        except ImportError:self.skipTest('Install tests/requirements-labels.txt for independent decoding')
        from PIL import Image
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage,QPainter
        layout=document();image=QImage(812,406,QImage.Format_RGB32);image.fill(Qt.white)
        painter=QPainter(image);paint_label(painter,layout,{'label_barcode':CODE},203);painter.end()
        path=Path(self.tmp.name)/'ean.png';image.save(str(path))
        decoded=zxingcpp.read_barcode(Image.open(path));self.assertIsNotNone(decoded);self.assertEqual(decoded.text,CODE)

    def test_linux_setup_installs_missing_tools_detects_usb_and_keeps_existing_queue(self):
        def which(name):return '/usr/bin/'+name if name in ('pacman','systemctl') else None
        results=[subprocess.CompletedProcess([],0,'',''),subprocess.CompletedProcess([],0,'direct usb://CITIZEN/CL-S621II?serial=SYNTHETIC\n','')]
        with patch.object(printer_setup.sys,'platform','linux'),patch.object(printer_setup.os,'geteuid',return_value=0),patch.object(printer_setup.shutil,'which',side_effect=which),patch.object(printer_setup,'run') as run,patch.object(printer_setup.subprocess,'run',side_effect=results):printer_setup.setup_linux()
        commands=[call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0],['pacman','-S','--needed','--noconfirm','cups','cups-filters'])
        self.assertEqual(commands[-1],['lpadmin','-p','Citizen_CL_S621II','-E','-v','usb://CITIZEN/CL-S621II?serial=SYNTHETIC','-m','raw'])
        with patch.object(printer_setup.sys,'platform','linux'),patch.object(printer_setup.os,'geteuid',return_value=0),patch.object(printer_setup.shutil,'which',return_value='/usr/bin/tool'),patch.object(printer_setup,'run') as run,patch.object(printer_setup.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'Citizen_CL_S621II accepting requests\n','')):
            printer_setup.setup_linux()
            self.assertEqual(run.call_args_list[0].args[0],['systemctl','enable','--now','cups.service']);self.assertEqual(run.call_count,1)

    def test_setup_never_guesses_multiple_or_missing_usb_devices(self):
        for devices in ('', 'direct usb://CITIZEN/CL-S621II?serial=ONE\ndirect usb://CITIZEN/CL-S621II?serial=TWO\n'):
            with patch.object(printer_setup.sys,'platform','linux'),patch.object(printer_setup.os,'geteuid',return_value=0),patch.object(printer_setup.shutil,'which',return_value='/usr/bin/tool'),patch.object(printer_setup,'run') as run,patch.object(printer_setup.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,'',''),subprocess.CompletedProcess([],0,devices,'')]),self.assertRaises(RuntimeError):printer_setup.setup_linux()
            self.assertEqual(run.call_count,1)  # service only, no guessed queue


if __name__=='__main__':unittest.main()
