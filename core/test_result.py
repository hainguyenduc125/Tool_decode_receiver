"""Verdict PASS/FAIL/N/A từ kết quả đối soát.

Đối soát theo KEY (node_id chuẩn hoá, seq) — seq duy nhất trên mỗi UID node.
Ngưỡng không hard-code — lấy từ config["pass"]:
  max_missing                    (default 0)
  min_delivery_rate_percent      (default 100.0)
PASS ⇔ missing ≤ max_missing VÀ delivery_rate ≥ min_delivery_rate_percent
       VÀ không có extra trên MQTT (extra = bản ghi MQTT không có ở Rx).
rx_total == 0 → N/A (không chia cho 0).
"""

from __future__ import annotations

from models.message import MatchResult, SessionSummary


def evaluate(res: MatchResult, pass_cfg: dict, target_count: int,
             stopped: bool = False) -> tuple[str, str]:
    if res.rx_total == 0:
        return "N/A", "Không nhận được data frame nào trên Receiver (Rx=0)."

    rate = res.delivery_rate
    max_missing = int(pass_cfg.get("max_missing", 0))
    min_rate = float(pass_cfg.get("min_delivery_rate_percent", 100.0))

    rate_ok = rate is None or rate + 1e-9 >= min_rate
    missing_ok = res.missing_count <= max_missing
    extra_ok = res.extra_mqtt == 0
    ok = missing_ok and rate_ok and extra_ok

    reason_lines = []
    if ok:
        reason_lines.append("Delivery rate đạt ngưỡng cấu hình.")
    else:
        if not missing_ok:
            reason_lines.append("Missing %d > max_missing %d." % (res.missing_count, max_missing))
        if not rate_ok:
            reason_lines.append("Delivery rate %.2f%% < %.2f%%." % (rate or 0.0, min_rate))
        if not extra_ok:
            reason_lines.append(
                "Có %d bản tin MQTT không khớp Rx (extra)." % res.extra_mqtt)
        if res.duplicates_mqtt:
            reason_lines.append("Phát hiện %d duplicate trên MQTT." % res.duplicates_mqtt)

    if stopped:
        reason_lines.append("Test bị dừng tay (STOP) trước khi hoàn tất.")

    result = "PASS" if ok else "FAIL"
    return result, "; ".join(reason_lines) or "OK"


def format_result_block(s: SessionSummary) -> str:
    """Format block TEST RESULT giống spec §26.

    Đối soát theo key (UID, seq); kèm dòng Matched/Extra để thấy rõ vì sao
    delivery rate có thể < 100% dù tổng số bản tin bằng nhau.
    """
    rate = "N/A" if s.delivery_rate is None else "%.2f%%" % s.delivery_rate
    lines = [
        "=" * 40,
        "TEST RESULT",
        "=" * 40,
        "",
        "Gateway UUID: %s" % s.gateway_uuid,
        "",
        "Target:",
        "%d" % s.target_count,
        "",
        "LoRa Receiver:",
        "%d" % s.receiver_count,
        "",
        "MQTT Server:",
        "%d" % s.mqtt_count,
        "",
        "Matched (UID,seq):",
        "%d" % (s.extra.get("matched_count", 0) if s.extra else 0),
        "",
        "Missing:",
        "%d" % s.missing_count,
        "",
        "Extra (MQTT):",
        "%d" % (s.extra.get("extra_mqtt", 0) if s.extra else 0),
        "",
        "Duplicate (RX):",
        "%d" % (s.extra.get("duplicates_rx", 0) if s.extra else 0),
        "",
        "Duplicate (MQTT):",
        "%d" % s.duplicate_mqtt,
        "",
        "Delivery Rate:",
        rate,
        "",
        "Result:",
        s.result,
        "",
        "Reason:",
        s.reason,
        "=" * 40,
    ]
    return "\n".join(lines)
