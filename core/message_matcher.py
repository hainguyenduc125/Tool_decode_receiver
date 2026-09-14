"""Đối soát receiver ↔ MQTT theo cặp (node_id, seq) sau settle window.

Missing = (node,seq) mà receiver nhận nhưng MQTT của đúng gateway không có.
Duplicate = (node,seq) xuất hiện > 1 lần (MQTT: re-publish/replay; RX: echo RF).

Vì node mới (UID 8 byte / 16 hex) được server chuyển đổi bằng cách LẮP THÊM ĐUÔI
để thành 24 hex giống node cũ, nên key giữa 2 nguồn có thể lệch độ dài. Hàm này
chuẩn hoá mọi id về dạng 24 hex (thêm `new_suffix` vào node mới) trước khi so.
"""

from __future__ import annotations

from models.message import MatchResult, NODE_KIND_NEW, node_kind_from_id

_DEFAULT_SUFFIX = "FC8B3004"  # đuôi lắp vào node mới để khớp cấu trúc node cũ


def normalize_id(node: str, suffix: str) -> str:
    """Chuẩn hoá id: node mới (16 hex) + suffix -> 24 hex; node cũ giữ nguyên.

    Dùng chung cho matcher và UI (bảng node live) để 2 nơi không lệch logic.
    """
    if node_kind_from_id(node) == NODE_KIND_NEW and suffix:
        return node + suffix.upper()
    return node


def _count_duplicates(d: dict) -> int:
    return sum(max(0, c - 1) for c in d.values())


def compute(rx_seqs: dict, rx_node_total: dict,
            mqtt_seqs: dict, mqtt_node_total: dict,
            rx_total: int, mqtt_total: int,
            have_seq: bool, new_suffix: str = _DEFAULT_SUFFIX) -> MatchResult:
    res = MatchResult()
    res.rx_total = rx_total
    res.mqtt_total = mqtt_total
    res.correlation = "AVAILABLE" if have_seq else "COUNT ONLY"

    # Map id Rx/MQTT sang dạng chuẩn (24 hex) — merge cả 2 nguồn.
    rx_norm: dict[str, dict[int, int]] = {}
    mqtt_norm: dict[str, dict[int, int]] = {}
    rx_node_norm: dict[str, int] = {}
    mqtt_node_norm: dict[str, int] = {}
    for node, seqs in rx_seqs.items():
        key = normalize_id(node, new_suffix)
        d = rx_norm.setdefault(key, {})
        for seq, cnt in seqs.items():
            d[seq] = d.get(seq, 0) + cnt
        rx_node_norm[key] = rx_node_norm.get(key, 0) + rx_node_total.get(node, 0)
    for node, seqs in mqtt_seqs.items():
        key = normalize_id(node, new_suffix)
        d = mqtt_norm.setdefault(key, {})
        for seq, cnt in seqs.items():
            d[seq] = d.get(seq, 0) + cnt
        mqtt_node_norm[key] = mqtt_node_norm.get(key, 0) + mqtt_node_total.get(node, 0)

    missing = 0
    matched = 0
    extra = 0
    dup_rx = 0
    dup_mqtt = 0
    # Đối soát theo KEY (node,seq) — seq duy nhất trên mỗi UID node nên
    # matched/missing/extra là SỐ KEY. `cnt` chỉ dùng để phát hiện duplicate.
    for node, seqs in rx_norm.items():
        mq_seqs = mqtt_norm.get(node, {})
        for seq, cnt in seqs.items():
            if mq_seqs.get(seq, 0) > 0:
                matched += 1
            else:
                missing += 1
            if cnt > 1:
                dup_rx += cnt - 1
    for node, seqs in mqtt_norm.items():
        rx_seqs_n = rx_norm.get(node, {})
        for seq, cnt in seqs.items():
            if rx_seqs_n.get(seq, 0) == 0:
                extra += 1
            if cnt > 1:
                dup_mqtt += cnt - 1

    rx_key_total = sum(len(s) for s in rx_norm.values())
    res.matched_count = matched
    res.missing_count = missing
    res.extra_mqtt = extra
    res.duplicates_rx = dup_rx
    res.duplicates_mqtt = dup_mqtt
    # delivery_rate = số key Rx có mặt trên MQTT / TỔNG số key Rx.
    res.delivery_rate = None if rx_key_total == 0 else (matched / rx_key_total * 100.0)

    # Bảng per-node (đã chuẩn hoá id).
    nodes = sorted(set(rx_norm) | set(mqtt_norm))
    for node in nodes:
        rx_seqs_n = rx_norm.get(node, {})
        mq_seqs_n = mqtt_norm.get(node, {})
        m_node = 0
        for seq, cnt in rx_seqs_n.items():
            if mq_seqs_n.get(seq, 0) == 0:
                m_node += 1
        rx_c = rx_node_norm.get(node, 0)
        mq_c = mqtt_node_norm.get(node, 0)
        rx_keys = len(rx_seqs_n)
        matched_node = sum(1 for seq in rx_seqs_n if mq_seqs_n.get(seq, 0) > 0)
        res.per_node.append({
            "node_uuid": node,
            "kind": node_kind_from_id(node),
            "receiver": rx_c,
            "mqtt": mq_c,
            "rx_keys": rx_keys,
            "missing": m_node,
            "delivery_rate": None if rx_keys == 0 else (matched_node / rx_keys * 100.0),
            "dup": _count_duplicates(mq_seqs_n),
        })
    return res
