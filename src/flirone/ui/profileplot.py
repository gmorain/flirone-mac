"""Line-profile plot: temperature along each drawn line."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..measure import Line

_SERIES = [
    QColor(255, 190, 60),
    QColor(120, 200, 255),
    QColor(160, 240, 150),
    QColor(255, 130, 200),
]


class ProfilePlot(QWidget):
    """Draws one polyline per measurement line, sharing a temperature axis."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(130)
        self._lines: list[Line] = []
        self._temps: np.ndarray | None = None

    def set_data(self, lines: list[Line], temps: np.ndarray | None) -> None:
        self._lines = lines
        self._temps = temps
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 24, 27))
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)

        if not self._lines or self._temps is None:
            painter.setPen(QColor(120, 120, 130))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "draw a line on the image to see its profile",
            )
            return

        series = [(line, *line.samples(self._temps)) for line in self._lines]
        values = np.concatenate([v for _, _, v in series])
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            return
        lo, hi = float(finite.min()), float(finite.max())
        if hi - lo < 0.5:
            lo, hi = lo - 0.25, hi + 0.25
        pad_l, pad_r, pad_t, pad_b = 46, 10, 10, 20
        w = max(self.width() - pad_l - pad_r, 1)
        h = max(self.height() - pad_t - pad_b, 1)

        painter.setPen(QPen(QColor(60, 60, 68), 1))
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = pad_t + h * frac
            painter.drawLine(QPointF(pad_l, y), QPointF(pad_l + w, y))
            painter.setPen(QColor(150, 150, 160))
            painter.drawText(QPointF(4, y + 4), f"{hi - (hi - lo) * frac:6.1f}")
            painter.setPen(QPen(QColor(60, 60, 68), 1))

        for i, (_line, dist, vals) in enumerate(series):
            painter.setPen(QPen(_SERIES[i % len(_SERIES)], 1.6))
            span = max(float(dist[-1]), 1e-6)
            points = [
                QPointF(
                    pad_l + float(d) / span * w, pad_t + h * (1.0 - (float(v) - lo) / (hi - lo))
                )
                for d, v in zip(dist, vals, strict=False)
                if np.isfinite(v)
            ]
            if len(points) > 1:
                painter.drawPolyline(points)
            painter.drawText(QPointF(pad_l + 6 + i * 58, pad_t + 12), f"L{i + 1}")

        painter.setPen(QColor(150, 150, 160))
        painter.drawText(QPointF(pad_l, self.height() - 5), "0 px")
        painter.drawText(QPointF(pad_l + w - 40, self.height() - 5), f"{series[0][1][-1]:.0f} px")
