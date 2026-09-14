"""Test matcher (missing/duplicate/rate) + verdict."""

import unittest

from core.message_matcher import compute
from core.test_result import evaluate, format_result_block
from models.message import MatchResult, SessionSummary

NODE_A = "0102030405060708090A0B0C"  # 24 hex (node cũ)
NODE_B = "1112131415161718191A1B1C"  # 24 hex (node cũ)
NODE_NEW = "A1B2C3D4E5F60708"       # 16 hex (node mới) — sẽ được thêm đuôi
NODE_NEW_PAD = NODE_NEW + "FC8B3004"  # đuôi mặc định trong matcher


def _seqs(count: int, start: int = 1) -> dict:
    return {i: 1 for i in range(start, start + count)}


class TestMatcher(unittest.TestCase):
    def test_missing_and_dup(self):
        # Đối soát theo KEY (node, seq): matched/missing là SỐ KEY, không phải số bản tin.
        rx_seqs = {NODE_A: _seqs(5)}
        rx_seqs[NODE_A][3] = 2                        # duplicate RX seq 3
        mq_seqs = {NODE_A: {1: 1, 2: 2, 3: 1, 4: 1}}  # thiếu key seq 5; dup seq 2
        rx_node = {NODE_A: 6}
        mq_node = {NODE_A: 5}
        res = compute(rx_seqs, rx_node, mq_seqs, mq_node, rx_total=6, mqtt_total=5,
                      have_seq=True)
        self.assertEqual(res.matched_count, 4)      # seq 1,2,3,4 có trên MQTT
        self.assertEqual(res.missing_count, 1)      # key seq 5 không tới MQTT
        self.assertEqual(res.extra_mqtt, 0)         # MQTT không có key lạ
        self.assertEqual(res.duplicates_rx, 1)      # seq 3 nhận 2 lần
        self.assertEqual(res.duplicates_mqtt, 1)    # seq 2 nhận 2 lần
        # delivery_rate = matched_keys / rx_keys = 4/5.
        self.assertAlmostEqual(res.delivery_rate, 4 / 5 * 100)
        self.assertEqual(res.correlation, "AVAILABLE")
        self.assertEqual(len(res.per_node), 1)
        self.assertEqual(res.per_node[0]["rx_keys"], 5)

    def test_extra_mqtt_keys(self):
        # MQTT có key mà receiver không nhận -> extra (re-publish/lạ).
        rx_seqs = {NODE_A: _seqs(3)}
        mq_seqs = {NODE_A: _seqs(4)}   # MQTT thừa key seq 4
        res = compute(rx_seqs, {NODE_A: 3}, mq_seqs, {NODE_A: 4}, 3, 4, True)
        self.assertEqual(res.matched_count, 3)
        self.assertEqual(res.missing_count, 0)
        self.assertEqual(res.extra_mqtt, 1)
        self.assertEqual(res.delivery_rate, 100.0)   # mẫu số là key Rx

    def test_empty_rx(self):
        res = compute({}, {}, {}, {}, 0, 0, False)
        self.assertIsNone(res.delivery_rate)
        self.assertEqual(res.missing_count, 0)

    def test_multi_node_rows(self):
        rx_seqs = {NODE_A: _seqs(3), NODE_B: _seqs(5)}
        mq_seqs = {NODE_A: _seqs(3), NODE_B: _seqs(4)}
        rx_node = {NODE_A: 3, NODE_B: 5}
        mq_node = {NODE_A: 3, NODE_B: 4}
        res = compute(rx_seqs, rx_node, mq_seqs, mq_node, 8, 7, True)
        rows = {r["node_uuid"]: r for r in res.per_node}
        self.assertEqual(rows[NODE_A]["missing"], 0)
        self.assertEqual(rows[NODE_B]["missing"], 1)

    def test_new_node_normalized_with_suffix(self):
        # Node mới (16 hex) trên Rx khớp với id 24 hex trên MQTT sau khi thêm đuôi.
        rx_seqs = {NODE_NEW: _seqs(3)}
        mq_seqs = {NODE_NEW_PAD: _seqs(3)}   # MQTT đã lắp đuôi
        rx_node = {NODE_NEW: 3}
        mq_node = {NODE_NEW_PAD: 3}
        res = compute(rx_seqs, rx_node, mq_seqs, mq_node, 3, 3, True)
        self.assertEqual(res.missing_count, 0)   # không thiếu vì đã chuẩn hoá
        self.assertEqual(len(res.per_node), 1)
        row = res.per_node[0]
        self.assertEqual(row["node_uuid"], NODE_NEW_PAD)
        self.assertEqual(row["kind"], "OLD")      # sau chuẩn hoá nhìn như node cũ
        self.assertEqual(row["missing"], 0)

    def test_new_node_missing_when_no_mqtt(self):
        # Nếu MQTT không có id tương ứng (đã chuẩn hoá) -> missing.
        rx_seqs = {NODE_NEW: _seqs(2)}
        mq_seqs = {NODE_NEW_PAD: _seqs(1)}
        rx_node = {NODE_NEW: 2}
        mq_node = {NODE_NEW_PAD: 1}
        res = compute(rx_seqs, rx_node, mq_seqs, mq_node, 2, 1, True)
        self.assertEqual(res.missing_count, 1)
        self.assertEqual(res.delivery_rate, 50.0)

    def test_custom_suffix(self):
        # Cho phép đổi đuôi tuỳ theo server, không hard-code.
        suffix = "DEADBEEF"
        rx_seqs = {NODE_NEW: _seqs(2)}
        mq_seqs = {NODE_NEW + suffix: _seqs(2)}
        res = compute(rx_seqs, {NODE_NEW: 2}, mq_seqs, {NODE_NEW + suffix: 2},
                      2, 2, True, new_suffix=suffix)
        self.assertEqual(res.missing_count, 0)


class TestVerdict(unittest.TestCase):
    def _res(self, rx, mq, missing=0, extra=0, matched=None) -> MatchResult:
        # delivery_rate theo KEY: matched / rx_keys; mặc định matched = rx - missing.
        if matched is None:
            matched = max(0, rx - missing)
        return MatchResult(rx_total=rx, mqtt_total=mq, matched_count=matched,
                           missing_count=missing, extra_mqtt=extra,
                           delivery_rate=(matched / rx * 100) if rx else None)

    def test_pass_when_complete(self):
        r, _ = evaluate(self._res(1000, 1000), {"max_missing": 0,
                                                "min_delivery_rate_percent": 100.0}, 1000)
        self.assertEqual(r, "PASS")

    def test_fail_when_extra_mqtt(self):
        # MQTT thừa key (re-publish/lạ) -> FAIL dù không missing.
        res = self._res(1000, 1001, missing=0, extra=1)
        r, reason = evaluate(res, {"max_missing": 0,
                                   "min_delivery_rate_percent": 100.0}, 1000)
        self.assertEqual(r, "FAIL")
        self.assertIn("extra", reason.lower())

    def test_fail_when_missing(self):
        # Ví dụ spec: Receiver 1000 / Server 998 → FAIL (missing 2 > max_missing 0)
        res = self._res(1000, 998, missing=2)
        res.duplicates_mqtt = 0
        r, reason = evaluate(res, {"max_missing": 0, "min_delivery_rate_percent": 100.0}, 1000)
        self.assertEqual(r, "FAIL")
        self.assertIn("Missing", reason)

    def test_fail_low_rate(self):
        res = self._res(1000, 990, missing=10)
        res.duplicates_mqtt = 0
        r, _ = evaluate(res, {"max_missing": 5, "min_delivery_rate_percent": 99.5}, 1000)
        self.assertEqual(r, "FAIL")

    def test_na_when_no_rx(self):
        res = MatchResult(rx_total=0, mqtt_total=0, missing_count=0,
                          delivery_rate=None)
        r, _ = evaluate(res, {"max_missing": 0, "min_delivery_rate_percent": 100.0}, 100)
        self.assertEqual(r, "N/A")

    def test_format_block(self):
        s = SessionSummary(gateway_uuid="64B5B30615E18000", receiver_count=1000,
                           mqtt_count=998, missing_count=2, result="FAIL",
                           delivery_rate=99.8, reason="x")
        block = format_result_block(s)
        self.assertIn("TEST RESULT", block)
        self.assertIn("FAIL", block)


if __name__ == "__main__":
    unittest.main()
