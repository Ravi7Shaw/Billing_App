"""The same inclusive date controls on every reporting screen."""
from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDateEdit, QPushButton


class PeriodFilter(QWidget):
    changed = Signal()

    def __init__(self, default='Last 30 days'):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel('Period:'))
        self.preset = QComboBox()
        self.preset.addItems(['Today', 'Last 7 days', 'Last 30 days', 'This month',
                             'Selected month', 'Month range', 'Custom dates', 'All time'])
        self.preset.setCurrentText(default)
        row.addWidget(self.preset)
        self.description = QLabel()
        self.description.setWordWrap(True)
        row.addWidget(self.description, 1)
        refresh = QPushButton('Apply / Refresh')
        refresh.clicked.connect(self._emit)
        row.addWidget(refresh)
        layout.addLayout(row)
        self.date_fields = QWidget()
        dates = QHBoxLayout(self.date_fields)
        dates.setContentsMargins(0, 0, 0, 0)
        self.date_from = self._date_edit('dd MMM yyyy', QDate.currentDate().addDays(-29))
        self.date_to = self._date_edit('dd MMM yyyy', QDate.currentDate())
        dates.addWidget(QLabel('From:'))
        dates.addWidget(self.date_from)
        dates.addWidget(QLabel('To:'))
        dates.addWidget(self.date_to)
        dates.addStretch()
        layout.addWidget(self.date_fields)
        self.month_fields = QWidget()
        months = QHBoxLayout(self.month_fields)
        months.setContentsMargins(0, 0, 0, 0)
        self.month_from = self._date_edit('MMMM yyyy', QDate.currentDate())
        self.month_to = self._date_edit('MMMM yyyy', QDate.currentDate())
        months.addWidget(QLabel('Month:'))
        months.addWidget(self.month_from)
        self.month_to_label = QLabel('Through:')
        months.addWidget(self.month_to_label)
        months.addWidget(self.month_to)
        months.addStretch()
        layout.addWidget(self.month_fields)
        self.preset.currentTextChanged.connect(self._emit)
        for field in (self.date_from, self.date_to, self.month_from, self.month_to):
            field.dateChanged.connect(self._emit)
        self._update_description()

    @staticmethod
    def _date_edit(format, value):
        field = QDateEdit(calendarPopup=True)
        field.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
        field.setDisplayFormat(format)
        field.setDate(value)
        return field

    def bounds(self):
        today = QDate.currentDate()
        mode = self.preset.currentText()
        if mode == 'All time':
            return None, None
        end = today
        if mode == 'Today':
            start = today
        elif mode == 'Last 7 days':
            start = today.addDays(-6)
        elif mode == 'Last 30 days':
            start = today.addDays(-29)
        elif mode == 'This month':
            start = QDate(today.year(), today.month(), 1)
        elif mode in ('Selected month', 'Month range'):
            first = self.month_from.date()
            last = self.month_to.date() if mode == 'Month range' else first
            start = QDate(first.year(), first.month(), 1)
            end = QDate(last.year(), last.month(), last.daysInMonth())
        else:
            start, end = self.date_from.date(), self.date_to.date()
        if start > end:
            raise ValueError('From date/month must be before or equal to To date/month.')
        return start.toString('yyyy-MM-dd'), end.toString('yyyy-MM-dd')

    def _update_description(self):
        mode = self.preset.currentText()
        self.date_fields.setVisible(mode == 'Custom dates')
        self.month_fields.setVisible(mode in ('Selected month', 'Month range'))
        self.month_to.setVisible(mode == 'Month range')
        self.month_to_label.setVisible(mode == 'Month range')
        try:
            start, end = self.bounds()
            self.description.setText(f'{start} to {end} (inclusive)' if start else 'All recorded dates')
        except ValueError as exc:
            self.description.setText(str(exc))

    def _emit(self, *_):
        self._update_description()
        self.changed.emit()
