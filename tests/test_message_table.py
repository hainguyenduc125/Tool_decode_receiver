"""Regression test cho bảng UI (QTableWidget) — vòng đời add_rows/overflow.

Bắt lỗi `AttributeError: 'MessageLogTable' object has no attribute 'removeRows'`:
QTableWidget KHÔNG có removeRows() (chỉ QAbstractItemModel mới có); widget chỉ
có removeRow(i) → phải gọi lặp khi cắt dòng cũ vượt `max_rows`.

Chạy headless bằng platform "offscreen"; tự skip nếu thiếu PyQt5.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtWidgets import QApplication
    from ui.message_table import MessageLogTable, NodeSummaryTable
    HAVE_QT = True
except Exception:  # PyQt5 chưa cài / không khởi tạo được
    HAVE_QT = False

_app = None


def _ensure_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


ROW = ["x"] * 9


@unittest.skipUnless(HAVE_QT, "PyQt5 không khả dụng")
class TestMessageLogTableOverflow(unittest.TestCase):
    def setUp(self):
        _ensure_app()

    def test_add_under_max(self):
        t = MessageLogTable(max_rows=10)
        t.add_rows([ROW] * 7)
        self.assertEqual(t.rowCount(), 7)

    def test_overflow_drops_oldest_rows(self):
        t = MessageLogTable(max_rows=10)
        t.add_rows([ROW] * 7)
        t.add_rows([ROW] * 6)          # 7 + 6 - 10 = 3 dòng cũ bị bỏ
        self.assertEqual(t.rowCount(), 10)
        self.assertIsNotNone(t.item(0, 0))
        self.assertIsNotNone(t.item(9, 0))

    def test_overflow_equal_to_rowcount(self):
        # Batch lớn hơn max → phải cắt hết dòng cũ, không lỗi index.
        t = MessageLogTable(max_rows=5)
        t.add_rows([ROW] * 3)
        t.add_rows([ROW] * 5)
        self.assertEqual(t.rowCount(), 5)

    def test_empty_batch_noop(self):
        t = MessageLogTable(max_rows=10)
        t.add_rows([])
        self.assertEqual(t.rowCount(), 0)


@unittest.skipUnless(HAVE_QT, "PyQt5 không khả dụng")
class TestNodeSummaryTable(unittest.TestCase):
    def setUp(self):
        _ensure_app()

    def test_refresh_uses_rx_keys_and_rate(self):
        t = NodeSummaryTable()
        t.refresh([{
            "node_uuid": "ABC", "receiver": 3, "mqtt": 3, "rx_keys": 3,
            "matched": 3, "missing": 0, "delivery_rate": 100.0, "dup": 0,
        }])
        self.assertEqual(t.rowCount(), 1)

    def test_refresh_na_rate(self):
        t = NodeSummaryTable()
        t.refresh([{
            "node_uuid": "ABC", "receiver": 0, "mqtt": 0, "rx_keys": 0,
            "matched": 0, "missing": 0, "delivery_rate": None, "dup": 0,
        }])
        self.assertEqual(t.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()
