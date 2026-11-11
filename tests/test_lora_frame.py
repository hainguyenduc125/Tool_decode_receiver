"""Test decode frame + CRC (mirror parse_node_packet firmware)."""

import struct
import unittest

from core.lora_frame import (
    crc16_modbus,
    crc_xor_old_ok,
    decode_frame,
    decode_console_line,
    decode_packet_details,
    hex_to_bytes,
    parse_hex_frame,
)

from .frame_helpers import (
    build_firmware_type3_data_frame,
    build_firmware_type3_info_frame,
    build_old_frame,
    build_type3_camera_frame,
    build_type3_data_frame,
    build_type3_info_frame,
)

UID = bytes.fromhex("4B3A5E0615E18000")
OLD_UUID = bytes.fromhex("4B3A5E0615E18000FC8B3004")


class TestCrc(unittest.TestCase):
    def test_crc16_modbus_check_value(self):
        # Giá trị check chuẩn CRC-16/MODBUS của "123456789" = 0x4B37.
        self.assertEqual(crc16_modbus(b"123456789"), 0x4B37)

    def test_old_crc_ok_crafted(self):
        frame = build_old_frame(42, OLD_UUID, seq=7)
        self.assertTrue(crc_xor_old_ok(frame))

    def test_old_crc_detect_tamper(self):
        frame = bytearray(build_old_frame(42, OLD_UUID, seq=7))
        frame[30] ^= 0xFF
        self.assertFalse(crc_xor_old_ok(bytes(frame)))

    def test_old_22_ok(self):
        frame = build_old_frame(22, OLD_UUID, seq=0)
        self.assertTrue(crc_xor_old_ok(frame))


class TestDecodeNew(unittest.TestCase):
    def test_type3_data(self):
        frame = build_type3_data_frame(UID, seq=5, mcu_temp_x100=3122,
                                       vdd_x100=330, ch_count=1, floats=[20.0])
        self.assertEqual(len(frame), 21)
        d = decode_frame(frame)
        self.assertEqual(d.gateway_result, "LENGTH_ERROR")
        self.assertIsNone(d.family)
        self.assertFalse(d.crc_ok)

    def test_legacy_type3_data_crc_bad(self):
        frame = bytearray(build_type3_data_frame(UID, seq=5, mcu_temp_x100=3122,
                                                 vdd_x100=330, ch_count=1, floats=[20.0]))
        frame[-1] ^= 0xFF
        d = decode_frame(bytes(frame))
        self.assertEqual(d.gateway_result, "LENGTH_ERROR")
        self.assertFalse(d.crc_ok)

    def test_current_type3_seq_zero_heartbeat_is_data(self):
        frame = build_firmware_type3_data_frame(
            UID, seq=0, node_type=3, mcu_temp_x100=0,
            vdd_x100=0, ch_count=0, floats=[],
        )
        d = decode_frame(frame)
        self.assertEqual(d.gateway_result, "OK")
        self.assertEqual(d.kind, "data")
        self.assertEqual(d.seq, 0)

    def test_type3_info_28b_unparsed_quirk(self):
        frame = build_type3_info_frame(UID)
        d = decode_frame(frame)
        self.assertEqual(d.gateway_result, "LENGTH_ERROR")
        self.assertEqual(d.kind, "unparsed")
        self.assertIsNone(d.family)

    def test_type3_camera_frame_uses_xor_and_gateway_accepts_it(self):
        frame = build_type3_camera_frame(OLD_UUID, 1, 3, 4, 2, 12345)
        decoded = decode_frame(frame)
        details = decode_packet_details(frame)
        self.assertEqual(decoded.gateway_result, "OK")
        self.assertEqual(decoded.family, "old")
        self.assertEqual(decoded.kind, "data")
        self.assertTrue(decoded.crc_ok)
        self.assertEqual(details.gateway_result, "OK")
        self.assertIn(("Sensor value", "123.45"), details.fields)

    def test_type3_camera_with_bad_exponent_is_gateway_invalid_format(self):
        frame = build_type3_camera_frame(OLD_UUID, 1, 3, 4, 8, 12345)
        decoded = decode_frame(frame)
        self.assertEqual(decoded.gateway_result, "INVALID_FORMAT")
        self.assertEqual(decoded.family, "old")
        self.assertTrue(decoded.crc_ok)

    def test_dual_crc_collision_uses_gateway_semantic_disambiguation(self):
        frame = bytes.fromhex(
            "4B3A5E0615E18000FC8B300401000010030402393000530F7C7F"
        )
        self.assertEqual(len(frame), 26)
        self.assertTrue(crc_xor_old_ok(frame))
        self.assertEqual(
            crc16_modbus(frame[:-2]),
            int.from_bytes(frame[-2:], "little"),
        )
        decoded = decode_frame(frame)
        self.assertEqual(decoded.gateway_result, "OK")
        self.assertEqual(decoded.family, "old")

    def test_modbus_valid_type3_with_invalid_channel_count_is_invalid_format(self):
        frame = build_firmware_type3_data_frame(
            UID, seq=5, node_type=3, mcu_temp_x100=3122,
            vdd_x100=330, ch_count=0, floats=[20.0],
        )
        decoded = decode_frame(frame)
        self.assertEqual(decoded.gateway_result, "INVALID_FORMAT")
        self.assertEqual(decoded.family, "new")
        self.assertTrue(decoded.crc_ok)

    def test_gateway_rejects_uid_outside_factory_format(self):
        frame = build_firmware_type3_data_frame(
            bytes.fromhex("0102030405060708"), seq=5, node_type=3,
            mcu_temp_x100=3122, vdd_x100=330, ch_count=1, floats=[20.0],
        )
        decoded = decode_frame(frame)
        self.assertEqual(decoded.gateway_result, "INVALID_UUID")
        self.assertTrue(decoded.crc_ok)

    def test_current_firmware_type3_data(self):
        frame = build_firmware_type3_data_frame(
            UID, seq=5, node_type=3, mcu_temp_x100=3122,
            vdd_x100=330, ch_count=1, floats=[20.0],
        )
        d = decode_frame(frame)
        self.assertEqual(d.family, "new")
        self.assertEqual(d.kind, "data")
        self.assertEqual(d.seq, 5)
        self.assertTrue(d.crc_ok)

        details = decode_packet_details(frame)
        self.assertEqual(details.layout, "Type3 firmware hiện tại (có TYPE, CRC-16/Modbus LE)")
        self.assertTrue(details.crc_ok)
        self.assertIn(("TYPE", "3"), details.fields)
        self.assertIn(("MCU temperature", "31.22 °C"), details.fields)
        self.assertIn(("MCU VDD", "3.30 V"), details.fields)
        self.assertIn(("Sensor channel 0", "20.00"), details.fields)

    def test_current_firmware_type3_info(self):
        frame = build_firmware_type3_info_frame(UID)
        d = decode_frame(frame)
        self.assertEqual(d.family, "new")
        self.assertEqual(d.kind, "info")
        self.assertEqual(d.seq, 0)
        details = decode_packet_details(frame)
        self.assertIn(("Frequency", "920600000 Hz"), details.fields)
        self.assertIn(("Join", "true"), details.fields)

    def test_current_type3_info_is_identified_by_length_not_sequence(self):
        frame = bytearray(build_firmware_type3_info_frame(UID))
        frame[8:10] = b"\x01\x00"
        frame[-2:] = struct.pack("<H", crc16_modbus(frame[:-2]))
        decoded = decode_frame(bytes(frame))
        self.assertEqual(decoded.gateway_result, "OK")
        self.assertEqual(decoded.kind, "info")
        details = decode_packet_details(bytes(frame))
        self.assertEqual(details.kind, "info")

    def test_hex_input_accepts_gateway_log_and_rejects_garbage(self):
        frame = bytes.fromhex("01020304")
        self.assertEqual(parse_hex_frame("DATA: 01 02,03,04"), frame)
        self.assertEqual(parse_hex_frame("+EVT:RXP2P:-45:8:01020304"), frame)
        with self.assertRaises(ValueError):
            parse_hex_frame("01GG03")
        with self.assertRaises(ValueError):
            parse_hex_frame("01020")
        self.assertEqual(hex_to_bytes("01GG03"), bytes.fromhex("0103"))


class TestDecodeOld(unittest.TestCase):
    def test_old_42_data(self):
        frame = build_old_frame(42, OLD_UUID, seq=7)
        d = decode_frame(frame)
        self.assertEqual(d.family, "old")
        self.assertEqual(d.kind, "data")
        self.assertEqual(d.node_uuid, OLD_UUID.hex().upper())
        self.assertEqual(d.seq, 7)
        self.assertTrue(d.crc_ok)
        details = decode_packet_details(frame)
        self.assertTrue(details.crc_ok)
        self.assertIn(("Sensor 0 value", "0.00"), details.fields)

    def test_d_record_rebuilds_old_frame_and_crc(self):
        line = (
            "D:4B3A5E0615E18000FC8B3004,3764,00,04,"
            "142A3106,2A,08,135D9706,2A,08,2AB49006,2A,08,00000007,2A,08,"
            "FF94,0019,72C0"
        )
        result = decode_console_line(line)
        self.assertEqual(result.packet.length, 42)
        self.assertTrue(result.packet.crc_ok)
        self.assertEqual(result.packet.seq, 0x3764)
        self.assertEqual(result.rssi, -108)
        self.assertEqual(result.snr, 25)
        self.assertIn(("Sensor 0 value", "1.32"), result.packet.fields)
        self.assertIn(("Sensor 1 unit index", "42"), result.packet.fields)

    def test_camera_d_record_rebuilds_legacy_frame(self):
        line = (
            "D:343947093330303128002B00,3059,00,10,00,00,"
            "10C8E002,01,00,FF9E,001B,E1E0"
        )
        result = decode_console_line(line)
        self.assertTrue(result.packet.crc_ok)
        self.assertEqual(result.packet.length, 26)
        self.assertEqual(result.packet.seq, 0x3059)
        self.assertEqual(result.rssi, -98)
        self.assertEqual(result.snr, 27)
        self.assertIn(("Sensor value", "11000.00"), result.packet.fields)

    def test_l_record_is_not_misreported_as_valid_raw_frame(self):
        result = decode_console_line(
            "L:3439470B3330303129001B00,0001,"
            "0E275E0615E18000FC8B30440000000000000000"
        )
        self.assertIsNone(result.packet.family)
        self.assertFalse(result.packet.crc_ok)
        self.assertEqual(result.packet.kind, "unparsed")
        self.assertIn("khớp trực tiếp", result.packet.notes[0])

    def test_evt_console_line_includes_radio_metadata(self):
        frame = build_firmware_type3_data_frame(
            UID, seq=5, node_type=3, mcu_temp_x100=3122, vdd_x100=330,
            ch_count=1, floats=[20.0],
        )
        result = decode_console_line(f"+EVT:RXP2P:-45:8:{frame.hex()}")
        self.assertTrue(result.packet.crc_ok)
        self.assertEqual(result.rssi, -45)
        self.assertEqual(result.snr, 8)

    def test_old_42_seq_little_endian_matches_firmware(self):
        # Firmware đọc SEQ: packet.data[13]<<8 | packet.data[12] (little-endian,
        # src/data_process.cpp:256). SEQ=0x1234 phải decode ra 0x1234.
        frame = build_old_frame(42, OLD_UUID, seq=0x1234)
        d = decode_frame(frame)
        self.assertEqual(d.family, "old")
        self.assertEqual(d.kind, "data")
        self.assertEqual(d.seq, 0x1234)
        self.assertTrue(d.crc_ok)
        # Kiểm tra trực tiếp byte layout: byte thấp ở offset 12, byte cao ở 13.
        self.assertEqual(frame[12], 0x34)
        self.assertEqual(frame[13], 0x12)

    def test_old_22_info(self):
        frame = build_old_frame(22, OLD_UUID, seq=0)
        d = decode_frame(frame)
        self.assertEqual(d.kind, "info")
        self.assertEqual(d.seq, 0)

    def test_old_22_info_values_follow_gateway_byte_order(self):
        frame = bytearray(build_old_frame(22, OLD_UUID, seq=0))
        frame[16:20] = bytes([0x01, 0x02, 0x03, 0x04])
        crc = 0
        for i in range((22 - 3) // 2):
            crc ^= frame[2 * i] | (frame[2 * i + 1] << 8)
        frame[-2:] = struct.pack("<H", crc)
        details = decode_packet_details(bytes(frame))
        self.assertIn(
            ("MCU temperature", f"{(0x0201 * 165 / 65535 - 40):.2f} °C"),
            details.fields,
        )
        self.assertIn(
            ("MCU VDD", f"{(0x0403 * 7 / 65535):.2f} V"),
            details.fields,
        )

    def test_garbage_length(self):
        d = decode_frame(b"\x01\x02\x03")
        self.assertEqual(d.kind, "unparsed")
        self.assertIsNone(d.family)


if __name__ == "__main__":
    unittest.main()
