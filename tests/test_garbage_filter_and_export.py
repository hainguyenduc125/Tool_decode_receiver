"""Test lọc bản tin rác + export CSV kết quả đối chiếu.

Bao gồm:
  - `SourceComparator.add_gateway_event` lọc CRC lỗi / non-DATA / seq<=0.
  - `ui.main_window._is_garbage_row` nhận diện đúng kind rác.
  - `core.compare_export.build_export` dựng đúng 3 section + kết luận từng key.
"""

import csv
import os
import tempfile
import unittest

from core.compare_export import (
    FIELDNAMES,
    FLOW_COM2_GW,
    FLOW_GW_MQTT,
    RESULT_EXTRA_GATEWAY,
    RESULT_MISSING_GATEWAY,
    RESULT_MISSING_MQTT,
    RESULT_OK,
    build_export,
    write_csv,
)
from core.source_comparator import DEFAULT_SUFFIX, SourceComparator
from models.message import (
    LoraRxEvent,
    MatchResult,
    RX_KIND_CRC_ERROR,
    RX_KIND_DATA,
    RX_KIND_UNPARSED,
    SRC_LORA,
    SRC_MQTT,
    SRC_REF,
)

UID_NEW = "0E275E0615E18000FC8B3004"
UID_BASE = "0E275E0615E18000"


def mk_evt(uid: str, seq: int, kind=RX_KIND_DATA, crc_ok=True) -> LoraRxEvent:
    return LoraRxEvent(
        kind=kind, pc_time="00:00:00.000", gw_time="", receiver_id="",
        node_uuid=uid, seq=seq, rssi=-80, snr=5, length=len(uid) // 2,
        frame_hex=uid, crc_ok=crc_ok,
    )


def mk_row(source: str, uid: str, seq, kind="LORA/DATA", rssi=-80, snr=5,
           pc_time="00:00:00.000") -> dict:
    return {
        "pc_time": pc_time, "source": source, "gateway_uuid": "GW1",
        "node_uuid": uid, "sequence": seq, "rssi": rssi, "snr": snr,
        "kind": kind, "topic": "", "payload": "",
    }


class TestGarbageRowDetection(unittest.TestCase):
    """`_is_garbage_row` — dùng chung cho lọc Message Log/CSV."""

    def setUp(self):
        # Import trong test để không cần QApplication cho phần này.
        from ui.main_window import _is_garbage_row
        self.f = _is_garbage_row

    def test_crc_error_is_garbage(self):
        self.assertTrue(self.f({"kind": "LORA/CRC_ERROR"}))
        self.assertTrue(self.f({"kind": "COM2/CRC_ERROR"}))

    def test_unparsed_and_incomplete_are_garbage(self):
        self.assertTrue(self.f({"kind": "LORA/UNPARSED"}))
        self.assertTrue(self.f({"kind": "LORA/INCOMPLETE"}))

    def test_data_and_info_are_not_garbage(self):
        self.assertFalse(self.f({"kind": "LORA/DATA"}))
        self.assertFalse(self.f({"kind": "LORA/INFO"}))
        self.assertFalse(self.f({"kind": "MQTT/DATA"}))
        self.assertFalse(self.f({}))


class TestGatewayEventFiltering(unittest.TestCase):
    """`add_gateway_event` phải lọc y hệt `add_second_source`."""

    def test_crc_error_frame_rejected(self):
        c = SourceComparator()
        c.add_gateway_event(mk_evt(UID_NEW, 1, crc_ok=False))
        r = c.compute()
        self.assertEqual(r["total_gateway"], 0)
        self.assertEqual(r["gateway_keys"], 0)

    def test_unparsed_frame_rejected(self):
        c = SourceComparator()
        c.add_gateway_event(mk_evt(UID_NEW, 1, kind=RX_KIND_UNPARSED))
        self.assertEqual(c.compute()["total_gateway"], 0)

    def test_crc_error_kind_rejected(self):
        c = SourceComparator()
        c.add_gateway_event(mk_evt(UID_NEW, 1, kind=RX_KIND_CRC_ERROR, crc_ok=False))
        self.assertEqual(c.compute()["total_gateway"], 0)

    def test_zero_seq_rejected(self):
        c = SourceComparator()
        c.add_gateway_event(mk_evt(UID_NEW, 0))
        self.assertEqual(c.compute()["total_gateway"], 0)

    def test_valid_frame_accepted_and_uid_normalised(self):
        c = SourceComparator()
        c.add_gateway_event(mk_evt(UID_NEW, 7))
        r = c.compute()
        self.assertEqual(r["total_gateway"], 1)
        self.assertEqual(r["gateway_keys"], 1)
        # UID node mới được bóc đuôi -> quan hệ key khớp với nguồn COM2.
        self.assertEqual(r["per_node"][0]["uid"], UID_BASE)

    def test_garbage_does_not_create_phantom_extra(self):
        # COM2 nhận key (1..2) sạch; gateway nhận cùng key sạch + 1 frame CRC lỗi.
        c = SourceComparator()
        c.add_second_source(mk_evt(UID_NEW, 1))
        c.add_second_source(mk_evt(UID_NEW, 2))
        c.add_gateway_event(mk_evt(UID_NEW, 1))
        c.add_gateway_event(mk_evt(UID_NEW, 99, crc_ok=False))
        r = c.compute()
        self.assertEqual(r["extra"], 0)
        self.assertEqual(r["missing"], 1)   # seq 2 chỉ có ở COM2


class TestBuildExport(unittest.TestCase):
    def _match(self) -> MatchResult:
        m = MatchResult()
        m.rx_total = 2
        m.mqtt_total = 1
        m.matched_count = 1
        m.missing_count = 1
        m.extra_mqtt = 0
        m.delivery_rate = 50.0
        m.per_node = [{
            "node_uuid": UID_BASE, "kind": "NEW", "receiver": 2, "mqtt": 1,
            "rx_keys": 2, "missing": 1, "delivery_rate": 50.0, "dup": 0,
        }]
        return m

    def _src(self) -> dict:
        return {
            "total_second": 2, "total_gateway": 2, "second_keys": 2,
            "gateway_keys": 2, "matched": 2, "missing": 0, "extra": 0,
            "delivery_rate": 100.0,
            "per_node": [{
                "uid": UID_BASE, "kind": "NEW", "second": 2, "gateway": 2,
                "rx_keys": 2, "matched": 2, "missing": 0, "extra": 0,
                "delivery_rate": 100.0,
            }],
        }

    def test_fieldnames_are_stable(self):
        rows = build_export([], match=self._match(), src_result=self._src())
        for r in rows:
            self.assertEqual(set(r.keys()), set(FIELDNAMES))

    def test_flow_section_has_two_flows(self):
        rows = build_export([], match=self._match(), src_result=self._src(),
                            gw_verdict=("FAIL", "x"), src_verdict=("PASS", "y"))
        flows = {r["flow"]: r for r in rows if r["section"] == "flow"}
        self.assertEqual(set(flows), {FLOW_GW_MQTT, FLOW_COM2_GW})
        self.assertEqual(flows[FLOW_GW_MQTT]["result"], "FAIL")
        self.assertEqual(flows[FLOW_GW_MQTT]["rx_keys"], 2)
        self.assertEqual(flows[FLOW_COM2_GW]["result"], "PASS")
        self.assertEqual(flows[FLOW_COM2_GW]["delivery_rate"], 100.0)

    def test_node_section_per_flow(self):
        rows = build_export([], match=self._match(), src_result=self._src())
        nodes = [r for r in rows if r["section"] == "node"]
        self.assertEqual(len(nodes), 2)
        self.assertEqual({r["flow"] for r in nodes}, {FLOW_GW_MQTT, FLOW_COM2_GW})
        gw = [r for r in nodes if r["flow"] == FLOW_GW_MQTT][0]
        self.assertEqual(gw["matched"], 1)
        self.assertEqual(gw["missing"], 1)

    def test_message_section_presence_and_results(self):
        rows_all = [
            mk_row(SRC_REF, UID_BASE, 1),
            mk_row(SRC_LORA, UID_BASE, 1),
            mk_row(SRC_MQTT, UID_BASE, 1),
            mk_row(SRC_REF, UID_BASE, 2),
            mk_row(SRC_LORA, UID_BASE, 2),
            mk_row(SRC_LORA, UID_BASE, 3),
        ]
        rows = build_export(rows_all, suffix=DEFAULT_SUFFIX)
        msgs = {r["seq"]: r for r in rows if r["section"] == "message"}
        self.assertEqual(set(msgs), {1, 2, 3})
        self.assertEqual(msgs[1]["result"], RESULT_OK)
        self.assertEqual(msgs[2]["result"], RESULT_MISSING_MQTT)
        self.assertEqual(msgs[3]["result"], RESULT_EXTRA_GATEWAY)
        self.assertEqual(msgs[2]["in_mqtt"], 0)

    def test_message_section_skips_garbage_rows(self):
        rows_all = [
            mk_row(SRC_REF, UID_BASE, 1, kind="COM2/CRC_ERROR"),
            mk_row(SRC_REF, UID_BASE, 5, kind="COM2/UNPARSED"),
        ]
        rows = build_export(rows_all)
        self.assertEqual([r for r in rows if r["section"] == "message"], [])

    def test_missing_gateway_result(self):
        rows_all = [mk_row(SRC_REF, UID_BASE, 4)]
        rows = build_export(rows_all)
        self.assertEqual(rows[0]["result"], RESULT_MISSING_GATEWAY)

    def test_uid_normalised_between_sources(self):
        # Cùng seq 9: gateway báo UID node mới (24 hex) + MQTT báo UID gốc
        # (16 hex) → phải quy về CÙNG 1 key. COM2 không có → extra so với COM2.
        rows_all = [mk_row(SRC_LORA, UID_NEW, 9), mk_row(SRC_MQTT, UID_BASE, 9)]
        rows = build_export(rows_all, suffix=DEFAULT_SUFFIX)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uid"], UID_BASE)
        self.assertEqual(rows[0]["in_gateway"], 1)
        self.assertEqual(rows[0]["in_mqtt"], 1)
        self.assertEqual(rows[0]["result"], RESULT_EXTRA_GATEWAY)

    def test_uid_normalised_all_three_sources(self):
        rows_all = [
            mk_row(SRC_REF, UID_BASE, 9),
            mk_row(SRC_LORA, UID_NEW, 9),
            mk_row(SRC_MQTT, UID_BASE, 9),
        ]
        rows = build_export(rows_all, suffix=DEFAULT_SUFFIX)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uid"], UID_BASE)
        self.assertEqual(rows[0]["result"], RESULT_OK)

    def test_empty_flows_are_skipped(self):
        empty_match = MatchResult()
        empty_src = {"total_second": 0, "total_gateway": 0, "second_keys": 0,
                     "gateway_keys": 0, "matched": 0, "missing": 0, "extra": 0,
                     "delivery_rate": None, "per_node": []}
        rows = build_export([], match=empty_match, src_result=empty_src)
        self.assertEqual(rows, [])

    def test_empty_inputs_produce_no_rows(self):
        self.assertEqual(build_export([]), [])


class TestWriteCsv(unittest.TestCase):
    def test_roundtrip(self):
        rows = [{"section": "flow", "flow": FLOW_GW_MQTT, "missing": 3}]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.csv")
            n = write_csv(p, rows)
            self.assertEqual(n, 1)
            with open(p, encoding="utf-8-sig", newline="") as f:
                got = list(csv.DictReader(f))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["missing"], "3")
        self.assertEqual(list(got[0].keys()), FIELDNAMES)


if __name__ == "__main__":
    unittest.main()
