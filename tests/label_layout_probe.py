"""Label workbench/main navigation geometry with production styling."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QRect,QPoint
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from database import Database
from theme import STYLESHEET
import main
app=QApplication([]);app.setStyleSheet(STYLESHEET)
errors=[]
original_hook=sys.excepthook
def record_error(*args):
    errors.append(str(args[1]));original_hook(*args)
sys.excepthook=record_error
with tempfile.TemporaryDirectory() as folder:
    db=Database(str(Path(folder)/'test.db'))
    with patch.object(main,'Database',return_value=db):window=main.MainWindow()
    assert window.tabs.count()==1 and window.labels_tab is None
    window.session.login('1852j');window._build_dashboard();window.show()
    tab=window.labels_tab
    window.section_picker.setCurrentIndex(window.tabs.indexOf(tab))
    assert window.tabs.currentWidget() is tab
    for width,height in ((640,480),(800,600),(1366,768),(1920,1080)):
        for page in range(tab.pages.count()):
            tab.pages.setCurrentIndex(page)
            window.resize(width,height);QTest.qWait(30)
            assert window.width()<=width and window.height()<=height,(width,height,page,window.size())
            for action in (tab.print_button,tab.export_button,tab.setup_button,window.section_picker,window.logout_button):
                rect=QRect(action.mapTo(window,QPoint()),action.size())
                assert window.rect().contains(rect) and action.isVisibleTo(window),(width,height,page,rect)
    assert tab.pages.count()==2 and not hasattr(tab,'scene')
    window._logout_admin();assert window.labels_tab is None and window.tabs.count()==1
    window.close();assert not errors,errors
print('Fixed Citizen label form, print/setup controls and navigation fit all tested sizes.')
