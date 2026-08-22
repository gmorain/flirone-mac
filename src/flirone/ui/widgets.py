"""Small composite widgets.

`SliderField` is a drop-in replacement for a spin box: same `value()`,
`setValue()` and `valueChanged`, so call sites do not care which is used.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget


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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._step = step
        self._decimals = decimals
        self._factor = round(1.0 / step)

        self.slider = QSlider(Qt.Orientation.Horizontal)
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
