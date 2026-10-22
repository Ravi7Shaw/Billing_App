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
            for subpage in range(tab.editor_tabs.count()) if page==1 else [0]:
                if page==1:tab.editor_tabs.setCurrentIndex(subpage)
                window.resize(width,height);QTest.qWait(30)
                assert window.width()<=width and window.height()<=height,(width,height,page,subpage,window.size())
                for action in (tab.print_button,tab.pdf_button,window.section_picker,window.logout_button):
                    rect=QRect(action.mapTo(window,QPoint()),action.size())
                    assert window.rect().contains(rect) and action.isVisibleTo(window),(width,height,page,rect)
    # Resize paper persists both dimensions and a movable/resizable element.
    tab.width.setValue(75);tab.height.setValue(35);tab.resize_paper()
    assert (tab.document['width'],tab.document['height'])==(75,35)
    tab.undo();assert tab.document['width']==100
    window._logout_admin();assert window.labels_tab is None and window.tabs.count()==1
    window.close();assert not errors,errors
print('Label editor sections, print controls and navigation fit all tested sizes.')
