"""HTTP tới Web API của gateway (đã có sẵn trong firmware — không tự tạo API).

  GET http://<ip>/api/status       → uuid/model/series/firmware_version/mac...
  GET http://<ip>/api/general-log   → toàn bộ config NVS (mqtt_topic, flags...)
Tham chiếu: src/web_task.cpp api_status_get / api_general_log_get.
"""

from __future__ import annotations

from typing import Any

import requests


def fetch_json(url: str, timeout_s: float = 3.0) -> tuple[bool, Any, str]:
    try:
        r = requests.get(url, timeout=timeout_s)
        if r.status_code != 200:
            return False, None, "HTTP %d" % r.status_code
        return True, r.json(), ""
    except requests.exceptions.RequestException as e:
        return False, None, str(e)


def gateway_status(ip: str, timeout_s: float = 3.0) -> tuple[bool, dict, str]:
    ok, data, err = fetch_json("http://%s/api/status" % ip, timeout_s)
    if not ok or not isinstance(data, dict):
        return False, {}, err or "invalid response"
    return True, data, ""


def gateway_config(ip: str, timeout_s: float = 3.0) -> tuple[bool, dict, str]:
    ok, data, err = fetch_json("http://%s/api/general-log" % ip, timeout_s)
    if not ok or not isinstance(data, dict):
        return False, {}, err or "invalid response"
    return True, data, ""
