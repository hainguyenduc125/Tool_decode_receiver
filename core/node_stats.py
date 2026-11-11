"""Helpers thuần Python để lọc và thống kê record nhận từ UART."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re
from typing import Literal

from core.lora_frame import ConsoleDecode

SeqParity = Literal["Chẵn", "Lẻ", "Không SEQ"]
SEQ_PARITIES: tuple[SeqParity, ...] = ("Chẵn", "Lẻ", "Không SEQ")
HEARTBEAT_SEQ: int | None = None
SEQ_MODULO = 65536
SEQ_STEP = 2
MAX_FORWARD_GAP = 2000
REORDER_WINDOW = 128

_TIMESTAMP_PREFIX = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*")


def _without_timestamp(line: str) -> str:
    return _TIMESTAMP_PREFIX.sub("", line.strip(), count=1)


def seq_parity(decoded: ConsoleDecode | None) -> SeqParity:
    """Phân luồng theo SEQ; frame thiếu SEQ và heartbeat thuộc nhóm riêng."""
    if decoded is None:
        return "Không SEQ"
    packet = decoded.packet
    seq = packet.seq
    if seq is None or (
        HEARTBEAT_SEQ is not None
        and packet.kind == "data"
        and seq == HEARTBEAT_SEQ
    ):
        return "Không SEQ"
    return "Chẵn" if seq % 2 == 0 else "Lẻ"


def record_uid(decoded: ConsoleDecode) -> str:
    """Lấy UID node từ frame hoặc Receiver UID của envelope L."""
    uid = decoded.packet.node_uuid.strip()
    if uid:
        return uid
    if decoded.packet.layout.startswith("L record"):
        return next(
            (value.strip() for name, value in decoded.packet.fields
             if name == "Receiver UID"),
            "",
        )
    return ""


def record_uid_for_line(line: str, decoded: ConsoleDecode | None) -> str:
    """Resolve a UID from the decode, or from the explicit L/D record header."""
    if decoded is not None:
        uid = record_uid(decoded)
        if uid:
            return uid
    text = _without_timestamp(line)
    if text.startswith(("L:", "D:")):
        candidate = text[2:].split(",", 1)[0].strip()
        if re.fullmatch(r"[0-9A-Fa-f]{24}", candidate):
            return candidate.upper()
    return ""


def uid_matches(uid: str, query: str) -> bool:
    """So khớp UID chính xác, không phân biệt hoa thường; query rỗng khớp tất cả."""
    value = query.strip()
    return not value or uid.strip().casefold() == value.casefold()


def record_matches_filters(
    uid: str,
    parity: SeqParity,
    uid_query: str,
    parities: set[SeqParity] | frozenset[SeqParity],
) -> bool:
    """Bộ lọc UID và SEQ parity dùng chung cho bảng, kết quả và thống kê."""
    return parity in parities and uid_matches(uid, uid_query)


@dataclass
class ReceivedFrame:
    ts: float
    raw_line: str
    parity: SeqParity
    uid: str
    decoded: ConsoleDecode | None
    error: str | None


@dataclass
class ParityStats:
    seq_first: int | None
    seq_last: int | None
    expected: int | None
    received_unique: int
    missing: int | None
    duplicate_seq: int
    resets: int
    missing_seqs: list[int]
    rssi_min: float | None
    rssi_avg: float | None
    rssi_max: float | None
    snr_avg: float | None
    crc_bad: int
    rssi_series: list[tuple[float, float]]


@dataclass
class PairStats:
    paired: int
    even_unpaired: int
    odd_unpaired: int


class SeqTracker:
    """Theo dõi một luồng SEQ tăng dần, kể cả wrap, mất gói và gói đến trễ."""

    def __init__(self) -> None:
        self.seq_first: int | None = None
        self.seq_last: int | None = None
        self.expected = 0
        self.received_unique = 0
        self.missing = 0
        self.duplicate_seq = 0
        self.resets = 0
        self.crc_bad = 0
        self._segment = 0
        self._missing_current: set[int] = set()
        self._recent_missing: deque[tuple[int, int]] = deque(maxlen=200)
        self._rssi_min: float | None = None
        self._rssi_max: float | None = None
        self._rssi_sum = 0.0
        self._rssi_count = 0
        self._snr_sum = 0.0
        self._snr_count = 0
        self.rssi_series: list[tuple[float, float]] = []

    def update(self, seq: int) -> bool:
        """Cập nhật một SEQ; trả True nếu phát hiện node khởi động lại."""
        if not 0 <= seq < SEQ_MODULO:
            raise ValueError(f"SEQ phải nằm trong khoảng 0..{SEQ_MODULO - 1}.")
        if self.seq_last is None:
            self._receive_first(seq)
            return False

        delta = (seq - self.seq_last) % SEQ_MODULO
        if delta == 0:
            self.duplicate_seq += 1
            return False
        if SEQ_STEP <= delta <= MAX_FORWARD_GAP:
            steps = delta // SEQ_STEP
            self.expected += steps
            self.received_unique += 1
            self.missing += steps - 1
            previous = self.seq_last
            for step in range(1, steps):
                missing_seq = (previous + SEQ_STEP * step) % SEQ_MODULO
                self._missing_current.add(missing_seq)
                self._recent_missing.append((self._segment, missing_seq))
            self.seq_last = seq
            return False
        if delta >= SEQ_MODULO - REORDER_WINDOW:
            if seq in self._missing_current:
                self._missing_current.remove(seq)
                self.missing -= 1
                self.received_unique += 1
                return False
            self.duplicate_seq += 1
            return False
        return True

    def reset_segment(self) -> None:
        self.seq_last = None
        self._missing_current.clear()
        self._segment += 1
        self.resets += 1

    def add_signal(self, ts: float, rssi: float | None, snr: float | None) -> None:
        if rssi is not None:
            self._rssi_min = rssi if self._rssi_min is None else min(self._rssi_min, rssi)
            self._rssi_max = rssi if self._rssi_max is None else max(self._rssi_max, rssi)
            self._rssi_sum += rssi
            self._rssi_count += 1
            self.rssi_series.append((ts, rssi))
        if snr is not None:
            self._snr_sum += snr
            self._snr_count += 1

    def stats(self) -> ParityStats:
        missing_seqs = [
            seq for segment, seq in self._recent_missing
            if segment != self._segment or seq in self._missing_current
        ]
        if self.seq_first is None:
            expected: int | None = None
            missing: int | None = None
        else:
            expected = self.expected
            missing = self.missing
        return ParityStats(
            seq_first=self.seq_first,
            seq_last=self.seq_last,
            expected=expected,
            received_unique=self.received_unique,
            missing=missing,
            duplicate_seq=self.duplicate_seq,
            resets=self.resets,
            missing_seqs=missing_seqs,
            rssi_min=self._rssi_min,
            rssi_avg=self._rssi_sum / self._rssi_count if self._rssi_count else None,
            rssi_max=self._rssi_max,
            snr_avg=self._snr_sum / self._snr_count if self._snr_count else None,
            crc_bad=self.crc_bad,
            rssi_series=self.rssi_series,
        )

    def _receive_first(self, seq: int) -> None:
        if self.seq_first is None:
            self.seq_first = seq
        self.seq_last = seq
        self.expected += 1
        self.received_unique += 1


class NodeStatsStore:
    """Lưu record hiển thị và thống kê tăng dần theo UID cùng luồng SEQ."""

    def __init__(self, max_frames: int = 50000) -> None:
        if max_frames < 1:
            raise ValueError("max_frames phải lớn hơn 0.")
        self._frames: deque[ReceivedFrame] = deque(maxlen=max_frames)
        self._trackers: dict[str, dict[SeqParity, SeqTracker]] = {}
        self._pair_times: dict[str, tuple[deque[float], deque[float]]] = {}
        self._pair_revision: dict[str, int] = {}
        self._pair_cache: dict[tuple[str, float, int], PairStats] = {}
        self._max_frames = max_frames

    def add(self, frame: ReceivedFrame) -> None:
        self._frames.append(frame)
        uid_key = frame.uid.strip().casefold()
        if not uid_key:
            return
        trackers = self._trackers.setdefault(
            uid_key, {parity: SeqTracker() for parity in SEQ_PARITIES}
        )
        tracker = trackers[frame.parity]
        decoded = frame.decoded
        if decoded is None or frame.error is not None:
            return

        packet = decoded.packet
        has_crc = packet.crc_calculated is not None and packet.crc_received is not None
        if has_crc and not packet.crc_ok:
            tracker.crc_bad += 1
            return

        tracker.add_signal(frame.ts, decoded.rssi, decoded.snr)
        seq = packet.seq
        if frame.parity == "Không SEQ" or seq is None:
            tracker.received_unique += 1
            return
        if tracker.update(seq):
            for other_tracker in trackers.values():
                other_tracker.reset_segment()
            tracker.update(seq)

        if frame.parity in ("Chẵn", "Lẻ"):
            even_times, odd_times = self._pair_times.setdefault(
                uid_key,
                (deque(maxlen=self._max_frames), deque(maxlen=self._max_frames)),
            )
            (even_times if frame.parity == "Chẵn" else odd_times).append(frame.ts)
            revision = self._pair_revision.get(uid_key, 0) + 1
            self._pair_revision[uid_key] = revision
            self._pair_cache = {
                key: value for key, value in self._pair_cache.items()
                if key[0] != uid_key
            }

    def frames_for(
        self, uid: str, parities: set[SeqParity] | frozenset[SeqParity]
    ) -> list[ReceivedFrame]:
        return [
            frame for frame in self._frames
            if record_matches_filters(frame.uid, frame.parity, uid, parities)
        ]

    def reset(self, uid: str) -> None:
        uid_key = uid.strip().casefold()
        self._trackers.pop(uid_key, None)
        self._pair_times.pop(uid_key, None)
        self._pair_revision.pop(uid_key, None)
        self._pair_cache = {
            key: value for key, value in self._pair_cache.items()
            if key[0] != uid_key
        }

    def compute(
        self,
        uid: str,
        parities: set[SeqParity] | frozenset[SeqParity],
        now: float,
    ) -> dict[SeqParity, ParityStats]:
        del now
        uid_key = uid.strip().casefold()
        trackers = self._trackers.get(uid_key, {})
        return {
            parity: trackers[parity].stats()
            for parity in SEQ_PARITIES
            if record_matches_filters(uid, parity, uid, parities)
            and parity in trackers
        }

    def pair_even_odd(self, uid: str, window_s: float) -> PairStats:
        if window_s < 0:
            raise ValueError("window_s không được âm.")
        uid_key = uid.strip().casefold()
        revision = self._pair_revision.get(uid_key, 0)
        cache_key = (uid_key, window_s, revision)
        cached = self._pair_cache.get(cache_key)
        if cached is not None:
            return cached
        even_times, odd_times = self._pair_times.get(uid_key, (deque(), deque()))
        left_times = sorted(even_times)
        right_times = sorted(odd_times)
        left = right = pairs = 0
        while left < len(left_times) and right < len(right_times):
            if left_times[left] < right_times[right] - window_s:
                left += 1
            elif right_times[right] < left_times[left] - window_s:
                right += 1
            else:
                pairs += 1
                left += 1
                right += 1
        result = PairStats(
            paired=pairs,
            even_unpaired=len(left_times) - pairs,
            odd_unpaired=len(right_times) - pairs,
        )
        self._pair_cache[cache_key] = result
        return result
