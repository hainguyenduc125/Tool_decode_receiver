"""Parser MQTT: topic + payload → MqttEvent chuẩn.

Format xác thực từ firmware (src/data_process.cpp) + xác nhận vận hành:
  - Topic xem bản tin node: `node/<uuid>/data` (data/telemetry + error packet
    new_system khi KHÔNG có `seq`) và `node/<uuid>/log` (log/status node —
    chỉ hiển thị/chẩn đoán, KHÔNG đếm). Topic `node/<uuid>/info` (legacy
    firmware) vẫn được nhận diện để tương thích.
  - Payload flat hệ mới:
      {"gateway_id","receiver_id","sensor_id","rssi","snr","timestamp","seq",...}
  - Hệ cũ (verbose, không gateway_id): topic tự do (vd "node_send"), payload
    {"UUID","VER","TagCode","RelayUnit","RSSI","SNR","TimeStamp","SEQ",...}
    → MQTT_KIND_VERBOSE: CHỈ đếm count-only, KHÔNG filter gateway.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from models.message import (
    MQTT_KIND_DATA,
    MQTT_KIND_ERROR,
    MQTT_KIND_INFO,
    MQTT_KIND_LOG,
    MQTT_KIND_OTHER,
    MQTT_KIND_VERBOSE,
    MqttEvent,
    node_kind_from_id,
)

RE_NODE_TOPIC = re.compile(r"^node/([0-9A-Fa-f]+)/(data|info|log)$")


def _num(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class MqttParser:
    """Phân loại message MQTT. KHÔNG quyết định đếm — counter/UI quyết định
    dựa trên gateway_uuid đang test + cờ filter."""

    def __init__(self) -> None:
        self.verbose_count = 0
        self.other_count = 0

    def parse(self, topic: str, payload: str, retain: bool, pc_time: str) -> MqttEvent:
        m = RE_NODE_TOPIC.match(topic)
        if m:
            return self._parse_flat(topic, m.group(1), m.group(2), payload, retain, pc_time)

        # Topic không thuộc node/<id>/... → thử payload hệ cũ.
        try:
            data = json.loads(payload)
        except ValueError:
            self.other_count += 1
            return MqttEvent(MQTT_KIND_OTHER, pc_time, topic, "", "", None,
                             None, None, "", payload, retain)
        if "UUID" in data and "SEQ" in data:
            self.verbose_count += 1
            uuid = str(data.get("UUID", "")).upper()
            return MqttEvent(
                kind=MQTT_KIND_VERBOSE,
                pc_time=pc_time,
                topic=topic,
                gateway_id="",
                node_uuid=uuid,
                node_kind=node_kind_from_id(uuid),
                seq=_num(data.get("SEQ")),
                rssi=_num(data.get("RSSI")),
                snr=_num(data.get("SNR")),
                gw_ts=str(data.get("TimeStamp", "")),
                payload=payload,
                retain=retain,
            )
        self.other_count += 1
        return MqttEvent(MQTT_KIND_OTHER, pc_time, topic, "", "", None,
                         None, None, "", payload, retain)

    def _parse_flat(self, topic: str, sensor_raw: str, sub: str, payload: str,
                    retain: bool, pc_time: str) -> MqttEvent:
        sensor = sensor_raw.upper()
        try:
            data = json.loads(payload)
        except ValueError:
            self.other_count += 1
            return MqttEvent(MQTT_KIND_OTHER, pc_time, topic, "", sensor, None,
                             None, None, "", payload, retain)

        gateway_id = str(data.get("gateway_id", "")).upper()
        seq = _num(data.get("seq"))
        gw_ts = str(data.get("timestamp", ""))

        if "error" in data and seq is None:
            kind = MQTT_KIND_ERROR
        elif sub == "log":
            # node/<uuid>/log — log/status của node: hiển thị, không đếm.
            kind = MQTT_KIND_LOG
        elif sub == "info" or seq == 0:
            kind = MQTT_KIND_INFO
        elif sub == "data" and seq is not None and seq > 0:
            kind = MQTT_KIND_DATA
        else:
            kind = MQTT_KIND_OTHER

        return MqttEvent(
            kind=kind,
            pc_time=pc_time,
            topic=topic,
            gateway_id=gateway_id,
            node_uuid=sensor,
            node_kind=node_kind_from_id(sensor),
            seq=seq,
            rssi=_num(data.get("rssi")),
            snr=_num(data.get("snr")),
            gw_ts=gw_ts,
            payload=payload,
            retain=retain,
        )
