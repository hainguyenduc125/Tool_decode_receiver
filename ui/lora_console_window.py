"""Ứng dụng console UART độc lập để xem và giải mã bản tin LoRa."""

from __future__ import annotations

import time

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.lora_frame import ConsoleDecode, decode_console_line
from lora_io.serial_manager import SerialWorker, list_serial_ports


class LoraConsoleWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LoRa Console & Decoder")
        self.resize(1180, 760)
        self.worker: SerialWorker | None = None
        self._build_ui()
        self._detect_ports()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Cổng COM:"))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumWidth(130)
        controls.addWidget(self.port_combo)

        self.detect_button = QPushButton("Quét COM")
        self.detect_button.clicked.connect(self._detect_ports)
        controls.addWidget(self.detect_button)

        controls.addWidget(QLabel("Baudrate:"))
        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(["115200", "9600", "57600", "230400", "460800"])
        self.baud_combo.setCurrentText("115200")
        self.baud_combo.setMaximumWidth(110)
        controls.addWidget(self.baud_combo)

        self.connect_button = QPushButton("Kết nối")
        self.connect_button.clicked.connect(self._toggle_connection)
        controls.addWidget(self.connect_button)
        self.status_label = QLabel("Chưa kết nối")
        self.status_label.setMinimumWidth(180)
        controls.addWidget(self.status_label)
        controls.addStretch(1)

        self.save_button = QPushButton("Lưu console")
        self.save_button.clicked.connect(self._save_console)
        controls.addWidget(self.save_button)
        self.clear_console_button = QPushButton("Xóa console")
        self.clear_console_button.clicked.connect(self._clear_console)
        controls.addWidget(self.clear_console_button)
        layout.addLayout(controls)

        uid_filter_row = QHBoxLayout()
        uid_filter_row.addWidget(QLabel("Lọc UID node:"))
        self.uid_filter = QLineEdit()
        self.uid_filter.setClearButtonEnabled(True)
        self.uid_filter.setPlaceholderText("Để trống để giải mã mọi UID")
        self.uid_filter.setToolTip(
            "Chỉ hiển thị kết quả giải mã có UID trùng khớp; không phân biệt chữ hoa/thường."
        )
        uid_filter_row.addWidget(self.uid_filter, 1)
        layout.addLayout(uid_filter_row)

        splitter = QSplitter(Qt.Horizontal)
        console_panel = QWidget()
        console_layout = QVBoxLayout(console_panel)
        console_layout.setContentsMargins(0, 0, 0, 0)
        console_layout.addWidget(QLabel("Console UART LoRa"))
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(10000)
        self.console.setFont(QFont("Consolas", 10))
        console_layout.addWidget(self.console)
        splitter.addWidget(console_panel)

        result_panel = QWidget()
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.addWidget(QLabel("Kết quả giải mã"))
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setFont(QFont("Consolas", 10))
        self.result.setPlaceholderText(
            "Bản tin LoRa nhận được sẽ tự giải mã tại đây.\n"
            "Có thể dán HEX hoặc record D:/L: ở ô bên dưới."
        )
        result_layout.addWidget(self.result)
        splitter.addWidget(result_panel)
        splitter.setSizes([560, 560])
        layout.addWidget(splitter, 1)

        layout.addWidget(QLabel("Dán HEX / +EVT:RXP2P: / D: / L: để giải mã thủ công"))
        self.decode_input = QPlainTextEdit()
        self.decode_input.setMaximumHeight(105)
        self.decode_input.setFont(QFont("Consolas", 10))
        self.decode_input.setPlaceholderText(
            "Mỗi dòng một frame hoặc record; có thể dán nhiều dòng cùng lúc."
        )
        layout.addWidget(self.decode_input)

        actions = QHBoxLayout()
        self.decode_button = QPushButton("Giải mã")
        self.decode_button.clicked.connect(self._decode_pasted)
        actions.addWidget(self.decode_button)
        self.clear_input_button = QPushButton("Xóa dữ liệu nhập")
        self.clear_input_button.clicked.connect(self.decode_input.clear)
        actions.addWidget(self.clear_input_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.setCentralWidget(root)

    def _detect_ports(self) -> None:
        current = self.port_combo.currentText().strip()
        ports = list_serial_ports()
        self.port_combo.clear()
        self.port_combo.addItems(ports)
        if current:
            self.port_combo.setCurrentText(current)
        elif ports:
            self.port_combo.setCurrentIndex(0)
        self.status_label.setText(f"Tìm thấy {len(ports)} cổng COM")

    def _toggle_connection(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker = None
            self.connect_button.setText("Kết nối")
            self.status_label.setText("Đã ngắt kết nối")
            return

        port = self.port_combo.currentText().strip()
        if not port:
            QMessageBox.warning(self, "Chưa chọn cổng", "Chọn hoặc nhập cổng COM trước.")
            return
        try:
            baudrate = int(self.baud_combo.currentText().strip())
            if baudrate <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Baudrate không hợp lệ", "Nhập baudrate dạng số dương.")
            return

        self.worker = SerialWorker(port, baudrate, self)
        self.worker.line.connect(self._on_serial_line)
        self.worker.status.connect(self._on_serial_status)
        self.worker.start()
        self.connect_button.setText("Ngắt kết nối")
        self.status_label.setText(f"Đang kết nối {port}...")

    def _on_serial_status(self, status: str) -> None:
        self.status_label.setText(status)
        if status.startswith("ERROR:") or status.startswith("DISCONNECTED"):
            self.connect_button.setText("Kết nối")
            if self.worker and not self.worker.isRunning():
                self.worker = None

    def _on_serial_line(self, line: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.console.appendPlainText(f"[{timestamp}] {line}")

        if line.startswith(("+EVT:RXP2P:", "D:", "L:")) or "DATA:" in line:
            self._show_decoded(line)

    def _uid_filter_value(self) -> str:
        value = self.uid_filter.text().strip().upper()
        for separator in (" ", "\t", "\r", "\n", ":", "-"):
            value = value.replace(separator, "")
        if value and any(char not in "0123456789ABCDEF" for char in value):
            raise ValueError("UID lọc chỉ được chứa các ký tự HEX (0-9, A-F).")
        return value

    def _decode_pasted(self) -> None:
        lines = [line.strip() for line in self.decode_input.toPlainText().splitlines() if line.strip()]
        if not lines:
            self.result.setPlainText("Chưa có dữ liệu để giải mã.")
            return
        try:
            uid_filter = self._uid_filter_value()
        except ValueError as exc:
            self.result.setPlainText(f"Bộ lọc UID không hợp lệ: {exc}")
            return

        results = []
        for index, line in enumerate(lines, start=1):
            try:
                decoded = decode_console_line(line)
                if uid_filter and decoded.packet.node_uuid.upper() != uid_filter:
                    continue
                title = f"Frame {index}" if len(lines) > 1 else "Frame"
                results.append(self._format_result(decoded, title))
            except ValueError as exc:
                results.append(f"Frame {index if len(lines) > 1 else ''} — lỗi đầu vào: {exc}")
        if not results and uid_filter:
            self.result.setPlainText(f"Không có bản tin nào khớp UID node {uid_filter}.")
            return
        self.result.setPlainText("\n\n" + ("\n" + "=" * 55 + "\n\n").join(results))

    def _show_decoded(self, line: str) -> None:
        try:
            uid_filter = self._uid_filter_value()
        except ValueError as exc:
            self.result.setPlainText(f"Bộ lọc UID không hợp lệ: {exc}")
            return
        try:
            decoded = decode_console_line(line)
        except ValueError as exc:
            self.result.setPlainText(f"Không giải mã được bản tin UART:\n{exc}")
            return
        if uid_filter and decoded.packet.node_uuid.upper() != uid_filter:
            return
        self.result.setPlainText(self._format_result(decoded, "Bản tin trực tiếp"))

    @staticmethod
    def _format_result(decoded: ConsoleDecode, title: str) -> str:
        packet = decoded.packet
        crc_text = "HỢP LỆ" if packet.crc_ok else "KHÔNG HỢP LỆ / KHÔNG XÁC ĐỊNH"
        lines = [
            title,
            f"CRC: {crc_text}",
            f"Layout: {packet.layout}",
            f"Loại: {packet.kind}",
            f"Độ dài: {packet.length} byte",
        ]
        if packet.node_uuid:
            lines.append(f"UID/UUID: {packet.node_uuid}")
        if packet.seq is not None:
            lines.append(f"SEQ: {packet.seq}")
        if decoded.rssi is not None:
            lines.append(f"RSSI: {decoded.rssi} dBm")
        if decoded.snr is not None:
            lines.append(f"SNR: {decoded.snr}")
        if packet.crc_calculated is not None and packet.crc_received is not None:
            lines.append(
                f"CRC tính: 0x{packet.crc_calculated:04X} | "
                f"CRC nhận: 0x{packet.crc_received:04X}"
            )
        if packet.fields:
            lines.extend(["", "Trường dữ liệu:"])
            lines.extend(f"  {name}: {value}" for name, value in packet.fields)
        if packet.notes:
            lines.extend(["", "Ghi chú:"])
            lines.extend(f"  - {note}" for note in packet.notes)
        return "\n".join(lines)

    def _save_console(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Lưu console UART", "lora_console.txt", "Text files (*.txt);;All files (*)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as output:
                output.write(self.console.toPlainText())

    def _clear_console(self) -> None:
        self.console.clear()
        self.result.clear()

    def closeEvent(self, event) -> None:
        if self.worker:
            self.worker.stop()
            self.worker = None
        event.accept()
