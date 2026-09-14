"""Parser log Serial của gateway → sự kiện packet LoRa + sự kiện chẩn đoán.

Marker chính xác (không phải "1 dòng = 1 packet") từ firmware:
  src/data_processing_task.cpp:472-488

  \r\n[LORA]    RECEIVER ID: <16 hex> | TIME: <iso>      <- mở packet (1 call LOG)
  [LORA]    RSSI: <n> | SNR: <n> | LENGTH: <n> | DATA: <hex>  <- finalize (1 packet)

Một packet được in thành ĐÚNG 1 block 2 dòng liên tiếp bởi Data_Decoding_Task
(sau whitelist, TRƯỚC parse CRC). Tool tự kiểm tra CRC/format để chỉ tính
data frame (seq > 0, CRC OK) — mô phỏng parse_node_packet.

Các dòng chẩn đoán khác được bắt riêng (IP gateway, report [INFO], publish OK,
sensor not allowed, module info).
"""

from __future__ import annotations

import json
import re

from core.lora_frame import FrameDecode, decode_frame, hex_to_bytes
from models.message import (
    GatewayBootInfo,
    GatewayReport,
    LoraRxEvent,
    RX_KIND_CRC_ERROR,
    RX_KIND_DATA,
    RX_KIND_INCOMPLETE,
    RX_KIND_INFO,
    RX_KIND_UNPARSED,
    SerialNotice,
    node_kind_from_id,
)

RE_RX_ANCHOR = re.compile(r"^\[LORA\]    RECEIVER ID: ([0-9A-F]{16}) \| TIME: (.*)$")
RE_RX_DATA = re.compile(
    r"^\[LORA\]    RSSI: (-?\d+) \| SNR: (-?\d+) \| LENGTH: (\d+) \| DATA: ([0-9A-Fa-f,]+)$"
)
RE_EVT_RXP2P = re.compile(r"^\+EVT:RXP2P:(-?\d+):(-?\d+):([0-9A-Fa-f]+)$")
RE_NOT_ALLOWED = re.compile(r"^\[FAIL\]    SENSOR IS NOT ALLOWED \[([^\]]*)\]$")
RE_PUBLISH_OK = re.compile(r"^\[ OK \]    MQTT PUBLISHED \[TOPIC: ([^\]]+)\]$")
RE_ETH_IP = re.compile(r"^\[ OK \]    ETHERNET CONNECTED \[IP: ([\d\.]+)\]$")
RE_WIFI_IP = re.compile(r"^\[ OK \]    WIFI CONNECTED \[SSID: [^\]]*, IP: ([\d\.]+)\]$")
RE_GW_REPORT = re.compile(r"^\[INFO\]    (\{.*\})$")
RE_MODULE_INFO = re.compile(
    r"^\[LORA\]    MODULE INFO \| UID: ([0-9A-F]{16}) \| "
    r"FW: (\d+) \| FREQ: (\d+) Hz \| BW: (\d+) kHz \| TXP: (-?\d+) dBm")

# Block boot banner gateway — firmware in NGAY sau reboot (nguồn nhanh nhất cho
# UUID gateway, trước cả HTTP /api/status lẫn [INFO] 60s):
#
#   [INFO]    DEVICE INFO:
#   [INFO]    Model : MGWI-V1
#   [INFO]    Series: MGWI-V1-26070019
#   [INFO]    UUID  : 4F9199E139C          <- hex rút gọn từ MAC efuse (11 ký tự)
#   [INFO]    MAC   : 04:F9:19:9E:13:9C
#   [INFO]    MQTT  : mqtt://...
#   [INFO]    LoRa  : 920600000 Hz | BW=0 | TXP=11 dBm
#
# Regex dung sai nhẹ về khoảng trắng (số space sau [INFO] / trước ':' có thể
# lệch giữa các bản firmware). Chỉ nhận dòng có dạng "Nhãn : giá trị".
RE_BOOT_HEADER = re.compile(r"^\[INFO\]\s+DEVICE INFO:?\s*$")
RE_BOOT_FIELD = re.compile(
    r"^\[INFO\]\s+([A-Za-z][A-Za-z ]*?)\s*:\s*(.*?)\s*$")

# MAC dạng xx:xx:xx:xx:xx:xx (hoặc thiếu dấu ':') — dùng để suy UUID khi block
# không in dòng UUID.
RE_MAC_HEX = re.compile(r"^[0-9A-Fa-f:]{11,17}$")

# Label nhận diện được trong block DEVICE INFO → (thuộc tính của GatewayBootInfo).
_BOOT_LABEL_MAP = {
    "uuid": "uuid",
    "model": "model",
    "series": "series",
    "mac": "mac",
    "firmware": "firmware_version",
    "fw": "firmware_version",
    "sdk": "firmware_version",
    "sdkarduino": "firmware_version",
}


def _normalise_label(raw: str) -> str:
    """'Model'/'MODEL'/'Model Version' → 'model'/'modelversion'."""
    return "".join(ch for ch in raw.lower() if ch.isalnum())


def _mac_to_uuid(mac: str) -> str:
    """MAC efuse dạng '04:F9:19:9E:13:9C' → UUID gateway kiểu firmware.

    Firmware: String(ESP.getEfuseMac(), HEX).toUpperCase() — hex KHÔNG dấu ':'.
    Kết quả giữ N bit thấp; số 0 đứng đầu (nibble) bị bỏ → MAC 04:F9:19:9E:13:9C
    cho UUID 4F9199E139C (11 ký tự).
    """
    hx = mac.replace(":", "").upper()
    hx = hx.lstrip("0")
    return hx.upper()


def _decode_kind(fd: FrameDecode) -> tuple[str, bool]:
    """Map FrameDecode → (RX kind, is_data_counted)."""
    if fd.family is None:
        return RX_KIND_UNPARSED, False
    if not fd.crc_ok:
        return RX_KIND_CRC_ERROR, False
    if fd.kind == "info":
        return RX_KIND_INFO, False
    return RX_KIND_DATA, True


class SerialLogParser:
    """State machine: nhận line → list[event].

    event là LoraRxEvent, GatewayReport, SerialNotice, hoặc str (dòng chưa xử lý,
    để UI hiển thị/ghi raw — thực tế UI ghi raw riêng, parser chỉ trả sự kiện).
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._pending: dict | None = None
        self.last_report: GatewayReport | None = None
        self.last_seen_ips: list[str] = []
        self.last_module_uid: str = ""
        # Trạng thái bắt block DEVICE INFO (boot banner). Khi gặp dòng header
        # mở block, các dòng `[INFO] Nhãn : giá trị` tiếp theo được gom vào
        # _boot_fields; dòng không phải field sẽ đóng block → emit 1 event.
        self._boot_fields: dict[str, str] = {}
        self._boot_open: bool = False
        self.last_boot: GatewayBootInfo | None = None

    # -- API -----------------------------------------------------------------
    def feed(self, line: str, pc_time: str) -> list:
        line = line.rstrip("\r\n")
        results: list = []

        if not line:
            # Dòng trống chấm dứt block boot nếu đang mở (an toàn, không gây
            # nuốt dòng: dòng trống không có ý nghĩa khác).
            if self._boot_open:
                results += self._close_boot()
            return results

        # 0) Block DEVICE INFO (boot banner) — ưu tiên kiểm tra trước packet vì
        #    dòng header có cùng tiền tố [INFO] với report JSON 60s.
        if RE_BOOT_HEADER.match(line):
            # Nếu có packet LoRa đang mở (anchor chưa có DATA) → chốt incomplete
            # trước, không để block boot nuốt dòng DATA của nó.
            if self._pending is not None:
                results += self._flush_pending(pc_time, incomplete=True)
            if self._boot_open:
                # Header lặp (boot 2 lần liên tiếp) → chốt block cũ trước.
                results += self._close_boot()
            self._boot_open = True
            self._boot_fields = {}
            return results

        if self._boot_open:
            m = RE_BOOT_FIELD.match(line)
            if m:
                label = _normalise_label(m.group(1))
                attr = _BOOT_LABEL_MAP.get(label)
                if attr:
                    self._boot_fields[attr] = m.group(2).strip()
                return results
            # Dòng không phải field → block đã kết thúc; chốt rồi xử lý dòng
            # này như bình thường (packet / diag) bên dưới.
            results += self._close_boot()

        # 1) Dòng mở packet.
        m = RE_RX_ANCHOR.match(line)
        if m:
            results += self._flush_pending(pc_time, incomplete=True)
            self._pending = {
                "pc_time": pc_time,
                "receiver_id": m.group(1),
                "gw_time": m.group(2).strip(),
            }
            return results

        # 2) Dòng DATA — finalize packet đang chờ.
        m = RE_RX_DATA.match(line)
        if m:
            if self._pending is None:
                return results  # orphan DATA line — bỏ qua
            results.append(self._build_rx(m, pc_time))
            self._pending = None
            return results

        # 2b) Dòng sự kiện +EVT:RXP2P từ mạch thu riêng (COM 2).
        m = RE_EVT_RXP2P.match(line)
        if m:
            results.append(self._build_rx_from_evt(m, pc_time))
            return results

        # 3) Các dòng chẩn đoán.
        results += self._diagnostic(line)

        # Nếu có packet đang mở mà dòng hiện tại không phải DATA của nó
        # (log khác chèn vào giữa block — hiếm) → finalize incomplete.
        if self._pending is not None:
            results += self._flush_pending(pc_time, incomplete=True)

        return results

    def flush(self, pc_time: str) -> list:
        out = self._flush_pending(pc_time, incomplete=True)
        if self._boot_open:
            out += self._close_boot()
        return out

    # -- Internal ------------------------------------------------------------
    def _close_boot(self) -> list:
        """Chốt block DEVICE INFO đang mở → 1 GatewayBootInfo (hoặc [] nếu rỗng).

        UUID suy từ MAC nếu block không in dòng UUID riêng (firmware chỉ in MAC
        efuse ở banner đầu). MAC bỏ ':' + bỏ nibble 0 đầu = UUID gateway.
        """
        self._boot_open = False
        fields = self._boot_fields
        self._boot_fields = {}
        if not fields:
            return []

        mac = fields.get("mac", "").strip()
        if not RE_MAC_HEX.match(mac):
            mac = ""
        uuid = fields.get("uuid", "").strip().upper()
        if not uuid and mac:
            uuid = _mac_to_uuid(mac)

        info = GatewayBootInfo(
            uuid=uuid,
            model=fields.get("model", ""),
            series=fields.get("series", ""),
            mac=mac,
            firmware_version=fields.get("firmware_version", ""),
        )
        # Chỉ emit khi có ít nhất UUID hoặc MAC — block rỗng/model-only không
        # đủ để nhận diện gateway (tránh làm nhiễu UI với giá trị rác).
        if not (info.uuid or info.mac):
            return []
        self.last_boot = info
        return [info]

    def _flush_pending(self, pc_time: str, incomplete: bool) -> list:
        if self._pending is None:
            return []
        p = self._pending
        self._pending = None
        if not incomplete:
            return []
        evt = LoraRxEvent(
            kind=RX_KIND_INCOMPLETE,
            pc_time=pc_time,
            gw_time=p.get("gw_time", ""),
            receiver_id=p.get("receiver_id", ""),
            node_uuid="",
            seq=None,
            rssi=None,
            snr=None,
            length=0,
            frame_hex="",
            crc_ok=False,
        )
        return [evt]

    def _build_rx(self, m: re.Match, pc_time: str) -> LoraRxEvent:
        p = self._pending
        assert p is not None
        rssi = int(m.group(1))
        snr = int(m.group(2))
        length = int(m.group(3))
        frame_hex = m.group(4).replace(",", "")
        raw = hex_to_bytes(frame_hex)
        fd = decode_frame(raw) if raw else FrameDecode(None, "unparsed", "", None, False, len(raw))
        kind, _ = _decode_kind(fd)
        return LoraRxEvent(
            kind=kind,
            pc_time=pc_time,
            gw_time=p.get("gw_time", ""),
            receiver_id=p.get("receiver_id", ""),
            node_uuid=fd.node_uuid,
            node_kind=node_kind_from_id(fd.node_uuid),
            seq=fd.seq,
            rssi=rssi,
            snr=snr,
            length=length,
            frame_hex=frame_hex,
            crc_ok=fd.crc_ok,
        )

    def _build_rx_from_evt(self, m: re.Match, pc_time: str) -> LoraRxEvent:
        """Tạo LoraRxEvent từ dòng +EVT:RXP2P:<rssi>:<snr>:<hex>.

        Payload hex bắt đầu bằng sensor_id (16 hex = node mới hoặc 24 hex = đã lắp
        đuôi FC8B3004), phần còn lại là frame số liệu. KHÔNG có receiver_id riêng ở
        nguồn này — để trống; node_uuid lấy từ phần đầu payload rồi decode seq/CRC.
        """
        rssi = int(m.group(1))
        snr = int(m.group(2))
        hx = m.group(3)
        raw = hex_to_bytes(hx)
        fd = decode_frame(raw) if raw else FrameDecode(None, "unparsed", "", None, False, len(raw))
        kind, _ = _decode_kind(fd)
        node_uuid = fd.node_uuid
        if not node_uuid and raw:
            # Fallback: 16 hoặc 24 hex đầu làm ID.
            node_uuid = hx[:24 if len(hx) >= 24 else 16].upper()
        return LoraRxEvent(
            kind=kind,
            pc_time=pc_time,
            gw_time="",
            receiver_id="",
            node_uuid=node_uuid,
            node_kind=node_kind_from_id(node_uuid),
            seq=fd.seq,
            rssi=rssi,
            snr=snr,
            length=len(raw),
            frame_hex=hx,
            crc_ok=fd.crc_ok,
        )

    def _diagnostic(self, line: str) -> list:
        out: list = []

        m = RE_NOT_ALLOWED.match(line)
        if m:
            out.append(SerialNotice(kind="not_allowed", uuid=m.group(1)))
            return out

        m = RE_PUBLISH_OK.match(line)
        if m:
            out.append(SerialNotice(kind="publish_ok", topic=m.group(1)))
            return out

        m = RE_ETH_IP.match(line)
        if m:
            ip = m.group(1)
            self.last_seen_ips.append(ip)
            out.append(SerialNotice(kind="ip", ip=ip))
            return out

        m = RE_WIFI_IP.match(line)
        if m:
            ip = m.group(1)
            self.last_seen_ips.append(ip)
            out.append(SerialNotice(kind="ip", ip=ip))
            return out

        m = RE_GW_REPORT.match(line)
        if m:
            try:
                data = json.loads(m.group(1))
            except ValueError:
                return out
            rep = GatewayReport(
                uuid=str(data.get("uuid", "")),
                model=str(data.get("model", "")),
                series=str(data.get("series", "")),
                firmware_version=str(data.get("firmware_version", "")),
                status=str(data.get("status", "")),
                eth_ip=str(data.get("eth_ip", "")),
                wifi_ip=str(data.get("wifi_ip", "")),
                eth_mac=str(data.get("eth_mac", "")),
                wifi_mac=str(data.get("wifi_mac", "")),
                raw=data,
            )
            self.last_report = rep
            out.append(rep)
            return out

        m = RE_MODULE_INFO.match(line)
        if m:
            self.last_module_uid = m.group(1)
            out.append(SerialNotice(
                kind="module_info",
                uuid=m.group(1),
                module_fw=m.group(2),
                module_freq=m.group(3),
                module_bw=m.group(4),
                module_txp=m.group(5),
            ))
            return out

        return out
