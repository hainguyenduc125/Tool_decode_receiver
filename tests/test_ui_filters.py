"""Test bộ lọc tìm kiếm UI: node (UID node) + gateway (UID gateway).

Kiểm tra 2 helper thuần trong `ui/main_window.py` (`_node_matches`, `_gw_matches`)
và `_row_matches` ghép cả hai — cô lập bản tin của 1 node / do 1 gateway bắn khi
có nhiều gateway cùng bắn trên broker.
"""

import unittest

from core.source_comparator import DEFAULT_SUFFIX
from ui.main_window import _gw_matches, _node_matches, _row_matches

UID_NEW = "0E275E0615E18000FC8B3004"    # node mới: 24 hex đã lắp đuôi
UID_NEW_BASE = "0E275E0615E18000"        # UID gốc 16 hex
UID_OLD = "4F9199E139C"                  # node cũ (không đuôi)


class TestNodeMatches(unittest.TestCase):
    def test_empty_query_matches_all(self):
        self.assertTrue(_node_matches(UID_NEW, "", DEFAULT_SUFFIX))
        self.assertTrue(_node_matches("", "", DEFAULT_SUFFIX))

    def test_substring_of_bare_uid(self):
        self.assertTrue(_node_matches(UID_NEW, "0E275E", DEFAULT_SUFFIX))

    def test_matches_base_uid_against_24hex(self):
        # UID gốc 16 hex phải khớp node mới 24 hex (đã bóc suffix để so).
        self.assertTrue(_node_matches(UID_NEW, UID_NEW_BASE, DEFAULT_SUFFIX))

    def test_old_node_substring(self):
        self.assertTrue(_node_matches(UID_OLD, "99E1", DEFAULT_SUFFIX))

    def test_non_match(self):
        self.assertFalse(_node_matches(UID_NEW, "DEADBEEF", DEFAULT_SUFFIX))

    def test_empty_node_uuid_does_not_match(self):
        self.assertFalse(_node_matches("", "ABC", DEFAULT_SUFFIX))

    def test_case_insensitive(self):
        self.assertTrue(_node_matches(UID_OLD, "4f9199", DEFAULT_SUFFIX))


class TestGatewayMatches(unittest.TestCase):
    def test_empty_query_matches_all(self):
        self.assertTrue(_gw_matches("4F9199E139C", ""))

    def test_substring_match(self):
        self.assertTrue(_gw_matches("4F9199E139C", "99E1"))

    def test_case_insensitive(self):
        self.assertTrue(_gw_matches("4F9199E139C", "4f9199"))

    def test_non_match(self):
        self.assertFalse(_gw_matches("4F9199E139C", "AAAA"))

    def test_row_without_gateway_passes(self):
        # Dòng không gắn gateway (COM2 / payload hệ cũ) luôn đi qua — giữ để
        # không phá khối đối chiếu 3 nguồn của node.
        self.assertTrue(_gw_matches("", "4F9199E139C"))


class TestRowMatches(unittest.TestCase):
    def _row(self, node="", gw=""):
        return {"node_uuid": node, "gateway_uuid": gw}

    def test_both_empty(self):
        self.assertTrue(_row_matches(self._row(UID_OLD, "4F9199E139C"),
                                     "", "", DEFAULT_SUFFIX))

    def test_node_only(self):
        self.assertTrue(_row_matches(self._row(UID_OLD, "4F9199E139C"),
                                     "4F91", "", DEFAULT_SUFFIX))
        self.assertFalse(_row_matches(self._row(UID_NEW, "4F9199E139C"),
                                      "4F91", "", DEFAULT_SUFFIX))

    def test_gateway_only(self):
        self.assertTrue(_row_matches(self._row(UID_OLD, "4F9199E139C"),
                                     "", "4F91", DEFAULT_SUFFIX))
        self.assertFalse(_row_matches(self._row(UID_OLD, "AAAA"),
                                      "", "4F91", DEFAULT_SUFFIX))

    def test_both_filters_must_match(self):
        # Khớp node nhưng sai gateway → loại.
        self.assertFalse(_row_matches(self._row(UID_OLD, "AAAABBBB"),
                                      "4F91", "4F91", DEFAULT_SUFFIX))
        # Khớp cả 2 → nhận.
        self.assertTrue(_row_matches(self._row(UID_OLD, "4F9199E139C"),
                                     "4F91", "4F91", DEFAULT_SUFFIX))


if __name__ == "__main__":
    unittest.main()
