"""QPlainTextEdit giới hạn dòng hiển thị (raw log) — không lag khi chạy lâu.
Dữ liệu đầy đủ vẫn được ghi vào file session riêng (test_session.py).
"""

from __future__ import annotations

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QPlainTextEdit


class LogViewer(QPlainTextEdit):
    def __init__(self, max_lines: int = 5000, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)

    def append_line(self, text: str) -> None:
        self.appendPlainText(text)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())
