"""Giao diện giải mã thủ công frame LoRa từ HEX hoặc log gateway."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.lora_frame import decode_packet_details, parse_hex_frame


class LoraDecoderWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        hint = QLabel(
            "Dán frame HEX, dòng +EVT:RXP2P, hoặc dòng log gateway có DATA:. "
            "Hỗ trợ node Type3 (firmware hiện tại và legacy) và node cũ."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ví dụ: 0102030405060708 0500 ...")
        self.input.setMaximumHeight(110)
        layout.addWidget(self.input)

        actions = QHBoxLayout()
        self.decode_button = QPushButton("Giải mã")
        self.decode_button.clicked.connect(self.decode_input)
        self.clear_button = QPushButton("Xóa")
        self.clear_button.clicked.connect(self.clear)
        actions.addWidget(self.decode_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Kết quả giải mã sẽ hiển thị ở đây.")
        layout.addWidget(self.output, 1)

    def clear(self) -> None:
        self.input.clear()
        self.output.clear()

    def decode_input(self) -> None:
        try:
            raw = parse_hex_frame(self.input.toPlainText())
        except ValueError as exc:
            self.output.setPlainText(f"Lỗi dữ liệu đầu vào: {exc}")
            return

        decoded = decode_packet_details(raw)
        status = "HỢP LỆ" if decoded.crc_ok else "KHÔNG HỢP LỆ / KHÔNG XÁC ĐỊNH"
        lines = [
            f"Kết quả: {status}",
            f"Layout: {decoded.layout}",
            f"Loại bản tin: {decoded.kind}",
            f"Độ dài: {decoded.length} byte",
        ]
        if decoded.node_uuid:
            lines.append(f"UID/UUID: {decoded.node_uuid}")
        if decoded.seq is not None:
            lines.append(f"Sequence: {decoded.seq}")
        if decoded.crc_calculated is not None and decoded.crc_received is not None:
            lines.append(
                "CRC: tính được 0x%04X | nhận được 0x%04X"
                % (decoded.crc_calculated, decoded.crc_received)
            )
        if decoded.fields:
            lines.extend(["", "Các trường:"])
            lines.extend(f"  {name}: {value}" for name, value in decoded.fields)
        if decoded.notes:
            lines.extend(["", "Ghi chú:"])
            lines.extend(f"  - {note}" for note in decoded.notes)
        self.output.setPlainText("\n".join(lines))
