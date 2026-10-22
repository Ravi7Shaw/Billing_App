"""Bound widget creation to one page without truncating reports or exports."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton


class Pager(QWidget):
    changed = Signal()
    size = 200

    def __init__(self):
        super().__init__()
        self.page = 0
        self.total = 0
        self.key = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        row.addWidget(self.label, 1)
        self.previous = QPushButton('Previous')
        self.next = QPushButton('Next')
        row.addWidget(self.previous)
        row.addWidget(self.next)
        self.previous.clicked.connect(lambda: self._move(-1))
        self.next.clicked.connect(lambda: self._move(1))
        self.set_total(0)

    @property
    def offset(self):
        return self.page*self.size

    def filter(self, key):
        if key != self.key:
            self.page = 0
            self.key = key

    def set_total(self, total):
        self.total = total
        self.page = min(self.page, max(0, (total-1)//self.size))
        self.label.setText(f'{self.offset+1 if total else 0}–{min(self.offset+self.size,total)} of {total}')
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.offset+self.size < total)

    def _move(self, direction):
        self.page = max(0, min(self.page+direction, max(0,(self.total-1)//self.size)))
        self.changed.emit()
