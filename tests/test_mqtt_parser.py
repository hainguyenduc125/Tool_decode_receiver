"""Test MqttParser + quyết định đếm của MessageCounter."""

import unittest

from core.message_counter import MessageCounter
from models.message import (
    LoraRxEvent,
    MQTT_KIND_ERROR,
    MQTT_KIND_INFO,
    MQTT_KIND_LOG,
    MQTT_KIND_OTHER,
    MQTT_KIND_VERBOSE,
    RX_KIND_DATA,
    now_pc_str,
)
from mqtt.mqtt_parser import MqttParser

GW_A = "64B5B30615E18000"
GW_B = "AABBCCDD00000000"
NODE = "0102030405060708"

FLAT = ('{"gateway_id":"%s","receiver_id":"0011223344556677","sensor_id":"%s",'
        '"rssi":-45,"snr":8,"timestamp":"2026-09-08T09:30:01+07:00","seq":%d,'
        '"mcu_temp":31.22,"mcu_vdd":3.3,"sensor_count":1,"sensor_value_0":[20.0]}')


class TestMqttParser(unittest.TestCase):
    def setUp(self):
        self.parser = MqttParser()

    def test_flat_data(self):
        evt = self.parser.parse("node/%s/data" % NODE, FLAT % (GW_A, NODE, 5), False, "t")
        self.assertEqual(evt.kind, "DATA")
        self.assertEqual(evt.gateway_id, GW_A)
        self.assertEqual(evt.node_uuid, NODE)
        self.assertEqual(evt.seq, 5)
        self.assertEqual(evt.rssi, -45)

    def test_log_topic(self):
        # node/<uuid>/log — log/status của node → LOG (diag, không đếm).
        payload = FLAT % (GW_A, NODE, 5)
        evt = self.parser.parse("node/%s/log" % NODE, payload, False, "t")
        self.assertEqual(evt.kind, MQTT_KIND_LOG)
        self.assertEqual(evt.seq, 5)

    def test_info_topic(self):
        evt = self.parser.parse("node/%s/info" % NODE,
                                '{"gateway_id":"%s","seq":0,"fw_version":1}' % GW_A,
                                False, "t")
        self.assertEqual(evt.kind, MQTT_KIND_INFO)

    def test_error_payload_no_seq(self):
        payload = ('{"gateway_id":"%s","receiver_id":"x","UUID":"%s",'
                   '"error":"CRC ERROR","packet_length":21,"raw_data":[1,2]}') % (GW_A, NODE)
        evt = self.parser.parse("node/%s/data" % NODE, payload, False, "t")
        self.assertEqual(evt.kind, MQTT_KIND_ERROR)
        self.assertIsNone(evt.seq)

    def test_verbose_old(self):
        payload = ('{"UUID":"%s","VER":"2.0","TagCode":"MTE-A","RelayUnit":"x",'
                   '"RSSI":-45,"SNR":8,"TimeStamp":"...","SEQ":5,"MCU":{}}') % NODE
        evt = self.parser.parse("node_send", payload, False, "t")
        self.assertEqual(evt.kind, MQTT_KIND_VERBOSE)
        self.assertEqual(evt.gateway_id, "")
        self.assertEqual(evt.seq, 5)

    def test_bad_json_other(self):
        evt = self.parser.parse("node/%s/data" % NODE, "not-json", False, "t")
        self.assertEqual(evt.kind, MQTT_KIND_OTHER)


def _rx(node: str, seq: int) -> LoraRxEvent:
    return LoraRxEvent(RX_KIND_DATA, now_pc_str(), "", "64B5B30615E18000",
                       node, seq, -45, 8, 21, "", True)


class TestCounter(unittest.TestCase):
    def setUp(self):
        self.counter = MessageCounter()
        self.parser = MqttParser()

    def test_rx_data_counted(self):
        self.counter.add_rx(_rx(NODE, 1))
        self.counter.add_rx(_rx(NODE, 2))
        self.counter.add_rx(_rx(NODE, 2))  # duplicate RX
        self.assertEqual(self.counter.rx_total, 3)
        self.assertEqual(self.counter.rx_seqs[NODE][2], 2)

    def test_rx_info_not_counted(self):
        evt = LoraRxEvent("INFO", now_pc_str(), "", "r", NODE, 0, -45, 8, 28, "", True)
        self.counter.add_rx(evt)
        self.assertEqual(self.counter.rx_total, 0)
        self.assertEqual(self.counter.rx_info, 1)

    def test_mqtt_filter_gateway(self):
        evt_a = self.parser.parse("node/%s/data" % NODE, FLAT % (GW_A, NODE, 1), False, "t")
        evt_b = self.parser.parse("node/%s/data" % NODE, FLAT % (GW_B, NODE, 2), False, "t")
        self.assertEqual(self.counter.handle_mqtt(evt_a, GW_A, True, False, False), "counted")
        self.assertEqual(self.counter.handle_mqtt(evt_b, GW_A, True, False, False), "other_gateway")
        self.assertEqual(self.counter.mqtt_total, 1)
        self.assertEqual(self.counter.mqtt_other_gateway, 1)

    def test_mqtt_wait_uuid(self):
        evt = self.parser.parse("node/%s/data" % NODE, FLAT % (GW_A, NODE, 1), False, "t")
        self.assertEqual(self.counter.handle_mqtt(evt, "", True, False, False), "wait_uuid")
        self.assertEqual(self.counter.mqtt_total, 0)

    def test_mqtt_retained_ignored(self):
        evt = self.parser.parse("node/%s/data" % NODE, FLAT % (GW_A, NODE, 1), True, "t")
        self.assertEqual(self.counter.handle_mqtt(evt, GW_A, True, False, False),
                         "retained_ignored")
        self.assertEqual(self.counter.mqtt_total, 0)

    def test_mqtt_only_known_rx_after_freeze(self):
        self.counter.add_rx(_rx(NODE, 1))
        evt = self.parser.parse("node/%s/data" % NODE, FLAT % (GW_A, NODE, 1), False, "t")
        self.assertEqual(self.counter.handle_mqtt(evt, GW_A, True, False, False,
                                                  only_known_rx=True), "counted")
        evt2 = self.parser.parse("node/%s/data" % NODE, FLAT % (GW_A, NODE, 99), False, "t")
        self.assertEqual(self.counter.handle_mqtt(evt2, GW_A, True, False, False,
                                                  only_known_rx=True), "ignored_post_stop")
        self.assertEqual(self.counter.mqtt_total, 1)

    def test_mqtt_log_not_counted(self):
        # Message node/<uuid>/log không bao giờ vào metric mqtt_total.
        evt = self.parser.parse("node/%s/log" % NODE, FLAT % (GW_A, NODE, 5), False, "t")
        self.assertEqual(self.counter.handle_mqtt(evt, GW_A, True, False, False), "log")
        self.assertEqual(self.counter.mqtt_total, 0)
        self.assertEqual(self.counter.mqtt_log, 1)

    def test_verbose_requires_count_only_mode(self):
        payload = ('{"UUID":"%s","SEQ":5,"RSSI":-45,"SNR":8,"TimeStamp":"..."}') % NODE
        evt = self.parser.parse("node_send", payload, False, "t")
        # Filter bật → verbose không đếm (cảnh báo)
        self.assertEqual(self.counter.handle_mqtt(evt, GW_A, True, False, False),
                         "verbose_skipped")
        # Chế độ COUNT-ONLY (filter tắt) → đếm
        self.assertEqual(self.counter.handle_mqtt(evt, "", False, False, True), "counted")


if __name__ == "__main__":
    unittest.main()
