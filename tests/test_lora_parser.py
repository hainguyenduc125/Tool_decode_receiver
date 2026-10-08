"""Test SerialLogParser — block log firmware → sự kiện đúng."""

import unittest

from models.message import (
    GatewayBootInfo,
    GatewayReport,
    LoraRxEvent,
    RX_KIND_CRC_ERROR,
    RX_KIND_DATA,
    RX_KIND_INCOMPLETE,
    RX_KIND_UNPARSED,
    SerialNotice,
)
from lora_io.lora_parser import SerialLogParser

from .frame_helpers import (
    build_firmware_type3_data_frame,
    build_old_frame,
    build_type3_data_frame,
    build_type3_info_frame,
    type3_display_data,
)

UID = bytes.fromhex("0102030405060708")
OLD_UUID = bytes.fromhex("AABBCCDDEEFF001122334455")


class TestSerialParser(unittest.TestCase):
    def test_type3_data_block(self):
        frame = build_type3_data_frame(UID, seq=5, mcu_temp_x100=3122,
                                       vdd_x100=330, ch_count=1, floats=[20.0])
        parser = SerialLogParser()
        events = []
        events += parser.feed("", "10:00:00.000")
        events += parser.feed(
            "[LORA]    RECEIVER ID: 64B5B30615E18000 | TIME: 2026-09-08T09:30:01+07:00",
            "10:00:00.001")
        events += parser.feed(
            "[LORA]    RSSI: -45 | SNR: 8 | LENGTH: %d | DATA: %s" % (len(frame), type3_display_data(frame)),
            "10:00:00.002")
        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertIsInstance(evt, LoraRxEvent)
        self.assertEqual(evt.kind, RX_KIND_DATA)
        self.assertEqual(evt.node_uuid, "0102030405060708")
        self.assertEqual(evt.seq, 5)
        self.assertEqual(evt.rssi, -45)
        self.assertEqual(evt.snr, 8)
        self.assertEqual(evt.receiver_id, "64B5B30615E18000")

    def test_current_firmware_type3_data_block(self):
        frame = build_firmware_type3_data_frame(
            UID, seq=6, node_type=3, mcu_temp_x100=3122,
            vdd_x100=330, ch_count=1, floats=[20.0],
        )
        parser = SerialLogParser()
        parser.feed(
            "[LORA]    RECEIVER ID: 64B5B30615E18000 | TIME: 2026-09-08T09:30:01+07:00",
            "10:00:00.001")
        events = parser.feed(
            "[LORA]    RSSI: -45 | SNR: 8 | LENGTH: %d | DATA: %s"
            % (len(frame), type3_display_data(frame)),
            "10:00:00.002")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, RX_KIND_DATA)
        self.assertEqual(events[0].node_uuid, "0102030405060708")
        self.assertEqual(events[0].seq, 6)

    def test_crc_error_block(self):
        frame = bytearray(build_type3_data_frame(UID, seq=5, mcu_temp_x100=3122,
                                                 vdd_x100=330, ch_count=1, floats=[20.0]))
        frame[-1] ^= 0x01  # hỏng CRC
        parser = SerialLogParser()
        parser.feed("[LORA]    RECEIVER ID: 64B5B30615E18000 | TIME: ",
                    "10:00:00.001")
        events = parser.feed(
            "[LORA]    RSSI: -45 | SNR: 8 | LENGTH: %d | DATA: %s" % (len(frame), type3_display_data(bytes(frame))),
            "10:00:00.002")
        self.assertEqual(events[0].kind, RX_KIND_CRC_ERROR)

    def test_info_28b_unparsed(self):
        frame = build_type3_info_frame(UID)
        parser = SerialLogParser()
        parser.feed("[LORA]    RECEIVER ID: 64B5B30615E18000 | TIME: ",
                    "10:00:00.001")
        events = parser.feed(
            "[LORA]    RSSI: -45 | SNR: 8 | LENGTH: %d | DATA: %s" % (len(frame), type3_display_data(frame)),
            "10:00:00.002")
        self.assertEqual(events[0].kind, RX_KIND_UNPARSED)

    def test_old_node_data_block(self):
        frame = build_old_frame(42, OLD_UUID, seq=7)
        parser = SerialLogParser()
        parser.feed("[LORA]    RECEIVER ID: 64B5B30615E18000 | TIME: ",
                    "10:00:00.001")
        events = parser.feed(
            "[LORA]    RSSI: -50 | SNR: 5 | LENGTH: %d | DATA: %s" % (len(frame), type3_display_data(frame)),
            "10:00:00.002")
        evt = events[0]
        self.assertEqual(evt.kind, RX_KIND_DATA)
        self.assertEqual(evt.node_uuid, "AABBCCDDEEFF001122334455")
        self.assertEqual(evt.seq, 7)

    def test_notices(self):
        parser = SerialLogParser()
        evs = []
        evs += parser.feed("[FAIL]    SENSOR IS NOT ALLOWED [AABBCCDDEEFF001122334455]", "t")
        evs += parser.feed("[ OK ]    MQTT PUBLISHED [TOPIC: node/0102030405060708/data]", "t")
        evs += parser.feed("[ OK ]    ETHERNET CONNECTED [IP: 192.168.1.100]", "t")
        self.assertEqual(evs[0].kind, "not_allowed")
        self.assertEqual(evs[1].kind, "publish_ok")
        self.assertEqual(evs[1].topic, "node/0102030405060708/data")
        self.assertEqual(evs[2].kind, "ip")
        self.assertEqual(evs[2].ip, "192.168.1.100")

    def test_module_info(self):
        parser = SerialLogParser()
        line = ("[LORA]    MODULE INFO | UID: 64B5B30615E18000 | FW: 12 | "
                "FREQ: 470000000 Hz | BW: 125 kHz | TXP: 22 dBm")
        evs = parser.feed(line, "t")
        self.assertEqual(len(evs), 1)
        evt = evs[0]
        self.assertIsInstance(evt, SerialNotice)
        self.assertEqual(evt.kind, "module_info")
        self.assertEqual(evt.uuid, "64B5B30615E18000")
        self.assertEqual(evt.module_fw, "12")
        self.assertEqual(evt.module_freq, "470000000")
        self.assertEqual(evt.module_bw, "125")
        self.assertEqual(evt.module_txp, "22")
        self.assertEqual(parser.last_module_uid, "64B5B30615E18000")

    def test_module_info_with_negative_txp(self):
        parser = SerialLogParser()
        line = ("[LORA]    MODULE INFO | UID: 64B5B30615E18000 | FW: 12 | "
                "FREQ: 470000000 Hz | BW: 500 kHz | TXP: -10 dBm")
        evs = parser.feed(line, "t")
        evt = evs[0]
        self.assertEqual(evt.kind, "module_info")
        self.assertEqual(evt.module_txp, "-10")

    def test_wifi_ip_parsed(self):
        parser = SerialLogParser()
        line = '[ OK ]    WIFI CONNECTED [SSID: MyNet, IP: 10.0.0.77]'
        evs = parser.feed(line, "t")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].kind, "ip")
        self.assertEqual(evs[0].ip, "10.0.0.77")
        self.assertIn("10.0.0.77", parser.last_seen_ips)

    def test_gateway_boot_info_block(self):
        """Block DEVICE INFO sau reboot → GatewayBootInfo với uuid+model+mac.

        Dùng đúng banner người dùng paste (UUID 11 hex rút gọn từ MAC efuse).
        """
        parser = SerialLogParser()
        lines = [
            "[INFO]    DEVICE INFO:",
            "[INFO]    Model : MGWI-V1",
            "[INFO]    Series: MGWI-V1-26070019",
            "[INFO]    UUID  : 4F9199E139C",
            "[INFO]    MAC   : 04:F9:19:9E:13:9C",
            "[INFO]    MQTT  : mqtt://45.117.170.179:1885",
            "[INFO]    LoRa  : 920600000 Hz | BW=0 | TXP=11 dBm",
            "",  # dòng trống chốt block
        ]
        evs = []
        for ln in lines:
            evs += parser.feed(ln, "10:00:00.000")
        self.assertEqual(len(evs), 1)
        boot = evs[0]
        self.assertIsInstance(boot, GatewayBootInfo)
        self.assertEqual(boot.uuid, "4F9199E139C")
        self.assertEqual(boot.model, "MGWI-V1")
        self.assertEqual(boot.series, "MGWI-V1-26070019")
        self.assertEqual(boot.mac, "04:F9:19:9E:13:9C")
        self.assertTrue(boot.complete)
        self.assertEqual(parser.last_boot.uuid, "4F9199E139C")

    def test_gateway_boot_info_mac_only_derives_uuid(self):
        """Bản firmware chỉ in MAC (không có dòng UUID) → suy UUID từ MAC."""
        parser = SerialLogParser()
        lines = [
            "[INFO]    DEVICE INFO:",
            "[INFO]    Model : MGWI-V1",
            "[INFO]    Series: MGWI-V1-26070019",
            "[INFO]    MAC   : 04:F9:19:9E:13:9C",
        ]
        evs = []
        for ln in lines:
            evs += parser.feed(ln, "10:00:00.000")
        evs += parser.feed("[ OK ]    ETHERNET INITIALIZED", "10:00:00.001")
        # Dòng [ OK ] ETHERNET chỉ làm nhiệm vụ chốt block (không sinh event);
        # event duy nhất là GatewayBootInfo vừa chốt.
        self.assertEqual(len(evs), 1)
        boot = evs[0]
        self.assertIsInstance(boot, GatewayBootInfo)
        # 04F9199E139C bỏ nibble 0 đầu → 4F9199E139C
        self.assertEqual(boot.uuid, "4F9199E139C")

    def test_gateway_full_real_boot_banner(self):
        """Chạy parser qua toàn bộ banner boot THẬT (user paste) — byte-exact.

        Đảm bảo: (1) DEVICE INFO chốt đúng UUID/Model/Series/MAC; (2) các dòng
        ESP-ROM/banner trang trí không gây event rác; (3) [LORA] MODULE INFO sau
        đó vẫn bắt receiver UID; (4) UUID 11 hex (KHÔNG phải 16 hex).
        """
        banner = """ESP-ROM:esp32s3-20210327
Build:Mar 27 2021
rst:0xc (RTC_SW_CPU_RST),boot:0x1c (SPI_FAST_FLASH_BOOT)
Saved PC:0x4037adf6
SPIWP:0xee
mode:DIO, clock div:1
load:0x3fce2820,len:0x10cc
load:0x403c8700,len:0xc2c
load:0x403cb700,len:0x30b0
entry 0x403c88b8

===========================================================
  LORA GATEWAY
  Firmware      : 0.0.1
  SDK (Arduino) : v5.5.5
  Chip          : ESP32-S3 (rev 2, 2 core, 240 MHz)
  Flash         : 8 MB
  PSRAM         : 2 MB
  SRAM/Heap     : 300124 B free
  MAC (efuse)   : 04:F9:19:9E:13:9C
===========================================================

[INFO]    DEVICE INFO:
[INFO]    Model : MGWI-V1
[INFO]    Series: MGWI-V1-26070019
[INFO]    UUID  : 4F9199E139C
[INFO]    MAC   : 04:F9:19:9E:13:9C
[INFO]    MQTT  : mqtt://45.117.170.179:1885
[INFO]    LoRa  : 920600000 Hz | BW=0 | TXP=11 dBm

[ OK ]    ETHERNET INITIALIZED
[LORA]    CONFIG REQUEST [MASK: 0x0007 | FREQ: 920600000 Hz | BW: 125 kHz | TXP: 11 dBm]
[LORA]    MODULE INFO | UID: FC8DB40615E18000 | FW: 1 | FREQ: 920600000 Hz | BW: 125 kHz | TXP: 11 dBm
[ OK ]    SD CARD INITIALIZED...."""
        parser = SerialLogParser()
        evs = []
        for ln in banner.splitlines():
            evs += parser.feed(ln, "10:00:00.000")
        # Chỉ 2 event có nghĩa: GatewayBootInfo + module_info (các dòng khác
        # không sinh event — raw hiển thị riêng).
        boots = [e for e in evs if isinstance(e, GatewayBootInfo)]
        mods = [e for e in evs if isinstance(e, SerialNotice) and e.kind == "module_info"]
        self.assertEqual(len(boots), 1)
        self.assertEqual(len(mods), 1)
        boot = boots[0]
        self.assertEqual(boot.uuid, "4F9199E139C")          # 11 hex — không phải 16
        self.assertEqual(boot.model, "MGWI-V1")
        self.assertEqual(boot.series, "MGWI-V1-26070019")
        self.assertEqual(boot.mac, "04:F9:19:9E:13:9C")
        self.assertTrue(boot.complete)
        self.assertEqual(parser.last_boot.uuid, "4F9199E139C")
        # Receiver UID từ MODULE INFO — KHÔNG nhầm với gateway UUID.
        self.assertEqual(mods[0].uuid, "FC8DB40615E18000")
        self.assertEqual(boot.mac, "04:F9:19:9E:13:9C")

    def test_gateway_boot_block_terminated_by_rx_anchor(self):
        """Block boot kết thúc khi dòng LoRa anchor xuất hiện — không nuốt packet."""
        parser = SerialLogParser()
        evs = []
        evs += parser.feed("[INFO]    DEVICE INFO:", "10:00:00.000")
        evs += parser.feed("[INFO]    UUID  : 4F9199E139C", "10:00:00.000")
        evs += parser.feed("[INFO]    MAC   : 04:F9:19:9E:13:9C", "10:00:00.000")
        evs += parser.feed(
            "[LORA]    RECEIVER ID: 64B5B30615E18000 | TIME: 2026-09-08T09:30:01+07:00",
            "10:00:00.001")
        evs += parser.feed(
            "[LORA]    RSSI: -45 | SNR: 8 | LENGTH: 22 | DATA: 01,02,03",
            "10:00:00.002")
        # 1 boot info + 1 packet (có thể INCOMPLETE do data giả — không quan trọng)
        kinds = [e.kind if hasattr(e, "kind") else type(e).__name__ for e in evs]
        self.assertIn("GatewayBootInfo", kinds)
        boot = evs[0]
        self.assertIsInstance(boot, GatewayBootInfo)
        self.assertEqual(boot.uuid, "4F9199E139C")
        # Packet anchor sau đó vẫn được xử lý bình thường.
        rx = [e for e in evs if isinstance(e, LoraRxEvent)]
        self.assertGreaterEqual(len(rx), 1)
        self.assertEqual(rx[0].receiver_id, "64B5B30615E18000")

    def test_gateway_report(self):
        parser = SerialLogParser()
        line = ('[INFO]    {"uuid":"64B5B30615E18000","model":"MGWI-V1",'
                '"series":"MGWI-V1-2606-01-002","firmware_version":"0.0.1",'
                '"status":"online","eth_ip":"192.168.1.100"}')
        evs = parser.feed(line, "t")
        self.assertEqual(len(evs), 1)
        rep = evs[0]
        self.assertIsInstance(rep, GatewayReport)
        self.assertEqual(rep.uuid, "64B5B30615E18000")
        self.assertEqual(parser.last_report.uuid, "64B5B30615E18000")

    def test_interleaved_line_flushes_incomplete(self):
        parser = SerialLogParser()
        parser.feed("[LORA]    RECEIVER ID: 64B5B30615E18000 | TIME: ", "t1")
        # Dòng lạ chèn vào giữa block (không phải dòng DATA) → packet treo
        # được finalize dạng INCOMPLETE (không tính vào metric).
        evs = parser.feed("[FAIL]    MQTT DISCONNECTED [ERROR: 0]", "t2")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].kind, RX_KIND_INCOMPLETE)


if __name__ == "__main__":
    unittest.main()
