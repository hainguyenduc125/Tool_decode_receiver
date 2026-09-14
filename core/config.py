"""Load/save cấu hình tool từ config.json (kế bên main.py).

Mọi thông số vận hành (host/port/topic/ngưỡng PASS...) đều nằm trong config,
KHÔNG hard-code trong source.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = TOOL_ROOT / "logs"
CONFIG_PATH = TOOL_ROOT / "config.json"

DEFAULTS: dict = {
    "mqtt": {
        "host": "45.117.170.179",
        "port": 1885,
        "username": "",
        "password": "",
        "subscribe_topics": ["node/+/data", "node/+/log"],
        "filter_by_gateway_uuid": True,
        "count_retained": False,
    },
    "serial": {"baudrate": 115200},
    "gateway_http": {"enabled": True, "timeout_s": 3},
    "node": {
        # Đuôi 8 hex (4 byte) được server lắp thêm vào node MỚI (UID 8 byte)
        # để cấu trúc id thành 24 hex giống node cũ khi publish lên MQTT.
        # Tool dùng đuôi này để chuẩn hoá id 2 nguồn khi đối chiếu Rx <-> MQTT.
        "new_node_suffix": "FC8B3004",
    },
    "test": {
        "default_target_messages": 1000,
        "default_duration_seconds": 600,
        "settle_seconds": 5,
    },
    "pass": {"max_missing": 0, "min_delivery_rate_percent": 100.0},
    "ui": {
        "raw_log_lines": 5000,
        "table_max_rows": 50000,
        # Lọc bản tin rác (CRC lỗi / unparsed / incomplete) khỏi Message Log +
        # CSV — rác RF vẫn được đếm riêng ở diag (rx_crc_error/...).
        "filter_garbage": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    p = path or CONFIG_PATH
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg = _deep_merge(cfg, user)
        except (ValueError, OSError):
            pass
    return cfg


def save_config(cfg: dict, path: Path | None = None) -> None:
    p = path or CONFIG_PATH
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
    except OSError:
        pass


def ensure_logs_dir() -> Path:
    os.makedirs(LOGS_DIR, exist_ok=True)
    return LOGS_DIR
