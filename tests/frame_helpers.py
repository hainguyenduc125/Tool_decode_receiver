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


def build_firmware_type3_data_frame(
    uid: bytes,
    seq: int,
    node_type: int,
    mcu_temp_x100: int,
    vdd_x100: int,
    ch_count: int,
    floats: list[float],
) -> bytes:
    """Current firmware Type3 data frame, including TYPE at offset 10."""
    body = bytearray(uid)
    body += struct.pack("<H", seq)
    body += bytes([node_type])
    body += struct.pack("<h", mcu_temp_x100)
    body += struct.pack("<H", vdd_x100)
    body += bytes([ch_count])
    for value in floats:
        body += struct.pack("<f", value)
    body += struct.pack("<H", crc16_modbus(bytes(body)))
    return bytes(body)


def build_firmware_type3_info_frame(uid: bytes) -> bytes:
    """Current 32-byte Type3 info frame described by the gateway firmware."""
    body = bytearray(uid)
    body += struct.pack("<H", 0)       # SEQ
    body += bytes([3, 7])              # TYPE, FW_VER
    body += struct.pack("<I", 920600000)
    body += bytes([125, 11, 1, 20])    # BW, TX power, RX mode/window
    body += struct.pack("<H", 120)     # INFO_INT
    body += bytes([1])                 # JOIN
    body += struct.pack("<H", 300)     # WRN_D
    body += struct.pack("<H", 60)      # ACQ_I
    body += struct.pack("<H", 1800)    # SND_I
    body += bytes([20])                # DIST
    body += struct.pack("<H", crc16_modbus(bytes(body)))
    assert len(body) == 32
    return bytes(body)


def build_type3_info_frame(uid: bytes) -> bytes:
    """28-byte legacy Info frame, not accepted by current Gateway Type0."""
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


def build_type3_camera_frame(
    uid: bytes,
    seq: int,
    x: int,
    y: int,
    exponent: int,
    value: int,
    padding: bytes = b"\x01\x00",
) -> bytes:
    """Build the Type3 camera node's 26-byte, 12-byte-UID XOR frame."""
    assert len(uid) == 12
    assert len(padding) == 2
    body = bytearray(uid)
    body += struct.pack("<H", seq)
    body += bytes([0, 16, x, y, exponent])
    body += value.to_bytes(3, "little")
    body += padding
    assert len(body) == 24
    calc = 0
    for i in range((26 - 3) // 2):
        calc ^= body[2 * i] | (body[2 * i + 1] << 8)
    body += struct.pack("<H", calc)
    return bytes(body)


def build_type6_energy_frame(uid: bytes, seq: int, sensor_type: int) -> bytes:
    """Build a Type6 42-byte energy frame with the firmware's channel markers."""
    assert sensor_type in (8, 9)
    frame = bytearray(build_old_frame(42, uid, seq))
    unit_index = 42 if sensor_type == 8 else 37
    for i in range(4):
        offset = 16 + 6 * i
        frame[offset:offset + 6] = bytes([
            2,
            (2300 + i * 100) & 0xFF,
            ((2300 + i * 100) >> 8) & 0xFF,
            ((2300 + i * 100) >> 16) & 0xFF,
            unit_index,
            sensor_type,
        ])

    calc = 0
    for i in range((42 - 3) // 2):
        calc ^= frame[2 * i] | (frame[2 * i + 1] << 8)
    frame[-2:] = struct.pack("<H", calc)
    return bytes(frame)


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
