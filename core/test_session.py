"""TestSession — mỗi lần START tạo 1 session riêng.

  logs/<session_id>/
    uart_raw.log     (toàn bộ dòng serial trong session)
    mqtt_raw.log     (toàn bộ message MQTT nhận được)
    messages.csv     (từng message đã parse)
    summary.json     (thông số + kết quả)
    result.txt       (block TEST RESULT)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional

from core.config import ensure_logs_dir

CSV_COLUMNS = [
    "pc_time", "source", "gateway_uuid", "node_uuid", "sequence",
    "rssi", "snr", "kind", "topic", "payload",
]


def default_row() -> dict:
    return {c: "" for c in CSV_COLUMNS}


class TestSession:
    def __init__(self) -> None:
        self.dir: Optional[Path] = None
        self.session_id = ""
        self._f_uart: object = None
        self._f_mqtt: object = None
        self._f_csv: object = None
        self._csv_writer = None
        self.messages_path: Optional[Path] = None

    @property
    def active(self) -> bool:
        return self._f_csv is not None

    def start(self, session_id: str, gateway_uuid: str = "") -> Path:
        self.stop_session_io()
        base = ensure_logs_dir()
        self.session_id = session_id
        self.dir = base / session_id
        self.dir.mkdir(parents=True, exist_ok=True)

        self._f_uart = open(self.dir / "uart_raw.log", "w", encoding="utf-8", newline="")
        self._f_mqtt = open(self.dir / "mqtt_raw.log", "w", encoding="utf-8", newline="")
        self.messages_path = self.dir / "messages.csv"
        self._f_csv = open(self.messages_path, "w", encoding="utf-8", newline="")
        self._csv_writer = csv.DictWriter(self._f_csv, fieldnames=CSV_COLUMNS)
        self._csv_writer.writeheader()
        self._f_csv.flush()

        meta = {"session_id": session_id, "gateway_uuid": gateway_uuid}
        with open(self.dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        return self.dir

    def append_uart(self, text: str) -> None:
        if self._f_uart:
            self._f_uart.write(text if text.endswith("\n") else text + "\n")

    def append_mqtt_raw(self, line: str) -> None:
        if self._f_mqtt:
            self._f_mqtt.write(line if line.endswith("\n") else line + "\n")

    def record_row(self, row: dict) -> None:
        if self._csv_writer is None:
            return
        r = default_row()
        r.update({k: v for k, v in row.items() if v is not None})
        self._csv_writer.writerow(r)
        self._f_csv.flush()

    def flush_raw(self) -> None:
        for f in (self._f_uart, self._f_mqtt):
            if f:
                f.flush()

    def finish(self, summary: dict, result_text: str) -> Path:
        self.flush_raw()
        with open(self.dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        with open(self.dir / "result.txt", "w", encoding="utf-8") as f:
            f.write(result_text)
        self.stop_session_io()
        return self.dir

    def stop_session_io(self) -> None:
        for attr in ("_f_uart", "_f_mqtt", "_f_csv"):
            f = getattr(self, attr)
            if f:
                try:
                    f.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._csv_writer = None
