"""MainWindow — Gateway LoRa Message Counter (PyQt5).

Wiring: SerialWorker/MqttWorker (QThread) → queued signal → slot trên UI thread →
parser/counter. UI luôn responsive; raw log hiển thị giới hạn dòng, file session
lưu đầy đủ.
"""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import ensure_logs_dir, load_config, save_config
from core.message_counter import MessageCounter
from core.message_matcher import compute as compute_match, normalize_id as _normalize_id
from core.test_result import evaluate as evaluate_result, format_result_block
from core.test_session import TestSession
from models.message import (
    GatewayBootInfo,
    GatewayInfo,
    GatewayReport,
    LoraRxEvent,
    MatchResult,
    SessionSummary,
    SerialNotice,
    SRC_LORA,
    SRC_MQTT,
    SRC_REF,
    now_file_ts,
    now_pc_str,
    node_kind_from_id,
)
from core.gateway_api import gateway_status
from mqtt.mqtt_client import MqttWorker
from mqtt.mqtt_parser import MqttParser
from lora_io.lora_parser import SerialLogParser
from lora_io.serial_manager import SerialWorker, list_serial_ports
from ui.log_viewer import LogViewer
from ui.lora_decoder_widget import LoraDecoderWidget
from ui.message_table import (
    MessageLogTable,
    NodeCompareTable,
    NodeSummaryTable,
    SourceCompareTable,
)
from ui.serial_source_widget import SerialSourceWidget
from core.source_comparator import SourceComparator, normalize_uid
from core.compare_export import build_export as build_compare_export, write_csv as write_compare_csv

STATE_IDLE = "IDLE"
STATE_RUNNING = "RUNNING"
STATE_SETTLE = "WAIT_SETTLE"
STATE_DONE = "DONE"

# Kind (phần sau "src/") của bản tin LoRa KHÔNG hợp lệ — rác RF/nhiễu. Khi bật
# "Lọc rác" thì không đưa vào Message Log/CSV (vẫn đếm riêng ở diag để theo dõi).
GARBAGE_RX_KINDS = {"CRC_ERROR", "UNPARSED", "INCOMPLETE"}


class HttpStatusWorker(QThread):
    """Gọi GET http://<ip>/api/status ngoài luồng UI.

    Sau reboot gateway, firmware log IP lên Serial (Ethernet/WiFi) khá sớm.
    Tool dùng IP đó để gọi /api/status lấy uuid/model/series/firmware/mac mà
    không cần chờ bản tin [INFO] 60s của hệ thống.
    """

    done = pyqtSignal(object)   # GatewayInfo

    def __init__(self, ip: str, timeout_s: float = 3.0, parent=None) -> None:
        super().__init__(parent)
        self.ip = ip
        self.timeout_s = timeout_s

    def run(self) -> None:
        ok, data, err = gateway_status(self.ip, timeout_s=self.timeout_s)
        info = GatewayInfo(source="http")
        if ok:
            info.uuid = str(data.get("uuid", ""))
            info.model = str(data.get("model", ""))
            info.series = str(data.get("series", ""))
            info.firmware_version = str(data.get("firmware_version", ""))
            info.mac = str(data.get("eth_mac", "") or data.get("wifi_mac", ""))
            info.ip = self.ip
            info.mqtt_connected = bool(data.get("mqtt_connected"))
            info.extra = data
        else:
            info.uuid = ""
            info.extra = {"error": err, "ip": self.ip}
        self.done.emit(info)

_ROW_KEYS = [
    "pc_time", "source", "gateway_uuid", "node_uuid", "sequence",
    "rssi", "snr", "kind", "topic", "payload",
]


def _row_row_list(r: dict) -> list:
    """row dict → list cột hiển thị trên MessageLogTable (payload cắt gọn)."""
    payload = str(r.get("payload", ""))
    return [
        r.get("pc_time", ""),
        r.get("source", ""),
        r.get("gateway_uuid", ""),
        r.get("node_uuid", ""),
        r.get("sequence", ""),
        r.get("rssi", ""),
        r.get("snr", ""),
        r.get("kind", ""),
        (payload[:120] + "…") if len(payload) > 121 else payload,
    ]


def _node_matches(node_uuid: str, q: str, suffix: str) -> bool:
    """Bản tin thuộc node đang tìm không? q rỗng → khớp tất cả.

    So chuỗi con trên cả 2 dạng: UID tự nhiên (vd 4F9199E139C) và UID đã bóc
    suffix 8 hex (node mới 16 hex ↔ 24 hex trên MQTT) — cùng cơ chế với bộ so
    sánh COM2↔GW, để tìm 1 node dù mỗi nguồn hiện UID khác độ dài.
    """
    if not q:
        return True
    node = str(node_uuid).upper()
    if not node:
        return False
    qq = q.upper()
    if qq in node:
        return True
    return qq in normalize_uid(node, suffix)


def _gw_matches(gateway_uuid: str, q: str) -> bool:
    """Bản tin do gateway đang tìm bắn không? q rỗng → khớp tất cả.

    Nhiều gateway cùng bắn bản tin trên cùng broker → lọc theo `gateway_uuid`
    của từng dòng để chỉ xem bản tin MQTT phát bởi gateway đó. So chuỗi con
    (1 phần UID cũng được), in hoa để khớp kiểu firmware (hex không dấu ':').

    Dòng KHÔNG gắn gateway (vd nguồn COM2 — mạch thu riêng, hoặc MQTT payload
    hệ cũ thiếu `gateway_id`) luôn đi qua: không thể quy kết gateway nên giữ
    lại để không phá khối đối chiếu 3 nguồn của node.
    """
    if not q:
        return True
    gw = str(gateway_uuid).upper()
    if not gw:
        return True
    return q.upper() in gw


def _row_matches(r: dict, q_node: str, q_gw: str, suffix: str) -> bool:
    """Một dòng có khớp CẢ bộ lọc node lẫn bộ lọc gateway không?"""
    return (_node_matches(r.get("node_uuid", ""), q_node, suffix)
            and _gw_matches(r.get("gateway_uuid", ""), q_gw))


def _is_garbage_row(r: dict) -> bool:
    """Dòng là bản tin rác LoRa (CRC lỗi / unparsed / incomplete)?

    Dùng để lọc khỏi Message Log + CSV khi bật `ui.filter_garbage`. Áp cho cả
    nguồn gateway (LORA) lẫn mạch thu riêng (COM2/REF) — cả 2 đều dùng chung
    `SerialLogParser` nên kind giống nhau.
    """
    kind = str(r.get("kind", ""))
    return kind.split("/", 1)[-1] in GARBAGE_RX_KINDS


def _mac_to_uuid_fallback(mac: str) -> str:
    """MAC efuse '04:F9:19:9E:13:9C' → UUID gateway kiểu firmware (bỏ ':' và
    nibble 0 đầu → '4F9199E139C'). Dùng khi block DEVICE INFO chỉ in MAC."""
    hx = (mac or "").replace(":", "").strip().upper().lstrip("0")
    return hx


def _row_from_keys(d: dict) -> list:
    return [d.get(k, "") for k in _ROW_KEYS]


def _lora_row(evt: LoraRxEvent) -> dict:
    return {
        "pc_time": evt.pc_time,
        "source": SRC_LORA,
        "gateway_uuid": "",
        "node_uuid": evt.node_uuid,
        "sequence": evt.seq if evt.seq is not None else "",
        "rssi": evt.rssi if evt.rssi is not None else "",
        "snr": evt.snr if evt.snr is not None else "",
        "kind": "LORA/" + evt.kind,
        "topic": "serial",
        "payload": evt.frame_hex,
    }


def _ref_row(evt: LoraRxEvent) -> dict:
    """Row CSV/table cho data frame mạch thu riêng (COM 2)."""
    return {
        "pc_time": evt.pc_time,
        "source": SRC_REF,
        "gateway_uuid": "",
        "node_uuid": evt.node_uuid,
        "sequence": evt.seq if evt.seq is not None else "",
        "rssi": evt.rssi if evt.rssi is not None else "",
        "snr": evt.snr if evt.snr is not None else "",
        "kind": "LORA/" + evt.kind,
        "topic": "serial2",
        "payload": evt.frame_hex,
    }


def _mqtt_row(evt) -> dict:
    return {
        "pc_time": evt.pc_time,
        "source": SRC_MQTT,
        "gateway_uuid": evt.gateway_id,
        "node_uuid": evt.node_uuid,
        "sequence": evt.seq if evt.seq is not None else "",
        "rssi": evt.rssi if evt.rssi is not None else "",
        "snr": evt.snr if evt.snr is not None else "",
        "kind": "MQTT/" + evt.kind,
        "topic": evt.topic,
        "payload": evt.payload,
    }


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Gateway LoRa Message Counter")
        self.resize(1320, 900)

        self.cfg = load_config()
        self.serial_worker: SerialWorker | None = None
        self.mqtt_worker: MqttWorker | None = None
        self.serial_parser = SerialLogParser()
        self.mqtt_parser = MqttParser()
        self.counter = MessageCounter()
        self.session = TestSession()

        # So sánh nguồn: mạch thu riêng (COM 2) vs gateway — dùng đuôi chuyển đổi
        # node mới từ config để bóc về UID gốc.
        self.comparator = SourceComparator(
            self.cfg.get("node", {}).get("new_node_suffix", "FC8B3004"))
        self._node_suffix = self.comparator.suffix
        self.src_widget: SerialSourceWidget | None = None

        # Search bar node: tìm kiếm + đối chiếu bản tin của 1 node trên 3 nguồn
        # (LORA gateway | COM2 | MQTT). Filter có debounce 300ms tránh lag UI.
        self._search_text = ""
        self._search_text_gw = ""
        self._search_debounce: QTimer | None = None
        # Lọc bản tin rác (CRC lỗi / unparsed / incomplete) khỏi Message Log + CSV.
        # Rác RF vẫn đếm riêng ở diag (rx_crc_error) — đây chỉ là lọc hiển thị.
        self._filter_garbage = bool(self.cfg.get("ui", {}).get("filter_garbage", True))
        # Bảng so sánh node (3 nguồn theo seq) tái dựng khi có dòng mới khớp —
        # debounce để không quét lại toàn bộ `_rows_all` ở tần số refresh timer.
        self._nc_debounce: QTimer | None = None

        self.state = STATE_IDLE
        self._counting = False          # chỉ đếm khi test đang chạy / settle
        self._rx_frozen = False
        self._started_mono = 0.0
        self._started_str = ""
        self._target = 0
        self._duration_s = 0.0
        self._match: MatchResult | None = None
        self._summary: SessionSummary | None = None
        self._rows_all: deque = deque(maxlen=200000)
        self._pending_rows: list = []
        self._pending_since = time.monotonic()

        # Sau reboot: tool đợi IP xuất hiện trên Serial rồi gọi HTTP /api/status
        # để tự điền UUID/model/MAC — tránh chờ bản tin [INFO] 60s.
        self._http_worker: QThread | None = None
        self._fetched_ips: set = set()      # các IP đã thử /api/status
        self._reboot_pending = False        # đang trong cửa sổ chờ sau reboot
        self._reboot_watch: QTimer | None = None
        self._reboot_poll: QTimer | None = None

        self._build_ui()
        self._wire_timers()
        self._update_controls()

    # ------------------------------------------------------------------ UI ---
    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)

        # -- Bố cục chính: cột cấu hình trái (hẹp, cuộn được) + vùng chính. --
        splitter = QSplitter(Qt.Horizontal)

        # Cột trái: các khối cấu hình xếp dọc, cuộn khi cửa sổ nhỏ.
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(360)
        left_holder = QWidget()
        left_v = QVBoxLayout(left_holder)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(6)
        left_v.addWidget(self._build_gateway_box())
        left_v.addWidget(self._build_serial_box())
        left_v.addWidget(self._build_mqtt_box())
        left_v.addWidget(self._build_test_box())
        left_v.addWidget(self._build_counter_box())
        # Mạch thu riêng (COM 2) + nút so sánh — nguồn đối chiếu riêng.
        self.src_widget = self._build_source_widget()
        left_v.addWidget(self.src_widget)
        self.bt_compare = QPushButton("So sánh nguồn (COM2 ↔ GW)")
        self.bt_compare.setToolTip(
            "Đối chiếu các data frame mạch thu riêng đọc được (COM 2) với các "
            "data frame gateway nhận được theo cặp (UID gốc 8 byte, seq).")
        self.bt_compare.clicked.connect(self.on_compare_sources)
        left_v.addWidget(self.bt_compare)
        left_v.addStretch(1)
        left_scroll.setWidget(left_holder)

        # Vùng chính phải: node summary + tabs (log + đối chiếu node).
        right = QWidget()
        right_v = QVBoxLayout(right)
        right_v.setContentsMargins(6, 0, 6, 0)
        right_v.setSpacing(4)
        right_v.addWidget(self._build_node_status_row())
        right_v.addWidget(self._build_tabs(), 1)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right)
        splitter.setSizes([380, 940])
        outer.addWidget(splitter)

        self.setCentralWidget(root)
        self.statusBar().showMessage("Sẵn sàng — nạp config.json")

    def _build_search_bar(self) -> QWidget:
        """Search bar node: nhập UID/ID node + UID gateway để lọc + đối chiếu 3 nguồn.

        2 ô lọc: (1) node theo UID node, (2) gateway theo UID gateway — áp dụng
        cùng lúc cho bảng Message Log. Có nhiều gateway cùng bắn bản tin nên ô
        gateway giúp cô lập đúng bản tin do 1 gateway phát (lọc theo
        `gateway_uuid` trên mỗi dòng).
        """
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        # Dòng 1: tìm node (UID node).
        h1 = QHBoxLayout()
        h1.addWidget(QLabel("Tìm node (UID):"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Nhập UID node (vd 4F9199E139C, có thể 1 phần)")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.on_search_text)
        h1.addWidget(self.search_edit, 1)
        v.addLayout(h1)
        # Dòng 2: tìm gateway (UID gateway) — lọc bản tin MQTT do gateway đó bắn.
        h2 = QHBoxLayout()
        h2.addWidget(QLabel("Tìm gateway (UID):"))
        self.search_gw_edit = QLineEdit()
        self.search_gw_edit.setPlaceholderText("Nhập UID gateway (vd 4F9199E139C, có thể 1 phần)")
        self.search_gw_edit.setClearButtonEnabled(True)
        self.search_gw_edit.textChanged.connect(self.on_search_gw_text)
        h2.addWidget(self.search_gw_edit, 1)
        v.addLayout(h2)
        # Dòng 3: lọc rác RF (CRC lỗi / unparsed / incomplete) khỏi Message Log
        # + CSV export. Rác vẫn hiện trong thống kê diag (rx_crc_error) của tab
        # Counters để biết chất lượng đường RF. Áp cho cả gateway lẫn COM2.
        h3 = QHBoxLayout()
        self.chk_filter_garbage = QCheckBox("Lọc bản tin rác (CRC lỗi / unparsed)")
        self.chk_filter_garbage.setToolTip(
            "Bỏ các frame LoRa không hợp lệ (CRC sai, không parse được, thiếu "
            "byte) khỏi bảng Message Log và file CSV — tránh lẫn vào đối chiếu. "
            "Các frame này vẫn được đếm riêng ở tab Counters (rx_crc_error).")
        self.chk_filter_garbage.setChecked(self._filter_garbage)
        self.chk_filter_garbage.toggled.connect(self.on_filter_garbage_toggled)
        h3.addWidget(self.chk_filter_garbage)
        h3.addStretch(1)
        v.addLayout(h3)
        # Bảng so sánh nguồn chỉ hiển thị khi có search — đặt trên tab So sánh node.
        return w

    def _build_gateway_box(self) -> QGroupBox:
        # Gateway UUID dùng để lọc gateway_id khi đếm MQTT. Tool tự lấy từ bản
        # tin [INFO] 60s trên Serial; ô vẫn editable để người dùng sửa tay nếu cần.
        box = QGroupBox("Gateway")
        f = QFormLayout(box)
        self.gw_uuid = QLineEdit()
        self.gw_uuid.setPlaceholderText("UUID — tự bắt serial boot / HTTP / [INFO] 60s")
        self.gw_uuid.editingFinished.connect(self._on_uuid_changed)
        self.bt_reboot = QPushButton("Reboot GW")
        self.bt_reboot.setToolTip(
            "Gửi {\"reboot\": true} qua Serial. Sau khi gateway boot lại, tool tự "
            "bắt block DEVICE INFO trên Serial (UUID/Model/Series/MAC — nguồn "
            "nhanh nhất). Nếu lỡ block (COM bận/connect trễ) → fallback log IP "
            "+ HTTP /api/status, rồi tới [INFO] 60s. (Chỉ sửa công cụ, không đổi "
            "firmware)")
        self.bt_reboot.clicked.connect(self.on_reboot)
        uuid_row = QHBoxLayout()
        uuid_row.addWidget(self.gw_uuid, 1)
        uuid_row.addWidget(self.bt_reboot)
        self.lbl_uuid_src = QLabel("")
        self.lbl_uuid_src.setStyleSheet("color: gray; font-style: italic;")
        self.gw_sn = QLineEdit()
        self.gw_mac = QLineEdit()
        self.gw_model = QLineEdit()
        self.gw_ip = QLineEdit()
        self.gw_ip.setPlaceholderText("vd 192.168.1.100 (tùy chọn)")
        for w in (self.gw_sn, self.gw_mac, self.gw_model):
            w.setReadOnly(True)
        f.addRow("Gateway UUID:", uuid_row)
        f.addRow("UUID nguồn:", self.lbl_uuid_src)
        f.addRow("S/N:", self.gw_sn)
        f.addRow("MAC:", self.gw_mac)
        f.addRow("Model:", self.gw_model)
        f.addRow("IP:", self.gw_ip)
        return box

    def _build_serial_box(self) -> QGroupBox:
        # COM serial nối thẳng vào GATEWAY (USB CDC). Receiver LoRa KHÔNG cắm vào
        # PC — nó gắn trên gateway qua UART (Serial1). Log receiver được gateway
        # in ra cùng dòng log của chính nó: tool đọc log GATEWAY qua COM này.
        box = QGroupBox("Serial (COM -> Gateway / log)")
        f = QFormLayout(box)
        self.com_combo = QComboBox()
        self.com_combo.setEditable(True)
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["115200", "9600", "57600", "230400", "460800"])
        self.baud_combo.setCurrentText(str(self.cfg["serial"]["baudrate"]))
        self.bt_detect = QPushButton("Detect COM")
        self.bt_detect.clicked.connect(self.on_detect_serial)
        self.bt_serial_toggle = QPushButton("Connect")
        self.bt_serial_toggle.clicked.connect(self.on_toggle_serial)
        bt = QHBoxLayout()
        bt.addWidget(self.bt_detect)
        bt.addWidget(self.bt_serial_toggle)
        f.addRow("COM Port:", self.com_combo)
        f.addRow("Baudrate:", self.baud_combo)
        f.addRow(bt)
        self.lbl_serial = QLabel("DISCONNECTED")
        f.addRow("Gateway (COM):", self.lbl_serial)
        # UID mạch LoRa receiver (gắn trên gateway, qua UART) — tự lấy từ log
        # [LORA] MODULE INFO | UID: hoặc từ mỗi packet [LORA] RECEIVER ID:.
        self.lbl_receiver_uid = QLabel("-")
        self.lbl_receiver_uid.setStyleSheet("font-weight: bold; color: #007acc;")
        f.addRow("Receiver UID:", self.lbl_receiver_uid)
        return box

    def _build_source_widget(self) -> SerialSourceWidget:
        w = SerialSourceWidget(
            "Mạch thu riêng (COM 2)", default_baud=115200,
            comparator=self.comparator,
            on_rx_event=self._on_ref_event, parent=self)
        w.setToolTip(
            "Nguồn đối chiếu độc lập: mạch thu LoRa riêng cắm vào PC.\n"
            "Khi bấm START TEST, tool tự kết nối COM này (nếu đã chọn COM mà "
            "chưa Connect) để đối chiếu song song với gateway.\n"
            "Nếu để trống COM, luồng đối chiếu nguồn sẽ là N/A.")
        return w

    def _build_mqtt_box(self) -> QGroupBox:
        box = QGroupBox("MQTT Broker")
        f = QFormLayout(box)
        mq = self.cfg["mqtt"]
        self.mqtt_host = QLineEdit(str(mq["host"]))
        self.mqtt_port = QSpinBox()
        self.mqtt_port.setRange(1, 65535)
        self.mqtt_port.setValue(int(mq["port"]))
        self.mqtt_user = QLineEdit(str(mq.get("username", "")))
        self.mqtt_pass = QLineEdit(str(mq.get("password", "")))
        self.mqtt_pass.setEchoMode(QLineEdit.Password)
        self.mqtt_topics = QLineEdit(",".join(mq.get("subscribe_topics", [])))
        self.mqtt_topics.setPlaceholderText("node/+/data,node/+/log")
        self.bt_mqtt_toggle = QPushButton("Connect MQTT")
        self.bt_mqtt_toggle.clicked.connect(self.on_toggle_mqtt)
        f.addRow("Host:", self.mqtt_host)
        f.addRow("Port:", self.mqtt_port)
        f.addRow("User:", self.mqtt_user)
        f.addRow("Pass:", self.mqtt_pass)
        f.addRow("Topics:", self.mqtt_topics)
        f.addRow(self.bt_mqtt_toggle)
        self.lbl_mqtt = QLabel("DISCONNECTED")
        self.lbl_filter = QLabel("MQTT Filter: OFF")
        f.addRow("MQTT:", self.lbl_mqtt)
        f.addRow("Filter:", self.lbl_filter)
        return box

    def _build_test_box(self) -> QGroupBox:
        box = QGroupBox("Test Control")
        g = QGridLayout(box)
        t = self.cfg["test"]
        self.target_spin = QSpinBox()
        self.target_spin.setRange(0, 10000000)
        self.target_spin.setValue(int(t.get("default_target_messages", 1000)))
        self.target_spin.setSuffix(" msg (0 = vô hạn)")
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(0, 24 * 60)
        self.duration_spin.setValue(int(t.get("default_duration_seconds", 600)) // 60)
        self.duration_spin.setSuffix(" min (0 = vô hạn)")
        self.bt_start = QPushButton("START TEST")
        self.bt_start.setStyleSheet("font-weight:bold;")
        self.bt_start.clicked.connect(self.on_start_test)
        self.bt_stop = QPushButton("STOP TEST")
        self.bt_stop.clicked.connect(self.on_stop_test)
        self.bt_clear = QPushButton("CLEAR")
        self.bt_clear.clicked.connect(self.on_clear)
        self.lbl_test_state = QLabel("IDLE")
        self.lbl_test_state.setAlignment(Qt.AlignCenter)
        self.lbl_session = QLabel("Session: -")
        btns = QHBoxLayout()
        btns.addWidget(self.bt_start)
        btns.addWidget(self.bt_stop)
        btns.addWidget(self.bt_clear)
        g.addWidget(QLabel("Target messages:"), 0, 0)
        g.addWidget(self.target_spin, 0, 1)
        g.addWidget(QLabel("Test duration:"), 1, 0)
        g.addWidget(self.duration_spin, 1, 1)
        g.addLayout(btns, 2, 0, 1, 2)
        g.addWidget(QLabel("Test status:"), 3, 0)
        g.addWidget(self.lbl_test_state, 3, 1)
        g.addWidget(self.lbl_session, 4, 0, 1, 2)
        return box

    def _build_counter_box(self) -> QGroupBox:
        box = QGroupBox("Message Counter")
        g = QGridLayout(box)
        style_big = "font-size:22px; font-weight:bold;"

        def big_label() -> QLabel:
            lb = QLabel("-")
            lb.setAlignment(Qt.AlignCenter)
            lb.setStyleSheet(style_big)
            return lb

        self.cnt_rx = big_label()
        self.cnt_mqtt = big_label()
        self.cnt_missing = big_label()
        self.cnt_rate = big_label()
        self.cnt_dup = big_label()
        self.cnt_time = QLabel("first/last: -")
        self.cnt_time.setWordWrap(True)

        g.addWidget(QLabel("LoRa Receiver (Rx):"), 0, 0)
        g.addWidget(self.cnt_rx, 0, 1)
        g.addWidget(QLabel("MQTT Server:"), 0, 2)
        g.addWidget(self.cnt_mqtt, 0, 3)
        g.addWidget(QLabel("Missing:"), 1, 0)
        g.addWidget(self.cnt_missing, 1, 1)
        g.addWidget(QLabel("Duplicate (MQTT/RX):"), 1, 2)
        g.addWidget(self.cnt_dup, 1, 3)
        g.addWidget(QLabel("Rate (Rx | MQTT):"), 2, 0)
        g.addWidget(self.cnt_rate, 2, 1, 1, 3)
        g.addWidget(self.cnt_time, 3, 0, 1, 4)
        return box

    def _build_node_status_row(self) -> QWidget:
        # Dòng trạng thái node: trái = per-node (gateway ↔ MQTT), phải = nguồn
        # đối chiếu COM2 ↔ gateway (cập nhật live từ SourceComparator).
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)

        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Per-node (gateway data ↔ MQTT):"))
        self.node_table = NodeSummaryTable()
        self.node_table.setMaximumHeight(160)
        lv.addWidget(self.node_table)
        h.addWidget(left, 1)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("So sánh mạch thu riêng (COM 2) ↔ Gateway (live):"))
        self.src_compare_table = SourceCompareTable()
        self.src_compare_table.setMaximumHeight(160)
        rv.addWidget(self.src_compare_table)
        h.addWidget(right, 1)

        self.lbl_corr = QLabel("Message correlation: COUNT ONLY")
        self.lbl_diag = QLabel("diag: -")
        self.lbl_diag.setWordWrap(True)

        v.addLayout(h)
        v.addWidget(self.lbl_corr)
        v.addWidget(self.lbl_diag)
        return wrap

    def _build_tabs(self) -> QWidget:
        self.tabs = QTabWidget()
        raw_lines = int(self.cfg["ui"].get("raw_log_lines", 5000))
        max_rows = int(self.cfg["ui"].get("table_max_rows", 50000))

        # -- Tab 1: Message Log + search bar node ----------------------------
        tab_ml = QWidget()
        ml = QVBoxLayout(tab_ml)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(self._build_search_bar())
        self.lbl_search_status = QLabel(
            "Gõ UID node (1 phần cũng được) để lọc bản tin của node đó trên cả 3 nguồn.")
        self.lbl_search_status.setStyleSheet("color: gray;")
        self.lbl_search_status.setWordWrap(True)
        ml.addWidget(self.lbl_search_status)
        self.msg_table = MessageLogTable(max_rows=max_rows)
        ml.addWidget(self.msg_table, 1)
        self.tabs.addTab(tab_ml, "Message Log")

        self.lora_decoder = LoraDecoderWidget()
        self.tabs.addTab(self.lora_decoder, "Giải mã LoRa")

        self.uart_view = LogViewer(max_lines=raw_lines)
        tab_u = QWidget()
        lu = QVBoxLayout(tab_u)
        bu = QHBoxLayout()
        self.bt_clear_uart = QPushButton("Clear UART")
        self.bt_clear_uart.clicked.connect(self.uart_view.clear)
        bu.addWidget(self.bt_clear_uart)
        bu.addStretch(1)
        lu.addLayout(bu)
        lu.addWidget(self.uart_view)
        self.tabs.addTab(tab_u, "RAW UART LOG")

        self.mqtt_view = LogViewer(max_lines=raw_lines)
        tab_m = QWidget()
        lm = QVBoxLayout(tab_m)
        bm = QHBoxLayout()
        self.bt_clear_mqtt = QPushButton("Clear MQTT")
        self.bt_clear_mqtt.clicked.connect(self.mqtt_view.clear)
        self.bt_save = QPushButton("Save Log")
        self.bt_save.clicked.connect(self.on_save_log)
        self.bt_export = QPushButton("Export CSV")
        self.bt_export.clicked.connect(self.on_export_csv)
        self.bt_export_cmp = QPushButton("Export đối chiếu CSV")
        self.bt_export_cmp.setToolTip(
            "Xuất kết quả đối chiếu ra 1 file CSV phẳng: tóm tắt 2 luồng "
            "(GATEWAY↔MQTT và COM2↔GATEWAY), per-node và per-(UID,seq) cho biết "
            "từng key có mặt ở nguồn nào (COM2/gateway/MQTT) + kết luận.")
        self.bt_export_cmp.clicked.connect(self.on_export_compare_csv)
        bm.addWidget(self.bt_clear_mqtt)
        bm.addWidget(self.bt_save)
        bm.addWidget(self.bt_export)
        bm.addWidget(self.bt_export_cmp)
        bm.addStretch(1)
        lm.addLayout(bm)
        lm.addWidget(self.mqtt_view)
        self.tabs.addTab(tab_m, "RAW MQTT LOG")

        # -- Tab: So sánh node (3 nguồn LORA | COM2 | MQTT theo Seq) --------
        tab_nc = QWidget()
        ncv = QVBoxLayout(tab_nc)
        ncv.setContentsMargins(0, 0, 0, 0)
        self.lbl_nc_hint = QLabel(
            "Nhập UID node ở ô tìm kiếm (tab Message Log) — bảng này ráp 3 nguồn "
            "theo Seq: LORA (gateway) | COM2 (mạch thu riêng) | MQTT (server).\n"
            "✓ = có bản tin tại seq đó; thiếu ở nguồn nào sẽ thấy ngay.")
        self.lbl_nc_hint.setStyleSheet("color: gray;")
        self.lbl_nc_hint.setWordWrap(True)
        ncv.addWidget(self.lbl_nc_hint)
        self.node_compare_table = NodeCompareTable()
        ncv.addWidget(self.node_compare_table, 1)
        self.tabs.addTab(tab_nc, "So sánh node (3 nguồn)")

        self.result_view = QPlainTextEdit()
        self.result_view.setReadOnly(True)
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        self.result_view.setFont(font)
        self.tabs.addTab(self.result_view, "Kết quả")
        return self.tabs

    # ------------------------------------------------------------- timers ----
    def _wire_timers(self) -> None:
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(200)
        self._refresh_timer.timeout.connect(self._on_refresh)
        self._refresh_timer.start()
        self._duration_timer = QTimer(self)
        self._duration_timer.setSingleShot(True)
        self._duration_timer.timeout.connect(self._on_duration_end)

    # ----------------------------------------------------------- helpers -----
    def _gateway_uuid(self) -> str:
        return self.gw_uuid.text().strip().upper()

    def _filter_enabled(self) -> bool:
        return bool(self.cfg["mqtt"].get("filter_by_gateway_uuid", True))

    def _count_retained(self) -> bool:
        return bool(self.cfg["mqtt"].get("count_retained", False))

    def _topics(self) -> list[str]:
        parts = [p.strip() for p in self.mqtt_topics.text().split(",")]
        return [p for p in parts if p]

    def _update_controls(self) -> None:
        serial_on = self.serial_worker is not None and self.serial_worker.isRunning()
        mqtt_on = self.mqtt_worker is not None and self.mqtt_worker.isRunning()
        self.bt_serial_toggle.setText("Disconnect" if serial_on else "Connect")
        self.bt_mqtt_toggle.setText("Disconnect MQTT" if mqtt_on else "Connect MQTT")
        running = self.state == STATE_RUNNING
        settling = self.state == STATE_SETTLE
        self.bt_start.setEnabled(not running and not settling)
        self.bt_stop.setEnabled(running or settling)
        self.bt_serial_toggle.setEnabled(self.state == STATE_IDLE or self.state == STATE_DONE)
        self.bt_mqtt_toggle.setEnabled(True)

    def _set_state(self, state: str) -> None:
        self.state = state
        if state == STATE_RUNNING:
            self.lbl_test_state.setText("RUNNING")
        elif state == STATE_SETTLE:
            self.lbl_test_state.setText("WAIT_SETTLE ...")
        elif state == STATE_DONE:
            self.lbl_test_state.setText("DONE")
        else:
            self.lbl_test_state.setText("IDLE")
        self._update_controls()

    def _status(self, text: str) -> None:
        self.statusBar().showMessage(text, 15000)

    def _on_uuid_changed(self) -> None:
        self.gw_uuid.setText(self._gateway_uuid())
        self._refresh_filter_label()

    # ------------------------------------------------------------- Serial ----
    def on_detect_serial(self) -> None:
        ports = list_serial_ports()
        self.com_combo.clear()
        self.com_combo.addItems(ports)
        if ports:
            self.com_combo.setCurrentIndex(0)
        self._status("Detect: %d COM" % len(ports))

    def on_toggle_serial(self) -> None:
        if self.serial_worker and self.serial_worker.isRunning():
            self.serial_worker.stop()
            self.serial_worker = None
            self.lbl_serial.setText("DISCONNECTED")
            self._update_controls()
            return
        port = self.com_combo.currentText().strip()
        if not port:
            self._status("Chưa chọn COM port")
            return
        baud = int(self.baud_combo.currentText())
        self.serial_worker = SerialWorker(port, baud, self)
        self.serial_worker.line.connect(self.on_serial_line)
        self.serial_worker.status.connect(self._on_serial_status)
        self.serial_worker.start()
        self._update_controls()

    def on_reboot(self) -> None:
        # Gửi lệnh reboot xuống gateway qua Serial. Sau reboot firmware in
        # block DEVICE INFO (UUID/Model/Series/MAC) NGAY trên Serial — nguồn
        # nhanh nhất, ưu tiên bắt block này (serial boot). Nếu lỡ mất block
        # (COM bận / connect trễ), tool fallback: log IP → HTTP /api/status,
        # rồi tới [INFO] 60s.
        if self.serial_worker is None or not self.serial_worker.isRunning():
            self._status("Chưa kết nối Serial — hãy Connect COM trước khi Reboot")
            return
        cmd = b'{"reboot": true}\n'
        ok = self.serial_worker.write(cmd)
        if ok:
            self._status("Đã gửi lệnh reboot — chờ serial DEVICE INFO (hoặc HTTP fallback)")
            self._arm_reboot_auto_fetch()
        else:
            self._status("Gửi lệnh reboot thất bại — kiểm tra kết nối Serial")

    # ------------------------------------------- post-reboot auto identity --
    def _set_uuid_from(self, uuid: str, label: str, colour: str) -> bool:
        """Điền Gateway UUID kèm nhãn nguồn, chỉ khi ô UUID đang trống.

        Ưu tiên nguồn ĐẦU TIÊN lấy được (serial boot > HTTP > [INFO] 60s >
        manual): nếu UUID đã có thì chỉ bổ sung các ô khác, KHÔNG đè nhãn nguồn
        — tránh HTTP/[INFO] đến sau ghi đè nhãn "serial boot".
        Trả về True nếu ô UUID vừa được điền lần đầu.
        """
        uuid = uuid.strip().upper()
        if not uuid:
            return False
        was_empty = not self._gateway_uuid()
        self.gw_uuid.setText(uuid)
        if was_empty:
            self.lbl_uuid_src.setText(label)
            self.lbl_uuid_src.setStyleSheet("color: %s; font-weight: bold;" % colour)
        return was_empty

    def _arm_reboot_auto_fetch(self) -> None:
        """Kích hoạt theo dõi sau reboot trong ~25s.

        Khi gateway reboot, serial bị ngắt và tool sẽ thấy DISCONNECTED rồi
        CONNECTED lại (nếu người dùng bấm Connect). Từng IP mới log trên Serial
        (Ethernet/WiFi) sẽ kích hoạt một lần gọi HTTP /api/status.
        """
        self._stop_reboot_watch()
        self._reboot_pending = True
        # Xoá các IP đã thử từ trước để cho phép fetch lại với IP mới sau boot.
        self._fetched_ips.clear()
        self._reboot_watch = QTimer(self)
        self._reboot_watch.setSingleShot(True)
        self._reboot_watch.timeout.connect(self._on_reboot_watch_expired)
        self._reboot_watch.start(30000)
        # Poll định kỳ: thử lại mọi IP đã biết (kể cả đã fail) cho tới khi lấy
        # được UUID — phòng trường hợp lỡ log IP lúc gateway boot (COM bận).
        self._reboot_poll = QTimer(self)
        self._reboot_poll.setInterval(2000)
        self._reboot_poll.timeout.connect(self._poll_reboot_ips)
        self._reboot_poll.start()

    def _stop_reboot_watch(self) -> None:
        if self._reboot_watch is not None:
            self._reboot_watch.stop()
            self._reboot_watch = None
        if self._reboot_poll is not None:
            self._reboot_poll.stop()
            self._reboot_poll = None
        self._reboot_pending = False

    def _on_reboot_watch_expired(self) -> None:
        self._reboot_pending = False
        self._reboot_watch = None
        self._stop_reboot_watch()
        if not self._gateway_uuid():
            self._status("Reboot: chưa thấy IP/HTTP sau 30s — sẽ lấy UUID từ [INFO] 60s khi có")

    def _poll_reboot_ips(self) -> None:
        """Timer 2s trong cửa sổ reboot: thử từng IP đã biết tới khi có UUID.

        Cho phép thử lại các IP đã từng thất bại (không thêm vào _fetched_ips),
        phòng trường hợp gateway chưa kịp bật HTTP khi ta gọi lần đầu.
        """
        if not self._reboot_pending:
            return
        if self._gateway_uuid() and self.gw_sn.text() and self.gw_mac.text():
            # Đã lấy đủ — dừng poll.
            self._stop_reboot_watch()
            return
        if self._http_worker is not None and self._http_worker.isRunning():
            return
        ips: list[str] = list(self.serial_parser.last_seen_ips)
        if self.gw_ip.text().strip() and self.gw_ip.text().strip() not in ips:
            ips.append(self.gw_ip.text().strip())
        for ip in reversed(ips):
            if not ip:
                continue
            if ip in self._fetched_ips:
                # Đã thử thành công từ sự kiện ip trước → không qua đây.
                continue
            if self._start_http_fetch(ip):
                return  # đã khởi động 1 worker

    def _maybe_fetch_http_after_ip(self, ip: str) -> bool:
        """Sau reboot: có IP mới → gọi HTTP /api/status 1 lần cho IP đó.

        Nếu thành công sẽ tự điền Gateway UUID + Model/S/N/FW/MAC/IP (nguồn
        "HTTP sau reboot") — không cần chờ bản tin [INFO] 60s.
        Trả về True nếu đã khởi động worker fetch cho ip.
        """
        if not ip or not self._reboot_pending:
            return False
        http_cfg = self.cfg.get("gateway_http", {})
        if not http_cfg.get("enabled", True):
            return False
        if ip in self._fetched_ips:
            return False
        # Một worker HTTP đang chạy → chờ nó xong rồi mới fetch IP khác
        # (tránh đè tham chiếu/GC QThread đang chạy).
        if self._http_worker is not None and self._http_worker.isRunning():
            return False
        # Đã có UUID đầy đủ rồi (vd từ [INFO] trước đó) thì không cần fetch lại.
        if self._gateway_uuid() and self.gw_sn.text() and self.gw_mac.text():
            return False
        # KHÔNG thêm ip vào _fetched_ips trước: nếu HTTP fail, poll sẽ thử lại
        # ip này (chỉ đánh dấu thành công trong _on_http_status).
        return self._start_http_fetch(ip)

    def _start_http_fetch(self, ip: str) -> bool:
        http_cfg = self.cfg.get("gateway_http", {})
        if self._http_worker is not None and self._http_worker.isRunning():
            return False
        timeout = float(http_cfg.get("timeout_s", 3))
        worker = HttpStatusWorker(ip, timeout_s=timeout, parent=self)
        worker.done.connect(self._on_http_status)
        worker.finished.connect(self._on_http_worker_finished)
        self._http_worker = worker
        worker.start()
        return True

    def _on_http_worker_finished(self) -> None:
        # Thread đã kết thúc hẳn → có thể để một worker khác chạy / GC an toàn.
        self._http_worker = None

    def _on_http_status(self, info: GatewayInfo) -> None:
        # Không đặt self._http_worker = None ở đây để giữ tham chiếu QThread
        # cho tới khi thread thực sự kết thúc (tránh GC object đang chạy).
        if not info.uuid:
            err = (info.extra or {}).get("error", "không có uuid")
            self._status("HTTP /api/status thất bại (%s): %s"
                         % (info.ip or "?", err))
            return
        if info.ip:
            self._fetched_ips.add(info.ip)
        # Serial boot đã có UUID → HTTP chỉ bổ sung ô thiếu, KHÔNG đè nhãn nguồn.
        first = self._set_uuid_from(info.uuid, " HTTP sau reboot ", "#2e7d32")
        if first:
            # Lấy được UUID (nguồn đầu tiên là HTTP) → dừng poll/watch reboot.
            self._stop_reboot_watch()
            self._status("Tự lấy UUID sau reboot (HTTP %s): %s"
                         % (info.ip or "?", info.uuid.upper()))
        if info.model and not self.gw_model.text():
            self.gw_model.setText(info.model)
        if info.series and not self.gw_sn.text():
            self.gw_sn.setText(info.series)
        if info.firmware_version:
            self.gw_model.setToolTip("Firmware: %s" % info.firmware_version)
        if info.mac and not self.gw_mac.text():
            self.gw_mac.setText(info.mac)
        if info.ip and not self.gw_ip.text():
            self.gw_ip.setText(info.ip)
        self._refresh_filter_label()

    def _on_serial_status(self, text: str) -> None:
        if text == "CONNECTED":
            self.lbl_serial.setText("CONNECTED")
            # Vừa connect (hoặc reconnect sau reboot): nếu đang trong cửa sổ
            # chờ sau reboot và đã thấy IP trong quá khứ → thử /api/status ngay.
            if self._reboot_pending:
                ips = self.serial_parser.last_seen_ips
                for ip in reversed(ips):
                    if self._maybe_fetch_http_after_ip(ip):
                        break
        elif text.startswith("CONNECTING"):
            self.lbl_serial.setText("CONNECTING ...")
        else:
            self.lbl_serial.setText(text)
        self._status("Serial: %s" % text)

    def on_serial_line(self, line: str) -> None:
        now = now_pc_str()
        self.uart_view.append_line("%s | %s" % (now, line))
        if self.session.active:
            self.session.append_uart("%s | %s" % (now, line))
        for evt in self.serial_parser.feed(line, now):
            self._on_serial_event(evt, now)

    def _on_serial_event(self, evt, now: str) -> None:
        if isinstance(evt, LoraRxEvent):
            # Sau khi đủ target (freeze): packet mới phát sau đó KHÔNG thuộc test —
            # không đếm, không ghi CSV (raw log vẫn còn trong panel).
            if self._rx_frozen:
                return
            row = _lora_row(evt)
            row["gateway_uuid"] = self._gateway_uuid()
            self._push_row(row)
            if self._counting and not self._rx_frozen:
                self.counter.add_rx(evt)
                # Song song: đưa vào bộ so sánh nguồn theo (UID gốc 8 byte, seq).
                # Dùng `add_gateway_event` (không phải `add_gateway`) để lọc cùng
                # chuẩn với nguồn COM2: chỉ DATA + CRC OK + seq>0. Frame CRC lỗi
                # vẫn đọc được uid/seq nên nếu đưa thẳng vào sẽ sinh `extra` giả.
                self.comparator.add_gateway_event(evt)
                self._maybe_target_done()
            return
        if isinstance(evt, GatewayBootInfo):
            # Block DEVICE INFO trên Serial ngay sau reboot — nguồn nhanh nhất
            # để lấy UUID gateway (không cần chờ IP/HTTP hay [INFO] 60s).
            if evt.uuid or evt.mac:
                first = self._set_uuid_from(evt.uuid, " serial boot ", "#2e7d32")
                if evt.series and not self.gw_sn.text():
                    self.gw_sn.setText(evt.series)
                if evt.model and not self.gw_model.text():
                    self.gw_model.setText(evt.model)
                if evt.mac and not self.gw_mac.text():
                    self.gw_mac.setText(evt.mac)
                if evt.firmware_version:
                    self.gw_model.setToolTip("Firmware: %s" % evt.firmware_version)
                if first:
                    self._status("Tự lấy UUID gateway từ serial boot: %s"
                                 % (evt.uuid or _mac_to_uuid_fallback(evt.mac)))
                    self._refresh_filter_label()
                # Đủ UUID + S/N + MAC từ serial → không cần HTTP fallback nữa.
                if self._gateway_uuid() and self.gw_sn.text() and self.gw_mac.text():
                    self._stop_reboot_watch()
            return
        if isinstance(evt, GatewayReport):
            # Tự lấy UUID gateway từ bản tin [INFO] 60s trên Serial — điền vào ô
            # nhập tay (vẫn để editable để người dùng sửa nếu cần).
            if evt.uuid:
                first = self._set_uuid_from(evt.uuid, " [INFO] 60s ", "#2e7d32")
                if first:
                    self._status("Tự lấy UUID gateway từ [INFO]: %s" % evt.uuid.upper())
                    self._refresh_filter_label()
            if evt.series and not self.gw_sn.text():
                self.gw_sn.setText(evt.series)
            if evt.model and not self.gw_model.text():
                self.gw_model.setText(evt.model)
            if evt.firmware_version:
                self.gw_model.setToolTip("Firmware: %s" % evt.firmware_version)
            if evt.eth_mac and not self.gw_mac.text():
                self.gw_mac.setText(evt.eth_mac)
            if evt.wifi_mac and not self.gw_mac.text():
                self.gw_mac.setText(evt.wifi_mac)
            if evt.eth_ip and not self.gw_ip.text():
                self.gw_ip.setText(evt.eth_ip)
            return
        if isinstance(evt, SerialNotice):
            if evt.kind == "publish_ok":
                if self._counting:
                    self.counter.add_notice("publish_ok")
            elif evt.kind == "not_allowed":
                if self._counting:
                    self.counter.add_notice("not_allowed")
            elif evt.kind == "module_info":
                # Tự lấy UID mạch LoRa receiver từ [LORA] MODULE INFO — hiển thị.
                # Kèm FW/FREQ/BW/TXP để xác minh cấu hình module sau reboot.
                detail = evt.uuid
                if evt.module_fw or evt.module_freq or evt.module_bw or evt.module_txp:
                    parts = []
                    if evt.module_fw:
                        parts.append("FW %s" % evt.module_fw)
                    if evt.module_freq:
                        parts.append("%s Hz" % evt.module_freq)
                    if evt.module_bw:
                        parts.append("BW %s kHz" % evt.module_bw)
                    if evt.module_txp:
                        parts.append("TXP %s dBm" % evt.module_txp)
                    detail += "  [" + ", ".join(parts) + "]"
                self.lbl_receiver_uid.setText(detail)
                self.lbl_receiver_uid.setToolTip(detail)
                self.lbl_receiver_uid.setStyleSheet(
                    "font-weight: bold; color: #007acc;")
                self.counter.add_notice("module_info", uuid=evt.uuid)
                self._status("Tự lấy Receiver UID từ [MODULE INFO]: %s" % detail)
            elif evt.kind == "ip":
                # Gateway vừa lên mạng (boot / reconnect) → có IP để gọi HTTP
                # /api/status lấy UUID + model + MAC mà không cần chờ [INFO] 60s.
                ip = evt.ip
                if ip and not self.gw_ip.text():
                    self.gw_ip.setText(ip)
                self._maybe_fetch_http_after_ip(ip)

    def _on_ref_event(self, evt) -> None:
        """Data frame mạch thu riêng (COM 2) — chỉ tới khi đang thu (cửa sổ test).

        Đẩy row vào table/CSV như gateway; không vào MessageCounter (luồng
        gateway <-> MQTT tách biệt, chỉ dùng log gateway COM).
        """
        if isinstance(evt, LoraRxEvent) and not self._rx_frozen:
            self._push_row(_ref_row(evt))

    # -------------------------------------------------------------- MQTT -----
    def _persist_cfg(self) -> None:
        # Chỉ cập nhật phần mqtt do người dùng sửa trên UI — không ghi đè
        # toàn bộ config đã merge (tránh "đóng băng" default/runtime khác).
        try:
            base = load_config()
        except Exception:
            base = {}
        mq = base.setdefault("mqtt", {})
        mq["host"] = self.mqtt_host.text().strip()
        mq["port"] = self.mqtt_port.value()
        mq["username"] = self.mqtt_user.text().strip()
        mq["password"] = self.mqtt_pass.text()
        mq["subscribe_topics"] = self._topics()
        # Cờ lọc rác thuộc UI — nhớ giữa các lần chạy.
        base.setdefault("ui", {})["filter_garbage"] = bool(self._filter_garbage)
        save_config(base)

    def on_toggle_mqtt(self) -> None:
        if self.mqtt_worker and self.mqtt_worker.isRunning():
            self.mqtt_worker.stop()
            self.mqtt_worker = None
            self.lbl_mqtt.setText("DISCONNECTED")
            self._update_controls()
            return
        self._persist_cfg()
        host = self.mqtt_host.text().strip()
        if not host:
            self._status("Chưa nhập MQTT host")
            return
        topics = self._topics()
        self.mqtt_worker = MqttWorker(
            host=host,
            port=self.mqtt_port.value(),
            username=self.mqtt_user.text().strip(),
            password=self.mqtt_pass.text(),
            topics=topics,
            parent=self,
        )
        self.mqtt_worker.message.connect(self.on_mqtt_message)
        self.mqtt_worker.status.connect(self._on_mqtt_status)
        self.mqtt_worker.start()
        self._update_controls()

    def _on_mqtt_status(self, text: str) -> None:
        if text.startswith("CONNECTED") or text.startswith("SUBSCRIBED"):
            self.lbl_mqtt.setText(text)
            self._status("MQTT: %s" % text)
        else:
            self.lbl_mqtt.setText(text)
        self._update_controls()

    def on_mqtt_message(self, topic: str, payload: str, retain: bool) -> None:
        now = now_pc_str()
        line = "%s | %s | %s%s" % (now, topic, "[RETAINED] " if retain else "", payload)
        self.mqtt_view.append_line(line)
        if self.session.active:
            self.session.append_mqtt_raw(line)

        evt = self.mqtt_parser.parse(topic, payload, retain, now)
        row = _mqtt_row(evt)
        # Gateway UUID lấy từ ô nhập tay; KHÔNG tự gợi ý từ payload MQTT nữa.
        self._push_row(row)

        if not self._counting:
            return
        decision = self.counter.handle_mqtt(
            evt,
            self._gateway_uuid(),
            self._filter_enabled(),
            self._count_retained(),
            allow_verbose=not self._filter_enabled(),
            only_known_rx=self._rx_frozen,
        )
        if decision == "counted" and not self._gateway_uuid():
            pass  # filter tắt — count-only
        self._maybe_target_done()

    # ------------------------------------------------------------ counters ----
    def _push_row(self, row: dict) -> None:
        # Chỉ thêm vào table/CSV khi đang có session (từ lúc START đến lúc finalize).
        # Trước/sau session: raw log vẫn đầy đủ ở panel RAW.
        if not self.session.active:
            return
        # Lọc rác RF (CRC lỗi / unparsed / incomplete) — áp cho CẢ gateway lẫn
        # mạch thu COM2 vì cả 2 cùng đổ qua đây. Counter/diag vẫn đếm riêng.
        if self._filter_garbage and _is_garbage_row(row):
            return
        self._rows_all.append(row)
        self.session.record_row(row)
        self._pending_rows.append(row)
        # Drain nếu nhiều dòng liên tiếp — timer 200ms sẽ flush.
        if len(self._pending_rows) >= 200:
            self._flush_rows()

    def _flush_rows(self) -> None:
        if not self._pending_rows:
            return
        rows = self._pending_rows
        self._pending_rows = []
        if self._search_text:
            # Filter node đang bật: chỉ chèn các dòng khớp (không phá view lọc),
            # rồi lên lịch cập nhật bảng so sánh node khi có dòng mới khớp.
            shown = [_row_row_list(r) for r in rows
                     if _row_matches(r, self._search_text, self._search_text_gw,
                                     self._node_suffix)]
            if shown:
                self.msg_table.add_rows(shown)
                self._schedule_node_compare()
            return
        shown = [_row_row_list(r) for r in rows
                 if _row_matches(r, "", self._search_text_gw, self._node_suffix)]
        self.msg_table.add_rows(shown)

    def _schedule_node_compare(self) -> None:
        """Dồn tái dựng bảng so sánh node khi đang chạy live với filter bật."""
        if self._nc_debounce is None:
            self._nc_debounce = QTimer(self)
            self._nc_debounce.setSingleShot(True)
            self._nc_debounce.timeout.connect(self._refresh_node_compare)
        self._nc_debounce.start(400)

    # --------------------------------------------------------------- test ----
    def _maybe_target_done(self) -> None:
        if self.state != STATE_RUNNING or self._rx_frozen:
            return
        if self._target > 0 and self.counter.rx_total >= self._target:
            self._begin_settle("target", stopped=False)

    def _begin_settle(self, reason: str, stopped: bool = False) -> None:
        self._rx_frozen = True
        # Freeze là hết cửa sổ test cho cả 2 nguồn radio: gateway đã dừng feed
        # (xem _on_serial_event), mạch thu riêng (COM 2) cũng dừng theo — giữ hai
        # luồng cùng mốc kết thúc để không sinh missing giả.
        if self.src_widget is not None:
            self.src_widget.set_recording(False)
        self._set_state(STATE_SETTLE)
        settle = float(self.cfg["test"].get("settle_seconds", 5))
        self._status("Đã đủ điều kiện kết thúc (%s) — chờ settle %.0fs" % (reason, settle))
        QTimer.singleShot(int(settle * 1000), lambda: self._finalize(stopped=stopped))

    def _on_duration_end(self) -> None:
        if self.state == STATE_RUNNING:
            self._begin_settle("duration", stopped=False)

    def on_start_test(self) -> None:
        if self.state == STATE_RUNNING or self.state == STATE_SETTLE:
            return
        self._clear_counters_keep_raw()
        # Mỗi test là 1 cửa sổ đối chiếu riêng: reset bộ so sánh nguồn và bắt
        # đầu thu data frame từ mạch thu riêng (COM 2) song song với gateway.
        self.comparator.reset()
        if self.src_widget is not None:
            # Tự động kết nối mạch thu riêng (COM 2) nếu đã chọn COM mà chưa
            # connect — để 2 luồng đối chiếu chạy song song ngay khi START.
            # Việc connect là bất đồng bộ; data từ lúc worker CONNECTED sẽ vào
            # comparator vì set_recording(True) được bật bên dưới. Nếu chưa
            # chọn COM2 / connect lỗi → vẫn START, luồng nguồn sẽ là N/A.
            if not self.src_widget.ensure_connected():
                self._status("START — mạch thu riêng (COM 2) chưa kết nối: "
                             "chọn COM rồi Connect, hoặc bỏ qua (nguồn = N/A)")
            self.src_widget.set_recording(True)
        self._target = self.target_spin.value()
        self._duration_s = float(self.duration_spin.value()) * 60.0

        gw = self._gateway_uuid() or "NOUUID"
        sid = "%s_%s" % (now_file_ts(), gw[:16])
        self.session.start(sid, gw)
        self.lbl_session.setText("Session: %s" % sid)

        self._started_mono = time.monotonic()
        self._started_str = time.strftime("%Y-%m-%d %H:%M:%S")
        self._counting = True
        self._rx_frozen = False
        self._match = None
        self._summary = None
        self.result_view.clear()

        if self._duration_s > 0:
            self._duration_timer.start(int(self._duration_s * 1000))

        self._set_state(STATE_RUNNING)
        if not gw:
            self._status("START — chưa có Gateway UUID: MQTT chưa filter (chỉ đếm khi có UUID)")
        else:
            self._status("START — session %s (target=%d)" % (sid, self._target or -1))

    def on_stop_test(self) -> None:
        if self.state != STATE_RUNNING and self.state != STATE_SETTLE:
            return
        self._duration_timer.stop()
        self._rx_frozen = True
        self._begin_settle("manual stop", stopped=True)

    def _finalize(self, stopped: bool) -> None:
        self._counting = False
        self._set_state(STATE_DONE)
        self._duration_timer.stop()
        match = compute_match(
            self.counter.rx_seqs,
            self.counter.rx_node_total,
            self.counter.mqtt_seqs,
            self.counter.mqtt_node_total,
            self.counter.rx_total,
            self.counter.mqtt_total,
            self.counter.have_seq,
            self.cfg.get("node", {}).get("new_node_suffix", ""),
        )
        self._match = match

        result, reason = evaluate_result(
            match, self.cfg["pass"], self._target, stopped=stopped)

        s = SessionSummary()
        s.session_id = self.session.session_id
        s.gateway_uuid = self._gateway_uuid()
        s.model = self.gw_model.text()
        s.series = self.gw_sn.text()
        s.firmware_version = ""
        s.mac = self.gw_mac.text()
        s.start_time = self._started_str
        s.end_time = time.strftime("%Y-%m-%d %H:%M:%S")
        s.target_count = self._target
        s.receiver_count = match.rx_total
        s.mqtt_count = match.mqtt_total
        s.missing_count = match.missing_count
        s.duplicate_count = match.duplicates_mqtt
        s.duplicate_mqtt = match.duplicates_mqtt
        s.delivery_rate = match.delivery_rate
        s.result = result
        s.reason = reason
        s.diag = self.counter.diagnostics()
        s.extra["duplicates_rx"] = match.duplicates_rx
        s.extra["matched_count"] = match.matched_count
        s.extra["extra_mqtt"] = match.extra_mqtt
        # Mọi key Rx đều hoặc matched hoặc missing → tổng key Rx = matched + missing.
        s.extra["rx_key_total"] = match.matched_count + match.missing_count
        s.extra["target_count"] = self._target
        self._summary = s

        block = format_result_block(s)
        # Luồng đối chiếu 2 (mạch thu riêng COM 2 vs gateway) — tự chấm như luồng
        # gateway <-> MQTT rồi lưu kèm trong cùng session (summary + result.txt).
        sec = self._source_compare_section()
        if sec is not None:
            data, sec_text = sec
            s.extra["source_compare"] = data
            block = format_result_block(s) + sec_text
        self.result_view.setPlainText(block)
        self.lbl_corr.setText("Message correlation: %s" % match.correlation)

        if self.session.active:
            self.session.finish(s.to_dict(), block)
        self._status("Kết thúc test: %s (%s)" % (result, reason))
        self._refresh_live_labels()

    def on_clear(self) -> None:
        self._duration_timer.stop()
        self.session.stop_session_io()
        self._clear_counters_keep_raw()
        self.serial_parser.reset()
        if self.src_widget:
            self.src_widget.reset()
            self.src_widget.set_recording(False)
        self.comparator.reset()
        self._match = None
        self._summary = None
        self._set_state(STATE_IDLE)
        self.lbl_session.setText("Session: -")
        # Reset trạng thái đã tự lấy UUID/Receiver UID để bắt đầu lại khi chọn
        # COM/gateway mới.
        self._stop_reboot_watch()
        self._reboot_pending = False
        self._fetched_ips.clear()
        self.gw_uuid.clear()
        self.lbl_uuid_src.setText("")
        self.lbl_receiver_uid.setText("-")
        self.lbl_receiver_uid.setStyleSheet("font-weight: bold; color: #007acc;")
        self._refresh_filter_label()
        self._status("Đã CLEAR counters (kết nối giữ nguyên)")

    def on_compare_sources(self) -> None:
        """Đối chiếu data frame của mạch thu riêng (COM 2) với gateway."""
        result = self.comparator.compute()
        verdict = self._source_compare_verdict(result)
        text = self._compare_result_text(result, verdict)
        box = QMessageBox(self)
        box.setWindowTitle("Đối chiếu nguồn thu")
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.setText(text)
        box.setStandardButtons(QMessageBox.Close)
        box.exec_()

    def _source_compare_verdict(self, r: dict) -> tuple[str, str]:
        """Chấm luồng mạch thu riêng (COM 2) -> gateway bằng ngưỡng config pass.

        FAIL = gateway mất bản tin mà mạch thu riêng vẫn nhận (missing quá ngưỡng
        hoặc tỷ lệ khớp thấp). N/A khi không có data frame nào từ mạch thu riêng.
        """
        if not r["total_second"]:
            return ("N/A",
                    "Không có data frame nào từ mạch thu riêng (COM 2) — chưa đối chiếu được.")
        max_missing = int(self.cfg["pass"].get("max_missing", 0))
        min_rate = float(self.cfg["pass"].get("min_delivery_rate_percent", 100.0))
        missing_ok = r["missing"] <= max_missing
        rate = r["delivery_rate"]
        rate_ok = rate is not None and rate + 1e-9 >= min_rate
        extra_ok = r.get("extra", 0) == 0
        reasons = []
        if not missing_ok:
            reasons.append("Missing %d > max_missing %d" % (r["missing"], max_missing))
        if not rate_ok:
            reasons.append("Tỷ lệ khớp %.2f%% < %.2f%%" % ((rate or 0.0), min_rate))
        if not extra_ok:
            reasons.append("Có %d key chỉ có ở gateway (extra)" % r.get("extra", 0))
        ok = missing_ok and rate_ok and extra_ok
        return ("PASS" if ok else "FAIL"), ("; ".join(reasons) or "Tỷ lệ khớp đạt ngưỡng cấu hình.")

    def _compare_result_text(self, r: dict, verdict: tuple[str, str] | None = None) -> str:
        lines = [
            "SO SÁNH NGUỒN THU (mạch thu riêng COM 2 vs gateway)",
            "=" * 48,
            "",
            "Data frame mạch thu nhận : %d" % r["total_second"],
            "Data frame gateway nhận   : %d" % r["total_gateway"],
            "Key mạch thu (UID,seq)    : %d" % r.get("second_keys", 0),
            "Key gateway (UID,seq)     : %d" % r.get("gateway_keys", 0),
            "Khớp (có ở nguồn 2 và GW): %d" % r["matched"],
            "Missing (nguồn 2 có, GW không): %d" % r["missing"],
            "Extra (GW có, nguồn 2 không) : %d" % r.get("extra", 0),
            "Tỷ lệ khớp             : %s" % (
                "N/A" if r["delivery_rate"] is None else "%.2f%%" % r["delivery_rate"]),
            "",
            "Per-node:",
            "%-24s %7s %7s %7s %7s %7s %8s" % (
                "UID (8 byte)", "Nguồn2", "GW", "Keys", "Khớp", "Missing", "%khớp"),
            "-" * 74,
        ]
        for row in r["per_node"]:
            rate = "N/A" if row["delivery_rate"] is None else "%.2f%%" % row["delivery_rate"]
            lines.append("%-24s %7d %7d %7d %7d %7d %8s" % (
                row["uid"], row["second"], row["gateway"], row.get("rx_keys", 0),
                row.get("matched", 0), row["missing"], rate))
        if verdict is not None:
            result, reason = verdict
            lines.append("")
            lines.append("Verdict (mạch thu riêng → gateway): %s" % result)
            lines.append("Reason: %s" % reason)
        return "\n".join(lines)

    def _source_compare_section(self) -> tuple[dict, str] | None:
        """Tính + format khối đối chiếu nguồn (COM 2 vs gateway) cho session.

        Trả về (data để lưu summary.json, text nối vào result.txt) hoặc None nếu
        không có data frame nguồn nào trong cửa sổ test.
        """
        r = self.comparator.compute()
        if not (r["total_second"] or r["total_gateway"]):
            return None
        result, reason = self._source_compare_verdict(r)
        data = {
            "result": result,
            "reason": reason,
            "total_second": r["total_second"],
            "total_gateway": r["total_gateway"],
            "second_keys": r.get("second_keys", 0),
            "gateway_keys": r.get("gateway_keys", 0),
            "matched": r["matched"],
            "missing": r["missing"],
            "extra": r.get("extra", 0),
            "delivery_rate": r["delivery_rate"],
            "per_node": r["per_node"],
        }
        return data, "\n" + self._compare_result_text(r, (result, reason))

    def _clear_counters_keep_raw(self) -> None:
        self._counting = False
        self._rx_frozen = False
        self.counter.reset()
        self._rows_all.clear()
        self._pending_rows.clear()
        self.msg_table.setRowCount(0)
        self.node_table.setRowCount(0)
        self.src_compare_table.setRowCount(0)
        self.node_compare_table.setRowCount(0)
        # Bỏ filter node đang áp (nếu có) — các bảng đã rỗng nên không giữ state
        # tìm kiếm cũ, tránh nhầm khi bắt test mới.
        if hasattr(self, "search_edit") and self.search_edit.text():
            self.search_edit.clear()
        if hasattr(self, "search_gw_edit") and self.search_gw_edit.text():
            self.search_gw_edit.clear()
        self._search_text = ""
        self._search_text_gw = ""
        if self._search_debounce is not None:
            self._search_debounce.stop()
        if self._nc_debounce is not None:
            self._nc_debounce.stop()
        self.result_view.clear()
        self.cnt_rx.setText("0")
        self.cnt_mqtt.setText("0")
        self.cnt_missing.setText("0")
        self.cnt_dup.setText("0")
        self.cnt_rate.setText("-")
        self.cnt_time.setText("first/last: -")
        self.lbl_corr.setText("Message correlation: COUNT ONLY")
        self.lbl_diag.setText("diag: -")

    # ------------------------------------------------------------ refresh ----
    def _on_refresh(self) -> None:
        self._flush_rows()
        self._refresh_live_labels()
        self._refresh_node_table()
        self._refresh_src_compare_table()
        self._refresh_filter_label()
        self._update_controls()

    def _refresh_live_labels(self) -> None:
        self.cnt_rx.setText(str(self.counter.rx_total))
        self.cnt_mqtt.setText(str(self.counter.mqtt_total))

        if self._match is not None:
            m = self._match
            self.cnt_missing.setText(str(m.missing_count))
            self.cnt_dup.setText("%d / %d" % (m.duplicates_mqtt, m.duplicates_rx))
            rate = m.delivery_rate
            self.cnt_rate.setText("N/A" if rate is None else "%.2f%%" % rate)
            t0 = self.counter.rx_first or self.counter.mqtt_first or ""
            t1 = self.counter.rx_last or self.counter.mqtt_last or ""
            self.cnt_time.setText("first: %s | last: %s" % (t0 or "-", t1 or "-"))
        elif self.state in (STATE_RUNNING, STATE_SETTLE):
            self.counter.sample()
            elapsed = time.monotonic() - self._started_mono
            rates = self.counter.rates(elapsed if elapsed > 0 else None)
            self.cnt_missing.setText(str(max(0, self.counter.rx_total - self.counter.mqtt_total)))
            self.cnt_rate.setText(
                "%.2f / %.2f msg/s  (%.2f / %.2f gần đây)"
                % (rates["rx_msg_s"], rates["mqtt_msg_s"],
                   rates["rx_recent_s"], rates["mqtt_recent_s"]))
            t0 = self.counter.rx_first or self.counter.mqtt_first or ""
            t1 = self.counter.rx_last or self.counter.mqtt_last or ""
            self.cnt_time.setText("first: %s | last: %s" % (t0 or "-", t1 or "-"))
            self.cnt_dup.setText("live…")
        else:
            self.cnt_missing.setText(str(self.counter.rx_total - self.counter.mqtt_total))
            self.cnt_rate.setText("-")
            self.cnt_dup.setText("0")
            self.cnt_time.setText("first/last: -")

        diag = self.counter.diagnostics()
        self.lbl_diag.setText(
            "diag RX: info=%d crc_err=%d unparsed=%d incomplete=%d | "
            "MQTT: info=%d log=%d err=%d verbose=%d other=%d other_gw=%d no_uuid=%d no_gwid=%d retained=%d | "
            "gw_publish_ok=%d not_allowed=%d module_uid=%s"
            % (diag["rx_info"], diag["rx_crc_error"], diag["rx_unparsed"],
               diag["rx_incomplete"], diag["mqtt_info"], diag["mqtt_log"],
               diag["mqtt_error"], diag["mqtt_verbose"], diag["mqtt_other"],
               diag["mqtt_other_gateway"], diag["mqtt_no_uuid"],
               diag["mqtt_no_gw_id"], diag["mqtt_retained_ignored"],
               diag["gateway_publish_ok"], diag["sensor_not_allowed"],
               diag["lora_module_uid"]))

    def _refresh_node_table(self) -> None:
        if self._match is not None:
            self.node_table.refresh(self._match.per_node)
            return
        # Chưa chấm điểm: dựng bảng tạm theo KEY (UID, seq) — seq duy nhất trên
        # mỗi UID node nên đối chiếu qua key mới phản ánh đúng missing/delivery.
        # Không dùng hiệu số đếm bản tin (rx - mq) vì dễ sai khi có re-publish.
        suffix = self._node_suffix
        rx_norm: dict[str, dict[int, int]] = {}
        mq_norm: dict[str, dict[int, int]] = {}
        rx_cnt: dict[str, int] = {}
        mq_cnt: dict[str, int] = {}
        for node, seqs in self.counter.rx_seqs.items():
            key = _normalize_id(node, suffix)
            d = rx_norm.setdefault(key, {})
            for seq, c in seqs.items():
                d[seq] = d.get(seq, 0) + c
            rx_cnt[key] = rx_cnt.get(key, 0) + self.counter.rx_node_total.get(node, 0)
        for node, seqs in self.counter.mqtt_seqs.items():
            key = _normalize_id(node, suffix)
            d = mq_norm.setdefault(key, {})
            for seq, c in seqs.items():
                d[seq] = d.get(seq, 0) + c
            mq_cnt[key] = mq_cnt.get(key, 0) + self.counter.mqtt_node_total.get(node, 0)

        rows = []
        for node in sorted(set(rx_norm) | set(mq_norm)):
            rx_seqs = rx_norm.get(node, {})
            mq_seqs = mq_norm.get(node, {})
            rx_keys = len(rx_seqs)
            matched = sum(1 for seq in rx_seqs if mq_seqs.get(seq, 0) > 0)
            missing = sum(1 for seq in rx_seqs if mq_seqs.get(seq, 0) == 0)
            rows.append({
                "node_uuid": node,
                "kind": node_kind_from_id(node),
                "receiver": rx_cnt.get(node, 0),
                "mqtt": mq_cnt.get(node, 0),
                "rx_keys": rx_keys,
                "missing": missing,
                "delivery_rate": None if rx_keys == 0 else (matched / rx_keys * 100.0),
                "dup": 0,
            })
        self.node_table.refresh(rows)

    def _refresh_filter_label(self) -> None:
        gw = self._gateway_uuid()
        if not self._filter_enabled():
            self.lbl_filter.setText("MQTT Filter: COUNT-ONLY (mọi data frame)")
            return
        if gw:
            self.lbl_filter.setText("MQTT Filter: ACTIVE (uuid=%s)" % gw)
        else:
            self.lbl_filter.setText("MQTT Filter: WAIT UUID")

    # ------------------------------------------------------- node search -----
    def on_search_text(self, text: str) -> None:
        """Search bar: tìm bản tin của 1 node (typing → debounce 300ms).

        Từ khoá là UID node — chuẩn hoá in hoa + so cả 2 dạng (tự nhiên / sau
        khi bóc suffix). Rỗng = hiện tất cả.
        """
        self._search_text = text.strip()
        self._schedule_search()

    def on_search_gw_text(self, text: str) -> None:
        """Search bar gateway: tìm bản tin do 1 gateway bắn (typing → debounce).

        Từ khoá là UID gateway (vd 4F9199E139C, 1 phần cũng được) — lọc theo
        `gateway_uuid` trên mỗi dòng để cô lập bản tin MQTT của đúng gateway đó
        khi có nhiều gateway cùng bắn trên broker. Rỗng = hiện tất cả.
        """
        self._search_text_gw = text.strip()
        self._schedule_search()

    def _schedule_search(self) -> None:
        if self._search_debounce is None:
            self._search_debounce = QTimer(self)
            self._search_debounce.setSingleShot(True)
            self._search_debounce.timeout.connect(self._apply_search)
        self._search_debounce.start(300)
        if self._search_text or self._search_text_gw:
            self.lbl_search_status.setText(
                "Đang lọc node '%s', gateway '%s' — hiện bản tin khớp trên cả 3 "
                "nguồn (LORA gateway | COM2 | MQTT)."
                % (self._search_text or "*", self._search_text_gw or "*"))
        else:
            self.lbl_search_status.setText(
                "Gõ UID node / UID gateway (1 phần cũng được) để lọc bản tin của "
                "node đó / do gateway đó bắn trên 3 nguồn.")

    def on_filter_garbage_toggled(self, checked: bool) -> None:
        """Bật/tắt lọc rác RF — rebuild Message Log + nhớ vào config.json."""
        self._filter_garbage = bool(checked)
        self._persist_cfg()
        self._refresh_msg_table_filtered()
        if self._filter_garbage:
            self.lbl_search_status.setText(
                "Đang LỌC bản tin rác (CRC lỗi/unparsed) — vẫn đếm ở tab Counters.")
        else:
            self.lbl_search_status.setText(
                "Đang HIỆN cả bản tin rác (CRC lỗi/unparsed) trong Message Log.")

    def _apply_search(self) -> None:
        if self._nc_debounce is not None:
            self._nc_debounce.stop()
        self._refresh_msg_table_filtered()
        # Rebuild đã render toàn bộ `_rows_all` — bỏ batch pending còn sót để
        # `_flush_rows` sau đó không chèn trùng dòng nữa.
        self._pending_rows.clear()
        self._refresh_node_compare()

    def _refresh_msg_table_filtered(self) -> None:
        """Xây lại Message Log theo filter node + gateway (gọi khi search thay đổi)."""
        max_rows = int(self.cfg["ui"].get("table_max_rows", 50000))
        q = self._search_text.strip()
        q_gw = self._search_text_gw.strip()
        items = []
        for row in self._rows_all:
            if not _row_matches(row, q, q_gw, self._node_suffix):
                continue
            items.append(row)
            if len(items) >= max_rows:
                break
        self.msg_table.setRowCount(0)
        self.msg_table.add_rows([_row_row_list(r) for r in items])

    # --------------------------------------------- compare 3 nguồn per-node --
    def _refresh_src_compare_table(self) -> None:
        """Bảng live (COM2 ↔ gateway) từ SourceComparator — hiển thị ngay khi bộ
        so sánh có dữ liệu (trong lúc test + sau finalize cho tới khi CLEAR)."""
        r = self.comparator.compute()
        if not (r["total_second"] or r["total_gateway"]):
            self.src_compare_table.refresh([])
            return
        self.src_compare_table.refresh(r["per_node"])

    def _get_node_rows(self, node_q: str) -> list:
        """Lọc bản tin của 1 node từ `_rows_all`.

        Dùng cùng cơ chế `_node_matches` (so cả UID tự nhiên + sau khi bóc
        suffix) — khớp node mới 16 hex và node cũ 24 hex. Áp thêm bộ lọc
        gateway đang bật (nếu có) để bảng đối chiếu node khớp Message Log.
        """
        out = []
        for row in self._rows_all:
            if _row_matches(row, node_q, self._search_text_gw, self._node_suffix):
                out.append(row)
        return out

    def _refresh_node_compare(self) -> None:
        """Ráp 3 nguồn cho node đang chọn theo Seq → bảng node_compare_table.

        Với mỗi seq, cho biết nguồn nào có bản tin (✓). Nguồn: LORA (gateway
        serial), REF (mạch thu riêng COM2), MQTT (server). Dùng để đối chiếu bản
        tin gateway vs mạch thu riêng sau khi decode theo (UID node, seq).
        """
        q = self._search_text
        if not q:
            self.node_compare_table.setRowCount(0)
            return

        rows = self._get_node_rows(q)
        by_seq: dict[str, dict] = {}
        for row in rows:
            seq = row.get("sequence", "")
            d = by_seq.setdefault(str(seq), {"seq": seq})
            src = row.get("source", "")
            rssi = row.get("rssi", "")
            snr = row.get("snr", "")
            if rssi not in ("", None):
                rs = "R%s/S%s" % (rssi, snr if snr not in ("", None) else "-")
            else:
                rs = ""
            cell = {"time": row.get("pc_time", ""), "rssi_snr": rs, "present": True}
            if src == SRC_LORA:
                d["lora"] = cell
            elif src == SRC_REF:
                d["ref"] = cell
            elif src == SRC_MQTT:
                d["mqtt"] = cell

        def seq_key(s: str) -> tuple:
            try:
                return (0, int(s))
            except (TypeError, ValueError):
                return (1, s)

        ordered = [by_seq[k] for k in sorted(by_seq.keys(), key=seq_key)]
        self.node_compare_table.refresh(ordered)

    def closeEvent(self, event) -> None:
        try:
            self._persist_cfg()
        except Exception:
            pass
        self._stop_reboot_watch()
        if self._search_debounce is not None:
            self._search_debounce.stop()
        if self._nc_debounce is not None:
            self._nc_debounce.stop()
        if self._http_worker and self._http_worker.isRunning():
            self._http_worker.wait(3000)
        if self.src_widget:
            self.src_widget.stop()
        if self.serial_worker:
            self.serial_worker.stop()
        if self.mqtt_worker:
            self.mqtt_worker.stop()
        self.session.stop_session_io()
        super().closeEvent(event)

    # ------------------------------------------------------------ export ------
    def on_save_log(self) -> None:
        try:
            from shutil import copy2
            base = ensure_logs_dir() / ("save_%s" % now_file_ts())
            base.mkdir(parents=True, exist_ok=True)
            if self.session.active and self.session.dir:
                src = self.session.dir
                for f in ("uart_raw.log", "mqtt_raw.log", "messages.csv"):
                    p = src / f
                    if p.exists():
                        copy2(p, base / f)
            else:
                (base / "uart_ui.log").write_text(
                    "\n".join(self.uart_view.toPlainText().splitlines()), encoding="utf-8")
                (base / "mqtt_ui.log").write_text(
                    "\n".join(self.mqtt_view.toPlainText().splitlines()), encoding="utf-8")
            self._status("Đã lưu log vào %s" % base)
        except Exception as e:
            self._status("Save Log lỗi: %s" % e)

    def on_export_csv(self) -> None:
        default = ""
        if self.session.active and self.session.messages_path:
            default = str(self.session.messages_path)
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", default, "CSV (*.csv)")
        if not path:
            return
        try:
            import csv
            if self.session.active and self.session.messages_path:
                from shutil import copy2
                copy2(self.session.messages_path, path)
            else:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=_ROW_KEYS)
                    w.writeheader()
                    for row in self._rows_all:
                        w.writerow(row)
            self._status("Đã export CSV: %s (%d rows)" % (path, len(self._rows_all)))
        except Exception as e:
            self._status("Export CSV lỗi: %s" % e)

    def on_export_compare_csv(self) -> None:
        """Export kết quả ĐỐI CHIẾU ra CSV phẳng (flow + node + message).

        Gồm tối đa 2 luồng: gateway↔MQTT (`self._match`) và COM2↔gateway
        (`self.comparator.compute()`) — kèm phần per-(UID,seq) cho biết mỗi key
        có mặt ở nguồn nào + kết luận. Hoạt động cả khi chưa chạy test (dữ liệu
        COM2/gateway đã thu vẫn xuất được).
        """
        default_dir = ensure_logs_dir()
        if self.session.active and self.session.dir:
            default_dir = self.session.dir
        default = str(Path(default_dir) / ("compare_%s.csv" % now_file_ts()))
        path, _ = QFileDialog.getSaveFileName(
            self, "Export kết quả đối chiếu", default, "CSV (*.csv)")
        if not path:
            return
        try:
            src_result = self.comparator.compute()
            src_verdict = self._source_compare_verdict(src_result)
            gw_verdict = None
            if self._match is not None:
                try:
                    gw_verdict = evaluate_result(
                        self._match, self.cfg["pass"], self._target)
                except Exception:
                    gw_verdict = None
            rows = build_compare_export(
                self._rows_all,
                match=self._match,
                src_result=src_result,
                suffix=self._node_suffix,
                gw_verdict=gw_verdict,
                src_verdict=src_verdict,
            )
            n = write_compare_csv(path, rows)
            self._status("Đã export đối chiếu: %s (%d dòng)" % (path, n))
        except Exception as e:
            self._status("Export đối chiếu lỗi: %s" % e)
