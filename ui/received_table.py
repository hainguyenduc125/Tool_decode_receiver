"""Bảng record UART nhận được và lọc hiển thị theo UID/SEQ parity."""

from __future__ import annotations

import time

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from core.node_stats import (
    SEQ_PARITIES,
    ReceivedFrame,
    SeqParity,
    record_matches_filters,
    uid_matches,
)

RECEIVED_COLUMNS = [
    "Thời gian", "Chẵn/Lẻ", "UID", "SEQ", "RSSI", "SNR",
    "Loại", "CRC", "Gateway",
]
_PARITY_COLORS = {
    "Chẵn": QColor("#e8f2ff"),
    "Lẻ": QColor("#e9f5e9"),
    "Không SEQ": None,
}
_ERROR_COLOR = QColor("#fde2e2")
_CRC_BAD_COLOR = QColor("#ffe8cc")


def _display_number(value: float | int | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"


class ReceivedMessageTable(QTableWidget):
    lineSelected = pyqtSignal(str, str, str)

    def __init__(self, max_rows: int = 50000, parent=None) -> None:
        if max_rows < 1:
            raise ValueError("max_rows phải lớn hơn 0.")
        super().__init__(0, len(RECEIVED_COLUMNS), parent)
        self.setHorizontalHeaderLabels(RECEIVED_COLUMNS)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.verticalHeader().setVisible(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((90, 90, 190, 65, 65, 55, 85, 65, 110)):
            header.resizeSection(column, width)
        self._max_rows = max_rows
        self._uid_query = ""
        self._parities: set[SeqParity] = set(SEQ_PARITIES)
        self._filter_counts: dict[SeqParity, int] = {
            parity: 0 for parity in SEQ_PARITIES
        }
        self.currentCellChanged.connect(self._on_current_cell_changed)

    @property
    def has_selection(self) -> bool:
        return self.currentRow() >= 0

    @property
    def filter_counts(self) -> dict[SeqParity, int]:
        return dict(self._filter_counts)

    def add_frames(self, frames: list[ReceivedFrame]) -> None:
        if not frames:
            return
        frames = frames[-self._max_rows:]
        was_at_bottom = (
            self.verticalScrollBar().value() >= self.verticalScrollBar().maximum()
        )
        had_selection = self.has_selection
        self.setUpdatesEnabled(False)
        try:
            overflow = self.rowCount() + len(frames) - self._max_rows
            removed = max(0, min(overflow, self.rowCount()))
            for row in range(removed):
                parity_item = self.item(row, 1)
                uid_item = self.item(row, 2)
                self._count_row(
                    parity_item.text() if parity_item else "",
                    uid_item.text() if uid_item else "",
                    -1,
                )
            for _ in range(removed):
                self.removeRow(0)
            start = self.rowCount()
            self.setRowCount(start + len(frames))
            for offset, frame in enumerate(frames):
                row = start + offset
                self._populate_row(row, frame)
                self._count_row(frame.parity, frame.uid, 1)
                self.setRowHidden(
                    row,
                    not record_matches_filters(
                        frame.uid, frame.parity, self._uid_query, self._parities
                    ),
                )
        finally:
            self.setUpdatesEnabled(True)
        if not had_selection and was_at_bottom:
            self.scrollToBottom()

    def apply_filters(
        self, uid_query: str, parities: set[SeqParity] | frozenset[SeqParity]
    ) -> None:
        self._uid_query = uid_query
        self._parities = set(parities)
        self._filter_counts = {parity: 0 for parity in SEQ_PARITIES}
        for row in range(self.rowCount()):
            uid_item = self.item(row, 2)
            parity_item = self.item(row, 1)
            uid = uid_item.text() if uid_item else ""
            parity = parity_item.text() if parity_item else ""
            self._count_row(parity, uid, 1)
            self.setRowHidden(
                row,
                not record_matches_filters(uid, parity, uid_query, self._parities)
                if parity in SEQ_PARITIES else True,
            )

    def count_for_uid(self, uid_query: str) -> dict[SeqParity, int]:
        counts = {parity: 0 for parity in SEQ_PARITIES}
        for row in range(self.rowCount()):
            uid_item = self.item(row, 2)
            parity_item = self.item(row, 1)
            uid = uid_item.text() if uid_item else ""
            parity = parity_item.text() if parity_item else ""
            if parity in counts and uid_matches(uid, uid_query):
                counts[parity] += 1
        return counts

    def clear_records(self) -> None:
        self.setRowCount(0)
        self._filter_counts = {parity: 0 for parity in SEQ_PARITIES}

    def _count_row(self, parity: str, uid: str, change: int) -> None:
        if parity in self._filter_counts and uid_matches(uid, self._uid_query):
            self._filter_counts[parity] += change

    def _populate_row(self, row: int, frame: ReceivedFrame) -> None:
        decoded = frame.decoded
        packet = decoded.packet if decoded else None
        time_text = time.strftime("%H:%M:%S", time.localtime(frame.ts))
        if frame.error:
            values = [
                time_text, frame.parity, frame.uid, "", "", "", "LỖI", "", "LỖI"
            ]
            background = _ERROR_COLOR
        else:
            has_crc = bool(
                packet
                and packet.crc_calculated is not None
                and packet.crc_received is not None
            )
            values = [
                time_text,
                frame.parity,
                frame.uid,
                str(packet.seq) if packet and packet.seq is not None else "",
                _display_number(decoded.rssi) if decoded else "",
                _display_number(decoded.snr) if decoded else "",
                packet.kind if packet else "",
                "—" if not has_crc else ("OK" if packet and packet.crc_ok else "SAI"),
                (
                    "CHẤP NHẬN" if packet and packet.gateway_result == "OK"
                    else packet.gateway_result if packet else ""
                ),
            ]
            background = (
                _CRC_BAD_COLOR if packet and has_crc and not packet.crc_ok
                else _PARITY_COLORS[frame.parity]
            )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setData(Qt.UserRole, frame.raw_line)
            if background is not None:
                item.setBackground(background)
            self.setItem(row, column, item)

    def _on_current_cell_changed(self, row: int) -> None:
        if row < 0:
            return
        time_item = self.item(row, 0)
        parity_item = self.item(row, 1)
        if time_item is None or parity_item is None:
            return
        raw_line = time_item.data(Qt.UserRole)
        if raw_line:
            self.lineSelected.emit(str(raw_line), parity_item.text(), time_item.text())
