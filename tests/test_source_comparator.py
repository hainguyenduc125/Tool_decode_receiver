"""Test đối chiếu 2 nguồn thu LoRa (mạch thu riêng vs gateway).

Kiểm tra: bóc đuôi FC8B3004, missing, matched, delivery_rate, per-node.
"""

import unittest

from core.source_comparator import DEFAULT_SUFFIX, SourceComparator, normalize_uid
from models.message import LoraRxEvent, RX_KIND_DATA

UID_NEW = "0E275E0615E18000FC8B3004"   # 24 hex - node mới đã lắp đuôi
UID_NEW_BASE = "0E275E0615E18000"       # 16 hex - UID gốc


def mk(uid: str, seq: int, kind=RX_KIND_DATA, crc_ok=True) -> LoraRxEvent:
    return LoraRxEvent(
        kind=kind, pc_time="00:00:00.000", gw_time="", receiver_id="",
        node_uuid=uid, seq=seq, rssi=-80, snr=5, length=len(uid) // 2,
        frame_hex=uid, crc_ok=crc_ok,
    )


class TestNormalizeUID(unittest.TestCase):
    def test_strips_new_node_suffix(self):
        self.assertEqual(normalize_uid(UID_NEW, DEFAULT_SUFFIX), UID_NEW_BASE)

    def test_keeps_bare_uid(self):
        self.assertEqual(normalize_uid(UID_NEW_BASE, DEFAULT_SUFFIX), UID_NEW_BASE)

    def test_keeps_old_node_without_suffix(self):
        old = "112233445566778899AABBCC"  # 24 hex nhưng không có đuôi
        self.assertEqual(normalize_uid(old, DEFAULT_SUFFIX), old)


class TestSourceComparator(unittest.TestCase):
    def test_match_all_when_same(self):
        c = SourceComparator()
        c.add_second_source(mk(UID_NEW, 1))
        c.add_gateway(UID_NEW_BASE, 1)
        r = c.compute()
        self.assertEqual(r["total_second"], 1)
        self.assertEqual(r["total_gateway"], 1)
        self.assertEqual(r["second_keys"], 1)
        self.assertEqual(r["gateway_keys"], 1)
        self.assertEqual(r["matched"], 1)
        self.assertEqual(r["missing"], 0)
        self.assertEqual(r["extra"], 0)
        self.assertEqual(r["delivery_rate"], 100.0)

    def test_missing_when_gw_misses(self):
        c = SourceComparator()
        c.add_second_source(mk(UID_NEW, 1))
        c.add_second_source(mk(UID_NEW, 2))
        c.add_gateway(UID_NEW_BASE, 1)  # chỉ có seq 1
        r = c.compute()
        self.assertEqual(r["total_second"], 2)
        self.assertEqual(r["matched"], 1)
        self.assertEqual(r["missing"], 1)
        self.assertEqual(r["extra"], 0)
        self.assertEqual(r["delivery_rate"], 50.0)

    def test_extra_when_gw_publishes_unseen(self):
        # Gateway publish key không có ở mạch thu riêng -> extra (không tính missing).
        c = SourceComparator()
        c.add_second_source(mk(UID_NEW, 1))
        c.add_gateway(UID_NEW_BASE, 1)
        c.add_gateway(UID_NEW_BASE, 99)  # gateway thừa seq 99
        r = c.compute()
        self.assertEqual(r["matched"], 1)
        self.assertEqual(r["missing"], 0)
        self.assertEqual(r["extra"], 1)
        self.assertEqual(r["delivery_rate"], 100.0)

    def test_duplicate_rx_does_not_inflate_rate(self):
        # Mạch thu nhận seq 1 hai lần + gateway 1 lần: matched vẫn là 1 KEY.
        c = SourceComparator()
        c.add_second_source(mk(UID_NEW, 1))
        c.add_second_source(mk(UID_NEW, 1))
        c.add_gateway(UID_NEW_BASE, 1)
        r = c.compute()
        self.assertEqual(r["total_second"], 2)   # đếm bản tin vẫn = 2
        self.assertEqual(r["second_keys"], 1)     # nhưng key duy nhất = 1
        self.assertEqual(r["matched"], 1)
        self.assertEqual(r["missing"], 0)
        self.assertEqual(r["delivery_rate"], 100.0)

    def test_ignores_non_data_and_crc_fail(self):
        c = SourceComparator()
        c.add_second_source(mk(UID_NEW, 0))            # info frame
        c.add_second_source(mk(UID_NEW, 1, crc_ok=False))  # CRC lỗi
        r = c.compute()
        self.assertEqual(r["total_second"], 0)

    def test_custom_suffix(self):
        c = SourceComparator(suffix="ABCD1234")
        c.add_second_source(mk("0E275E0615E18000ABCD1234", 7))
        c.add_gateway("0E275E0615E18000", 7)
        r = c.compute()
        self.assertEqual(r["matched"], 1)
        self.assertEqual(r["missing"], 0)

    def test_per_node_breakdown(self):
        c = SourceComparator()
        c.add_second_source(mk(UID_NEW, 1))
        c.add_gateway(UID_NEW_BASE, 1)
        r = c.compute()
        self.assertEqual(len(r["per_node"]), 1)
        row = r["per_node"][0]
        self.assertEqual(row["uid"], UID_NEW_BASE)
        self.assertEqual(row["kind"], "NEW")
        self.assertEqual(row["second"], 1)
        self.assertEqual(row["gateway"], 1)
        self.assertEqual(row["rx_keys"], 1)
        self.assertEqual(row["matched"], 1)
        self.assertEqual(row["missing"], 0)
        self.assertEqual(row["extra"], 0)
        self.assertEqual(row["delivery_rate"], 100.0)


if __name__ == "__main__":
    unittest.main()