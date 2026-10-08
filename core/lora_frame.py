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
import re
import struct
from typing import Optional


@dataclass
class FrameDecode:
    family: Optional[str]           # 'new' | 'old' | None
    kind: str                       # 'data' | 'info' | 'unparsed'
    node_uuid: str                  # '' nếu không xác định
    seq: Optional[int]              # None nếu không xác định
    crc_ok: bool
    length: int


@dataclass
class PacketDetails:
    family: Optional[str]
    layout: str
    kind: str
    node_uuid: str
    seq: Optional[int]
    crc_ok: bool
    crc_calculated: Optional[int]
    crc_received: Optional[int]
    length: int
    fields: list[tuple[str, str]]
    notes: list[str]


@dataclass
class ConsoleDecode:
    packet: PacketDetails
    rssi: Optional[int] = None
    snr: Optional[int] = None


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
    """Phân loại frame giống parser gateway, gồm cả layout Type3 hiện tại."""
    n = len(raw)

    # Firmware Type3 hiện tại thêm TYPE ở offset 10: data dài 18 + 4N,
    # info dài 32 byte. Thử Modbus trước vì các độ dài này trùng frame cũ.
    is_current_type3_len = n == 32 or (n >= 22 and (n - 18) % 4 == 0)
    if is_current_type3_len:
        crc = crc16_modbus(raw[: n - 2])
        crc_recv = raw[n - 2] | (raw[n - 1] << 8)
        if crc == crc_recv:
            seq = raw[8] | (raw[9] << 8)
            return FrameDecode(
                family="new", kind="info" if seq == 0 else "data",
                node_uuid=raw[:8].hex().upper(), seq=seq,
                crc_ok=True, length=n)

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

    # Layout Type3 cũ vẫn được dùng bởi các phiên bản tool/firmware trước đây.
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


def _crc_fields(raw: bytes, modbus: bool) -> tuple[int, int, bool]:
    if modbus:
        calculated = crc16_modbus(raw[:-2])
        received = raw[-2] | (raw[-1] << 8)
    else:
        n = len(raw)
        calculated = 0
        for i in range((n - 3) // 2):
            calculated ^= raw[2 * i] | (raw[2 * i + 1] << 8)
        received = (raw[-1] << 8) | raw[-2]
        if calculated != received:
            received = (raw[-2] << 8) | raw[-1]
    return calculated, received, calculated == received


def _number(value: float) -> str:
    return f"{value:.2f}"


def _type3_data_fields(
    raw: bytes,
    uid_len: int,
    seq_offset: int,
    temp_offset: int,
    vdd_offset: int,
    channel_offset: int,
    values_offset: int,
) -> tuple[list[tuple[str, str]], list[str]]:
    fields: list[tuple[str, str]] = []
    notes: list[str] = []
    seq = raw[seq_offset] | (raw[seq_offset + 1] << 8)
    temp_raw = int.from_bytes(raw[temp_offset:temp_offset + 2], "little", signed=True)
    vdd_raw = int.from_bytes(raw[vdd_offset:vdd_offset + 2], "little")
    ch_count_raw = raw[channel_offset]
    channel_count = ch_count_raw or 1
    fields.extend([
        (f"UID ({uid_len} byte)", raw[:uid_len].hex().upper()),
        ("SEQ (LE)", str(seq)),
        ("MCU temperature", f"{_number(temp_raw / 100)} °C"),
        ("MCU VDD", f"{_number(vdd_raw / 100)} V"),
        ("Channel count", str(ch_count_raw)),
    ])

    data_end = len(raw) - 2
    sample_bytes = raw[values_offset:data_end]
    if len(sample_bytes) % 4:
        notes.append("Vùng sensor không chia hết thành float 32-bit.")
        return fields, notes
    samples = [
        struct.unpack_from("<f", sample_bytes, offset)[0]
        for offset in range(0, len(sample_bytes), 4)
    ]
    fields.append(("Sensor samples (float32 LE)", str(len(samples))))

    if samples:
        per_channel, remainder = divmod(len(samples), channel_count)
        if remainder:
            notes.append(
                f"{len(samples)} mẫu không chia đều cho {channel_count} kênh; "
                "hiển thị theo thứ tự byte."
            )
            fields.append(("Sensor values (wire order)", ", ".join(_number(v) for v in samples)))
        else:
            for channel in range(channel_count):
                start = channel * per_channel
                channel_values = samples[start:start + per_channel]
                fields.append((
                    f"Sensor channel {channel}",
                    ", ".join(_number(v) for v in channel_values),
                ))
    return fields, notes


def _decode_current_type3(raw: bytes) -> PacketDetails:
    n = len(raw)
    seq = raw[8] | (raw[9] << 8)
    crc_calc, crc_recv, crc_ok = _crc_fields(raw, modbus=True)
    fields: list[tuple[str, str]] = [
        ("UID (8 byte)", raw[:8].hex().upper()),
        ("SEQ (LE)", str(seq)),
        ("TYPE", str(raw[10])),
    ]
    notes: list[str] = []

    if seq == 0:
        kind = "info"
        if n == 32:
            fields.extend([
                ("Firmware version", str(raw[11])),
                ("Frequency", f"{int.from_bytes(raw[12:16], 'little')} Hz"),
                ("Bandwidth", f"{raw[16]} kHz"),
                ("TX power", f"{raw[17]} dBm"),
                ("RX mode", str(raw[18])),
                ("RX window", str(raw[19])),
                ("Info interval", f"{int.from_bytes(raw[20:22], 'little')} s"),
                ("Join", "true" if raw[22] else "false"),
                ("Warning duty", str(int.from_bytes(raw[23:25], 'little'))),
                ("Acquire interval", f"{int.from_bytes(raw[25:27], 'little')} s"),
                ("Send interval", f"{int.from_bytes(raw[27:29], 'little')} s"),
                ("Distance", str(raw[29])),
            ])
        else:
            notes.append("SEQ=0 nhưng frame không dài 32 byte như Info frame Type3.")
    else:
        kind = "data"
        data_fields, data_notes = _type3_data_fields(
            raw, uid_len=8, seq_offset=8, temp_offset=11, vdd_offset=13,
            channel_offset=15, values_offset=16,
        )
        fields.extend(data_fields[2:])
        notes.extend(data_notes)

    return PacketDetails(
        family="new",
        layout="Type3 firmware hiện tại (có TYPE, CRC-16/Modbus LE)",
        kind=kind,
        node_uuid=raw[:8].hex().upper(),
        seq=seq,
        crc_ok=crc_ok,
        crc_calculated=crc_calc,
        crc_received=crc_recv,
        length=n,
        fields=fields,
        notes=notes,
    )


def _decode_legacy_type3_data(raw: bytes) -> PacketDetails:
    n = len(raw)
    seq = raw[8] | (raw[9] << 8)
    crc_calc, crc_recv, crc_ok = _crc_fields(raw, modbus=True)
    fields, notes = _type3_data_fields(
        raw, uid_len=8, seq_offset=8, temp_offset=10, vdd_offset=12,
        channel_offset=14, values_offset=15,
    )
    return PacketDetails(
        family="new",
        layout="Type3 legacy (không có TYPE, CRC-16/Modbus LE)",
        kind="info" if seq == 0 else "data",
        node_uuid=raw[:8].hex().upper(),
        seq=seq,
        crc_ok=crc_ok,
        crc_calculated=crc_calc,
        crc_received=crc_recv,
        length=n,
        fields=fields,
        notes=notes,
    )


def _decode_type3_info_28(raw: bytes) -> PacketDetails:
    crc_calc, crc_recv, crc_ok = _crc_fields(raw, modbus=True)
    seq = raw[8] | (raw[9] << 8)
    fields = [
        ("UID (8 byte)", raw[:8].hex().upper()),
        ("SEQ (LE)", str(seq)),
        ("Firmware version", str(raw[10])),
        ("Frequency", f"{int.from_bytes(raw[11:15], 'little')} Hz"),
        ("Bandwidth", f"{raw[15]} kHz"),
        ("TX power", f"{raw[16]} dBm"),
        ("RX mode", str(raw[17])),
        ("RX window", str(raw[18])),
        ("Warning duty", str(int.from_bytes(raw[19:21], 'little'))),
        ("Acquire interval", str(int.from_bytes(raw[21:23], 'little'))),
        ("Send interval", str(int.from_bytes(raw[23:25], 'little'))),
        ("Distance", str(raw[25])),
    ]
    return PacketDetails(
        family="new",
        layout="Type3 Info 28 byte (legacy)",
        kind="info",
        node_uuid=raw[:8].hex().upper(),
        seq=seq,
        crc_ok=crc_ok,
        crc_calculated=crc_calc,
        crc_received=crc_recv,
        length=len(raw),
        fields=fields,
        notes=["Info 28 byte không được parser gateway hiện tại nhận diện; chỉ giải mã tham khảo."],
    )


def _decode_old_packet(raw: bytes) -> PacketDetails:
    n = len(raw)
    crc_calc, crc_recv, crc_ok = _crc_fields(raw, modbus=False)
    uuid = raw[:12].hex().upper()
    if n == 22:
        temp_raw = int.from_bytes(raw[16:18], "little")
        vdd_raw = int.from_bytes(raw[18:20], "little")
        fields = [
            ("UUID (12 byte)", uuid),
            ("SEQ", "0"),
            ("Status", str(raw[14])),
            ("MCU temperature", f"{_number(temp_raw * 165 / 65535 - 40)} °C"),
            ("MCU VDD", f"{_number(vdd_raw * 7 / 65535)} V"),
        ]
        kind = "info"
        seq: Optional[int] = 0
        notes: list[str] = []
    else:
        seq = (raw[13] << 8) | raw[12]
        fields = [("UUID (12 byte)", uuid), ("SEQ (LE)", str(seq))]
        kind = "data" if seq > 0 else "info"
        notes = []
        if n == 26:
            exponent = raw[18]
            fields.extend([
                ("Camera X", str(raw[16])),
                ("Camera Y", str(raw[17])),
                ("Exponent", str(exponent)),
            ])
            if exponent <= 7:
                value = int.from_bytes(raw[19:22], "little") / (10 ** exponent)
                fields.append(("Sensor value", _number(value)))
            else:
                notes.append("Exponent camera vượt giới hạn firmware (0..7).")
        else:
            for i in range(4):
                offset = 16 + 6 * i
                exponent = raw[offset]
                fields.extend([
                    (f"Sensor {i} exponent", str(exponent)),
                    (f"Sensor {i} unit index", str(raw[offset + 4])),
                    (f"Sensor {i} type index", str(raw[offset + 5] & 0x0F)),
                ])
                if exponent <= 7:
                    value = int.from_bytes(raw[offset + 1:offset + 4], "little") / (10 ** exponent)
                    fields.append((f"Sensor {i} value", _number(value)))
                else:
                    notes.append(f"Exponent sensor {i} vượt giới hạn firmware (0..7).")

    return PacketDetails(
        family="old",
        layout=f"Node cũ ({n} byte, CRC XOR)",
        kind=kind,
        node_uuid=uuid,
        seq=seq,
        crc_ok=crc_ok,
        crc_calculated=crc_calc,
        crc_received=crc_recv,
        length=n,
        fields=fields,
        notes=notes,
    )


def decode_packet_details(raw: bytes) -> PacketDetails:
    """Giải mã chi tiết các layout firmware Type3 và node cũ."""
    n = len(raw)
    is_current_type3_len = n == 32 or (n >= 22 and (n - 18) % 4 == 0)
    is_old_len = n in (22, 26, 42)

    # Thứ tự giống gateway: Type3 Modbus trước, sau đó mới thử CRC XOR node cũ.
    if is_current_type3_len:
        current_crc = _crc_fields(raw, modbus=True)
        if current_crc[2]:
            return _decode_current_type3(raw)
    if is_old_len:
        old_crc = _crc_fields(raw, modbus=False)
        if old_crc[2]:
            return _decode_old_packet(raw)

    # Lưu khả năng giải mã các frame Type3 của parser/tài liệu legacy.
    if n == 28 and raw[8] == 0 and raw[9] == 0:
        return _decode_type3_info_28(raw)
    if n >= 17 and (n - 17) % 4 == 0:
        return _decode_legacy_type3_data(raw)

    if is_current_type3_len and not is_old_len:
        return _decode_current_type3(raw)
    if is_old_len:
        details = _decode_old_packet(raw)
        if is_current_type3_len:
            details.layout = f"{details.layout}; độ dài cũng khớp Type3 nhưng CRC không hợp lệ"
            details.family = None
            details.node_uuid = ""
            details.seq = None
            details.fields = [("Frame length", str(n))]
            details.notes.append("Không thể chọn layout vì CRC của cả Type3 và node cũ đều sai.")
        return details

    fd = decode_frame(raw)
    return PacketDetails(
        family=fd.family,
        layout="Không nhận diện được layout",
        kind="unparsed",
        node_uuid=fd.node_uuid,
        seq=fd.seq,
        crc_ok=False,
        crc_calculated=None,
        crc_received=None,
        length=n,
        fields=[("Frame length", str(n))],
        notes=["Độ dài frame không khớp các layout được hỗ trợ."],
    )


def _decode_d_record(line: str) -> ConsoleDecode:
    parts = [part.strip() for part in line[2:].split(",")]
    if len(parts) not in (12, 19):
        raise ValueError(
            f"D record cần 12 trường (camera) hoặc 19 trường (4 cảm biến), "
            f"nhận được {len(parts)}."
        )

    node_id = parts[0]
    if len(node_id) != 24 or not re.fullmatch(r"[0-9A-Fa-f]{24}", node_id):
        raise ValueError("UID trong D record phải gồm đúng 24 ký tự HEX.")

    def parse_hex(value: str, digits: int, field: str) -> bytes:
        if not re.fullmatch(rf"[0-9A-Fa-f]{{{digits}}}", value):
            raise ValueError(f"Trường {field} phải có đúng {digits} ký tự HEX.")
        return bytes.fromhex(value)

    seq_bytes = parse_hex(parts[1], 4, "SEQ")
    frame = bytearray.fromhex(node_id)
    frame.extend(seq_bytes[::-1])
    frame.extend(parse_hex(parts[2], 2, "header[0]"))
    frame.extend(parse_hex(parts[3], 2, "header[1]"))

    if len(parts) == 19:
        for i, field_index in enumerate((4, 7, 10, 13)):
            # D ghi word sensor 32-bit theo little-endian; đảo lại thành byte wire
            # [exponent, value_lo, value_mid, value_hi] trước khi áp dụng parser C++.
            frame.extend(parse_hex(parts[field_index], 8, f"sensor[{i}]")[::-1])
            frame.extend(parse_hex(parts[field_index + 1], 2, f"unit[{i}]"))
            frame.extend(parse_hex(parts[field_index + 2], 2, f"type[{i}]"))
        rssi_index, snr_index, crc_index, expected_len = 16, 17, 18, 42
    else:
        # D record camera 26B: XY, word [exponent,value24], hai byte mở rộng,
        # CRC. Hai byte mở rộng thuộc vùng firmware không đưa vào giá trị camera.
        frame.extend(parse_hex(parts[4], 2, "camera_x"))
        frame.extend(parse_hex(parts[5], 2, "camera_y"))
        frame.extend(parse_hex(parts[6], 8, "camera_sensor")[::-1])
        frame.extend(parse_hex(parts[7], 2, "camera_extra[0]"))
        frame.extend(parse_hex(parts[8], 2, "camera_extra[1]"))
        rssi_index, snr_index, crc_index, expected_len = 9, 10, 11, 26

    frame.extend(parse_hex(parts[crc_index], 4, "CRC"))
    if len(frame) != expected_len:
        raise ValueError(
            f"D record tạo thành {len(frame)} byte, cần đúng {expected_len} byte."
        )

    packet = _decode_old_packet(bytes(frame))
    rssi_raw = int.from_bytes(parse_hex(parts[rssi_index], 4, "RSSI"), "big")
    snr_raw = int.from_bytes(parse_hex(parts[snr_index], 4, "SNR"), "big")
    rssi = rssi_raw - 0x10000 if rssi_raw & 0x8000 else rssi_raw
    snr = snr_raw - 0x10000 if snr_raw & 0x8000 else snr_raw
    packet.layout = f"D record (node cũ {expected_len} byte, CRC XOR)"
    packet.fields.extend([("RSSI", f"{rssi} dBm"), ("SNR", str(snr))])
    if not packet.crc_ok:
        packet.notes.append("CRC XOR của frame dựng từ D record không hợp lệ.")
    return ConsoleDecode(packet=packet, rssi=rssi, snr=snr)


def decode_console_line(line: str) -> ConsoleDecode:
    """Giải mã HEX, event UART, record D hoặc nhận diện envelope L."""
    text = line.strip()
    if not text:
        raise ValueError("Chưa có dữ liệu để giải mã.")

    if text.startswith("D:"):
        return _decode_d_record(text)

    if text.startswith("L:"):
        parts = text[2:].split(",", 2)
        if len(parts) != 3:
            raise ValueError("L record cần dạng L:<receiver UID>,<type>,<payload HEX>.")
        receiver, record_type, payload_hex = (part.strip() for part in parts)
        if not re.fullmatch(r"[0-9A-Fa-f]{24}", receiver):
            raise ValueError("Receiver UID trong L record phải gồm đúng 24 ký tự HEX.")
        if not re.fullmatch(r"[0-9A-Fa-f]{4}", record_type):
            raise ValueError("Type trong L record phải gồm đúng 4 ký tự HEX.")
        raw = parse_hex_frame(payload_hex)
        packet = PacketDetails(
            family=None,
            layout="L record (envelope, không phải raw frame)",
            kind="unparsed",
            node_uuid="",
            seq=None,
            crc_ok=False,
            crc_calculated=None,
            crc_received=None,
            length=len(raw),
            fields=[
                ("Receiver UID", receiver.upper()),
                ("Record type", record_type.upper()),
                ("Payload HEX", raw.hex().upper()),
            ],
            notes=[
                "L record có envelope và payload riêng; payload này không khớp trực tiếp "
                "các layout/CRC của parse_node_packet() trong data_process.cpp."
            ],
        )
        return ConsoleDecode(packet=packet)

    event = re.search(r"\+EVT:RXP2P:(-?\d+):(-?\d+):([0-9A-Fa-f]+)", text)
    if event:
        raw = bytes.fromhex(event.group(3))
        details = decode_packet_details(raw)
        return ConsoleDecode(
            packet=details,
            rssi=int(event.group(1)),
            snr=int(event.group(2)),
        )

    rssi_match = re.search(r"\bRSSI:\s*(-?\d+)\s*\|\s*SNR:\s*(-?\d+)", text)
    raw = parse_hex_frame(text)
    return ConsoleDecode(
        packet=decode_packet_details(raw),
        rssi=int(rssi_match.group(1)) if rssi_match else None,
        snr=int(rssi_match.group(2)) if rssi_match else None,
    )


def parse_hex_frame(hex_str: str) -> bytes:
    """Parse HEX nghiêm ngặt; hỗ trợ dấu cách, dấu phẩy, ':' và log gateway."""
    text = hex_str.strip()
    event = re.search(r"\+EVT:RXP2P:-?\d+:-?\d+:([0-9A-Fa-f]+)", text)
    if event:
        text = event.group(1)
    elif "DATA:" in text:
        text = text.rsplit("DATA:", 1)[1]

    text = re.sub(r"0[xX]", "", text)
    invalid = re.sub(r"[0-9A-Fa-f\s,:-]", "", text)
    if invalid:
        raise ValueError(f"Ký tự không hợp lệ trong dữ liệu HEX: {invalid[0]!r}")
    s = re.sub(r"[\s,:-]", "", text)
    if not s or len(s) % 2:
        raise ValueError("Dữ liệu HEX rỗng hoặc có số ký tự lẻ.")
    return bytes.fromhex(s)


def hex_to_bytes(hex_str: str) -> bytes:
    """Chuyển HEX log nội bộ sang bytes; giữ hành vi rỗng khi dữ liệu không hợp lệ."""
    s = "".join(ch for ch in hex_str if ch in "0123456789abcdefABCDEF")
    if not s or len(s) % 2:
        return b""
    try:
        return bytes.fromhex(s)
    except ValueError:
        return b""
