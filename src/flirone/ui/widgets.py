"""Small composite widgets.

`SliderField` is a drop-in replacement for a spin box: same `value()`,
`setValue()` and `valueChanged`, so call sites do not care which is used.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QWidget,
)


class _StepSlider(QSlider):
    """A slider whose groove clicks nudge by one step instead of jumping.

    The macOS style jumps straight to the clicked position, which is useful for
    a volume control and wrong for an alignment offset: a stray click throws
    away a careful setting. Clicking beside the handle moves one step toward the
    click; dragging the handle is untouched.
    """

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            option.initFrom(self)
            option.minimum = self.minimum()
            option.maximum = self.maximum()
            option.sliderPosition = self.value()
            option.sliderValue = self.value()
            option.orientation = self.orientation()
            option.pageStep = self.pageStep()
            handle = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider,
                option,
                QStyle.SubControl.SC_SliderHandle,
                self,
            )
            position = event.position().toPoint()
            if not handle.contains(position):
                forward = position.x() > handle.center().x()
                self.setValue(self.value() + (1 if forward else -1) * self.singleStep())
                event.accept()
                return
        super().mousePressEvent(event)


class SliderField(QWidget):
    """A slider with its value beside it, working in real units.

    Sliders are integer-valued, so the real range is scaled internally and
    converted back on the way out. Dragging is for exploring; the arrow keys
    still move exactly one step, which is what fine alignment needs.
    """

    valueChanged = Signal(float)

    def __init__(
        self,
        minimum: float,
        maximum: float,
        step: float = 1.0,
        decimals: int = 0,
        step_on_click: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._step = step
        self._decimals = decimals
        self._factor = round(1.0 / step)

        slider_type = _StepSlider if step_on_click else QSlider
        self.slider = slider_type(Qt.Orientation.Horizontal)
        self.slider.setRange(round(minimum * self._factor), round(maximum * self._factor))
        self.slider.setSingleStep(1)
        self.slider.setPageStep(max(1, self._factor))
        self.slider.valueChanged.connect(self._on_slider)

        self.readout = QLabel()
        self.readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Fixed width, so the layout does not shift as digits come and go.
        widest = max(self._format(minimum), self._format(maximum), key=len)
        self.readout.setMinimumWidth(self.readout.fontMetrics().horizontalAdvance(widest) + 8)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.readout)
        self._refresh()

    def _format(self, value: float) -> str:
        # Offsets read better signed; scales and fractions do not.
        if self._decimals == 0:
            return f"{value:+.0f}"
        return f"{value:.{self._decimals}f}"

    def _refresh(self) -> None:
        self.readout.setText(self._format(self.value()))

    def _on_slider(self, _raw: int) -> None:
        self._refresh()
        self.valueChanged.emit(self.value())

    def value(self) -> float:
        return self.slider.value() / self._factor

    def setValue(self, value: float) -> None:
        self.slider.setValue(round(float(value) * self._factor))
        self._refresh()

    def setRange(self, minimum: float, maximum: float) -> None:
        self.slider.setRange(round(minimum * self._factor), round(maximum * self._factor))
