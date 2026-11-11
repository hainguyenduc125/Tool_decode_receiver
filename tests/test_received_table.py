"""Qt tests for received UART record table behavior."""

from __future__ import annotations

import os
import unittest
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    from core.lora_frame import decode_console_line
    from core.node_stats import ReceivedFrame, record_uid_for_line, seq_parity
    from ui.received_table import ReceivedMessageTable

    HAVE_QT = True
except ImportError:
    HAVE_QT = False

L_LINE = (
    "L:343947093330303128002B00,0001,"
    "0E275E0615E18000FC8B30440000000000000000"
)
D_LINE = (
    "D:343947093330303128002B00,3059,00,10,00,00,"
    "10C8E002,01,00,FF9E,001B,E1E0"
)
UID = "343947093330303128002B00"


def _frame(ts: float, line: str, seq: int | None = None) -> ReceivedFrame:
    try:
        decoded = decode_console_line(line)
        if seq is not None:
            decoded = deepcopy(decoded)
            decoded.packet.seq = seq
        return ReceivedFrame(
            ts, line, seq_parity(decoded), record_uid_for_line(line, decoded), decoded, None
        )
    except ValueError as error:
        return ReceivedFrame(ts, line, "Không SEQ", "", None, str(error))


@unittest.skipUnless(HAVE_QT, "PyQt5 không khả dụng")
class TestReceivedMessageTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_columns_readonly_and_raw_line_user_role(self):
        table = ReceivedMessageTable(max_rows=5)
        table.add_frames([_frame(0, L_LINE)])
        self.assertEqual(table.columnCount(), 9)
        self.assertTrue(table.verticalHeader().isHidden())
        self.assertEqual(table.item(0, 0).data(Qt.UserRole), L_LINE)
        self.assertEqual(table.item(0, 1).text(), "Không SEQ")
        self.assertEqual(table.item(0, 2).text(), UID)

    def test_uid_parity_filter_and_selection_signal(self):
        table = ReceivedMessageTable(max_rows=5)
        table.add_frames([_frame(0, L_LINE), _frame(1, D_LINE)])
        selected = []
        table.lineSelected.connect(lambda *args: selected.append(args))
        table.apply_filters(UID.lower(), {"Lẻ"})
        self.assertTrue(table.isRowHidden(0))
        self.assertFalse(table.isRowHidden(1))
        table.setCurrentCell(1, 0)
        self.assertEqual(selected[0][0], D_LINE)
        self.assertEqual(selected[0][1], "Lẻ")

    def test_oldest_rows_are_removed_at_limit(self):
        table = ReceivedMessageTable(max_rows=2)
        table.add_frames([
            _frame(0, L_LINE),
            _frame(1, D_LINE),
            _frame(2, L_LINE),
        ])
        self.assertEqual(table.rowCount(), 2)
        self.assertEqual(table.item(0, 0).data(Qt.UserRole), D_LINE)

    def test_decode_errors_are_marked(self):
        table = ReceivedMessageTable(max_rows=5)
        table.add_frames([_frame(0, "L:bad")])
        self.assertEqual(table.item(0, 6).text(), "LỖI")
        self.assertEqual(table.item(0, 6).background().color().name(), "#fde2e2")

    def test_bad_d_crc_is_marked_orange(self):
        table = ReceivedMessageTable(max_rows=5)
        table.add_frames([_frame(0, D_LINE[:-1] + "1")])
        self.assertEqual(table.item(0, 7).text(), "SAI")
        self.assertEqual(table.item(0, 7).background().color().name(), "#ffe8cc")

    def test_even_and_odd_rows_have_distinct_backgrounds(self):
        even = _frame(0, D_LINE, 10)
        odd = _frame(1, D_LINE, 11)
        table = ReceivedMessageTable(max_rows=5)
        table.add_frames([even, odd])
        self.assertEqual(table.item(0, 1).text(), "Chẵn")
        self.assertEqual(table.item(0, 1).background().color().name(), "#e8f2ff")
        self.assertEqual(table.item(1, 1).text(), "Lẻ")
        self.assertEqual(table.item(1, 1).background().color().name(), "#e9f5e9")


if __name__ == "__main__":
    unittest.main()
