"""Interactive thermal canvas: renders a frame and the measurements on it."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from ..measure import Box, Line, MeasurementSet, Spot, coldspot, hotspot

TOOL_NONE = "none"
TOOL_SPOT = "spot"
TOOL_BOX = "box"
TOOL_LINE = "line"

# Overlay text sits on a photo, often at retina density, so it needs to be
# larger than ordinary UI text to stay readable.
LABEL_POINT_SIZE = 13
# Markers are sized from the label text so the two stay in proportion when
# either is adjusted: a 6px cross next to 13pt text reads as an afterthought.
MARKER_ARM = round(LABEL_POINT_SIZE * 0.72)
MARKER_STROKE = 2.0
MARKER_CASING = MARKER_STROKE + 1.9


@lru_cache(maxsize=1)
def _label_metrics() -> QFontMetrics:
    """Metrics for the overlay label font, for laying markers out around it."""
    font = QFont()
    font.setPointSize(LABEL_POINT_SIZE)
    return QFontMetrics(font)


_ACCENT = QColor(255, 255, 255)
_SHADOW = QColor(0, 0, 0, 170)
_HOT = QColor(255, 80, 60)
_COLD = QColor(90, 170, 255)


def to_qimage(rgb: np.ndarray) -> QImage:
    """Wrap an (h, w, 3) uint8 array as a QImage, copying so it outlives the array."""
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class ThermalView(QWidget):
    """Displays the rendered frame and lets the user draw measurements on it."""

    measurements_changed = Signal()
    cursor_moved = Signal(int, int)  # sensor coordinates

    def __init__(self, measurements: MeasurementSet, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.measurements = measurements
        self.tool = TOOL_NONE
        self._pixmap: QPixmap | None = None
        self._temps: np.ndarray | None = None
        self._sensor_size = (160, 120)
        self._drag_start: tuple[int, int] | None = None
        self._drag_now: tuple[int, int] | None = None
        self._dragging_spot: Spot | None = None
        self._drag_active = False
        self.placeholder = "waiting for frames"
        self._highlight: tuple[int, int] | None = None

    # -- data ---------------------------------------------------------------

    def set_frame(self, rgb: np.ndarray, temps: np.ndarray) -> None:
        self._sensor_size = (temps.shape[1], temps.shape[0])
        self._pixmap = QPixmap.fromImage(to_qimage(rgb))
        self._temps = temps
        self.update()

    def set_highlight(self, point: tuple[int, int] | None) -> None:
        """Mark a sensor pixel, used to show where a hovered profile sample sits."""
        if self._highlight != point:
            self._highlight = point
            self.update()

    def set_drag_active(self, active: bool) -> None:
        """Highlight the canvas while a file is being dragged over the window."""
        if self._drag_active != active:
            self._drag_active = active
            self.update()

    # -- coordinate mapping -------------------------------------------------

    def _target_rect(self) -> QRect:
        """Where the sensor image is drawn, letterboxed to preserve aspect."""
        w, h = self._sensor_size
        if w == 0 or h == 0:
            return QRect(0, 0, 1, 1)
        scale = min(self.width() / w, self.height() / h)
        tw, th = int(w * scale), int(h * scale)
        return QRect((self.width() - tw) // 2, (self.height() - th) // 2, tw, th)

    def _to_sensor(self, pos: QPointF | QPoint) -> tuple[int, int]:
        rect = self._target_rect()
        w, h = self._sensor_size
        x = (pos.x() - rect.x()) / max(rect.width(), 1) * w
        y = (pos.y() - rect.y()) / max(rect.height(), 1) * h
        return int(np.clip(x, 0, w - 1)), int(np.clip(y, 0, h - 1))

    def _to_widget(self, x: float, y: float) -> QPointF:
        rect = self._target_rect()
        w, h = self._sensor_size
        return QPointF(
            rect.x() + (x + 0.5) / w * rect.width(),
            rect.y() + (y + 0.5) / h * rect.height(),
        )

    # -- interaction --------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        x, y = self._to_sensor(event.position())
        if event.button() == Qt.MouseButton.RightButton:
            self._remove_near(x, y)
            return
        if self.tool == TOOL_SPOT:
            self.measurements.spots.append(Spot(x, y))
            self.measurements_changed.emit()
            self.update()
            return
        if self.tool in (TOOL_BOX, TOOL_LINE):
            self._drag_start = (x, y)
            self._drag_now = (x, y)
            return
        # No active tool: grab the nearest spot to move it.
        self._dragging_spot = self._nearest_spot(x, y)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x, y = self._to_sensor(event.position())
        self.cursor_moved.emit(x, y)
        if self._drag_start is not None:
            self._drag_now = (x, y)
            self.update()
        elif self._dragging_spot is not None:
            self._dragging_spot.x, self._dragging_spot.y = x, y
            self.measurements_changed.emit()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None and self._drag_now is not None:
            (x0, y0), (x1, y1) = self._drag_start, self._drag_now
            if abs(x1 - x0) > 1 or abs(y1 - y0) > 1:
                if self.tool == TOOL_BOX:
                    self.measurements.boxes.append(Box(x0, y0, x1 + 1, y1 + 1))
                elif self.tool == TOOL_LINE:
                    self.measurements.lines.append(Line(x0, y0, x1, y1))
                self.measurements_changed.emit()
        self._drag_start = self._drag_now = None
        self._dragging_spot = None
        self.update()

    def _nearest_spot(self, x: int, y: int, limit: int = 8) -> Spot | None:
        best, best_d = None, limit
        for spot in self.measurements.spots:
            d = float(np.hypot(spot.x - x, spot.y - y))
            if d < best_d:
                best, best_d = spot, d
        return best

    def _remove_near(self, x: int, y: int, limit: int = 8) -> None:
        spot = self._nearest_spot(x, y, limit)
        if spot is not None:
            self.measurements.spots.remove(spot)
            self.measurements_changed.emit()
            self.update()
            return
        for box in list(self.measurements.boxes):
            bx0, by0, bx1, by1 = box.normalised((self._sensor_size[1], self._sensor_size[0]))
            if bx0 <= x < bx1 and by0 <= y < by1:
                self.measurements.boxes.remove(box)
                self.measurements_changed.emit()
                self.update()
                return
        for line in list(self.measurements.lines):
            if (
                min(abs(line.x0 - x), abs(line.x1 - x)) < limit
                and min(abs(line.y0 - y), abs(line.y1 - y)) < limit
            ):
                self.measurements.lines.remove(line)
                self.measurements_changed.emit()
                self.update()
                return

    # -- painting -----------------------------------------------------------

    def paintEvent(self, _event) -> None:
        # Explicit begin/end: early returns would otherwise leave the painter
        # attached to the widget until the local fell out of scope.
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor(18, 18, 20))
        if self._pixmap is None:
            painter.setPen(QColor(140, 140, 150))
            font = painter.font()
            font.setPointSize(15)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.placeholder)
            if self._drag_active:
                self._draw_drop_hint(painter)
            return

        rect = self._target_rect()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawPixmap(rect, self._pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        temps = self._temps
        if temps is None:
            return

        font = QFont()
        font.setPointSize(LABEL_POINT_SIZE)
        painter.setFont(font)

        for i, spot in enumerate(self.measurements.spots, 1):
            value = spot.measure(temps).mean
            self._draw_spot(
                painter, spot.x, spot.y, f"{value:.1f}°", _ACCENT, spot.label or f"S{i}"
            )

        for i, box in enumerate(self.measurements.boxes, 1):
            x0, y0, x1, y1 = box.normalised(temps.shape)
            tl, br = self._to_widget(x0, y0), self._to_widget(x1 - 1, y1 - 1)
            rect = QRect(tl.toPoint(), br.toPoint())
            painter.setPen(QPen(QColor(0, 0, 0, 170), 3.0))
            painter.drawRect(rect)
            painter.setPen(QPen(_ACCENT, 1.5))
            painter.drawRect(rect)
            s = box.measure(temps)
            self._label(
                painter,
                tl.x() + 3,
                tl.y() - 5,
                f"{box.label or f'B{i}'}  {s.minimum:.1f}/{s.mean:.1f}/{s.maximum:.1f}°",
            )

        for i, line in enumerate(self.measurements.lines, 1):
            a, b = self._to_widget(line.x0, line.y0), self._to_widget(line.x1, line.y1)
            painter.setPen(QPen(QColor(0, 0, 0, 170), 3.0))
            painter.drawLine(a, b)
            painter.setPen(QPen(_ACCENT, 1.5))
            painter.drawLine(a, b)
            self._label(painter, a.x() + 4, a.y() - 5, line.label or f"L{i}")

        if self.measurements.track_hotspot:
            x, y, v = hotspot(temps)
            self._draw_spot(painter, x, y, f"{v:.1f}°", _HOT, "max", below=True)
        if self.measurements.track_coldspot:
            x, y, v = coldspot(temps)
            self._draw_spot(painter, x, y, f"{v:.1f}°", _COLD, "min", below=True)

        if self._highlight is not None:
            self._draw_highlight(painter)

        if self._drag_active:
            self._draw_drop_hint(painter)

        if self._drag_start and self._drag_now:
            (x0, y0), (x1, y1) = self._drag_start, self._drag_now
            a, b = self._to_widget(x0, y0), self._to_widget(x1, y1)
            painter.setPen(QPen(QColor(255, 255, 255, 180), 1, Qt.PenStyle.DashLine))
            if self.tool == TOOL_BOX:
                painter.drawRect(QRect(a.toPoint(), b.toPoint()))
            else:
                painter.drawLine(a, b)

    def _draw_highlight(self, painter: QPainter) -> None:
        """Ring marking the pixel under the cursor in the profile plot."""
        x, y = self._highlight
        p = self._to_widget(x, y)
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        radius = MARKER_ARM * 0.78
        painter.setPen(QPen(QColor(0, 0, 0, 200), MARKER_CASING))
        painter.drawEllipse(p, radius, radius)
        painter.setPen(QPen(QColor(120, 230, 255), MARKER_STROKE))
        painter.drawEllipse(p, radius, radius)
        painter.drawLine(QPointF(p.x() - radius - 4, p.y()), QPointF(p.x() - radius + 3, p.y()))
        painter.drawLine(QPointF(p.x() + radius - 3, p.y()), QPointF(p.x() + radius + 4, p.y()))
        painter.restore()

    def _draw_drop_hint(self, painter: QPainter) -> None:
        """Overlay shown while a file is dragged over the window."""
        painter.save()
        rect = self.rect().adjusted(6, 6, -7, -7)
        painter.fillRect(self.rect(), QColor(30, 90, 170, 70))
        painter.setPen(QPen(QColor(120, 190, 255), 2, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(rect, 8, 8)
        font = painter.font()
        font.setPointSize(14)
        painter.setFont(font)
        painter.setPen(QColor(230, 240, 255))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "Drop a radiometric image, calibration or capture folder",
        )
        painter.restore()

    def _draw_spot(
        self,
        painter: QPainter,
        x: int,
        y: int,
        text: str,
        colour: QColor,
        tag: str,
        below: bool = False,
    ) -> None:
        p = self._to_widget(x, y)
        arm = MARKER_ARM
        horizontal = (QPointF(p.x() - arm, p.y()), QPointF(p.x() + arm, p.y()))
        vertical = (QPointF(p.x(), p.y() - arm), QPointF(p.x(), p.y() + arm))
        # Dark casing first, so the marker survives a saturated background.
        painter.setPen(QPen(QColor(0, 0, 0, 190), MARKER_CASING))
        painter.drawLine(*horizontal)
        painter.drawLine(*vertical)
        painter.setPen(QPen(colour, MARKER_STROKE))
        painter.drawLine(*horizontal)
        painter.drawLine(*vertical)
        # Tracked extremes label a full label-height downwards, so they clear the
        # label of a user spot sitting on the same hot feature. A fixed gap
        # stopped working once the text grew.
        offset = arm + _label_metrics().height() if below else -(arm - 3)
        self._label(painter, p.x() + arm + 3, p.y() + offset, f"{tag} {text}", colour)

    def _label(
        self, painter: QPainter, x: float, y: float, text: str, colour: QColor = _ACCENT
    ) -> None:
        """Draw a label on an opaque badge.

        Overlay text cannot rely on the image for contrast: a red marker on a
        saturated hot region is unreadable whatever the drop shadow. The badge
        makes legibility independent of what is underneath.
        """
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text)
        height = metrics.height()
        pad_x, pad_y = 5, 2

        # Keep the whole badge inside the widget.
        x = min(max(x, 2.0), max(self.width() - width - pad_x * 2 - 2.0, 2.0))
        y = min(max(y, height), self.height() - 3.0)

        badge = QRectF(
            x - pad_x,
            y - metrics.ascent() - pad_y,
            width + pad_x * 2,
            height + pad_y * 2,
        )
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 165))
        painter.drawRoundedRect(badge, 3, 3)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(colour)
        painter.drawText(QPointF(x, y), text)
        painter.restore()
