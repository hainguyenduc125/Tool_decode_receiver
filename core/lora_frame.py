"""Giải mã frame LoRa — mô phỏng ĐÚNG `parse_node_packet()` của firmware gateway.

Tham chiếu: src/data_process.cpp (parse_node_packet, LoRa_CRC_check, CRC_calc,
calc_crc16_modbus). Tool phải phân loại frame GIỐNG gateway để quyết định frame
nào là data frame (seq > 0, CRC OK) → khớp 1:1 với message MQTT hệ mới.

Node mới (Type3):
  Data : [UID 8][SEQ 2 LE][MCU_TEMP 2][VDD 2][CH_COUNT 1][float N*4][CRC16 Modbus 2 LE]
         tổng = 17 + N*4 byte
  Info : 28 byte, SEQ=0 (firmware hiện tại KHÔNG nhận diện được độ dài 28 —
         quirk giữ nguyên để tool phân loại giống gateway => "unparsed").

Node cũ (UUID 12B):
  frame 22 (info, SEQ=0) / 26 (camera) / 42 (4 cảm biến), CRC XOR.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FrameDecode:
    family: Optional[str]           # 'new' | 'old' | None
    kind: str                       # 'data' | 'info' | 'unparsed'
    node_uuid: str                  # '' nếu không xác định
    seq: Optional[int]              # None nếu không xác định
    crc_ok: bool
    length: int


def crc16_modbus(data: bytes) -> int:
    """CRC-16 Modbus (poly 0x8005, init 0xFFFF, RefIn/RefOut) — src/data_process.cpp:51."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def crc_xor_old_ok(data: bytes) -> bool:
    """Mirror `LoRa_CRC_check`/`CRC_calc` (src/data_process.cpp:11-47).

    Firmware XOR các word uint16 (little-endian) của (len-3) byte đầu, rồi so với
    2 byte cuối theo 2 thứ tự (BE trước, LE sau). Giữ nguyên hành vi (kể cả
    việc bỏ byte lẻ) để tool công nhận/loại CRC giống hệt gateway.
    """
    n = len(data)
    if n < 4:
        return False
    calc = 0
    words = (n - 3) // 2
    for i in range(words):
        calc ^= data[2 * i] | (data[2 * i + 1] << 8)
    be = (data[n - 1] << 8) | data[n - 2]          # buf[len-1]<<8 | buf[len-2]
    if calc == be:
        return True
    le = (data[n - 2] << 8) | data[n - 1]          # thứ tự ngược
    return calc == le


def decode_frame(raw: bytes) -> FrameDecode:
    """Phân loại frame giống parse_node_packet firmware."""
    n = len(raw)
    if n in (22, 26, 42):
        # ---- Node cũ (UUID 12B), CRC XOR ----
        # Seq lưu little-endian: firmware đọc `packet.data[13]<<8 | packet.data[12]`
        # (src/data_process.cpp:256,272), tool phải đọc Y HỆT để khớp seq MQTT.
        uuid = raw[:12].hex().upper()
        if n == 22:
            seq = 0
            kind = "info"
        else:
            seq = (raw[13] << 8) | raw[12]         # little-endian (== firmware)
            kind = "data" if seq > 0 else "info"
        return FrameDecode(family="old", kind=kind, node_uuid=uuid,
                           seq=seq, crc_ok=crc_xor_old_ok(raw), length=n)

    if n >= 17 and (n - 17) % 4 == 0:
        # ---- Node mới (UID 8B), CRC-16 Modbus LE ----
        uuid = raw[:8].hex().upper()
        seq = raw[8] | (raw[9] << 8)               # little-endian
        crc = crc16_modbus(raw[: n - 2])
        crc_recv = raw[n - 2] | (raw[n - 1] << 8)
        return FrameDecode(family="new", kind="data" if seq > 0 else "info",
                           node_uuid=uuid, seq=seq,
                           crc_ok=(crc == crc_recv), length=n)

    # Quirk: Info frame Type3 (28B) và mọi độ dài lạ → firmware trả LENGTH_ERROR.
    return FrameDecode(family=None, kind="unparsed", node_uuid="", seq=None,
                       crc_ok=False, length=n)


def hex_to_bytes(hex_str: str) -> bytes:
    """Hex string (đã bỏ dấu phẩy / khoảng trắng) → bytes. '' nếu chuỗi lỗi."""
    s = "".join(ch for ch in hex_str if ch in "0123456789abcdefABCDEF")
    if not s or len(s) % 2:
        return b""
    try:
        return bytes.fromhex(s)
    except ValueError:
        return b""
