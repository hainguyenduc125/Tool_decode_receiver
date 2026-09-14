"""Test decode frame + CRC (mirror parse_node_packet firmware)."""

import struct
import unittest

from core.lora_frame import crc16_modbus, crc_xor_old_ok, decode_frame

from .frame_helpers import (
    build_old_frame,
    build_type3_data_frame,
    build_type3_info_frame,
)

UID = bytes.fromhex("0102030405060708")
OLD_UUID = bytes.fromhex("AABBCCDDEEFF001122334455")


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
        self.assertEqual(d.family, "new")
        self.assertEqual(d.kind, "data")
        self.assertEqual(d.node_uuid, "0102030405060708")
        self.assertEqual(d.seq, 5)
        self.assertTrue(d.crc_ok)

    def test_type3_data_crc_bad(self):
        frame = bytearray(build_type3_data_frame(UID, seq=5, mcu_temp_x100=3122,
                                                 vdd_x100=330, ch_count=1, floats=[20.0]))
        frame[-1] ^= 0xFF
        d = decode_frame(bytes(frame))
        self.assertFalse(d.crc_ok)
        self.assertEqual(d.seq, 5)

    def test_type3_seq_zero_is_info(self):
        frame = build_type3_data_frame(UID, seq=0, mcu_temp_x100=0,
                                       vdd_x100=0, ch_count=1, floats=[0.0])
        d = decode_frame(frame)
        self.assertEqual(d.kind, "info")
        self.assertEqual(d.seq, 0)

    def test_type3_info_28b_unparsed_quirk(self):
        # Quirk firmware: 28-byte Info frame không khớp (len-17)%4==0 → unparsed.
        frame = build_type3_info_frame(UID)
        d = decode_frame(frame)
        self.assertEqual(d.kind, "unparsed")
        self.assertIsNone(d.family)


class TestDecodeOld(unittest.TestCase):
    def test_old_42_data(self):
        frame = build_old_frame(42, OLD_UUID, seq=7)
        d = decode_frame(frame)
        self.assertEqual(d.family, "old")
        self.assertEqual(d.kind, "data")
        self.assertEqual(d.node_uuid, "AABBCCDDEEFF001122334455")
        self.assertEqual(d.seq, 7)
        self.assertTrue(d.crc_ok)

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

    def test_garbage_length(self):
        d = decode_frame(b"\x01\x02\x03")
        self.assertEqual(d.kind, "unparsed")
        self.assertIsNone(d.family)


if __name__ == "__main__":
    unittest.main()
