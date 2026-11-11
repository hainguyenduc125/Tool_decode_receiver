"""Panel thống kê một node; pyqtgraph là tuỳ chọn để app vẫn chạy khi thiếu."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.node_stats import ParityStats, PairStats, SeqParity

try:
    import pyqtgraph as pg
except ImportError:
    pg = None


_SERIES_COLORS = {"Chẵn": "#3b82f6", "Lẻ": "#22a447"}
_TABLE_HEADERS = [
    "Luồng", "Đã nhận", "Tổng (theo SEQ)", "Mất", "Tỉ lệ", "Khoảng SEQ",
    "SEQ trùng", "Reset", "RSSI min/TB/max", "SNR TB", "CRC sai",
]


def _value(value: float | int | None, suffix: str = "") -> str:
    return "—" if value is None else f"{value:g}{suffix}"


def _seq_range(stats: ParityStats) -> str:
    if stats.seq_first is None or stats.seq_last is None:
        return "—"
    return f"{stats.seq_first} → {stats.seq_last}"


def _missing_ranges(sequences: list[int]) -> str:
    ordered = sorted(set(sequences))
    if not ordered:
        return "không có SEQ thiếu"
    ranges: list[str] = []
    start = previous = ordered[0]
    for seq in ordered[1:]:
        if seq == previous + 2:
            previous = seq
            continue
        ranges.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = seq
    ranges.append(str(start) if start == previous else f"{start}–{previous}")
    return ", ".join(ranges)


class NodeStatsPanel(QWidget):
    resetBaselineRequested = pyqtSignal()
    pairWindowChanged = pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.title = QLabel("Thống kê node")
        layout.addWidget(self.title)

        controls = QHBoxLayout()
        self.reset_button = QPushButton("Đặt lại mốc thống kê")
        controls.addWidget(self.reset_button)
        controls.addWidget(QLabel("Cửa sổ ghép Chẵn–Lẻ (giây):"))
        self.pair_window_spin = QDoubleSpinBox()
        self.pair_window_spin.setRange(0.0, 600.0)
        self.pair_window_spin.setSingleStep(0.5)
        self.pair_window_spin.setDecimals(1)
        self.pair_window_spin.setValue(2.0)
        controls.addWidget(self.pair_window_spin)
        controls.addStretch(1)
        layout.addLayout(controls)

        charts = QHBoxLayout()
        charts_widget = QWidget()
        charts_widget.setMaximumHeight(135)
        self.chart_error = QLabel()
        if pg is None:
            self.chart_error.setText("Thiếu pyqtgraph; biểu đồ không khả dụng.")
            charts.addWidget(self.chart_error, 1)
            self.line_plot = None
            self.hist_plot = None
            self.hist_kind = None
        else:
            self.chart_error.hide()
            self.line_plot = pg.PlotWidget(
                axisItems={"bottom": pg.DateAxisItem(orientation="bottom")}
            )
            self.line_plot.setLabel("left", "RSSI", units="dBm")
            self.line_plot.setLabel("bottom", "Thời gian")
            self.line_plot.addLegend()
            charts.addWidget(self.line_plot, 2)

            histogram_panel = QWidget()
            histogram_layout = QVBoxLayout(histogram_panel)
            histogram_layout.setContentsMargins(0, 0, 0, 0)
            histogram_controls = QHBoxLayout()
            histogram_controls.addWidget(QLabel("Phân bố RSSI:"))
            self.hist_kind = QComboBox()
            self.hist_kind.addItem("Các luồng đang chọn", "selected")
            self.hist_kind.addItem("Chẵn", "Chẵn")
            self.hist_kind.addItem("Lẻ", "Lẻ")
            histogram_controls.addWidget(self.hist_kind)
            histogram_layout.addLayout(histogram_controls)
            self.hist_plot = pg.PlotWidget()
            self.hist_plot.setLabel("left", "Số bản tin")
            self.hist_plot.setLabel("bottom", "RSSI", units="dBm")
            histogram_layout.addWidget(self.hist_plot)
            charts.addWidget(histogram_panel, 1)
        charts_widget.setLayout(charts)
        layout.addWidget(charts_widget)

        self.table = QTableWidget(0, len(_TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(_TABLE_HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(21)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMaximumHeight(125)
        layout.addWidget(self.table)
        self.pair_summary = QLabel("Ghép cặp Chẵn–Lẻ: —")
        layout.addWidget(self.pair_summary)

        self.reset_button.clicked.connect(self.resetBaselineRequested)
        self.pair_window_spin.valueChanged.connect(self.pairWindowChanged)
        if self.hist_kind is not None:
            self.hist_kind.currentIndexChanged.connect(self._refresh_histogram)
        self._stats: dict[SeqParity, ParityStats] = {}
        self._selected_parities: set[SeqParity] = set()
        self.setMaximumHeight(330)

    def set_uid(self, uid: str) -> None:
        self.title.setText(f"Thống kê node — {uid}")

    def refresh(
        self,
        stats: dict[SeqParity, ParityStats],
        pair_stats: PairStats | None,
        selected_parities: set[SeqParity],
    ) -> None:
        self._stats = stats
        self._selected_parities = set(selected_parities)
        rows: list[tuple[str, ParityStats]] = [
            (parity, stats[parity])
            for parity in ("Chẵn", "Lẻ", "Không SEQ")
            if parity in selected_parities and parity in stats
        ]
        seq_stats = [
            stats[parity] for parity in ("Chẵn", "Lẻ")
            if parity in selected_parities and parity in stats
        ]
        total_received = sum(item.received_unique for item in seq_stats)
        total_expected = sum(item.expected or 0 for item in seq_stats)
        total_missing = sum(item.missing or 0 for item in seq_stats)

        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows) + 1)
            for row, (parity, item) in enumerate(rows):
                is_sequenced = parity != "Không SEQ"
                rssi_summary = (
                    "—" if item.rssi_min is None
                    else " / ".join(
                        f"{value:g}" for value in
                        (item.rssi_min, item.rssi_avg, item.rssi_max)
                    )
                )
                ratio = (
                    f"{item.received_unique / item.expected * 100:.2f}%"
                    if item.expected else "—"
                )
                values = [
                    parity,
                    item.received_unique,
                    item.expected if is_sequenced else "—",
                    item.missing if is_sequenced else "—",
                    ratio if is_sequenced else "—",
                    _seq_range(item) if is_sequenced else "—",
                    item.duplicate_seq,
                    item.resets,
                    rssi_summary,
                    _value(item.snr_avg),
                    item.crc_bad,
                ]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(str(value))
                    if column == 3 and is_sequenced:
                        cell.setToolTip(
                            f"Xem SEQ bị mất: {_missing_ranges(item.missing_seqs)}"
                        )
                    self.table.setItem(row, column, cell)

            total_row = len(rows)
            total_ratio = (
                f"{total_received / total_expected * 100:.2f}%"
                if total_expected else "—"
            )
            total_values = [
                "Tổng",
                total_received,
                total_expected,
                total_missing,
                total_ratio,
                "—",
                sum(item.duplicate_seq for item in seq_stats),
                max((item.resets for item in seq_stats), default=0),
                "—",
                "—",
                sum(item.crc_bad for item in seq_stats),
            ]
            for column, value in enumerate(total_values):
                self.table.setItem(
                    total_row, column, QTableWidgetItem(str(value))
                )
        finally:
            self.table.setUpdatesEnabled(True)

        if pair_stats is None:
            self.pair_summary.setText(
                "Ghép cặp Chẵn–Lẻ: chọn cả Chẵn và Lẻ để xem thống kê."
            )
        else:
            self.pair_summary.setText(
                f"Ghép cặp Chẵn–Lẻ: {pair_stats.paired} cặp | "
                f"Chẵn lẻ loi: {pair_stats.even_unpaired} | "
                f"Lẻ lẻ loi: {pair_stats.odd_unpaired}"
            )
        self._refresh_charts()

    def _refresh_charts(self) -> None:
        if pg is None or self.line_plot is None or self.hist_plot is None:
            return
        self.line_plot.clear()
        self.line_plot.addLegend()
        for parity in ("Chẵn", "Lẻ"):
            if parity not in self._selected_parities:
                continue
            stats = self._stats.get(parity)
            color = _SERIES_COLORS[parity]
            if stats is None or not stats.rssi_series:
                self.line_plot.plot(
                    [], [], pen=pg.mkPen(color), name=f"{parity}: không có RSSI"
                )
                continue
            self.line_plot.plot(
                [ts for ts, _ in stats.rssi_series],
                [rssi for _, rssi in stats.rssi_series],
                pen=pg.mkPen(color, width=1.5),
                symbol="o",
                symbolSize=5,
                symbolBrush=color,
                name=parity,
            )
        self._refresh_histogram()

    def _refresh_histogram(self) -> None:
        if pg is None or self.hist_plot is None or self.hist_kind is None:
            return
        self.hist_plot.clear()
        selected = self.hist_kind.currentData()
        parities = (
            self._selected_parities
            if selected == "selected"
            else ({selected} & self._selected_parities)
        )
        values = [
            rssi for parity in parities if parity in self._stats
            for _, rssi in self._stats[parity].rssi_series
        ]
        if not values:
            return
        lower = int(min(values) // 5) * 5
        upper = int(max(values) // 5) * 5 + 5
        bins = list(range(lower, upper + 1, 5))
        counts = [0] * (len(bins) - 1)
        for value in values:
            index = min(int((value - lower) // 5), len(counts) - 1)
            counts[index] += 1
        centers = [(left + right) / 2 for left, right in zip(bins, bins[1:])]
        self.hist_plot.addItem(
            pg.BarGraphItem(
                x=centers,
                height=counts,
                width=5,
                brush="#60a5fa",
            )
        )
