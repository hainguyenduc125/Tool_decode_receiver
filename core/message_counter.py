"""Bộ đếm Receiver (Serial) và MQTT (server) — chỉ data frame (seq>0, CRC OK)
được tính vào metric; mọi loại khác đi vào nhóm chẩn đoán để operator debug.

Mọi phương thức gọi trên UI thread (event queue) nên không cần lock.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional

from models.message import (
    LoraRxEvent,
    MQTT_KIND_DATA,
    MQTT_KIND_ERROR,
    MQTT_KIND_INFO,
    MQTT_KIND_LOG,
    MQTT_KIND_OTHER,
    MQTT_KIND_VERBOSE,
    MqttEvent,
    NODE_KIND_NEW,
    NODE_KIND_OLD,
    RX_KIND_CRC_ERROR,
    RX_KIND_DATA,
    RX_KIND_INCOMPLETE,
    RX_KIND_INFO,
    RX_KIND_UNPARSED,
)

RATE_WINDOW_S = 5.0


class MessageCounter:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        # Receiver
        self.rx_total = 0
        self.rx_info = 0
        self.rx_crc_error = 0
        self.rx_unparsed = 0
        self.rx_incomplete = 0
        self.rx_old_total = 0          # data frame node cũ (24 hex)
        self.rx_new_total = 0          # data frame node mới (16 hex)
        self.rx_seqs: dict[str, dict[int, int]] = {}
        self.rx_kind_seqs: dict[str, dict[str, dict[int, int]]] = {}
        self.rx_node_total: dict[str, int] = {}
        self.rx_first = ""
        self.rx_last = ""

        # MQTT
        self.mqtt_total = 0
        self.mqtt_info = 0
        self.mqtt_log = 0            # node/<uuid>/log — diag, không đếm metric
        self.mqtt_error = 0
        self.mqtt_verbose = 0
        self.mqtt_other = 0
        self.mqtt_other_gateway = 0
        self.mqtt_no_uuid = 0          # filter bật nhưng chưa có UUID test
        self.mqtt_no_gw_id = 0         # topic node/# nhưng payload thiếu gateway_id
        self.mqtt_retained_ignored = 0
        self.mqtt_old_total = 0
        self.mqtt_new_total = 0
        self.mqtt_seqs: dict[str, dict[int, int]] = {}
        self.mqtt_kind_seqs: dict[str, dict[str, dict[int, int]]] = {}
        self.mqtt_node_total: dict[str, int] = {}
        self.mqtt_first = ""
        self.mqtt_last = ""

        # Chẩn đoán chung
        self.publish_ok = 0
        self.not_allowed = 0
        self.module_uid = ""

        # Correlation
        self.have_seq = False

        # Rate (ring mẫu)
        self._rate_buf: deque[tuple[float, int, int]] = deque()  # (mono, rx, mqtt)

    # -- Receiver --------------------------------------------------------------
    def add_rx(self, evt: LoraRxEvent) -> None:
        if evt.kind == RX_KIND_DATA and evt.seq is not None and evt.node_uuid:
            self.rx_total += 1
            self.have_seq = True
            self.rx_first = self.rx_first or evt.pc_time
            self.rx_last = evt.pc_time
            d = self.rx_seqs.setdefault(evt.node_uuid, {})
            d[evt.seq] = d.get(evt.seq, 0) + 1
            self.rx_node_total[evt.node_uuid] = self.rx_node_total.get(evt.node_uuid, 0) + 1
            if evt.node_kind == NODE_KIND_OLD:
                self.rx_old_total += 1
            elif evt.node_kind == NODE_KIND_NEW:
                self.rx_new_total += 1
            kd = self.rx_kind_seqs.setdefault(evt.node_kind, {}).setdefault(evt.node_uuid, {})
            kd[evt.seq] = kd.get(evt.seq, 0) + 1
        elif evt.kind == RX_KIND_INFO:
            self.rx_info += 1
        elif evt.kind == RX_KIND_CRC_ERROR:
            self.rx_crc_error += 1
        elif evt.kind == RX_KIND_UNPARSED:
            self.rx_unparsed += 1
        elif evt.kind == RX_KIND_INCOMPLETE:
            self.rx_incomplete += 1

    def add_notice(self, kind: str, **kw) -> None:
        if kind == "publish_ok":
            self.publish_ok += 1
        elif kind == "not_allowed":
            self.not_allowed += 1
        elif kind == "module_info":
            self.module_uid = kw.get("uuid", "")

    # -- MQTT -----------------------------------------------------------------
    def handle_mqtt(self, evt: MqttEvent, gateway_uuid: str, filter_enabled: bool,
                    count_retained: bool, allow_verbose: bool,
                    only_known_rx: bool = False) -> str:
        """Quyết định đếm/không đếm 1 message. Trả về nhãn để UI ghi chú."""
        if evt.retain and not count_retained:
            self.mqtt_retained_ignored += 1
            return "retained_ignored"

        # Sau khi đủ target: chỉ nhận nốt MQTT của (node,seq) ĐÃ có trong RX
        # (message đang bay trên đường) — không nhận seq mới phát sau khi freeze.
        if only_known_rx:
            if (evt.seq is None or evt.node_uuid not in self.rx_seqs
                    or evt.seq not in self.rx_seqs[evt.node_uuid]):
                return "ignored_post_stop"

        gw = gateway_uuid.strip().upper()

        if evt.kind == MQTT_KIND_OTHER:
            self.mqtt_other += 1
            return "ignored_other"

        if evt.kind == MQTT_KIND_VERBOSE:
            if not allow_verbose:
                self.mqtt_other += 1
                return "verbose_skipped"
            # Chỉ count-only (không filter gateway) — người dùng đã xác nhận.
            if evt.seq is not None and evt.seq > 0:
                self._mqtt_count(evt)
                return "counted"
            self.mqtt_info += 1
            return "verbose_info"

        # node/<id>/... : cần gateway_id xác định gateway
        if not evt.gateway_id:
            self.mqtt_no_gw_id += 1
            return "no_gateway_id"

        if filter_enabled:
            if not gw:
                self.mqtt_no_uuid += 1
                return "wait_uuid"
            if evt.gateway_id != gw:
                self.mqtt_other_gateway += 1
                return "other_gateway"

        if evt.kind == MQTT_KIND_DATA and evt.seq is not None and evt.seq > 0:
            self._mqtt_count(evt)
            return "counted"
        if evt.kind == MQTT_KIND_INFO:
            self.mqtt_info += 1
            return "info"
        if evt.kind == MQTT_KIND_LOG:
            self.mqtt_log += 1
            return "log"
        if evt.kind == MQTT_KIND_ERROR:
            self.mqtt_error += 1
            return "error"
        self.mqtt_other += 1
        return "ignored_other"

    def _mqtt_count(self, evt: MqttEvent) -> None:
        node_uuid = evt.node_uuid
        seq = evt.seq
        self.mqtt_total += 1
        self.have_seq = True
        self.mqtt_first = self.mqtt_first or evt.pc_time
        self.mqtt_last = evt.pc_time
        d = self.mqtt_seqs.setdefault(node_uuid, {})
        d[seq] = d.get(seq, 0) + 1
        self.mqtt_node_total[node_uuid] = self.mqtt_node_total.get(node_uuid, 0) + 1
        if evt.node_kind == NODE_KIND_OLD:
            self.mqtt_old_total += 1
        elif evt.node_kind == NODE_KIND_NEW:
            self.mqtt_new_total += 1
        kd = self.mqtt_kind_seqs.setdefault(evt.node_kind, {}).setdefault(node_uuid, {})
        kd[seq] = kd.get(seq, 0) + 1

    # -- Rate / snapshot --------------------------------------------------------
    def sample(self) -> None:
        now = time.monotonic()
        self._rate_buf.append((now, self.rx_total, self.mqtt_total))
        while self._rate_buf and now - self._rate_buf[0][0] > RATE_WINDOW_S + 1.0:
            self._rate_buf.popleft()

    def rates(self, elapsed_s: Optional[float]) -> dict:
        rx_rate = self.rx_total / elapsed_s if (elapsed_s and elapsed_s > 0) else 0.0
        mqtt_rate = self.mqtt_total / elapsed_s if (elapsed_s and elapsed_s > 0) else 0.0

        # Rate gần đây (cửa sổ trượt)
        rx_recent = 0.0
        mqtt_recent = 0.0
        if len(self._rate_buf) >= 2:
            t0, rx0, mq0 = self._rate_buf[0]
            t1, rx1, mq1 = self._rate_buf[-1]
            dt = t1 - t0
            if dt > 0.05:
                rx_recent = (rx1 - rx0) / dt
                mqtt_recent = (mq1 - mq0) / dt
        return {
            "rx_msg_s": rx_rate,
            "mqtt_msg_s": mqtt_rate,
            "rx_recent_s": rx_recent,
            "mqtt_recent_s": mqtt_recent,
        }

    def diagnostics(self) -> dict:
        return {
            "rx_info": self.rx_info,
            "rx_crc_error": self.rx_crc_error,
            "rx_unparsed": self.rx_unparsed,
            "rx_incomplete": self.rx_incomplete,
            "rx_old_total": self.rx_old_total,
            "rx_new_total": self.rx_new_total,
            "mqtt_info": self.mqtt_info,
            "mqtt_log": self.mqtt_log,
            "mqtt_error": self.mqtt_error,
            "mqtt_verbose": self.mqtt_verbose,
            "mqtt_other": self.mqtt_other,
            "mqtt_other_gateway": self.mqtt_other_gateway,
            "mqtt_no_uuid": self.mqtt_no_uuid,
            "mqtt_no_gw_id": self.mqtt_no_gw_id,
            "mqtt_retained_ignored": self.mqtt_retained_ignored,
            "mqtt_old_total": self.mqtt_old_total,
            "mqtt_new_total": self.mqtt_new_total,
            "gateway_publish_ok": self.publish_ok,
            "sensor_not_allowed": self.not_allowed,
            "lora_module_uid": self.module_uid,
        }
