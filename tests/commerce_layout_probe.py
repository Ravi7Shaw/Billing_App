"""Assert new admin screens fit small/large displays under the real stylesheet."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QPoint,QRect
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from database import Database
from theme import STYLESHEET
import main
app=QApplication([]);app.setStyleSheet(STYLESHEET)
errors=[]
sys.excepthook=lambda *args:errors.append(str(args[1]))
with tempfile.TemporaryDirectory() as d:
    db=Database(str(Path(d)/'layout.db'))
    with patch.object(main,'Database',return_value=db):window=main.MainWindow()
    window.session.login('1852j');window._build_dashboard();window.show()
    for width,height in ((640,480),(800,600),(1366,768),(1920,1080)):
        for tab in (window.purchases_tab,window.accounts_tab):
            window.tabs.setCurrentWidget(tab)
            for index in range(tab.pages.count()):
                if hasattr(tab,'section'):tab.section.setCurrentIndex(index)
                else:tab.pages.setCurrentIndex(index)
                window.resize(width,height);QTest.qWait(25)
                assert window.width()<=width and window.height()<=height,(type(tab).__name__,index,width,height,window.size())
                if tab is window.purchases_tab:
                    for action in (tab.save_button,tab.clear_button):
                        rect=QRect(action.mapTo(window,QPoint()),action.size())
                        assert window.rect().contains(rect) and action.isVisibleTo(window),(width,height,rect)
    window.close()
    assert not errors,errors
print('Purchases and all accounting sections fit 640x480 through 1920x1080.')
