"""Compact admin maintenance actions."""
import sys
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel, QPushButton, QMessageBox, QFileDialog
from window_utils import fit_window
from updater import checkout_location, check_update, find_python, start_update


def backup_now(parent, db):
    try:
        if not db.session.is_admin:
            return
        folder = QFileDialog.getExistingDirectory(parent, 'Choose where to save the database backup')
        if not folder:
            return
        path = db.backup_database(folder)
    except Exception as exc:
        QMessageBox.warning(parent, 'Backup failed', str(exc))
        return
    QMessageBox.information(parent, 'Backup saved', f'Complete database backup saved to:\n{path}')


class UpdateCheck(QThread):
    checked = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            if sys.platform != 'win32':
                raise RuntimeError('Automatic executable rebuilds require Windows. Source installations can use Git and their Python environment.')
            root, app_dir = checkout_location()
            plan = check_update(root, app_dir)
            self.checked.emit((plan, find_python(root)))
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.plan = self.python = None
        self.setWindowTitle('Update application')
        layout = QVBoxLayout(self)
        self.status = QLabel('Checking the current branch for updates…')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self.install = QPushButton('Back up, update and rebuild')
        self.install.setEnabled(False)
        self.install.clicked.connect(self._install)
        layout.addWidget(self.install)
        self.close_button = QPushButton('Close')
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)
        fit_window(self, 520, 260)
        self.worker = UpdateCheck(self)
        self.worker.checked.connect(self._ready)
        self.worker.failed.connect(self.status.setText)
        self.worker.finished.connect(self._checked)
        self.close_button.setEnabled(False)
        self.worker.start()

    def reject(self):
        if self.worker.isRunning():
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)

    def _ready(self, result):
        self.plan, self.python = result
        message = f"Branch: {self.plan['branch']}\n{self.plan['count']} new commit(s). "
        self.status.setText(message + 'Updating will close billing, back up your database, rebuild the executable and reopen it. '
                            'The old executable is kept if the build fails. You can rebuild even when the source is already current.')
    def _checked(self):
        self.close_button.setEnabled(True)
        self.install.setEnabled(self.plan is not None)

    def _install(self):
        if not self.db.session.is_admin or self.plan is None:
            return
        if QMessageBox.question(self, 'Update and restart', 'Back up the database and close billing to install this update?',
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.db.backup_database()
            start_update(self.plan, self.python)
        except Exception as exc:
            QMessageBox.warning(self, 'Could not start update', str(exc))
            return
        self.accept()
        QApplication.instance().quit()
