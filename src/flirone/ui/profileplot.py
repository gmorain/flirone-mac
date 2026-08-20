"""Line-profile plot: temperature along each drawn line.

Hovering reports the sample under the cursor and emits its sensor coordinates,
so a point on the curve can be tied back to a pixel in the image and to a row in
an exported profile CSV.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..measure import Line

_SERIES = [
    QColor(255, 190, 60),
    QColor(120, 200, 255),
    QColor(160, 240, 150),
    QColor(255, 130, 200),
]
PLOT_POINT_SIZE = 12


@dataclass
class _Series:
    line: Line
    distance: np.ndarray
    xs: np.ndarray
    ys: np.ndarray
    values: np.ndarray


@dataclass
class _Layout:
    """Plot geometry, shared by painting and hit-testing so they cannot drift.

    The paddings derive from the font metrics rather than fixed constants, so
    raising PLOT_POINT_SIZE cannot make the axis labels collide with the plot or
    with the distance row beneath it.
    """

    series: list[_Series]
    lo: float
    hi: float
    width: int
    height: int
    pad_l: int
    pad_t: int

    def x_of(self, series: _Series, index: int) -> float:
        span = max(float(series.distance[-1]), 1e-6)
        return self.pad_l + float(series.distance[index]) / span * self.width

    def y_of(self, value: float) -> float:
        return self.pad_t + self.height * (1.0 - (float(value) - self.lo) / (self.hi - self.lo))


class ProfilePlot(QWidget):
    """Draws one polyline per measurement line, sharing a temperature axis."""

    # line index, x_px, y_px, temperature, sample index
    sample_hovered = Signal(int, int, int, float, int)
    hover_cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(130)
        self.setMouseTracking(True)
        self._lines: list[Line] = []
        self._temps: np.ndarray | None = None
        self._hover: tuple[int, int] | None = None  # (series index, sample index)

    def set_data(self, lines: list[Line], temps: np.ndarray | None) -> None:
        self._lines = lines
        self._temps = temps
        if not lines or temps is None:
            self._hover = None
        self.update()

    # -- geometry -----------------------------------------------------------

    def _layout(self) -> _Layout | None:
        if not self._lines or self._temps is None:
            return None
        series = []
        for line in self._lines:
            distance, xs, ys, values = line.sample_points(self._temps)
            series.append(_Series(line, distance, xs, ys, values))
        values = np.concatenate([s.values for s in series])
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            return None
        lo, hi = float(finite.min()), float(finite.max())
        if hi - lo < 0.5:
            lo, hi = lo - 0.25, hi + 0.25
        font = QFont()
        font.setPointSize(PLOT_POINT_SIZE)
        metrics = QFontMetrics(font)
        # Gutter wide enough for the widest axis label, plus a little air.
        pad_l = metrics.horizontalAdvance(f"{max(abs(lo), abs(hi)):.1f}") + 12
        pad_r = metrics.horizontalAdvance("0000 px") // 2 + 10
        pad_t = metrics.height() // 2 + 4
        pad_b = metrics.height() + 10
        return _Layout(
            series=series,
            lo=lo,
            hi=hi,
            width=max(self.width() - pad_l - pad_r, 1),
            height=max(self.height() - pad_t - pad_b, 1),
            pad_l=pad_l,
            pad_t=pad_t,
        )

    # -- hover --------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        layout = self._layout()
        if layout is None:
            return
        pos = event.position()
        best = None
        for si, series in enumerate(layout.series):
            span = max(float(series.distance[-1]), 1e-6)
            frac = (pos.x() - layout.pad_l) / layout.width
            target = np.clip(frac, 0.0, 1.0) * span
            index = int(np.argmin(np.abs(series.distance - target)))
            value = series.values[index]
            if not np.isfinite(value):
                continue
            # Choose the curve whose plotted point is nearest the cursor.
            dy = abs(layout.y_of(value) - pos.y())
            if best is None or dy < best[0]:
                best = (dy, si, index)
        if best is None:
            return
        _dy, si, index = best
        if self._hover != (si, index):
            self._hover = (si, index)
            series = layout.series[si]
            self.sample_hovered.emit(
                si,
                int(series.xs[index]),
                int(series.ys[index]),
                float(series.values[index]),
                index,
            )
            self.update()

    def leaveEvent(self, _event) -> None:
        if self._hover is not None:
            self._hover = None
            self.hover_cleared.emit()
            self.update()

    # -- painting -----------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor(24, 24, 27))
        font = QFont()
        font.setPointSize(PLOT_POINT_SIZE)
        painter.setFont(font)

        layout = self._layout()
        if layout is None:
            painter.setPen(QColor(120, 120, 130))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "draw a line on the image to see its profile",
            )
            return

        w, h, lo, hi = layout.width, layout.height, layout.lo, layout.hi
        pad_l, pad_t = layout.pad_l, layout.pad_t
        metrics = painter.fontMetrics()

        painter.setPen(QPen(QColor(60, 60, 68), 1))
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = pad_t + h * frac
            painter.drawLine(QPointF(pad_l, y), QPointF(pad_l + w, y))
            painter.setPen(QColor(150, 150, 160))
            text = f"{hi - (hi - lo) * frac:.1f}"
            painter.drawText(
                QPointF(pad_l - metrics.horizontalAdvance(text) - 6, y + metrics.ascent() / 2 - 1),
                text,
            )
            painter.setPen(QPen(QColor(60, 60, 68), 1))

        for i, series in enumerate(layout.series):
            painter.setPen(QPen(_SERIES[i % len(_SERIES)], 1.6))
            points = [
                QPointF(layout.x_of(series, j), layout.y_of(v))
                for j, v in enumerate(series.values)
                if np.isfinite(v)
            ]
            if len(points) > 1:
                painter.drawPolyline(points)
            painter.drawText(
                QPointF(
                    pad_l + 8 + i * (metrics.horizontalAdvance("L00") + 18),
                    pad_t + metrics.height(),
                ),
                f"L{i + 1}",
            )

        painter.setPen(QColor(150, 150, 160))
        baseline = self.height() - metrics.descent() - 3
        painter.drawText(QPointF(pad_l, baseline), "0 px")
        far = f"{layout.series[0].distance[-1]:.0f} px"
        painter.drawText(QPointF(pad_l + w - metrics.horizontalAdvance(far), baseline), far)

        if self._hover is not None:
            self._draw_hover(painter, layout)

    def _draw_hover(self, painter: QPainter, layout: _Layout) -> None:
        si, index = self._hover
        if si >= len(layout.series):
            return
        series = layout.series[si]
        if index >= len(series.values) or not np.isfinite(series.values[index]):
            return
        x = layout.x_of(series, index)
        y = layout.y_of(series.values[index])

        painter.setPen(QPen(QColor(210, 210, 220, 130), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, layout.pad_t), QPointF(x, layout.pad_t + layout.height))

        colour = _SERIES[si % len(_SERIES)]
        painter.setPen(QPen(QColor(20, 20, 24), 2))
        painter.setBrush(colour)
        painter.drawEllipse(QPointF(x, y), 3.6, 3.6)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        text = (
            f"#{index}  ({int(series.xs[index])}, {int(series.ys[index])})  "
            f"{series.values[index]:.1f} °C"
        )
        metrics = painter.fontMetrics()
        tw = metrics.horizontalAdvance(text)
        tx = min(max(x + 8, layout.pad_l), self.width() - tw - 6)
        ty = max(y - 8, layout.pad_t + metrics.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 175))
        painter.drawRoundedRect(
            tx - 4, ty - metrics.ascent() - 2, tw + 8, metrics.height() + 4, 3, 3
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QColor(235, 240, 250))
        painter.drawText(QPointF(tx, ty), text)
