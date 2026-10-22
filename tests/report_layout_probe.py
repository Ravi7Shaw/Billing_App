"""Exercise a real Qt event loop in a fresh process, without earlier tests' widgets."""
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QDate
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea
from database import Database
from stats_tab import StatsTab
from theme import STYLESHEET

app = QApplication([])
app.setStyleSheet(STYLESHEET)
errors = []
sys.excepthook = lambda *exc: errors.append(str(exc[1]))
with tempfile.TemporaryDirectory() as folder:
    db = Database(str(Path(folder)/'layout.db'))
    db.save_bill(None, [dict(name='Sample', quantity=1, rate=10, subtotal=10)],
                 10, 0, 0, 10, 'Cash', '2024-02-29')
    tab = StatsTab(db)
    tab.resize(1280, 800)
    tab.show()
    for preset in ('Selected month', 'All time', 'Last 7 days', 'Selected month'):
        tab.period.month_from.setDate(QDate(2024, 2, 1))
        tab.period.preset.setCurrentText(preset)
        tab.refresh()
        QTest.qWait(30)
        container = tab.findChild(QScrollArea).widget()
        assert container.height() >= container.layout().minimumSize().height()
        boxes = [canvas.parent() for canvas in (tab.trend_canvas, tab.category_canvas, tab.monthly_canvas)]
        for first, second in zip(boxes, boxes[1:]):
            assert first.geometry().bottom() < second.geometry().top()
        for canvas in (tab.trend_canvas, tab.category_canvas, tab.monthly_canvas):
            assert canvas.geometry().bottom() <= canvas.parent().height()
    assert not errors, errors
    tab.close()
    tab.deleteLater()
    QTest.qWait(30)
print('Styled report layout and repeated filter changes passed in a fresh Qt process.')
