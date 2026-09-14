"""Đối chiếu 2 nguồn thu LoRa: mạch thu riêng (COM 2) vs gateway.

Sau khi chạy test, sưu tập các data frame (CRC OK, seq > 0) từ mạch thu riêng và từ
gateway rồi so sánh theo cặp (UID gốc 8 byte, seq) để biết gateway có nhận được
đầy đủ các bản tin mà mạch thu riêng nhận được hay không.

Đối chiếu theo KEY `(UID gốc, seq)` — vì `seq` là DUY NHẤT trên mỗi UID node nên 1
key = 1 bản tin logic. Mọi metric (matched/missing/extra/delivery_rate) tính theo
SỐ KEY; số lần nhận không dùng để tính tỷ lệ (tránh tỷ lệ vượt 100% do echo/repeat).

Quy ước đã xác thực:
  - `sensor_id` trên MQTT / log mạch thu có thể là 24 hex (12 byte) vì server lắp
    thêm đuôi `FC8B3004` vào node mới (UID 8 byte = 16 hex). Trước khi so, ta bóc
    đuôi 8 hex cuối để quy về UID gốc 8 byte.
  - Chỉ so data frame: chế independent khỏi info (seq=0) và CRC lỗi.
"""

from __future__ import annotations

from typing import Optional

from models.message import LoraRxEvent, NODE_KIND_NEW, node_kind_from_id

DEFAULT_SUFFIX = "FC8B3004"


def normalize_uid(node_uuid: str, suffix: str = DEFAULT_SUFFIX) -> str:
    """Chuẩn hoá về UID gốc 8 byte: bỏ đuôi 8 hex cuối nếu id đang 24 hex.

    - 16 hex (node mới): giữ nguyên.
    - 24 hex: nếu kết thúc bằng `suffix` -> bỏ 8 hex cuối; ngược lại giữ nguyên
      (coi là node cũ thật).
    - khác: giữ nguyên.
    """
    n = len(node_uuid)
    if n == 24 and suffix and node_uuid.endswith(suffix.upper()):
        return node_uuid[:-8]
    return node_uuid


class SourceComparator:
    """Sưu tập và đối chiếu data frame giữa 2 nguồn thu LoRa."""

    def __init__(self, suffix: str = DEFAULT_SUFFIX) -> None:
        self.suffix = suffix
        self.reset()

    def reset(self) -> None:
        # (uid_goc -> {seq: count})
        self.sec_seqs: dict[str, dict[int, int]] = {}
        self.gw_seqs: dict[str, dict[int, int]] = {}
        self.sec_uid_total: dict[str, int] = {}
        self.gw_uid_total: dict[str, int] = {}
        self.total_sec = 0  # số data frame mạch thu nhận
        self.total_gw = 0   # số data frame gateway nhận

    # -- API -----------------------------------------------------------------
    def add_second_source(self, evt: LoraRxEvent) -> None:
        """Lưu 1 data frame của mạch thu riêng (COM 2)."""
        if evt.kind != "DATA" or evt.seq is None or evt.seq <= 0:
            return
        if not evt.crc_ok:
            return
        uid = normalize_uid(evt.node_uuid, self.suffix)
        if not uid:
            return
        d = self.sec_seqs.setdefault(uid, {})
        d[evt.seq] = d.get(evt.seq, 0) + 1
        self.sec_uid_total[uid] = self.sec_uid_total.get(uid, 0) + 1
        self.total_sec += 1

    def add_gateway(self, uid: str, seq: int) -> None:
        """Lưu 1 data frame gateway nhận được (uid đã chuẩn hoá 8 byte).

        LƯU Ý: hàm này KHÔNG tự kiểm tra CRC/kind (đầu vào là uid+seq thuần).
        Caller nào có `LoraRxEvent` nên dùng `add_gateway_event()` để được lọc
        sẵn info/CRC lỗi/seq<=0.
        """
        if not uid or seq is None or seq <= 0:
            return
        uid_n = normalize_uid(uid, self.suffix)
        d = self.gw_seqs.setdefault(uid_n, {})
        d[seq] = d.get(seq, 0) + 1
        self.gw_uid_total[uid_n] = self.gw_uid_total.get(uid_n, 0) + 1
        self.total_gw += 1

    def add_gateway_event(self, evt: LoraRxEvent) -> None:
        """Lưu 1 data frame gateway nhận từ `LoraRxEvent` (đã lọc rác).

        Chỉ nhận data frame hợp lệ: kind == DATA (đã bao gồm CRC OK), seq > 0 và
        `crc_ok` — GIỐNG hệt `add_second_source` để hai nguồn lọc rác cùng chuẩn.
        Trước đây UI gọi `add_gateway(uid, seq)` trực tiếp nên frame CRC lỗi
        (seq đọc được nhưng nội dung rác) lọt vào key gateway → sinh `extra` giả.
        """
        if evt.kind != "DATA" or evt.seq is None or evt.seq <= 0:
            return
        if not evt.crc_ok:
            return
        self.add_gateway(evt.node_uuid, evt.seq)

    def compute(self) -> dict:
        """So sánh 2 nguồn, trả về tóm tắt + bảng per-node.

        Đối chiếu theo KEY (uid gốc, seq) — seq DUY NHẤT trên mỗi UID node nên
        matched/missing là SỐ KEY, không phải số lần nhận. Nhờ vậy delivery_rate
        không thể vượt 100% dù gateway nhận bản tin nhiều lần (echo/repeat).

        Return:
            {
              "total_second": int,  # số data frame mạch thu nhận (đếm bản tin)
              "total_gateway": int, # số data frame gateway nhận (đếm bản tin)
              "second_keys": int,   # số key (uid,seq) mạch thu có
              "gateway_keys": int,  # số key (uid,seq) gateway có
              "matched": int,       # số key mạch thu có mặt ở gateway
              "missing": int,       # số key mạch thu có nhưng gateway không
              "extra": int,         # số key CHỈ có ở gateway
              "delivery_rate": float|None,  # matched/second_keys*100
              "per_node": [ {uid, second, gateway, rx_keys, matched, missing,
                             extra, delivery_rate}, ... ]
            }
        """
        matched = 0
        missing = 0
        extra = 0
        for uid, sec_s in self.sec_seqs.items():
            gw_s = self.gw_seqs.get(uid, {})
            for seq in sec_s:
                if gw_s.get(seq, 0) > 0:
                    matched += 1
                else:
                    missing += 1
        for uid, gw_s in self.gw_seqs.items():
            sec_s = self.sec_seqs.get(uid, {})
            for seq in gw_s:
                if sec_s.get(seq, 0) == 0:
                    extra += 1

        second_keys = sum(len(s) for s in self.sec_seqs.values())
        gateway_keys = sum(len(s) for s in self.gw_seqs.values())

        per = []
        for uid in sorted(set(self.sec_seqs) | set(self.gw_seqs)):
            sec_s = self.sec_seqs.get(uid, {})
            gw_s = self.gw_seqs.get(uid, {})
            rx_keys = len(sec_s)
            m = sum(1 for seq in sec_s if gw_s.get(seq, 0) == 0)
            mt = rx_keys - m
            ex = sum(1 for seq in gw_s if sec_s.get(seq, 0) == 0)
            per.append({
                "uid": uid,
                "kind": node_kind_from_id(uid),
                "second": self.sec_uid_total.get(uid, 0),
                "gateway": self.gw_uid_total.get(uid, 0),
                "rx_keys": rx_keys,
                "matched": mt,
                "missing": m,
                "extra": ex,
                "delivery_rate": None if rx_keys == 0 else (mt / rx_keys * 100.0),
            })

        return {
            "total_second": self.total_sec,
            "total_gateway": self.total_gw,
            "second_keys": second_keys,
            "gateway_keys": gateway_keys,
            "matched": matched,
            "missing": missing,
            "extra": extra,
            "delivery_rate": (None if second_keys == 0
                              else matched / second_keys * 100.0),
            "per_node": per,
        }