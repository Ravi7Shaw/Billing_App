"""Stream complete filtered CSVs; replace the destination only after success."""
import csv
import os
from pathlib import Path
import sqlite3
import tempfile
from PySide6.QtWidgets import QFileDialog, QMessageBox


def export_csv(parent, title, filename, headers, rows):
    temporary = None
    try:
        path, _ = QFileDialog.getSaveFileName(parent, title, filename, 'CSV Files (*.csv)')
        if not path:
            return
        with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8-sig',
                                         dir=Path(path).parent, prefix='.billing-export-',
                                         suffix='.tmp', delete=False) as stream:
            temporary = stream.name
            writer = csv.writer(stream)
            writer.writerow(headers)
            for row in rows:
                # User-entered names/notes are text, never spreadsheet formulas.
                writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v for v in row])
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except (OSError, sqlite3.Error, ValueError) as exc:
        QMessageBox.warning(parent, 'Export failed', str(exc))
        return
    finally:
        if hasattr(rows, 'close'):
            rows.close()
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    QMessageBox.information(parent, 'Exported', f'Saved to:\n{path}')
