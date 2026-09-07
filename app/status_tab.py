import os
import sys
import socket

from PySide6.QtCore import QProcess, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
)


class StatusTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.server_process = None
        self.is_online = False
        self.health_timer = QTimer(self)
        self.health_timer.setInterval(1000)
        self.health_timer.timeout.connect(self.check_server)
        self._build_ui()

    def show_qr_code(self):
        # run_server.py saves the phone-connection QR here in source mode.
        if getattr(sys, "frozen", False):
            qr_path = os.path.join(os.path.dirname(sys.executable), "server_qr.png")
        else:
            qr_path = os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "clothshop_billing_server",
                    "server_qr.png",
                )
            )

        if not os.path.exists(qr_path):
            self.qr_label.setVisible(False)
            self.append_output(f"QR image not found: {qr_path}")
            return

        pixmap = QPixmap(qr_path)
        if pixmap.isNull():
            self.qr_label.setVisible(False)
            self.append_output(f"ERROR: Could not load QR image: {qr_path}")
            return

        scaled = pixmap.scaled(
            260,
            260,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.qr_label.setPixmap(scaled)
        self.qr_label.setVisible(True)

    def _build_ui(self):
        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(280, 280)
        self.qr_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(self.qr_label)

        title = QLabel("Application Status")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        status_row = QHBoxLayout()
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #dc3545; font-size: 22px;")
        self.status_label = QLabel("OFFLINE")
        self.status_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        layout.addLayout(status_row)

        self.server_info = QLabel("FastAPI Server\nServer is currently stopped.")
        layout.addWidget(self.server_info)

        button_row = QHBoxLayout()
        self.start_button = QPushButton("Start Server")
        self.start_button.clicked.connect(self.start_server)
        self.stop_button = QPushButton("Stop Server")
        self.stop_button.clicked.connect(self.stop_server)
        self.stop_button.setEnabled(False)
        self.open_button = QPushButton("Open Browser")
        self.open_button.clicked.connect(self.open_browser)
        self.open_button.setEnabled(False)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.open_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        log_title = QLabel("Server Output / Application Exceptions")
        log_title.setObjectName("sectionTitle")
        layout.addWidget(log_title)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Server output will appear here...")
        layout.addWidget(self.output, 1)

    def start_server(self):
        if (
            self.server_process is not None
            and self.server_process.state() != QProcess.ProcessState.NotRunning
        ):
            self.append_output("Server process is already running.")
            return

        if self._server_is_reachable():
            self.set_online()
            self.append_output("FastAPI server is already running on port 5000.")
            return

        if getattr(sys, "frozen", False):
            program = sys.executable
            arguments = ["--run-server"]
            working_directory = os.path.dirname(sys.executable)
            self.append_output("Starting FastAPI server from BillingApp.exe...")
        else:
            server_script = os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "clothshop_billing_server",
                    "run_server.py",
                )
            )
            if not os.path.exists(server_script):
                self.append_output(f"ERROR: Server script not found:\n{server_script}")
                return
            program = sys.executable
            arguments = [server_script]
            working_directory = os.path.dirname(server_script)
            self.append_output(f"Starting FastAPI server:\n{server_script}\n")

        self.server_process = QProcess(self)
        self.server_process.setProgram(program)
        self.server_process.setArguments(arguments)
        self.server_process.setWorkingDirectory(working_directory)
        self.server_process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self.server_process.readyReadStandardOutput.connect(self.read_server_output)
        self.server_process.finished.connect(self.server_finished)
        self.server_process.errorOccurred.connect(self.server_error)
        self.server_process.start()

        if not self.server_process.waitForStarted(3000):
            self.append_output("ERROR: FastAPI server failed to start.")
            self.server_process = None
            return

        self.append_output("Server process started.")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.health_timer.start()
        self.set_starting()

    def stop_server(self):
        if (
            self.server_process is None
            or self.server_process.state() == QProcess.ProcessState.NotRunning
        ):
            self.set_offline()
            return
        self.append_output("Stopping FastAPI server...")
        self.server_process.terminate()
        if not self.server_process.waitForFinished(3000):
            self.append_output("Server did not stop gracefully. Killing process...")
            self.server_process.kill()
            self.server_process.waitForFinished(2000)
        self.set_offline()

    def read_server_output(self):
        if self.server_process is None:
            return
        data = self.server_process.readAllStandardOutput()
        text = bytes(data).decode("utf-8", errors="replace")
        if text:
            self.append_output(text.rstrip())

    def server_finished(self, exit_code, exit_status):
        self.append_output(f"FastAPI server stopped (exit code={exit_code}).")
        self.set_offline()

    def server_error(self, error):
        self.append_output(f"FastAPI process error: {error}")
        self.set_offline()

    def check_server(self):
        if self._server_is_reachable():
            self.set_online()
        elif (
            self.server_process is not None
            and self.server_process.state() != QProcess.ProcessState.NotRunning
        ):
            # Uvicorn can take a moment to bind port 5000. Do NOT mark it offline
            # or stop the timer while the child process is still starting.
            self.set_starting()
        else:
            self.set_offline()

    def _server_is_reachable(self):
        try:
            sock = socket.create_connection(("127.0.0.1", 5000), timeout=0.3)
            sock.close()
            return True
        except OSError:
            return False

    def set_starting(self):
        self.is_online = False
        self.status_dot.setStyleSheet("color: #d99a00; font-size: 22px;")
        self.status_label.setText("STARTING")
        self.server_info.setText("FastAPI Server\nWaiting for Uvicorn on port 5000...")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.open_button.setEnabled(False)

    def set_online(self):
        if self.is_online:
            return
        self.is_online = True
        self.status_dot.setStyleSheet("color: #28a745; font-size: 22px;")
        self.status_label.setText("ONLINE")
        lan_ip = self._get_lan_ip()
        self.server_info.setText(f"FastAPI Server\nhttp://{lan_ip}:5000")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.show_qr_code()
        self.health_timer.start()

    def set_offline(self):
        self.is_online = False
        self.status_dot.setStyleSheet("color: #dc3545; font-size: 22px;")
        self.status_label.setText("OFFLINE")
        self.server_info.setText("FastAPI Server\nServer is currently stopped.")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.qr_label.clear()
        self.qr_label.setVisible(False)
        self.health_timer.stop()

    def open_browser(self):
        url = f"http://{self._get_lan_ip()}:5000"
        QDesktopServices.openUrl(QUrl(url))

    def _get_lan_ip(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            sock.close()
            return ip
        except OSError:
            return "127.0.0.1"

    def append_output(self, text):
        self.output.append(text)

    def shutdown_server(self):
        if (
            self.server_process is None
            or self.server_process.state() == QProcess.ProcessState.NotRunning
        ):
            return
        self.append_output("Application closing. Stopping FastAPI server...")
        self.server_process.terminate()
        if not self.server_process.waitForFinished(3000):
            self.server_process.kill()
            self.server_process.waitForFinished(2000)
