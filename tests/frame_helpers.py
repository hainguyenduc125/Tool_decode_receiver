"""Helpers dựng frame mẫu đúng chuẩn Node_Type3 / node cũ để test parser."""

from __future__ import annotations

import struct

from core.lora_frame import crc16_modbus


def build_type3_data_frame(uid: bytes, seq: int, mcu_temp_x100: int, vdd_x100: int,
                           ch_count: int, floats: list[float]) -> bytes:
    """Data frame Type3: [UID8][SEQ2 LE][MCU2][VDD2][CH1][float N*4][CRC16 2 LE]."""
    body = bytearray()
    body += uid
    body += struct.pack("<H", seq)
    body += struct.pack("<h", mcu_temp_x100)
    body += struct.pack("<H", vdd_x100)
    body += bytes([ch_count])
    for f in floats:
        body += struct.pack("<f", f)
    crc = crc16_modbus(bytes(body))
    body += struct.pack("<H", crc)
    return bytes(body)


def build_type3_info_frame(uid: bytes) -> bytes:
    """28-byte Info frame Type3 (SEQ=0) — firmware hiện tại KHÔNG nhận diện."""
    body = bytearray()
    body += uid
    body += struct.pack("<H", 0)          # SEQ = 0
    body += bytes([1])                    # FW_VER
    body += struct.pack("<I", 920600000)  # FREQ
    body += bytes([125])                  # BW
    body += bytes([11])                   # TX_POWER
    body += bytes([1])                    # RX_MODE
    body += bytes([20])                   # RX_WIN
    body += struct.pack("<H", 120)        # WRN_DUTY
    body += struct.pack("<H", 300)        # ACQ_INT
    body += struct.pack("<H", 1800)       # SEND_INT
    body += bytes([20])                   # DISTANCE
    crc = crc16_modbus(bytes(body))
    body += struct.pack("<H", crc)
    assert len(body) == 28
    return bytes(body)


def build_old_frame(nbytes: int, uuid12: bytes, seq: int) -> bytes:
    """Frame node cũ 22/26/42 với CRC XOR đúng quy ước LoRa_CRC_check của gateway.

    Mirror: CRC = XOR các word uint16 LE của (len-3) byte đầu so với 2 byte cuối
    theo big-endian (hoặc LE). Các byte bị firmware bỏ qua (len-3, len-2 region)
    set = 0.

    SEQ được ghi **little-endian** (`buf[12]=low, buf[13]=high`) — đúng quy ước
    firmware đọc `packet.data[13]<<8 | packet.data[12]` (src/data_process.cpp:256).
    """
    assert nbytes in (22, 26, 42)
    buf = bytearray(nbytes)
    buf[0:12] = uuid12
    buf[12] = seq & 0xFF          # byte thấp
    buf[13] = (seq >> 8) & 0xFF   # byte cao
    # Nội dung vùng giữa — giá trị bất kỳ (firmware chỉ cần CRC đúng).
    if nbytes == 42:
        # 4 cảm biến: exponent byte 16,22,28,34 <= 7 (parser yêu cầu)
        for i in (16, 22, 28, 34):
            buf[i] = 1
    # Tính CRC XOR giống gateway: XOR word LE của (n-3) byte đầu.
    calc = 0
    words = (nbytes - 3) // 2
    for i in range(words):
        calc ^= buf[2 * i] | (buf[2 * i + 1] << 8)
    buf[nbytes - 2] = calc & 0xFF          # data[40] low
    buf[nbytes - 1] = (calc >> 8) & 0xFF   # data[41] high → BE == calc
    return bytes(buf)


def type3_display_data(frame: bytes) -> str:
    """Tái tạo đúng cách firmware in DATA (thêm ',' sau byte 11 và byte len-3)."""
    parts = []
    n = len(frame)
    for i in range(n):
        parts.append("%02X" % frame[i])
        if i == 11 or i == n - 3:
            parts.append(",")
    return "".join(parts)
