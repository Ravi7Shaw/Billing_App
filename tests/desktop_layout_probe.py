"""Production-theme geometry checks in a fresh Qt process."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from database import Database
from receipt import ReceiptDialog
from theme import STYLESHEET
import main
app = QApplication([])
app.setStyleSheet(STYLESHEET)
errors = []
original_hook = sys.excepthook
def record_error(*exc):
    errors.append(str(exc[1])); original_hook(*exc)
sys.excepthook = record_error
with tempfile.TemporaryDirectory() as folder:
    db = Database(str(Path(folder)/'layout.db'))
    with patch.object(main, 'Database', return_value=db):
        window = main.MainWindow()
    window.show()
    for admin in (False, True):
        if admin:
            window.session.login('1852j')
            window._build_dashboard()
        tab = window.billing_tab
        category = db.get_categories()[0]
        iid = db.add_item('CK shirt', category['id'], None, '', '', '', 200, 20)
        tab._add_to_cart(iid, 'CK shirt', category['name'], 2, 200, 5)
        if admin:
            tab.cart[0]['discount'] = 20
            tab._render_cart(); tab._recalculate_totals()
            tab._toggle_discount_visibility()
        for width, height in ((640, 480), (800, 600), (1024, 600), (1366, 768), (1920, 1080)):
            window.resize(width, height)
            QTest.qWait(40)
            if tab._compact:
                tab.compact_tabs.setCurrentIndex(1)
            QTest.qWait(20)
            assert window.width() <= width and window.height() <= height, (admin, width, height, window.size())
            assert tab.cart_table.height() >= 100
            for button in (tab.complete_btn, window.admin_button if not admin else window.backup_button):
                rect = QRect(button.mapTo(window, QPoint(0, 0)), button.size())
                assert window.rect().contains(rect), (admin, width, height, rect)
                assert button.isVisibleTo(window)
        # Input data survives repeated changes of layout.
        tab.phone_input.setText('123')
        window.resize(800, 600); QTest.qWait(20)
        window.resize(1366, 768); QTest.qWait(20)
        assert tab.phone_input.text() == '123'
    bid, _ = db.save_bill(None, [dict(name='Test', quantity=1, rate=100, subtotal=100)],100,0,0,100,'UPI')
    bill, lines = db.get_bill(bid)
    class Screen:
        def availableGeometry(self): return QRect(0, 0, 800, 600)
    with patch('window_utils.QApplication.primaryScreen', return_value=Screen()):
        receipt = ReceiptDialog(bill, lines)
    receipt.show(); QTest.qWait(30)
    assert receipt.height() <= 536
    for button in (receipt.print_button, receipt.pdf_button, receipt.close_button):
        rect = QRect(button.mapTo(receipt, QPoint(0,0)), button.size())
        assert receipt.rect().contains(rect) and button.isVisibleTo(receipt)
    receipt.close(); window.close()
    assert not errors, errors
print('Adaptive billing and receipt actions fit all tested sizes in both roles.')
