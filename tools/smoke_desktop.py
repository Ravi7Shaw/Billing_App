"""Check a prepared desktop environment using only temporary shop data."""
import argparse
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', required=True, type=Path)
    args = parser.parse_args()
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['QT_API'] = 'pyside6'
    sys.path.insert(0, str(args.app_dir.resolve()))

    from PySide6.QtWidgets import QApplication
    from PySide6.QtPrintSupport import QPrinter
    from database import Database
    import main as desktop
    from receipt import ReceiptDialog, _make_payment_qr_image

    errors = []
    original_hook = sys.excepthook
    def record_error(*exc):
        errors.append(str(exc[1]))
        original_hook(*exc)
    sys.excepthook = record_error
    app = QApplication([])
    with tempfile.TemporaryDirectory(prefix='billing-setup-check-') as folder:
        db = Database(str(Path(folder) / 'check.db'))
        db.add_expense('Setup check', 1)
        desktop.Database = lambda: db
        window = desktop.MainWindow()
        if window.tabs.count() != 1 or window.session.is_admin:
            raise RuntimeError('Desktop must start with employee billing access only.')
        window.show()
        # Exercise admin widgets too; this smoke uses only the synthetic database.
        from unittest.mock import patch
        with patch.object(desktop.QInputDialog, 'getText', return_value=('1852j', True)):
            window._login_admin()
        if not window.session.is_admin:
            raise RuntimeError('Admin login failed.')
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            app.processEvents()
        bill_id, _ = db.save_bill(None, [{'name':'Setup check', 'quantity':1, 'rate':10,
                                         'subtotal':10}], 10, 0, 0, 10, 'Cash')
        bill, items = db.get_bill(bill_id)
        if _make_payment_qr_image(bill, 10).isNull():
            raise RuntimeError('Receipt QR support is missing.')
        dialog = ReceiptDialog(bill, items)
        printer = QPrinter(QPrinter.HighResolution)
        dialog._configure_printer(printer)
        pdf = Path(folder) / 'check.pdf'
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(str(pdf))
        dialog._prepare_document_for_printer(printer)
        dialog.text_edit.document().print_(printer)
        if not pdf.read_bytes().startswith(b'%PDF-'):
            raise RuntimeError('Receipt PDF output failed.')
        window._logout_admin()
        if window.tabs.count() != 1 or window.session.is_admin:
            raise RuntimeError('Admin logout did not restore employee access.')
        window.close()
        app.processEvents()
    if errors:
        raise RuntimeError('Desktop startup errors: ' + '; '.join(errors))
    print('Desktop startup, tabs, SQLite, QR codes and PDF output passed.')


if __name__ == '__main__':
    main()
