"""Bảng message log + bảng tổng hợp per-node (QTableWidget, batch insert)."""

from __future__ import annotations

from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

MSG_COLUMNS = [
    "Time", "Source", "Gateway UUID", "Node UUID", "Seq",
    "RSSI", "SNR", "Kind", "Payload",
]
NODE_COLUMNS = [
    "Node UUID", "Receiver", "MQTT", "RX Keys", "Matched", "Missing",
    "Delivery Rate", "Duplicate",
]
SRC_COMPARE_COLUMNS = [
    "Node UID", "Nguồn 2 (COM2)", "Gateway (LORA)", "RX Keys", "Matched",
    "Missing", "Extra", "% khớp",
]
NC_COLUMNS = [
    "Seq",
    "LORA Time", "LORA RSSI/SNR", "LORA ✓",
    "COM2 Time", "COM2 RSSI/SNR", "COM2 ✓",
    "MQTT Time", "MQTT RSSI/SNR", "MQTT ✓",
]


def _item(text) -> QTableWidgetItem:
    return QTableWidgetItem("" if text is None else str(text))


class MessageLogTable(QTableWidget):
    def __init__(self, max_rows: int = 50000, parent=None) -> None:
        super().__init__(0, len(MSG_COLUMNS), parent)
        self.setHorizontalHeaderLabels(MSG_COLUMNS)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(True)
        h.resizeSection(0, 110)
        h.resizeSection(1, 60)
        h.resizeSection(2, 150)
        h.resizeSection(3, 170)
        h.resizeSection(4, 60)
        h.resizeSection(5, 50)
        h.resizeSection(6, 50)
        h.resizeSection(7, 90)
        self._max_rows = max_rows

    def add_rows(self, rows: list[list]) -> None:
        if not rows:
            return
        self.setUpdatesEnabled(False)
        try:
            n = len(rows)
            # Giữ dung lượng ổn định: bỏ bớt dòng cũ khi vượt max.
            # Lưu ý: QTableWidget KHÔNG có removeRows() (chỉ QAbstractItemModel);
            # widget chỉ có removeRow(i) → gọi lặp để bỏ dòng cũ trên đỉnh.
            overflow = self.rowCount() + n - self._max_rows
            if overflow > 0:
                for _ in range(min(overflow, self.rowCount())):
                    self.removeRow(0)
            self.setRowCount(self.rowCount() + n)
            start = self.rowCount() - n
            for r, row in enumerate(rows):
                for c, val in enumerate(row):
                    self.setItem(start + r, c, _item(val))
        finally:
            self.setUpdatesEnabled(True)
        self.scrollToBottom()


class NodeSummaryTable(QTableWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(0, len(NODE_COLUMNS), parent)
        self.setHorizontalHeaderLabels(NODE_COLUMNS)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

    def refresh(self, rows: list[dict]) -> None:
        self.setUpdatesEnabled(False)
        try:
            self.setRowCount(0)
            self.setRowCount(len(rows))
            for r, row in enumerate(rows):
                rate = row.get("delivery_rate")
                rate_txt = "N/A" if rate is None else "%.2f%%" % rate
                rx_keys = row.get("rx_keys", 0)
                missing = row.get("missing", 0)
                vals = [
                    row.get("node_uuid", ""),
                    row.get("receiver", 0),
                    row.get("mqtt", 0),
                    rx_keys,
                    max(0, rx_keys - missing),
                    missing,
                    rate_txt,
                    row.get("dup", 0),
                ]
                for c, val in enumerate(vals):
                    self.setItem(r, c, _item(val))
        finally:
            self.setUpdatesEnabled(True)


class SourceCompareTable(QTableWidget):
    """Bảng live: đối chiếu mạch thu riêng (COM 2) vs gateway theo (UID, seq).

    Mỗi dòng 1 node (UID gốc 8 byte sau khi bóc suffix). Cập nhật mỗi 200ms từ
    `SourceComparator.compute()` — hiển thị Missing/% khớp ngay trong lúc test.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(0, len(SRC_COMPARE_COLUMNS), parent)
        self.setHorizontalHeaderLabels(SRC_COMPARE_COLUMNS)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Stretch)
        h.resizeSection(0, 180)

    def refresh(self, rows: list[dict]) -> None:
        self.setUpdatesEnabled(False)
        try:
            self.setRowCount(0)
            self.setRowCount(len(rows))
            for r, row in enumerate(rows):
                rate = row.get("delivery_rate")
                rate_txt = "N/A" if rate is None else "%.2f%%" % rate
                vals = [
                    row.get("uid", ""),
                    row.get("second", 0),
                    row.get("gateway", 0),
                    row.get("rx_keys", 0),
                    row.get("matched", 0),
                    row.get("missing", 0),
                    row.get("extra", 0),
                    rate_txt,
                ]
                for c, val in enumerate(vals):
                    self.setItem(r, c, _item(val))
        finally:
            self.setUpdatesEnabled(True)


class NodeCompareTable(QTableWidget):
    """Bảng detail 1 node: ráp 3 nguồn (LORA gateway | COM2 | MQTT) theo Seq.

    Mỗi Seq 1 dòng — 3 cụm cột (Time/RSSI-SNR/✓) cho từng nguồn để đối chiếu
    bằng mắt: gateway LoRa có nhận không, mạch thu riêng có nhận không, MQTT có
    đẩy lên không. Ô ✓ = có bản tin của nguồn đó tại seq.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(0, len(NC_COLUMNS), parent)
        self.setHorizontalHeaderLabels(NC_COLUMNS)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(True)
        for i, w in enumerate([70, 100, 90, 45, 100, 90, 45, 100, 90, 45]):
            h.resizeSection(i, w)

    def refresh(self, rows: list[dict]) -> None:
        self.setUpdatesEnabled(False)
        try:
            self.setRowCount(0)
            self.setRowCount(len(rows))
            for r, row in enumerate(rows):
                def src_cell(src: str) -> list:
                    d = row.get(src)
                    if not d:
                        return ["", "", ""]
                    ok = d.get("present")
                    ts = d.get("time", "")
                    rs = d.get("rssi_snr", "")
                    return [ts, rs, "✓" if ok else ""]
                lora, ref, mq = src_cell("lora"), src_cell("ref"), src_cell("mqtt")
                vals = [row.get("seq", "")] + lora + ref + mq
                green = QColor("#1b7f3b")
                gray = QColor("#c0c0c0")
                for c, val in enumerate(vals):
                    item = _item(val)
                    if c in (3, 6, 9) and val == "✓":
                        item.setForeground(QBrush(green))
                    elif c in (3, 6, 9) and not val:
                        item.setForeground(QBrush(gray))
                    self.setItem(r, c, item)
        finally:
            self.setUpdatesEnabled(True)
