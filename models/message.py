"""Data models dùng chung cho tool Gateway LoRa Message Counter.

Mọi sự kiện từ Serial/MQTT được chuẩn hoá về dataclass ở đây trước khi vào
counter/matcher/UI. Giờ PC luôn là giờ local của máy chạy tool (HH:MM:SS.mmm);
giờ gateway (TIME:) chỉ là metadata, có thể rỗng nếu NVS/NTP chưa sync.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Hằng số loại message / sự kiện
# ---------------------------------------------------------------------------
# Receiver (Serial) kinds — chỉ RX_KIND_DATA được tính vào metric PASS/FAIL.
RX_KIND_DATA = "DATA"
RX_KIND_INFO = "INFO"
RX_KIND_CRC_ERROR = "CRC_ERROR"
RX_KIND_UNPARSED = "UNPARSED"
RX_KIND_INCOMPLETE = "INCOMPLETE"

# MQTT kinds — chỉ MQTT_KIND_DATA được tính khi gateway filter khớp.
MQTT_KIND_DATA = "DATA"
MQTT_KIND_INFO = "INFO"
MQTT_KIND_LOG = "LOG"       # topic node/<uuid>/log — log/status node (diag, không đếm)
MQTT_KIND_ERROR = "ERROR"
MQTT_KIND_VERBOSE = "VERBOSE"   # payload hệ cũ (không gateway_id) — count-only
MQTT_KIND_OTHER = "OTHER"       # json hỏng / topic lạ

SRC_LORA = "LORA"      # log gateway (COM chính) — data frame mạch thu gateway
SRC_REF = "REF"        # mạch thu riêng (COM 2) — nguồn đối chiếu độc lập
SRC_MQTT = "MQTT"


def now_pc_str() -> str:
    """Giờ local PC dạng HH:MM:SS.mmm."""
    t = time.localtime()
    ms = int(time.time() * 1000) % 1000
    return "%02d:%02d:%02d.%03d" % (t.tm_hour, t.tm_min, t.tm_sec, ms)


def now_file_ts() -> str:
    """YYYYMMDD_HHMMSS cho session id / tên file."""
    return time.strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Loại node: phân biệt node cũ (UUID 12 byte = 24 hex) và node mới (UID 8 byte
# = 16 hex). Dùng chung cho cả Rx (Serial) lẫn MQTT nhờ độ dài chuỗi id.
# ---------------------------------------------------------------------------
NODE_KIND_OLD = "OLD"   # 24 hex (12 byte) — frame 22/26/42, XOR CRC
NODE_KIND_NEW = "NEW"   # 16 hex (8 byte)  — data 17+4N / info 28, CRC-16 Modbus
NODE_KIND_UNKNOWN = "UNKNOWN"


def node_kind_from_id(node_id: str) -> str:
    """Xác định loại node từ chuỗi id hex (đã loại chữ thường/hoa).

    - 24 ký tự hex -> NODE_KIND_OLD
    - 16 ký tự hex -> NODE_KIND_NEW
    - khác         -> NODE_KIND_UNKNOWN
    """
    n = len(node_id)
    if n == 24:
        return NODE_KIND_OLD
    if n == 16:
        return NODE_KIND_NEW
    return NODE_KIND_UNKNOWN


@dataclass
class LoraRxEvent:
    """Một packet LoRa mà gateway vừa log trên Serial (block 2 dòng)."""

    kind: str                       # RX_KIND_*
    pc_time: str                    # giờ PC lúc tool nhận dòng log
    gw_time: str                    # TIME: từ firmware (rỗng nếu NTP chưa sync)
    receiver_id: str                # UID module thu LoRa (16 hex) — KHÔNG phải gateway uuid
    node_uuid: str                  # sensor id hex (16/24 hex), '' nếu unparsed
    seq: Optional[int]              # sequence của frame, None nếu không xác định
    rssi: Optional[int]
    snr: Optional[int]
    length: int
    frame_hex: str                  # hex frame (đã bỏ dấu phẩy)
    crc_ok: bool = False
    raw_lines: list = field(default_factory=list)
    node_kind: str = ""             # NODE_KIND_* — tính từ độ dài node_uuid


@dataclass
class MqttEvent:
    """Một MQTT message đã parse."""

    kind: str                       # MQTT_KIND_*
    pc_time: str
    topic: str
    gateway_id: str                 # '' nếu payload không có (hệ cũ / json hỏng)
    node_uuid: str
    seq: Optional[int]
    rssi: Optional[int]
    snr: Optional[int]
    gw_ts: str                      # timestamp từ firmware ('' nếu không có)
    payload: str                    # raw payload (text)
    retain: bool = False
    node_kind: str = ""             # NODE_KIND_* — tính từ độ dài node_uuid


@dataclass
class SerialNotice:
    """Sự kiện phụ trên Serial (không phải packet): dùng chẩn đoán."""

    kind: str       # 'not_allowed' | 'publish_ok' | 'subscribe' | 'ip' | 'module_info' | 'text'
    text: str = ""
    topic: str = ""
    uuid: str = ""
    ip: str = ""
    # Thông tin mô-đun LoRa (kind == "module_info") — FW/FREQ/BW/TXP từ log
    # "[LORA] MODULE INFO | UID: ... | FW: ... | FREQ: ... | BW: ... | TXP: ...".
    module_fw: str = ""          # phiên bản firmware mô-đun (số nguyên dạng chuỗi)
    module_freq: str = ""        # tần số (Hz)
    module_bw: str = ""          # băng thông (kHz)
    module_txp: str = ""         # công suất phát (dBm)


@dataclass
class GatewayReport:
    """Report 60s của gateway qua Serial `[INFO] {...}` — nguồn UUID fallback."""

    uuid: str = ""
    model: str = ""
    series: str = ""
    firmware_version: str = ""
    status: str = ""
    eth_ip: str = ""
    wifi_ip: str = ""
    eth_mac: str = ""
    wifi_mac: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class GatewayBootInfo:
    """Identity của gateway bắt từ block boot banner trên Serial:

        [INFO]    DEVICE INFO:
        [INFO]    Model : MGWI-V1
        [INFO]    Series: MGWI-V1-26070019
        [INFO]    UUID  : 4F9199E139C
        [INFO]    MAC   : 04:F9:19:9E:13:9C
        ...

    Block này firmware in NGAY sau reboot (trước [ OK ] ETHERNET INITIALIZED),
    nên là nguồn nhanh nhất để lấy UUID — nhanh hơn HTTP /api/status (phải chờ
    IP) và [INFO] 60s. uuid là dạng hex rút gọn từ MAC efuse (vd MAC
    04:F9:19:9E:13:9C → UUID 4F9199E139C — 11 ký tự, KHÔNG nhất thiết 16 hex).
    """

    uuid: str = ""              # hex rút gọn từ MAC efuse (thường 11 ký tự)
    model: str = ""
    series: str = ""
    mac: str = ""               # dạng xx:xx:xx:xx:xx:xx
    firmware_version: str = ""
    source: str = "serial"      # 'serial' — để phân biệt với http/manual

    @property
    def complete(self) -> bool:
        """Đủ dữ kiện nhận diện gateway (UUID + MAC)."""
        return bool(self.uuid and self.mac)


@dataclass
class GatewayInfo:
    """Identity lấy từ HTTP /api/status + /api/general-log."""

    uuid: str = ""
    model: str = ""
    series: str = ""
    firmware_version: str = ""
    mac: str = ""                   # eth_mac ưu tiên, fallback wifi_mac
    ip: str = ""
    mqtt_connected: Optional[bool] = None
    source: str = ""                # 'http' | 'serial' | 'manual'
    extra: dict = field(default_factory=dict)


@dataclass
class MatchResult:
    """Kết quả đối soát receiver ↔ MQTT sau settle window.

    Đối soát theo KEY `(node_id chuẩn hoá, seq)` — vì `seq` là DUY NHẤT trên mỗi
    UID node nên 1 key = 1 bản tin logic. Mọi metric chấm điểm (matched/missing/
    extra/delivery_rate) tính theo SỐ KEY, không theo số lần nhận; số lần nhận
    chỉ dùng để phát hiện duplicate.
    """

    rx_total: int = 0                # số data frame Rx nhận (đếm bản tin)
    mqtt_total: int = 0              # số data frame MQTT nhận (đếm bản tin)
    matched_count: int = 0           # (node,seq) có ở CẢ Rx và MQTT
    missing_count: int = 0           # rx (node,seq) không thấy trên MQTT
    extra_mqtt: int = 0              # (node,seq) CHỈ có trên MQTT (re-publish/echo)
    duplicates_rx: int = 0
    duplicates_mqtt: int = 0
    delivery_rate: Optional[float] = None   # matched_count / số key Rx; None = N/A
    per_node: list = field(default_factory=list)  # list[dict] cho bảng node
    per_kind: dict = field(default_factory=dict)  # thống kê tách theo loại node (OLD/NEW)
    correlation: str = "COUNT ONLY"          # 'AVAILABLE' khi có (node,seq)


@dataclass
class TestParams:
    target_messages: int = 0         # 0 = vô hạn (chỉ theo duration)
    duration_seconds: float = 0.0    # 0 = vô hạn (chỉ theo target)
    settle_seconds: float = 5.0


@dataclass
class SessionSummary:
    """Dữ liệu lưu summary.json + hiển thị TEST RESULT."""

    session_id: str = ""
    gateway_uuid: str = ""
    model: str = ""
    series: str = ""
    firmware_version: str = ""
    mac: str = ""
    start_time: str = ""
    end_time: str = ""
    target_count: int = 0
    receiver_count: int = 0
    mqtt_count: int = 0
    missing_count: int = 0
    duplicate_count: int = 0
    duplicate_mqtt: int = 0
    delivery_rate: Optional[float] = None
    result: str = ""                # PASS / FAIL / N/A / STOPPED
    reason: str = ""
    diag: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "session_id": self.session_id,
            "gateway_uuid": self.gateway_uuid,
            "model": self.model,
            "series": self.series,
            "firmware_version": self.firmware_version,
            "mac": self.mac,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "target_count": self.target_count,
            "receiver_count": self.receiver_count,
            "mqtt_count": self.mqtt_count,
            "missing_count": self.missing_count,
            "duplicate_count": self.duplicate_count,
            "duplicate_mqtt": self.duplicate_mqtt,
            "delivery_rate": self.delivery_rate,
            "result": self.result,
            "reason": self.reason,
            "diagnostics": dict(self.diag),
        }
        d.update(self.extra)
        return d
