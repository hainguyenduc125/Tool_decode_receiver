"""Widget giao diện cho mạch thu riêng (COM 2) — đọc log LoRa riêng song song
với gateway để sau đó đối chiếu.

Dùng chung `SerialWorker` (thread đọc serial) và `SerialLogParser` (parse bằng
logic của gateway) — không viết lại parser. Mỗi `LoraRxEvent` được đẩy vào
`SourceComparator` (cung cấp từ ngoài) để so với gateway sau khi test kết thúc.

Việc "thu" vào comparator/CSV chỉ xảy ra trong cửa sổ test (bật bằng
`set_recording(True)` lúc START, tắt lúc freeze/CLEAR) để hai nguồn (mạch thu
riêng vs gateway) cùng một mốc thời gian — tránh missing giả do thu lệch cửa sổ.
Ngoài cửa sổ test, widget chỉ hiển thị log raw, không đẩy vào so sánh.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
)

from core.source_comparator import SourceComparator  # noqa: F401  (re-export)
from lora_io.lora_parser import SerialLogParser
from lora_io.serial_manager import SerialWorker, list_serial_ports
from models.message import LoraRxEvent, SerialNotice  # noqa: F401
from ui.log_viewer import LogViewer


class SerialSourceWidget(QGroupBox):
    """Khung điều khiển + hiển thị một nguồn serial LoRa riêng."""

    def __init__(self, title: str, default_baud: int = 115200,
                 comparator: SourceComparator | None = None,
                 on_rx_event=None, parent=None) -> None:
        super().__init__(title, parent)
        self.comparator = comparator
        self.on_rx_event = on_rx_event     # callable(LoraRxEvent) khi đang thu
        self._recording = False            # thu vào comparator/CSV theo cửa sổ test
        self.parser = SerialLogParser()
        self.worker: SerialWorker | None = None

        f = QFormLayout(self)
        self.com_combo = QComboBox()
        self.com_combo.setEditable(True)
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["115200", "9600", "57600", "230400", "460800"])
        self.baud_combo.setCurrentText(str(default_baud))
        self.bt_detect = QPushButton("Detect COM")
        self.bt_detect.clicked.connect(self.on_detect)
        self.bt_toggle = QPushButton("Connect")
        self.bt_toggle.clicked.connect(self.on_toggle)
        row_bt = QHBoxLayout()
        row_bt.addWidget(self.bt_detect)
        row_bt.addWidget(self.bt_toggle)
        f.addRow("COM Port:", self.com_combo)
        f.addRow("Baudrate:", self.baud_combo)
        f.addRow(row_bt)
        self.lbl_status = QLabel("DISCONNECTED")
        f.addRow("Trạng thái:", self.lbl_status)
        self.lbl_uid = QLabel("-")
        self.lbl_uid.setStyleSheet("font-weight: bold; color: #007acc;")
        f.addRow("Node UID:", self.lbl_uid)
        self.view = LogViewer(max_lines=5000)
        f.addRow(self.view)

    # -- API -----------------------------------------------------------------
    def set_recording(self, on: bool) -> None:
        """Bật/tắt thu data frame vào comparator + callback CSV theo cửa sổ test.

        Gọi từ MainWindow: True lúc START, False lúc freeze/settle, CLEAR, đóng.
        """
        self._recording = bool(on)

    def on_detect(self) -> None:
        ports = list_serial_ports()
        self.com_combo.clear()
        self.com_combo.addItems(ports)
        if ports:
            self.com_combo.setCurrentIndex(0)
        self.lbl_status.setText("Detect: %d cổng" % len(ports))

    def on_toggle(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker = None
            self._recording = False
            self.lbl_status.setText("DISCONNECTED")
            return
        port = self.com_combo.currentText().strip()
        if not port:
            self.lbl_status.setText("Chưa chọn COM port")
            return
        self._start_worker(port)

    def ensure_connected(self) -> bool:
        """Tự kết nối COM 2 (mạch thu riêng) nếu đã chọn COM mà chưa connect.

        Dùng khi bấm START TEST để cả 2 luồng đối chiếu chạy song song ngay.
        Việc connect là bất đồng bộ (QThread) — data từ lúc worker CONNECTED sẽ
        vào comparator nhờ _recording đang bật trong cửa sổ test. Trả True nếu
        đã có worker chạy (hoặc vừa khởi động connect), False nếu chưa chọn COM
        / connect thất bại ngay.
        """
        if self.worker and self.worker.isRunning():
            return True
        port = self.com_combo.currentText().strip()
        if not port:
            return False
        # Tự connect: không reset parser (giữ UID đã biết), chỉ bật connect.
        self._start_worker(port)
        # Worker mới start → có thể chưa CONNECTED ngay; coi như đang tiến hành.
        return self.worker is not None

    def _start_worker(self, port: str) -> None:
        baud = int(self.baud_combo.currentText())
        self.parser.reset()
        self.worker = SerialWorker(port, baud, self)
        self.worker.line.connect(self._on_line)
        self.worker.status.connect(self._on_status)
        self.worker.start()
        self.bt_toggle.setText("Disconnect")

    def stop(self) -> None:
        if self.worker:
            self.worker.stop()
            self.worker = None
        self._recording = False

    def reset(self) -> None:
        self.parser.reset()
        self.lbl_uid.setText("-")

    # -- Internal -------------------------------------------------------------
    def _on_status(self, text: str) -> None:
        if text == "CONNECTED":
            self.lbl_status.setText("CONNECTED")
        elif text.startswith("CONNECTING"):
            self.lbl_status.setText("CONNECTING ...")
        else:
            self.lbl_status.setText(text)
        self.bt_toggle.setText("Disconnect" if self.worker and self.worker.isRunning() else "Connect")

    def _on_line(self, line: str) -> None:
        self.view.append_line(line)
        for evt in self.parser.feed(line, ""):
            if isinstance(evt, LoraRxEvent):
                # Luôn hiển thị UID/packet lên widget; chỉ thu vào comparator
                # và CSV khi đang trong cửa sổ test (recording on).
                self.lbl_uid.setText(evt.node_uuid or "-")
                if self._recording:
                    if self.comparator is not None:
                        self.comparator.add_second_source(evt)
                    if self.on_rx_event is not None:
                        self.on_rx_event(evt)
            elif isinstance(evt, SerialNotice):
                if evt.kind == "module_info" and evt.uuid:
                    self.lbl_uid.setText(evt.uuid)