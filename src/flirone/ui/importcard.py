"""Summary card shown after a radiometric image is imported.

Reports what was recovered from the file and what was measured from it, side by
side, so the two can be compared rather than silently merged.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_MUTED = "color: palette(mid);"
_GOOD = "color: #2e7d32;"
_WARN = "color: #b26a00;"


@dataclass
class ImportSummary:
    """Everything the card displays."""

    filename: str
    camera: str
    serial: str
    calibrated: bool
    planck_source: str
    width: int
    height: int
    temp_min: float
    temp_max: float
    recorded_scale: float | None
    recorded_dx: float | None
    recorded_dy: float | None
    measured_scale: float
    measured_dx: float
    measured_dy: float
    match_measured: float
    match_recorded: float | None
    distance: str


class ImportCard(QDialog):
    def __init__(self, summary: ImportSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import summary")
        self.setModal(False)
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        title = QLabel(summary.filename)
        font = title.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        title.setFont(font)
        title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        outer.addWidget(title)

        subtitle = QLabel(f"{summary.camera} · {summary.serial}")
        subtitle.setStyleSheet(_MUTED)
        subtitle.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        outer.addWidget(subtitle)
        outer.addWidget(self._rule())

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(1, 1)
        row = 0

        def section(text: str) -> None:
            nonlocal row
            label = QLabel(text)
            f = label.font()
            f.setBold(True)
            label.setFont(f)
            grid.addWidget(label, row, 0, 1, 2)
            row += 1

        def entry(name: str, value: str, style: str = "") -> None:
            nonlocal row
            key = QLabel(name)
            key.setStyleSheet(_MUTED)
            key.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            val = QLabel(value)
            val.setWordWrap(True)
            if style:
                val.setStyleSheet(style)
            grid.addWidget(key, row, 0)
            grid.addWidget(val, row, 1)
            row += 1

        section("Radiometry")
        entry(
            "calibration",
            "this camera's own constants"
            if summary.calibrated
            else "NOT this camera - relative only",
            _GOOD if summary.calibrated else _WARN,
        )
        entry("source", summary.planck_source)
        entry("thermal", f"{summary.width} × {summary.height} px")
        entry("range", f"{summary.temp_min:.1f} to {summary.temp_max:.1f} °C")

        section("Alignment")
        if summary.recorded_scale is not None:
            entry(
                "scale",
                f"{summary.measured_scale:.4f} measured, {summary.recorded_scale:.4f} recorded",
            )
            dx_gap = abs(summary.measured_dx - (summary.recorded_dx or 0.0))
            dy_gap = abs(summary.measured_dy - (summary.recorded_dy or 0.0))
            entry(
                "boresight dx",
                f"{summary.measured_dx:+.1f} px measured, "
                f"{summary.recorded_dx:+.1f} recorded ({dx_gap:.1f} apart)",
                _GOOD if dx_gap < 2.0 else "",
            )
            entry(
                "parallax dy",
                f"{summary.measured_dy:+.1f} px measured, "
                f"{summary.recorded_dy:+.1f} recorded ({dy_gap:.1f} apart)",
                _WARN if dy_gap >= 2.0 else _GOOD,
            )
        else:
            entry("scale", f"{summary.measured_scale:.4f} measured")
            entry("offset", f"{summary.measured_dx:+.1f} / {summary.measured_dy:+.1f} px measured")

        if summary.match_recorded is not None:
            entry(
                "edge match",
                f"{summary.match_measured:.3f} measured, "
                f"{summary.match_recorded:.3f} using recorded values",
            )
        else:
            entry("edge match", f"{summary.match_measured:.3f}")

        section("Distance")
        uncalibrated = "not calibrated" in summary.distance or "needs " in summary.distance
        entry("estimate", summary.distance, _MUTED if uncalibrated else "")
        outer.addLayout(grid)

        note = QLabel(
            "The camera derives its recorded offsets from the user-set object "
            "distance, so the parallax axis is only as good as that setting. "
            "The measured values come from the image itself."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_MUTED)
        outer.addWidget(self._rule())
        outer.addWidget(note)

        button = QPushButton("OK")
        button.setDefault(True)
        button.clicked.connect(self.accept)
        outer.addWidget(button)

    @staticmethod
    def _rule() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line
