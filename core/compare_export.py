"""Xuất CSV kết quả đối chiếu (2 luồng + per-node + per-message).

Gom toàn bộ kết quả đối chiếu của 1 session vào MỘT file CSV phẳng, tự mô tả
bằng cột `section`:

  - `section="flow"`   : tóm tắt 2 luồng — GATEWAY↔MQTT và COM2↔GATEWAY
                         (result / matched / missing / extra / delivery_rate).
  - `section="node"`   : per-node của từng luồng (`flow` phân biệt).
  - `section="message"`: per-(UID, seq) — nguồn nào có bản tin (COM2 / gateway /
                         MQTT), thời điểm + RSSI/SNR + kết luận từng key.

Mọi UID được chuẩn hoá về UID gốc 8 byte (bóc đuôi 8 hex của node mới) để cùng
1 key so khớp giữa các nguồn, khớp quy ước `SourceComparator`.
"""

from __future__ import annotations

import csv
from typing import Optional

from core.source_comparator import normalize_uid
from models.message import SRC_LORA, SRC_MQTT, SRC_REF

# Thứ tự cột cố định — dùng chung cho cả 3 loại section (ô không dùng để trống).
FIELDNAMES = [
    "section", "flow", "uid", "seq", "kind",
    "in_com2", "in_gateway", "in_mqtt",
    "com2_time", "gateway_time", "mqtt_time",
    "com2_rssi_snr", "gateway_rssi_snr", "mqtt_rssi_snr",
    "result", "second", "gateway", "rx_keys", "matched", "missing", "extra",
    "delivery_rate",
]

FLOW_GW_MQTT = "GATEWAY_MQTT"
FLOW_COM2_GW = "COM2_GATEWAY"

# Kết luận từng key (presence COM2 / gateway / MQTT).
RESULT_OK = "OK"
RESULT_MISSING_GATEWAY = "MISSING_GATEWAY"   # COM2 có, gateway không
RESULT_EXTRA_GATEWAY = "EXTRA_GATEWAY"       # gateway có, COM2 không
RESULT_MISSING_MQTT = "MISSING_MQTT"         # radio có, MQTT không
RESULT_EXTRA_MQTT = "EXTRA_MQTT"             # chỉ MQTT có


def _blank() -> dict:
    return {k: "" for k in FIELDNAMES}


def _rssi_snr(row: dict) -> str:
    rssi = row.get("rssi", "")
    snr = row.get("snr", "")
    if rssi in ("", None):
        return ""
    return "R%s/S%s" % (rssi, snr if snr not in ("", None) else "-")


def _is_data_row(row: dict) -> bool:
    """Chỉ lấy row data (kind '*/DATA') — bỏ rác CRC/UNPARSED/INFO."""
    kind = str(row.get("kind", ""))
    return kind.split("/", 1)[-1] == "DATA"


def _radio_result(in_com2: bool, in_gw: bool, in_mqtt: bool) -> str:
    """Kết luận 1 key theo mức ưu tiên (issue nặng nhất trước)."""
    if in_com2 and not in_gw:
        return RESULT_MISSING_GATEWAY
    if in_gw and not in_com2:
        return RESULT_EXTRA_GATEWAY
    if (in_com2 or in_gw) and not in_mqtt:
        return RESULT_MISSING_MQTT
    if in_mqtt and not in_com2 and not in_gw:
        return RESULT_EXTRA_MQTT
    return RESULT_OK


def build_export(rows_all, match=None, src_result: Optional[dict] = None,
                 suffix: str = "FC8B3004",
                 gw_verdict: Optional[tuple] = None,
                 src_verdict: Optional[tuple] = None) -> list[dict]:
    """Dựng danh sách row CSV (dict theo FIELDNAMES) từ dữ liệu session.

    Args:
        rows_all: iterable row dict (pc_time/source/node_uuid/sequence/rssi/snr/kind).
        match: `MatchResult` (luồng gateway↔MQTT) hoặc None.
        src_result: dict `SourceComparator.compute()` (luồng COM2↔gateway) hoặc None.
        suffix: đuôi 8 hex của node mới (để chuẩn hoá UID).
        gw_verdict / src_verdict: tuple (result, reason) — ghi vào section flow.
    """
    out: list[dict] = []

    # -- Section flow: tóm tắt 2 luồng ------------------------------------
    # Chỉ ghi luồng nào THỰC SỰ có dữ liệu (tránh dòng 0/0 gây hiểu nhầm) —
    # cùng quy ước với `MainWindow._source_compare_section`.
    if match is not None and (match.rx_total or match.mqtt_total):
        rx_keys = match.matched_count + match.missing_count
        r = _blank()
        r.update({
            "section": "flow", "flow": FLOW_GW_MQTT,
            "result": (gw_verdict[0] if gw_verdict else ""),
            "rx_keys": rx_keys,
            "matched": match.matched_count,
            "missing": match.missing_count,
            "extra": match.extra_mqtt,
            "delivery_rate": ("" if match.delivery_rate is None
                              else round(match.delivery_rate, 4)),
        })
        out.append(r)
    if src_result is not None and (src_result.get("total_second", 0)
                                   or src_result.get("total_gateway", 0)):
        r = _blank()
        r.update({
            "section": "flow", "flow": FLOW_COM2_GW,
            "result": (src_verdict[0] if src_verdict else ""),
            "second": src_result.get("total_second", 0),
            "gateway": src_result.get("total_gateway", 0),
            "rx_keys": src_result.get("second_keys", 0),
            "matched": src_result.get("matched", 0),
            "missing": src_result.get("missing", 0),
            "extra": src_result.get("extra", 0),
            "delivery_rate": ("" if src_result.get("delivery_rate") is None
                              else round(src_result["delivery_rate"], 4)),
        })
        out.append(r)

    # -- Section node: per-node từng luồng --------------------------------
    if match is not None and (match.rx_total or match.mqtt_total):
        for nd in match.per_node:
            r = _blank()
            r.update({
                "section": "node", "flow": FLOW_GW_MQTT,
                "uid": nd.get("node_uuid", ""), "kind": nd.get("kind", ""),
                "rx_keys": nd.get("rx_keys", 0),
                "gateway": nd.get("mqtt", 0),
                "matched": max(0, nd.get("rx_keys", 0) - nd.get("missing", 0)),
                "missing": nd.get("missing", 0),
                "delivery_rate": ("" if nd.get("delivery_rate") is None
                                  else round(nd["delivery_rate"], 4)),
            })
            out.append(r)
    if src_result is not None and (src_result.get("total_second", 0)
                                   or src_result.get("total_gateway", 0)):
        for nd in src_result.get("per_node", []):
            r = _blank()
            r.update({
                "section": "node", "flow": FLOW_COM2_GW,
                "uid": nd.get("uid", ""), "kind": nd.get("kind", ""),
                "second": nd.get("second", 0),
                "gateway": nd.get("gateway", 0),
                "rx_keys": nd.get("rx_keys", 0),
                "matched": nd.get("matched", 0),
                "missing": nd.get("missing", 0),
                "extra": nd.get("extra", 0),
                "delivery_rate": ("" if nd.get("delivery_rate") is None
                                  else round(nd["delivery_rate"], 4)),
            })
            out.append(r)

    # -- Section message: per-(UID, seq) 3 nguồn --------------------------
    keys: dict[tuple, dict] = {}
    for row in rows_all:
        if not _is_data_row(row):
            continue
        uid = normalize_uid(str(row.get("node_uuid", "")), suffix)
        seq = row.get("sequence", "")
        if not uid or seq in ("", None):
            continue
        d = keys.setdefault((uid, seq), {
            "uid": uid, "seq": seq, "com2": None, "gw": None, "mqtt": None,
        })
        src = row.get("source", "")
        if src == SRC_REF:
            d["com2"] = row
        elif src == SRC_LORA:
            d["gw"] = row
        elif src == SRC_MQTT:
            d["mqtt"] = row

    def seq_key(k: tuple):
        try:
            return (0, k[0], int(k[1]))
        except (TypeError, ValueError):
            return (1, k[0], str(k[1]))

    for key in sorted(keys.keys(), key=seq_key):
        d = keys[key]
        com2, gw, mqtt = d["com2"], d["gw"], d["mqtt"]
        r = _blank()
        r.update({
            "section": "message",
            "uid": d["uid"], "seq": d["seq"],
            "kind": (gw or com2 or mqtt or {}).get("kind", "").split("/", 1)[-1],
            "in_com2": int(com2 is not None),
            "in_gateway": int(gw is not None),
            "in_mqtt": int(mqtt is not None),
            "com2_time": (com2 or {}).get("pc_time", ""),
            "gateway_time": (gw or {}).get("pc_time", ""),
            "mqtt_time": (mqtt or {}).get("pc_time", ""),
            "com2_rssi_snr": _rssi_snr(com2) if com2 else "",
            "gateway_rssi_snr": _rssi_snr(gw) if gw else "",
            "mqtt_rssi_snr": _rssi_snr(mqtt) if mqtt else "",
            "result": _radio_result(com2 is not None, gw is not None,
                                    mqtt is not None),
        })
        out.append(r)

    return out


def write_csv(path, rows: list[dict]) -> int:
    """Ghi list row (dict) ra CSV theo FIELDNAMES. Trả về số dòng đã ghi."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return len(rows)
