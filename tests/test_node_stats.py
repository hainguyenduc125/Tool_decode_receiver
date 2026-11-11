"""Tests for SEQ parity filtering and incremental node statistics."""

from __future__ import annotations

from copy import deepcopy
import unittest

from core.lora_frame import decode_console_line
from core.node_stats import (
    NodeStatsStore,
    ReceivedFrame,
    record_matches_filters,
    record_uid,
    record_uid_for_line,
    seq_parity,
    uid_matches,
)

L_LINE = (
    "L:343947093330303128002B00,0001,"
    "0E275E0615E18000FC8B30440000000000000000"
)
D_LINE = (
    "D:343947093330303128002B00,3059,00,10,00,00,"
    "10C8E002,01,00,FF9E,001B,E1E0"
)
UID = "343947093330303128002B00"
OTHER_UID = "343947093330303128002B01"


def frame(ts: float, line: str = D_LINE, seq: int | None = None) -> ReceivedFrame:
    try:
        decoded = decode_console_line(line)
    except ValueError as error:
        return ReceivedFrame(ts, line, "Không SEQ", "", None, str(error))
    if seq is not None:
        decoded = deepcopy(decoded)
        decoded.packet.seq = seq
    return ReceivedFrame(
        ts, line, seq_parity(decoded), record_uid_for_line(line, decoded), decoded, None
    )


def frame_for_uid(ts: float, uid: str, seq: int) -> ReceivedFrame:
    received = frame(ts, seq=seq)
    received.uid = uid
    return received


class TestRecordHelpers(unittest.TestCase):
    def test_seq_parity_for_even_odd_and_missing_sequence(self):
        self.assertEqual(seq_parity(frame(0, seq=10).decoded), "Chẵn")
        self.assertEqual(seq_parity(frame(0, seq=11).decoded), "Lẻ")
        self.assertEqual(seq_parity(decode_console_line(L_LINE)), "Không SEQ")
        self.assertEqual(seq_parity(None), "Không SEQ")

    def test_l_record_uid_comes_from_receiver_uid(self):
        decoded = decode_console_line(L_LINE)
        self.assertEqual(decoded.packet.node_uuid, "")
        self.assertEqual(record_uid(decoded), UID)
        self.assertEqual(record_uid_for_line("L:bad", None), "")

    def test_d_sequence_is_four_hex_digits_and_16_bit(self):
        decoded = decode_console_line(D_LINE.replace(",3059,", ",FFFF,"))
        self.assertEqual(decoded.packet.seq, 65535)

    def test_uid_matches_exactly_and_ignores_case(self):
        self.assertTrue(uid_matches(UID.lower(), UID))
        self.assertFalse(uid_matches(UID, UID[:-1]))
        self.assertTrue(uid_matches("", ""))

    def test_shared_uid_and_parity_filter(self):
        even = frame(0, seq=2)
        odd = frame(1, seq=3)
        self.assertTrue(record_matches_filters(even.uid, even.parity, UID, {"Chẵn"}))
        self.assertFalse(record_matches_filters(even.uid, even.parity, UID, {"Lẻ"}))
        self.assertFalse(
            record_matches_filters(OTHER_UID, odd.parity, UID, {"Lẻ"})
        )


class TestNodeStatsStore(unittest.TestCase):
    def test_even_and_odd_sequences_are_tracked_independently(self):
        store = NodeStatsStore()
        for ts, seq in enumerate((10, 11, 12, 13, 14, 17, 16)):
            store.add(frame(float(ts), seq=seq))
        stats = store.compute(UID, {"Chẵn", "Lẻ"}, now=20)
        self.assertEqual((stats["Chẵn"].received_unique, stats["Chẵn"].expected), (4, 4))
        self.assertEqual((stats["Lẻ"].received_unique, stats["Lẻ"].expected), (3, 4))
        self.assertEqual(stats["Lẻ"].missing_seqs, [15])
        self.assertEqual(stats["Lẻ"].missing, 1)

    def test_duplicate_sequence_does_not_increase_received(self):
        store = NodeStatsStore()
        for ts, seq in enumerate((10, 12, 12)):
            store.add(frame(float(ts), seq=seq))
        stats = store.compute(UID, {"Chẵn"}, now=3)["Chẵn"]
        self.assertEqual(stats.received_unique, 2)
        self.assertEqual(stats.expected, 2)
        self.assertEqual(stats.duplicate_seq, 1)

    def test_sequence_wraps_for_both_parities(self):
        store = NodeStatsStore()
        for ts, seq in enumerate((65532, 65533, 65534, 65535, 0, 1, 2)):
            store.add(frame(float(ts), seq=seq))
        stats = store.compute(UID, {"Chẵn", "Lẻ"}, now=7)
        self.assertEqual((stats["Chẵn"].received_unique, stats["Chẵn"].expected), (4, 4))
        self.assertEqual((stats["Lẻ"].received_unique, stats["Lẻ"].expected), (3, 3))

    def test_late_packet_fills_missing_sequence(self):
        store = NodeStatsStore()
        for ts, seq in enumerate((10, 14, 12)):
            store.add(frame(float(ts), seq=seq))
        stats = store.compute(UID, {"Chẵn"}, now=3)["Chẵn"]
        self.assertEqual((stats.received_unique, stats.expected, stats.missing), (3, 3, 0))
        self.assertEqual(stats.missing_seqs, [])

    def test_reset_starts_new_segments_for_both_parities(self):
        store = NodeStatsStore()
        for ts, seq in enumerate((500, 501, 4, 5)):
            store.add(frame(float(ts), seq=seq))
        stats = store.compute(UID, {"Chẵn", "Lẻ"}, now=4)
        for parity in ("Chẵn", "Lẻ"):
            self.assertEqual(stats[parity].resets, 1)
            self.assertEqual(stats[parity].expected, 2)
            self.assertEqual(stats[parity].received_unique, 2)
            self.assertEqual(stats[parity].missing, 0)
        self.assertEqual(stats["Chẵn"].seq_first, 500)
        self.assertEqual(stats["Chẵn"].seq_last, 4)

    def test_first_large_sequence_does_not_count_prior_sequences_missing(self):
        store = NodeStatsStore()
        store.add(frame(0, seq=3764))
        stats = store.compute(UID, {"Chẵn"}, now=0)["Chẵn"]
        self.assertEqual((stats.seq_first, stats.seq_last), (3764, 3764))
        self.assertEqual((stats.expected, stats.received_unique, stats.missing), (1, 1, 0))

    def test_no_sequence_and_bad_crc_are_not_counted_as_received(self):
        store = NodeStatsStore()
        store.add(frame(0, L_LINE))
        store.add(frame(1, D_LINE[:-1] + "1"))
        stats = store.compute(UID, {"Không SEQ", "Lẻ"}, now=1)
        self.assertEqual(stats["Không SEQ"].received_unique, 1)
        self.assertIsNone(stats["Không SEQ"].expected)
        self.assertEqual(stats["Lẻ"].received_unique, 0)
        self.assertEqual(stats["Lẻ"].crc_bad, 1)

    def test_even_odd_pairing_with_unpaired_messages(self):
        store = NodeStatsStore()
        for ts, seq in ((0, 10), (10, 12), (1, 11), (20, 13)):
            store.add(frame(float(ts), seq=seq))
        result = store.pair_even_odd(UID, 2.0)
        self.assertEqual((result.paired, result.even_unpaired, result.odd_unpaired), (1, 1, 1))

    def test_reset_clears_uid_trackers_until_next_packet(self):
        store = NodeStatsStore()
        store.add(frame(0, seq=10))
        store.add(frame(1, seq=12))
        store.reset(UID)
        store.add(frame(2, seq=100))
        store.add(frame(3, seq=102))
        stats = store.compute(UID, {"Chẵn"}, now=3)["Chẵn"]
        self.assertEqual((stats.seq_first, stats.seq_last), (100, 102))
        self.assertEqual((stats.received_unique, stats.expected, stats.missing), (2, 2, 0))

    def test_frame_limit_discards_oldest_display_records(self):
        store = NodeStatsStore(max_frames=2)
        for ts, seq in enumerate((2, 4, 6)):
            store.add(frame(float(ts), seq=seq))
        self.assertEqual([item.ts for item in store.frames_for(UID, {"Chẵn"})], [1, 2])

    def test_pair_stats_are_uid_specific(self):
        store = NodeStatsStore()
        store.add(frame_for_uid(0, UID, 10))
        store.add(frame_for_uid(1, OTHER_UID, 11))
        self.assertEqual(store.pair_even_odd(UID, 2.0).paired, 0)
        store.add(frame_for_uid(1, UID, 11))
        self.assertEqual(store.pair_even_odd(UID, 2.0).paired, 1)


if __name__ == "__main__":
    unittest.main()
