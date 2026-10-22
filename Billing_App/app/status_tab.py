import os
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QMessageBox,
    QFileDialog,
)


class StatusTab(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("Application Status")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        # ---------------- Database backup ----------------
        backup_title = QLabel("Database Backup")
        backup_title.setObjectName("sectionTitle")
        layout.addWidget(backup_title)

        backup_row = QHBoxLayout()
        self.backup_button = QPushButton("Backup Database Now")
        self.backup_button.setToolTip(
            "Copies the live database to a timestamped file. Safe to run "
            "any time, even while billing."
        )
        self.backup_button.clicked.connect(self.backup_database_now)
        backup_row.addWidget(self.backup_button)

        self.export_csv_button = QPushButton("Export Whole Database (CSV)")
        self.export_csv_button.setProperty("role", "secondary")
        self.export_csv_button.setToolTip(
            "Exports every table (bills, items, customers, expenses, "
            "payments, etc) to its own CSV file in a folder you choose."
        )
        self.export_csv_button.clicked.connect(self.export_database_csv)
        backup_row.addWidget(self.export_csv_button)
        backup_row.addStretch()
        layout.addLayout(backup_row)

        self.backup_info_label = QLabel(
            "" if self.db is None else f"Live database: {self.db.path}"
        )
        self.backup_info_label.setStyleSheet("color: #6c757d; font-size: 11px;")
        self.backup_info_label.setWordWrap(True)
        layout.addWidget(self.backup_info_label)

        log_title = QLabel("Backup / Export Activity")
        log_title.setObjectName("sectionTitle")
        layout.addWidget(log_title)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Backup and export results will appear here...")
        layout.addWidget(self.output, 1)

    def append_output(self, text):
        self.output.append(text)

    def backup_database_now(self):
        if self.db is None:
            QMessageBox.warning(self, "No database", "No database is connected.")
            return
        try:
            path = self.db.backup_database()
        except Exception as exc:
            QMessageBox.warning(self, "Backup failed", str(exc))
            self.append_output(f"Backup failed: {exc}")
            return
        self.append_output(f"Backup saved: {path}")
        QMessageBox.information(
            self,
            "Backup complete",
            f"A copy of the database was saved to:\n\n{path}",
        )

    def export_database_csv(self):
        if self.db is None:
            QMessageBox.warning(self, "No database", "No database is connected.")
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Choose a folder to export CSV files into"
        )
        if not folder:
            return
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        export_dir = os.path.join(folder, f"cloth_shop_csv_export_{stamp}")
        try:
            files = self.db.export_all_tables_csv(export_dir)
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            self.append_output(f"CSV export failed: {exc}")
            return
        self.append_output(f"Exported {len(files)} CSV file(s) to: {export_dir}")
        QMessageBox.information(
            self,
            "Export complete",
            f"Exported {len(files)} table(s) as CSV to:\n\n{export_dir}",
        )
