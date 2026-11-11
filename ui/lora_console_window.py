"""Ứng dụng console UART độc lập để xem và giải mã bản tin LoRa."""

from __future__ import annotations

import re
import time
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox,
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import load_config
from core.lora_frame import ConsoleDecode, decode_console_line
from core.node_stats import (
    NodeStatsStore,
    ReceivedFrame,
    SEQ_PARITIES,
    SeqParity,
    record_matches_filters,
    record_uid_for_line,
    seq_parity,
)
from lora_io.serial_manager import SerialWorker, list_serial_ports
from ui.node_stats_panel import NodeStatsPanel
from ui.received_table import ReceivedMessageTable


class ClickableConsole(QPlainTextEdit):
    lineClicked = pyqtSignal(str)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.lineClicked.emit(self.cursorForPosition(event.pos()).block().text())


class LoraConsoleWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LoRa Console & Decoder")
        self.resize(1180, 760)
        self.worker: SerialWorker | None = None
        self._last_live_decoded: ConsoleDecode | None = None
        self._last_live_parity: SeqParity = "Không SEQ"
        self._last_live_uid = ""
        self.cfg = load_config()
        self.stats_store = NodeStatsStore(
            max_frames=int(self.cfg["ui"].get("table_max_rows", 50000))
        )
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

        self.save_button = QPushButton("Chụp kết quả")
        self.save_button.clicked.connect(self._capture_result)
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
        self.uid_filter.textChanged.connect(self._refresh_live_result)
        uid_filter_row.addWidget(self.uid_filter, 1)
        uid_filter_row.addWidget(QLabel("SEQ:"))
        self.parity_filters: dict[SeqParity, QCheckBox] = {}
        for parity in SEQ_PARITIES:
            checkbox = QCheckBox(f"{parity} (0)")
            checkbox.setChecked(True)
            checkbox.stateChanged.connect(self._on_parity_filter_changed)
            self.parity_filters[parity] = checkbox
            uid_filter_row.addWidget(checkbox)
        layout.addLayout(uid_filter_row)

        splitter = QSplitter(Qt.Horizontal)
        received_panel = QWidget()
        received_layout = QVBoxLayout(received_panel)
        received_layout.setContentsMargins(0, 0, 0, 0)
        received_layout.addWidget(QLabel("Bản tin nhận được"))
        self.received_tabs = QTabWidget()
        self.received_table = ReceivedMessageTable(
            max_rows=int(self.cfg["ui"].get("table_max_rows", 50000))
        )
        self.received_table.lineSelected.connect(self._decode_selected_record)
        self.uid_filter.textChanged.connect(self._refresh_record_filters)
        self.received_tabs.addTab(self.received_table, "Bản tin")
        self.console = ClickableConsole()
        self.console.lineClicked.connect(self._decode_console_line)
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(10000)
        self.console.setFont(QFont("Consolas", 10))
        self.received_tabs.addTab(self.console, "Console UART thô")
        received_layout.addWidget(self.received_tabs)
        self._refresh_record_filters()
        splitter.addWidget(received_panel)

        manual_panel = QWidget()
        manual_layout = QVBoxLayout(manual_panel)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.addWidget(QLabel("Giải mã thủ công"))
        self.decode_input = QPlainTextEdit()
        self.decode_input.setFont(QFont("Consolas", 10))
        self.decode_input.setPlaceholderText(
            "Dán HEX, +EVT:RXP2P:, DATA:, D: hoặc L:; mỗi dòng một record."
        )
        manual_layout.addWidget(self.decode_input, 1)
        actions = QHBoxLayout()
        self.decode_button = QPushButton("Giải mã")
        self.decode_button.clicked.connect(self._decode_pasted)
        actions.addWidget(self.decode_button)
        self.clear_input_button = QPushButton("Xóa")
        self.clear_input_button.clicked.connect(self.decode_input.clear)
        actions.addWidget(self.clear_input_button)
        actions.addStretch(1)
        manual_layout.addLayout(actions)
        manual_layout.addWidget(QLabel("Kết quả giải mã"))
        self.manual_result = QPlainTextEdit()
        self.manual_result.setReadOnly(True)
        self.manual_result.setFont(QFont("Consolas", 10))
        manual_layout.addWidget(self.manual_result, 2)
        splitter.addWidget(manual_panel)

        result_panel = QWidget()
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.addWidget(QLabel("Kết quả trực tiếp"))
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setFont(QFont("Consolas", 10))
        self.result.setPlaceholderText("Bản tin LoRa nhận được sẽ tự giải mã tại đây.")
        result_layout.addWidget(self.result)
        splitter.addWidget(result_panel)
        splitter.setSizes([480, 380, 360])
        layout.addWidget(splitter, 1)
        self.node_stats_panel = NodeStatsPanel()
        self.node_stats_panel.hide()
        self.node_stats_panel.resetBaselineRequested.connect(self._reset_stats_baseline)
        self.node_stats_panel.pairWindowChanged.connect(self._refresh_node_stats)
        layout.addWidget(self.node_stats_panel)
        self.uid_filter.textChanged.connect(self._update_node_stats_visibility)
        self.stats_timer = QTimer(self)
        self.stats_timer.setInterval(500)
        self.stats_timer.timeout.connect(self._refresh_node_stats)
        self.stats_timer.start()
        self.setCentralWidget(root)
        self._update_node_stats_visibility()

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

        if self._is_uart_record(line):
            self._show_decoded(line)

    @staticmethod
    def _is_uart_record(line: str) -> bool:
        return line.startswith(("[RX]", "+EVT:RXP2P:", "D:", "L:")) or "DATA:" in line

    def _decode_selected_record(self, line: str, parity: str, timestamp: str) -> None:
        self.decode_input.setPlainText(line)
        try:
            decoded = decode_console_line(line)
        except ValueError as exc:
            self.manual_result.setPlainText(
                f"Bản tin {parity} – {timestamp}\n\nLỗi giải mã:\n{exc}"
            )
            return
        self.manual_result.setPlainText(
            self._format_result(decoded, f"Bản tin {parity} – {timestamp}")
        )

    def _decode_console_line(self, displayed_line: str) -> None:
        line = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s*", "", displayed_line, count=1)
        if not self._is_uart_record(line):
            return
        self.decode_input.setPlainText(line)
        try:
            decoded = decode_console_line(line)
        except ValueError as exc:
            self.manual_result.setPlainText(f"Lỗi giải mã bản tin UART:\n{exc}")
            return
        parity = seq_parity(decoded)
        timestamp = time.strftime("%H:%M:%S")
        self.manual_result.setPlainText(
            self._format_result(decoded, f"Bản tin {parity} – {timestamp}")
        )

    def _uid_filter_value(self) -> str:
        value = self.uid_filter.text().strip().upper()
        for separator in (" ", "\t", "\r", "\n", ":", "-"):
            value = value.replace(separator, "")
        if value and any(char not in "0123456789ABCDEF" for char in value):
            raise ValueError("UID lọc chỉ được chứa các ký tự HEX (0-9, A-F).")
        return value

    def _selected_parities(self) -> set[SeqParity]:
        return {
            parity for parity, checkbox in self.parity_filters.items()
            if checkbox.isChecked()
        }

    def _on_parity_filter_changed(self) -> None:
        if not self._selected_parities():
            checkbox = self.sender()
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(True)
        self._refresh_record_filters()
        self._refresh_live_result()
        self._refresh_node_stats()

    def _refresh_record_filters(self) -> None:
        if not hasattr(self, "received_table"):
            return
        try:
            uid_filter = self._uid_filter_value()
        except ValueError:
            uid_filter = "\0"
        self.received_table.apply_filters(uid_filter, self._selected_parities())
        self._update_parity_count_labels()

    def _update_parity_count_labels(self) -> None:
        counts = self.received_table.filter_counts
        for parity, checkbox in self.parity_filters.items():
            checkbox.setText(f"{parity} ({counts[parity]})")

    def _update_node_stats_visibility(self) -> None:
        if not hasattr(self, "node_stats_panel"):
            return
        try:
            uid = self._uid_filter_value()
        except ValueError:
            uid = ""
        if not uid:
            self.node_stats_panel.hide()
            return
        self.node_stats_panel.set_uid(uid)
        self.node_stats_panel.show()
        self._refresh_node_stats()

    def _refresh_node_stats(self) -> None:
        if not hasattr(self, "node_stats_panel") or self.node_stats_panel.isHidden():
            return
        try:
            uid = self._uid_filter_value()
        except ValueError:
            return
        if not uid:
            return
        parities = self._selected_parities()
        stats = self.stats_store.compute(uid, parities, time.time())
        pair_stats = (
            self.stats_store.pair_even_odd(
                uid, self.node_stats_panel.pair_window_spin.value()
            )
            if {"Chẵn", "Lẻ"}.issubset(parities)
            else None
        )
        self.node_stats_panel.refresh(stats, pair_stats, parities)

    def _reset_stats_baseline(self) -> None:
        try:
            uid = self._uid_filter_value()
        except ValueError:
            return
        if uid:
            self.stats_store.reset(uid)
            self._refresh_node_stats()

    def _decode_pasted(self) -> None:
        lines = [line.strip() for line in self.decode_input.toPlainText().splitlines() if line.strip()]
        if not lines:
            self.manual_result.setPlainText("Chưa có dữ liệu để giải mã.")
            return
        try:
            uid_filter = self._uid_filter_value()
        except ValueError as exc:
            self.manual_result.setPlainText(f"Bộ lọc UID không hợp lệ: {exc}")
            return

        results = []
        for index, line in enumerate(lines, start=1):
            try:
                decoded = decode_console_line(line)
                parity = seq_parity(decoded)
                if (
                    not record_matches_filters(
                        record_uid_for_line(line, decoded),
                        parity,
                        uid_filter,
                        self._selected_parities(),
                    )
                ):
                    continue
                title = f"Frame {index}" if len(lines) > 1 else "Frame"
                results.append(self._format_result(decoded, title))
            except ValueError as exc:
                results.append(f"Frame {index if len(lines) > 1 else ''} — lỗi đầu vào: {exc}")
        if not results and (uid_filter or self._selected_parities() != set(SEQ_PARITIES)):
            self.manual_result.setPlainText("Không có bản tin nào khớp bộ lọc hiện tại.")
            return
        self.manual_result.setPlainText("\n\n" + ("\n" + "=" * 55 + "\n\n").join(results))

    def _show_decoded(self, line: str) -> None:
        try:
            decoded = decode_console_line(line)
        except ValueError as exc:
            decoded = None
            error = str(exc)
        else:
            error = None
        ts = time.time()
        parity = seq_parity(decoded)
        received_frame = ReceivedFrame(
            ts, line, parity, record_uid_for_line(line, decoded), decoded, error
        )
        self.stats_store.add(received_frame)
        self.received_table.add_frames([received_frame])
        self._update_parity_count_labels()
        if decoded is None:
            self._last_live_decoded = None
            self.result.clear()
            return
        self._last_live_decoded = decoded
        self._last_live_parity = parity
        self._last_live_uid = received_frame.uid
        self._refresh_live_result()

    def _refresh_live_result(self) -> None:
        if self._last_live_decoded is None:
            return
        try:
            uid_filter = self._uid_filter_value()
        except ValueError as exc:
            self.result.setPlainText(f"Bộ lọc UID không hợp lệ: {exc}")
            return
        decoded = self._last_live_decoded
        if (
            not record_matches_filters(
                self._last_live_uid,
                self._last_live_parity,
                uid_filter,
                self._selected_parities(),
            )
        ):
            self.result.setPlainText(
                "Không có bản tin trực tiếp nào khớp bộ lọc hiện tại."
            )
            return
        self.result.setPlainText(
            self._format_result(
                decoded, f"Bản tin trực tiếp ({self._last_live_parity})"
            )
        )

    @staticmethod
    def _format_result(decoded: ConsoleDecode, title: str) -> str:
        packet = decoded.packet
        gateway_text = (
            "CHẤP NHẬN"
            if packet.gateway_result == "OK"
            else f"TỪ CHỐI: {packet.gateway_result}"
        )
        crc_text = "HỢP LỆ" if packet.crc_ok else "KHÔNG HỢP LỆ / KHÔNG XÁC ĐỊNH"
        lines = [
            title,
            f"Gateway Type0: {gateway_text}",
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

    def _capture_result(self) -> None:
        screenshot_dir = Path(__file__).resolve().parent.parent / "screenshots"
        try:
            screenshot_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QMessageBox.warning(
                self,
                "Không thể tạo thư mục",
                f"Không thể tạo thư mục lưu ảnh:\n{screenshot_dir}\n\n{error}",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Lưu ảnh chụp kết quả",
            str(screenshot_dir / "ket_qua.png"),
            "PNG images (*.png)",
        )
        if not path:
            return

        if Path(path).suffix.lower() != ".png":
            path += ".png"

        if not self.grab().save(path, "PNG"):
            QMessageBox.warning(
                self,
                "Không thể lưu ảnh",
                f"Không thể lưu ảnh chụp tại:\n{path}",
            )

    def _clear_console(self) -> None:
        self.console.clear()
        self.received_table.clear_records()
        self._update_parity_count_labels()
        self.stats_store = NodeStatsStore(
            max_frames=int(self.cfg["ui"].get("table_max_rows", 50000))
        )
        self.result.clear()
        self.manual_result.clear()
        self._last_live_decoded = None
        self._last_live_uid = ""
        self.decode_input.clear()
        self._refresh_node_stats()

    def closeEvent(self, event) -> None:
        if self.worker:
            self.worker.stop()
            self.worker = None
        event.accept()
