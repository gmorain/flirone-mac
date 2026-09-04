"""FLIR One viewer and measurement tool for macOS."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import flirjpeg, registration, timelapse
from ..calibration import DEFAULT_PLANCK, Conditions, Planck, Trust, raw_to_celsius
from ..calibration_store import CalibrationStore, adopt_from_image
from ..compose import MODE_EDGES, MODES, Alignment, compose
from ..decode import DecodedFrame
from ..export import profile_csv, save_capture, save_profiles
from ..measure import Delta, MeasurementSet, spot_quality
from ..palettes import DEFAULT_PALETTE, PALETTES
from ..paths import short as short_path
from ..planck_import import load as load_planck
from ..sources import ReplayFrameSource, StillFrameSource, SyntheticFrameSource, UsbFrameSource
from .imageview import TOOL_BOX, TOOL_LINE, TOOL_NONE, TOOL_SPOT, ThermalView
from .importcard import ImportCard, ImportSummary
from .profileplot import ProfilePlot
from .widgets import SliderField

SOURCE_USB = "Camera (USB)"
SOURCE_SYNTHETIC = "Synthetic scene"
SOURCE_REPLAY = "Replay folder..."
PANEL_MIN_WIDTH = 300
# Width reserved for the image and profile, independent of the control panel.
IMAGE_AREA_WIDTH = 980
SOURCE_STILL = "Radiometric image"


# Written once in portable form. Qt maps Ctrl to Command on macOS, so both the
# binding and any label naming it have to be rendered per platform rather than
# spelled out for one of them.
SHORTCUT_CAPTURE = "Ctrl+S"
SHORTCUT_EXPORT_PROFILE = "Ctrl+E"
SHORTCUT_OPEN_IMAGE = "Ctrl+O"


def shortcut_label(sequence: str) -> str:
    """How this platform writes a shortcut: "Ctrl+S" on Linux, "\u2318S" on macOS."""
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FLIR One")

        self.measurements = MeasurementSet()
        self.planck: Planck = DEFAULT_PLANCK
        self.conditions = Conditions()
        self.alignment = Alignment()
        self.source = None
        self.frame: DecodedFrame | None = None
        self.temps: np.ndarray | None = None
        self.output_dir = Path.home() / "Pictures" / "FLIR One"
        self.timelapse_dir = self.output_dir / "timelapse"
        self.recorder: timelapse.Recorder | None = None
        self._import_image = None
        self._import_path = Path()
        # Serial of the camera that produced the data on screen, when known.
        # Constants adopted from elsewhere are checked against it.
        self.current_serial: str | None = None
        self.calibrations = CalibrationStore()
        # Serial of the live camera we have already offered stored constants
        # for, so a match is adopted once per connection rather than per frame.
        self._store_checked_for: str | None = None
        self._locked_range: tuple[float, float] | None = None
        self._frame_times: list[float] = []

        self.view = ThermalView(self.measurements)
        self.view.measurements_changed.connect(self._refresh_readout)
        self.view.cursor_moved.connect(self._on_cursor)

        self.profile = ProfilePlot()
        self.profile.sample_hovered.connect(self._on_profile_hover)
        self.profile.hover_cleared.connect(self._on_profile_leave)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.view)
        splitter.addWidget(self.profile)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter, 1)
        panel = self._build_panel()
        layout.addWidget(panel)
        # Grow the window by whatever the panel needs, so restoring readable
        # text does not come out of the image area.
        self.resize(IMAGE_AREA_WIDTH + panel.width(), 820)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.status = self.statusBar()
        # Permanent, because a coordinate that disappears cannot be written down
        # or matched against a row in an exported profile.
        self.cursor_label = QLabel("")
        self.cursor_label.setMinimumWidth(300)
        self.status.addPermanentWidget(self.cursor_label)
        self._build_menu()

        self.setAcceptDrops(True)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

        self.view.placeholder = (
            "Drop a radiometric image here\n\nor choose another input on the right"
        )
        self.source_status.setText("waiting for an image")

    # -- construction -------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        capture = QAction("&Capture", self)
        capture.setShortcut(SHORTCUT_CAPTURE)
        capture.triggered.connect(self._capture)
        file_menu.addAction(capture)

        export_profile = QAction("Export line &profile as CSV...", self)
        export_profile.setShortcut(SHORTCUT_EXPORT_PROFILE)
        export_profile.triggered.connect(self._export_profiles)
        file_menu.addAction(export_profile)

        open_image = QAction("&Open radiometric image...", self)
        open_image.setShortcut(SHORTCUT_OPEN_IMAGE)
        open_image.triggered.connect(self._open_radiometric)
        file_menu.addAction(open_image)
        file_menu.addSeparator()

        calib = QAction("Load calibration from radiometric JPEG...", self)
        calib.triggered.connect(self._load_calibration)
        file_menu.addAction(calib)

        self.forget_camera_action = QAction("Forget this camera's calibration", self)
        self.forget_camera_action.triggered.connect(self._forget_this_camera)
        file_menu.addAction(self.forget_camera_action)

        forget_all = QAction("Forget all stored calibrations...", self)
        forget_all.triggered.connect(self._forget_all_calibrations)
        file_menu.addAction(forget_all)

        folder = QAction("Set capture folder...", self)
        folder.triggered.connect(self._choose_folder)
        file_menu.addAction(folder)

    def _build_panel(self) -> QWidget:
        """Two tabs, split by how often a control is touched.

        Measure holds what changes constantly. Setup holds what is set once for
        a session: measurement conditions, parallax alignment, calibration.
        """
        tabs = QTabWidget()
        tabs.addTab(self._build_measure_tab(), "Measure")
        tabs.addTab(self._build_setup_tab(), "Setup")
        # System font, and a width taken from what the controls actually need
        # rather than a guess: shrinking the text to fit a chosen width made the
        # panel hard to read.
        tabs.setFixedWidth(max(PANEL_MIN_WIDTH, tabs.sizeHint().width()))
        return tabs

    @staticmethod
    def _compact(widget: QWidget) -> QVBoxLayout | QFormLayout:
        pass

    @staticmethod
    def _group(title: str) -> tuple[QGroupBox, QFormLayout]:
        group = QGroupBox(title)
        form = QFormLayout(group)
        form.setContentsMargins(8, 6, 8, 6)
        form.setSpacing(4)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        return group, form

    def _build_measure_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(6, 6, 6, 6)
        box.setSpacing(6)

        group, form = self._group("Source")
        self.source_combo = QComboBox()
        self.source_combo.addItems([SOURCE_STILL, SOURCE_USB, SOURCE_SYNTHETIC, SOURCE_REPLAY])
        self.source_combo.setCurrentText(SOURCE_STILL)
        self.source_combo.currentTextChanged.connect(self._set_source)
        form.addRow("Input", self.source_combo)
        self.source_status = QLabel("idle")
        self.source_status.setWordWrap(True)
        form.addRow("Status", self.source_status)
        box.addWidget(group)

        group, form = self._group("Display")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(MODES)
        self.mode_combo.setCurrentText(MODE_EDGES)
        form.addRow("Mode", self.mode_combo)
        self.palette_combo = QComboBox()
        self.palette_combo.addItems(list(PALETTES))
        self.palette_combo.setCurrentText(DEFAULT_PALETTE)
        form.addRow("Palette", self.palette_combo)
        self.blend_spin = SliderField(0.0, 1.0, step=0.01, decimals=2)
        self.blend_spin.setValue(0.6)
        form.addRow("Blend", self.blend_spin)
        self.scale_combo = QComboBox()
        self.scale_combo.addItems(["Auto", "Full range", "Manual", "Lock current"])
        self.scale_combo.currentTextChanged.connect(self._on_scale_mode)
        form.addRow("Scale", self.scale_combo)

        # Manual limits share one row and only appear when they apply.
        self.vmin_spin = self._temp_spin(-40.0, 0.0)
        self.vmax_spin = self._temp_spin(-40.0, 120.0)
        limits = QWidget()
        row = QHBoxLayout(limits)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(self.vmin_spin)
        row.addWidget(self.vmax_spin)
        self._scale_form = form
        self._scale_row = form.rowCount()
        form.addRow("Range °C", limits)
        form.setRowVisible(self._scale_row, False)
        box.addWidget(group)

        group = QGroupBox("Measurement")
        inner = QVBoxLayout(group)
        inner.setContentsMargins(8, 6, 8, 6)
        inner.setSpacing(4)
        tools = QHBoxLayout()
        tools.setSpacing(2)
        for label, tool in (
            ("Cursor", TOOL_NONE),
            ("Spot", TOOL_SPOT),
            ("Box", TOOL_BOX),
            ("Line", TOOL_LINE),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _c, t=tool, b=button: self._set_tool(t, b))
            tools.addWidget(button)
            if tool == TOOL_NONE:
                button.setChecked(True)
            setattr(self, f"_btn_{tool}", button)
        inner.addLayout(tools)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.hot_check = QCheckBox("Track max")
        self.hot_check.setChecked(True)
        self.cold_check = QCheckBox("Track min")
        self.hot_check.toggled.connect(lambda v: setattr(self.measurements, "track_hotspot", v))
        self.cold_check.toggled.connect(lambda v: setattr(self.measurements, "track_coldspot", v))
        row.addWidget(self.hot_check)
        row.addWidget(self.cold_check)
        inner.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(4)
        delta_button = QPushButton("\u0394 last two")
        delta_button.clicked.connect(self._add_delta)
        clear_button = QPushButton("Clear all")
        clear_button.clicked.connect(self._clear)
        row.addWidget(delta_button)
        row.addWidget(clear_button)
        inner.addLayout(row)

        self.readout = QTableWidget(0, 2)
        self.readout.setHorizontalHeaderLabels(["Item", "Value"])
        header = self.readout.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        # Never scroll sideways: the item name must stay visible, and long
        # values elide with the full text on the tooltip instead.
        self.readout.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.readout.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.readout.verticalHeader().setVisible(False)
        self.readout.verticalHeader().setDefaultSectionSize(18)
        self.readout.setMinimumHeight(150)
        inner.addWidget(self.readout)
        box.addWidget(group, 1)

        capture_button = QPushButton(f"Capture  ({shortcut_label(SHORTCUT_CAPTURE)})")
        capture_button.clicked.connect(self._capture)
        box.addWidget(capture_button)
        return page

    def _build_setup_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(6, 6, 6, 6)
        box.setSpacing(6)

        group, form = self._group("Conditions")
        self.emissivity_spin = QDoubleSpinBox()
        self.emissivity_spin.setRange(0.01, 1.0)
        self.emissivity_spin.setSingleStep(0.01)
        self.emissivity_spin.setValue(self.conditions.emissivity)
        self.reflected_spin = self._temp_spin(-40.0, 20.0)
        self.atmospheric_spin = self._temp_spin(-40.0, 20.0)
        self.humidity_spin = QDoubleSpinBox()
        self.humidity_spin.setRange(0.0, 1.0)
        self.humidity_spin.setSingleStep(0.05)
        self.humidity_spin.setValue(0.5)
        self.distance_spin = QDoubleSpinBox()
        self.distance_spin.setRange(0.0, 100.0)
        self.distance_spin.setSingleStep(0.1)
        self.distance_spin.setValue(1.0)
        form.addRow("Emissivity", self.emissivity_spin)
        form.addRow("Reflected °C", self.reflected_spin)
        form.addRow("Air °C", self.atmospheric_spin)
        form.addRow("Humidity", self.humidity_spin)
        form.addRow("Distance m", self.distance_spin)
        for widget in (
            self.emissivity_spin,
            self.reflected_spin,
            self.atmospheric_spin,
            self.humidity_spin,
            self.distance_spin,
        ):
            widget.valueChanged.connect(self._update_conditions)
        box.addWidget(group)

        group, form = self._group("Visible alignment")
        self.align_scale = SliderField(0.8, 1.6, step=0.001, decimals=3)
        self.align_scale.setValue(1.0)
        # Measured offsets sit within about +/-25 px, and the recorded ones
        # within +/-30. A +/-400 range would put nearly three pixels under every
        # pixel of slider, too coarse to align with; the arrow keys still step
        # exactly one.
        self.align_dx = SliderField(-150, 150, step=1, decimals=0, step_on_click=True)
        self.align_dy = SliderField(-150, 150, step=1, decimals=0, step_on_click=True)
        form.addRow("Scale", self.align_scale)
        form.addRow("Shift X", self.align_dx)
        form.addRow("Shift Y", self.align_dy)
        align_button = QPushButton("Auto-align from edges")
        align_button.clicked.connect(self._auto_align)
        form.addRow(align_button)
        self.align_status = QLabel("not aligned")
        self.align_status.setWordWrap(True)
        form.addRow(self.align_status)
        box.addWidget(group)

        group = QGroupBox("Calibration")
        inner = QVBoxLayout(group)
        inner.setContentsMargins(8, 6, 8, 6)
        inner.setSpacing(4)
        self.calib_label = QLabel()
        self.calib_label.setWordWrap(True)
        inner.addWidget(self.calib_label)
        load_button = QPushButton("Load from radiometric JPEG...")
        load_button.clicked.connect(self._load_calibration)
        inner.addWidget(load_button)
        box.addWidget(group)
        self._update_calib_label()

        group = QGroupBox("Output")
        inner = QVBoxLayout(group)
        inner.setContentsMargins(8, 6, 8, 6)
        inner.setSpacing(4)
        self.geotiff_check = QCheckBox("Also write GeoTIFF")
        inner.addWidget(self.geotiff_check)
        folder_button = QPushButton("Capture folder...")
        folder_button.clicked.connect(self._choose_folder)
        inner.addWidget(folder_button)
        box.addWidget(group)

        group = QGroupBox("Timelapse")
        form = QFormLayout(group)
        form.setContentsMargins(8, 6, 8, 6)
        form.setSpacing(4)

        self.timelapse_interval = QDoubleSpinBox()
        self.timelapse_interval.setRange(0.05, 3600.0)
        self.timelapse_interval.setValue(1.0)
        self.timelapse_interval.setDecimals(2)
        self.timelapse_interval.setSuffix(" s")
        form.addRow("Every", self.timelapse_interval)

        self.timelapse_frames = QSpinBox()
        self.timelapse_frames.setRange(0, 1_000_000)
        self.timelapse_frames.setSpecialValueText("no limit")
        form.addRow("Frames", self.timelapse_frames)

        self.timelapse_minutes = QDoubleSpinBox()
        self.timelapse_minutes.setRange(0.0, 10_000.0)
        self.timelapse_minutes.setDecimals(1)
        self.timelapse_minutes.setSpecialValueText("no limit")
        form.addRow("Minutes", self.timelapse_minutes)

        self.timelapse_folder_button = QPushButton("Folder...")
        self.timelapse_folder_button.clicked.connect(self._choose_timelapse_folder)
        form.addRow(self.timelapse_folder_button)

        self.timelapse_button = QPushButton("Start recording")
        self.timelapse_button.clicked.connect(self._toggle_timelapse)
        form.addRow(self.timelapse_button)

        self.timelapse_label = QLabel()
        self.timelapse_label.setWordWrap(True)
        form.addRow(self.timelapse_label)
        box.addWidget(group)
        self._update_timelapse_label()

        box.addStretch(1)
        return page

    @staticmethod
    def _temp_spin(minimum: float, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, 700.0)
        spin.setSingleStep(0.5)
        spin.setValue(value)
        return spin

    # -- source handling ----------------------------------------------------

    def _set_source(self, name: str) -> None:
        if name == SOURCE_STILL:
            # Selecting it with nothing loaded means the user wants to pick one.
            if self.frame is None:
                self._open_radiometric()
            return
        if self.source is not None:
            self.source.stop()
            self.source = None
        if name == SOURCE_USB:
            self.source = UsbFrameSource()
        elif name == SOURCE_SYNTHETIC:
            self.source = SyntheticFrameSource()
        else:
            chosen = QFileDialog.getExistingDirectory(self, "Choose a folder of captures")
            if not chosen:
                self.source_combo.setCurrentText(SOURCE_SYNTHETIC)
                return
            self.source = ReplayFrameSource(Path(chosen))
        self.source.start()

    # -- interaction --------------------------------------------------------

    def _set_tool(self, tool: str, button) -> None:
        for name in (TOOL_NONE, TOOL_SPOT, TOOL_BOX, TOOL_LINE):
            other = getattr(self, f"_btn_{name}", None)
            if other is not None and other is not button:
                other.setChecked(False)
        button.setChecked(True)
        self.view.tool = tool

    def _on_scale_mode(self, mode: str) -> None:
        self._scale_form.setRowVisible(self._scale_row, mode in ("Manual", "Lock current"))
        if mode == "Lock current" and self.temps is not None:
            self._locked_range = (float(np.nanmin(self.temps)), float(np.nanmax(self.temps)))
            self.vmin_spin.setValue(self._locked_range[0])
            self.vmax_spin.setValue(self._locked_range[1])

    def _update_conditions(self) -> None:
        self.conditions = Conditions(
            emissivity=self.emissivity_spin.value(),
            reflected_c=self.reflected_spin.value(),
            atmospheric_c=self.atmospheric_spin.value(),
            humidity=self.humidity_spin.value(),
            distance_m=self.distance_spin.value(),
        )

    def _add_delta(self) -> None:
        if len(self.measurements.spots) < 2:
            QMessageBox.information(self, "Delta", "Place at least two spots first.")
            return
        a, b = self.measurements.spots[-2], self.measurements.spots[-1]
        self.measurements.deltas.append(Delta(a, b))
        self._refresh_readout()

    def _clear(self) -> None:
        self.measurements.clear()
        self._refresh_readout()
        self.view.update()

    def _on_cursor(self, x: int, y: int) -> None:
        """Report the pixel under the mouse, in sensor coordinates."""
        if self.temps is None:
            return
        h, w = self.temps.shape
        if not (0 <= x < w and 0 <= y < h):
            self.cursor_label.setText("")
            return
        unit = "" if self.planck.trusted else " (relative)"
        text = f"x {x}  y {y}   {self.temps[y, x]:.1f} °C{unit}"

        # A reading on a target smaller than a few detector pixels is a blend of
        # the target and its surroundings, which is the commonest way to get a
        # confidently wrong temperature.
        # The distance is whatever the user set in Conditions: a figure they
        # measured beats one estimated from parallax, and it is already there.
        quality = spot_quality(self.temps, x, y, distance_m=self.conditions.distance_m)
        note = quality.describe()
        if note:
            text += f"   ·  {note}"
        self.cursor_label.setText(text)
        self.cursor_label.setStyleSheet("" if quality.resolved else "color: #d08030;")

    def _on_profile_hover(self, series: int, x: int, y: int, value: float, index: int) -> None:
        """Tie a point on the profile curve back to its pixel in the image."""
        self.view.set_highlight((x, y))
        unit = "" if self.planck.trusted else " (relative)"
        self.cursor_label.setText(
            f"L{series + 1} row {index}   x {x}  y {y}   {value:.1f} °C{unit}"
        )

    def _on_profile_leave(self) -> None:
        self.view.set_highlight(None)
        self.cursor_label.setText("")

    def _load_calibration(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Radiometric JPEG or saved calibration",
            str(Path.home()),
            "Calibration (*.jpg *.jpeg *.json)",
        )
        if not path:
            return
        self._adopt_calibration(Path(path))

    def _adopt_calibration(self, path: Path) -> bool:
        """Take constants from another file, checking whose camera they describe.

        Constants are calibrated per unit, so adopting them is only sound when
        the serials agree. Where they demonstrably do not, the readings stay
        marked relative and the user is told, rather than being handed absolute
        temperatures computed with the wrong camera's calibration.
        """
        try:
            adopted = load_planck(path).adopted_for(self.current_serial)
        except Exception as exc:
            QMessageBox.warning(self, "Calibration", f"Could not read constants.\n\n{exc}")
            return False

        self.planck = adopted
        # Remember it, so this camera stays calibrated between sessions and
        # swapping cameras picks up the right constants rather than the last
        # ones loaded. Constants with no serial are refused by the store.
        if adopted.trust is not Trust.MISMATCH:
            # Constants and container together, the same call the command line
            # makes, so neither has to know a template is filed beside them.
            try:
                adopt_from_image(path, self.calibrations, self.current_serial)
            except Exception:
                pass
        self._update_calib_label()
        if adopted.trust is Trust.MISMATCH:
            QMessageBox.warning(
                self,
                "Calibration",
                f"These constants belong to camera {adopted.serial}, but the image on "
                f"screen came from {self.current_serial}.\n\nReadings will stay marked "
                "relative, because constants from another unit give temperatures that "
                "look plausible and are wrong.",
            )
        self.status.showMessage(f"Calibration: {adopted.trust.describe()}", 6000)
        return True

    def _adopt_stored_for_live_camera(self) -> None:
        """Apply remembered constants when a camera identifies itself.

        The live stream carries no calibration, but the accessory handshake
        reports the camera's serial. A stored entry whose serial matches came
        from this very unit, which is the condition that makes constants
        absolute rather than merely adopted.
        """
        serial = getattr(self.source, "serial", None)
        if not serial:
            return
        self.current_serial = serial
        if serial == self._store_checked_for:
            return
        self._store_checked_for = serial

        stored = self.calibrations.find(serial)
        if stored is None:
            return
        self.planck = stored
        self._update_calib_label()
        self.status.showMessage(f"Calibration restored for camera {serial}", 6000)

    def _forget_this_camera(self) -> None:
        serial = self.current_serial
        if not serial:
            QMessageBox.information(
                self, "Calibration", "No camera is identified, so there is nothing to forget."
            )
            return
        if not self.calibrations.forget(serial):
            QMessageBox.information(self, "Calibration", f"Nothing stored for camera {serial}.")
            return
        self.planck = DEFAULT_PLANCK
        self._store_checked_for = None
        self._update_calib_label()
        self.status.showMessage(f"Forgot the calibration for {serial}", 6000)

    def _forget_all_calibrations(self) -> None:
        entries = self.calibrations.entries()
        if not entries:
            QMessageBox.information(self, "Calibration", "No calibrations are stored.")
            return
        listing = "\n".join(f"  {e.label}" for e in entries)
        confirm = QMessageBox.question(
            self,
            "Forget all stored calibrations",
            f"Forget the constants for {len(entries)} camera(s)?\n\n{listing}\n\n"
            "Readings will go back to being relative until a calibration is loaded again.",
        )
        if confirm is not QMessageBox.StandardButton.Yes:
            return
        dropped = self.calibrations.forget_all()
        self.planck = DEFAULT_PLANCK
        self._store_checked_for = None
        self._update_calib_label()
        self.status.showMessage(f"Forgot {dropped} stored calibration(s)", 6000)

    def _update_calib_label(self) -> None:
        """Say which of the three trust states the current constants are in."""
        trust = self.planck.trust
        if trust is Trust.CAMERA:
            self.calib_label.setText(f"Calibrated from {self.planck.source}")
            self.calib_label.setStyleSheet("")
            return

        if trust is Trust.MISMATCH:
            message = (
                f"Constants belong to camera {self.planck.serial}, not the one that took "
                "this image. Relative readings only."
            )
        elif trust is Trust.UNVERIFIED:
            message = (
                f"Adopted from {self.planck.source}, camera not verified. Relative "
                "readings only until the serial can be checked."
            )
        else:
            message = (
                "Uncalibrated: using another camera's Planck constants. "
                "Relative readings only, do not quote as absolute temperatures."
            )
        self.calib_label.setText(message)
        self.calib_label.setStyleSheet("color: #d08030;")

    def _open_radiometric(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Radiometric JPEG", str(Path.home()), "FLIR images (*.jpg *.jpeg)"
        )
        if path:
            self.open_path(Path(path))

    # -- opening files ------------------------------------------------------

    def open_path(self, path: Path) -> bool:
        """Open whatever was handed to us: image, calibration, or capture folder.

        Shared by the file dialog, drag and drop, the Dock icon and argv.
        """
        path = Path(path)
        if path.is_dir():
            return self._open_replay(path)
        if path.suffix.lower() == ".json":
            return self._apply_calibration_file(path)
        return self._open_radiometric_path(path)

    def _open_replay(self, folder: Path) -> bool:
        if not list(folder.glob("*.npz")):
            QMessageBox.warning(self, "Open folder", f"No captures (.npz) in {folder.name}.")
            return False
        if self.source is not None:
            self.source.stop()
        self.source = ReplayFrameSource(folder)
        self.source.start()
        self._select_source_entry(SOURCE_REPLAY)
        self.status.showMessage(f"Replaying {folder.name}", 5000)
        return True

    def _apply_calibration_file(self, path: Path) -> bool:
        return self._adopt_calibration(path)

    def _open_radiometric_path(self, path: Path) -> bool:
        """Open a radiometric JPEG shot with the phone app.

        These carry this camera's own Planck constants and the conditions the
        shot was taken under, so the measurement tools run properly calibrated
        rather than relative.
        """
        try:
            image = flirjpeg.load(path)
        except Exception as exc:
            QMessageBox.warning(self, "Open image", f"Could not read this image.\n\n{exc}")
            return False

        if self.source is not None:
            self.source.stop()
        self.planck = image.planck
        self.conditions = image.conditions
        self._sync_condition_widgets()
        self._update_calib_label()
        # Apply the camera's own alignment immediately; it is exact on the
        # boresight axis and costs nothing. Edge matching then refines it.
        self._import_image = image
        self._import_path = Path(path)
        self.current_serial = image.planck.serial
        if image.alignment is not None:
            self.alignment = image.alignment
            self._sync_alignment_widgets(image.alignment)
        self.source = StillFrameSource(image.as_frame(), label=Path(path).name)
        self.source.start()
        self._select_source_entry(SOURCE_STILL)
        self.status.showMessage(f"Loaded {Path(path).name}", 5000)
        # Align once the first frame has been decoded, not before.
        QTimer.singleShot(300, self._auto_align_quietly)
        return True

    def _auto_align_quietly(self) -> None:
        """Auto-align without complaining if there is nothing to align yet."""
        if self.frame is not None and self.temps is not None and self.frame.visible is not None:
            self._auto_align()

    def _select_source_entry(self, name: str) -> None:
        """Point the combo at a source we opened directly, without rebuilding it."""
        self.source_combo.blockSignals(True)
        if self.source_combo.findText(name) < 0:
            self.source_combo.addItem(name)
        self.source_combo.setCurrentText(name)
        self.source_combo.blockSignals(False)

    # -- drag and drop ------------------------------------------------------

    @staticmethod
    def _droppable(urls) -> list[Path]:
        paths = []
        for url in urls:
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir() or path.suffix.lower() in (".jpg", ".jpeg", ".json"):
                paths.append(path)
        return paths

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and self._droppable(event.mimeData().urls()):
            event.acceptProposedAction()
            self.view.set_drag_active(True)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() and self._droppable(event.mimeData().urls()):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self.view.set_drag_active(False)

    def dropEvent(self, event) -> None:
        self.view.set_drag_active(False)
        paths = self._droppable(event.mimeData().urls())
        if not paths:
            return
        event.acceptProposedAction()
        # Several files at once: apply any calibration first so the image that
        # follows is rendered with it.
        for path in sorted(paths, key=lambda p: p.suffix.lower() != ".json"):
            self.open_path(path)

    def _auto_align(self) -> None:
        """Recover scale and offset by matching edges in the two images.

        The camera records its own alignment, but it derives that from the
        user-set object distance, which is usually left at the default. Matching
        the actual edges measures what the scene really is.
        """
        if self.frame is None or self.temps is None or self.frame.visible is None:
            QMessageBox.information(
                self, "Auto-align", "Needs a frame with both thermal and visible images."
            )
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            prior = self.alignment.scale if self.alignment is not None else None
            result = registration.estimate(self.temps, self.frame.visible, scale_prior=prior)
        except registration.InsufficientContrast as exc:
            QApplication.restoreOverrideCursor()
            self.align_status.setText("scene too flat to align")
            QMessageBox.information(self, "Auto-align", str(exc))
            return
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Auto-align", f"Could not align.\n\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        for widget, value in (
            (self.align_scale, result.scale),
            (self.align_dx, int(round(result.dx))),
            (self.align_dy, int(round(result.dy))),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self.align_status.setText(
            f"scale {result.scale:.3f}, shift {result.dx:+.0f}/{result.dy:+.0f}, "
            f"match {result.confidence:.2f}"
        )
        self._show_import_card(result)
        self.status.showMessage(f"Aligned: {result}", 6000)

    def _sync_alignment_widgets(self, alignment) -> None:
        for widget, value in (
            (self.align_scale, float(alignment.scale)),
            (self.align_dx, int(round(alignment.dx))),
            (self.align_dy, int(round(alignment.dy))),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

    def _show_import_card(self, result) -> None:
        """Summarise what was read from the file against what was measured."""
        image = self._import_image
        if image is None or self.temps is None:
            return
        self._import_image = None

        recorded = image.alignment
        match_recorded = None
        if recorded is not None and self.frame is not None and self.frame.visible is not None:
            thermal_edges = registration._gradient_magnitude(self.temps)
            visible_edges = registration._gradient_magnitude(
                registration._resample_visible(self.frame.visible, self.temps.shape, recorded.scale)
            )
            match_recorded = registration.score(
                thermal_edges, visible_edges, recorded.dx, recorded.dy
            )

        summary = ImportSummary(
            filename=self._import_path.name,
            camera=str(image.tags.get("CameraModel", "unknown camera")),
            serial=str(image.tags.get("CameraSerialNumber", "unknown serial")),
            calibrated=image.planck.trusted,
            planck_source=image.planck.source,
            width=int(self.temps.shape[1]),
            height=int(self.temps.shape[0]),
            temp_min=float(np.nanmin(self.temps)),
            temp_max=float(np.nanmax(self.temps)),
            recorded_scale=None if recorded is None else recorded.scale,
            recorded_dx=None if recorded is None else recorded.dx,
            recorded_dy=None if recorded is None else recorded.dy,
            measured_scale=result.scale,
            measured_dx=result.dx,
            measured_dy=result.dy,
            match_measured=result.confidence,
            match_recorded=match_recorded,
            distance="",
        )
        card = ImportCard(summary, self)
        card.show()

    def _sync_condition_widgets(self) -> None:
        """Push loaded conditions into the spin boxes without re-triggering them."""
        for widget, value in (
            (self.emissivity_spin, self.conditions.emissivity),
            (self.reflected_spin, self.conditions.reflected_c),
            (self.atmospheric_spin, self.conditions.atmospheric_c),
            (self.humidity_spin, self.conditions.humidity),
            (self.distance_spin, self.conditions.distance_m),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

    def _choose_timelapse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Timelapse folder", str(self.timelapse_dir))
        if chosen:
            self.timelapse_dir = Path(chosen)
            self._update_timelapse_label()

    def _toggle_timelapse(self) -> None:
        if self.recorder is not None and self.recorder.status.running:
            self.recorder.stop()
            self._update_timelapse_label()
            return

        frames = self.timelapse_frames.value() or None
        minutes = self.timelapse_minutes.value() or None
        settings = timelapse.Settings(
            folder=self.timelapse_dir,
            interval_s=self.timelapse_interval.value(),
            max_frames=frames,
            max_seconds=minutes * 60.0 if minutes else None,
        )
        camera = timelapse.calibration_for(self.source, self.calibrations)
        if not camera.serial:
            QMessageBox.warning(
                self,
                "Timelapse",
                "No camera is streaming. Timelapse records a live camera; an "
                "image opened from a file is already saved.",
            )
            return
        recorder = timelapse.Recorder(
            settings,
            self.planck,
            self.conditions,
            camera.template or b"",
            self.alignment,
        )
        # Whether the run can be trusted comes first: it needs answering
        # whatever the camera is doing this instant.
        try:
            recorder.check(camera.serial)
        except timelapse.TimelapseError as exc:
            QMessageBox.warning(self, "Timelapse", str(exc))
            return
        if self.frame is None:
            QMessageBox.warning(
                self,
                "Timelapse",
                "Wait until the image is live. A run started before the first frame "
                "arrives opens with a hole in the numbering, which is supposed to "
                "mean the camera stalled.",
            )
            return
        # Offered before the clock starts: the first tick fires at once and
        # would otherwise beat this frame to the queue and record a gap.
        recorder.offer(self.frame)
        recorder.start(camera.serial)
        self.recorder = recorder
        self._update_timelapse_label()

    def _update_timelapse_label(self) -> None:
        recording = self.recorder is not None and self.recorder.status.running
        self.timelapse_button.setText("Stop recording" if recording else "Start recording")
        for widget in (
            self.timelapse_interval,
            self.timelapse_frames,
            self.timelapse_minutes,
            self.timelapse_folder_button,
        ):
            widget.setEnabled(not recording)

        if self.recorder is None:
            self.timelapse_label.setText(short_path(self.timelapse_dir))
            return
        status = self.recorder.status
        if status.error:
            self.timelapse_label.setText(f"Stopped: {status.error}")
            return
        skipped = f", {status.skipped} skipped" if status.skipped else ""
        state = "recording" if status.running else "stopped"
        line = f"{state}: {status.written} written{skipped}, {status.elapsed_s:.0f} s"
        if status.late:
            line += f"\nbehind by up to {status.worst_late_s:.1f} s on {status.late} ticks"
        self.timelapse_label.setText(f"{line}\n{short_path(status.folder)}")

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Capture folder", str(self.output_dir))
        if chosen:
            self.output_dir = Path(chosen)

    def _export_profiles(self) -> None:
        """Write the drawn line profiles as CSV: one row per pixel step."""
        if self.temps is None or not self.measurements.lines:
            QMessageBox.information(
                self,
                "Export line profile",
                "Draw a line on the image first. Its profile is what gets exported.",
            )
            return

        if len(self.measurements.lines) == 1:
            line = self.measurements.lines[0]
            label = line.label or "L1"
            suggested = str(Path(self.output_dir) / f"profile_{label}.csv")
            path, _ = QFileDialog.getSaveFileName(
                self, "Export line profile", suggested, "CSV (*.csv)"
            )
            if not path:
                return
            Path(path).write_text(
                profile_csv(line, self.temps, self.planck, self.conditions, label)
            )
            self.status.showMessage(f"Wrote {Path(path).name}", 5000)
            return

        directory = QFileDialog.getExistingDirectory(
            self, "Export line profiles to folder", str(self.output_dir)
        )
        if not directory:
            return
        written = save_profiles(
            Path(directory), self.measurements, self.temps, self.planck, self.conditions
        )
        self.status.showMessage(f"Wrote {len(written)} profiles to {Path(directory).name}", 6000)

    def _capture(self) -> None:
        if self.frame is None or self.temps is None:
            QMessageBox.information(self, "Capture", "No frame yet.")
            return
        vmin, vmax = self._scale_range()
        try:
            out = save_capture(
                self.output_dir,
                self.frame,
                self.temps,
                self.planck,
                self.conditions,
                palette=self.palette_combo.currentText(),
                measurements=self.measurements,
                vmin=vmin,
                vmax=vmax,
                write_geotiff=self.geotiff_check.isChecked(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Capture", f"Save failed.\n\n{exc}")
            return
        self.status.showMessage(f"Saved {out}", 6000)

    # -- frame loop ---------------------------------------------------------

    def _scale_range(self) -> tuple[float | None, float | None]:
        """Display limits for the current scale mode.

        Auto clips to the 1st-99th percentile rather than the true min and max.
        A handful of saturated pixels otherwise consume most of the palette and
        flatten the whole scene, which is what the camera's own app avoids.
        """
        mode = self.scale_combo.currentText()
        if mode == "Manual":
            return self.vmin_spin.value(), self.vmax_spin.value()
        if mode == "Lock current" and self._locked_range is not None:
            return self._locked_range
        if mode == "Auto" and self.temps is not None:
            lo, hi = np.nanpercentile(self.temps, (1.0, 99.0))
            if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
                return float(lo), float(hi)
        return None, None

    def _tick(self) -> None:
        if self.source is not None:
            status = getattr(self.source, "status", "")
            error = getattr(self.source, "error", None)
            self.source_status.setText(f"{status}\n{error}" if error else status)

            self._adopt_stored_for_live_camera()

        frame = self.source.latest(timeout=0.0) if self.source is not None else None
        if self.recorder is not None:
            # Before the early return below. A stalled camera is exactly when
            # the panel has something to say, and it was the one moment the
            # label stopped updating.
            self.recorder.offer(frame)
            self._update_timelapse_label()
        if frame is None:
            return

        self.frame = frame
        self.temps = raw_to_celsius(frame.raw, self.planck, self.conditions, frame.scale)

        now = time.monotonic()
        self._frame_times = [t for t in self._frame_times + [now] if now - t < 2.0]

        vmin, vmax = self._scale_range()
        rgb = compose(
            self.temps,
            frame.visible,
            self.mode_combo.currentText(),
            self.palette_combo.currentText(),
            vmin,
            vmax,
            blend=self.blend_spin.value(),
            align=Alignment(self.align_scale.value(), self.align_dx.value(), self.align_dy.value()),
        )
        self.view.set_frame(rgb, self.temps)
        self.profile.set_data(self.measurements.lines, self.temps)
        self._refresh_readout()

        size = f"{self.temps.shape[1]}x{self.temps.shape[0]}"
        # A still republishes one frame, so a rate computed from arrivals says
        # nothing about it and reads as though the picture were live.
        if getattr(self.source, "continuous", True):
            detail = f"{len(self._frame_times) / 2.0:.1f} fps"
        else:
            detail = getattr(self.source, "label", "still")
        self.setWindowTitle(
            f"FLIR One  |  {size}  |  {detail}"
            + ("" if self.planck.trusted else "  |  UNCALIBRATED")
        )

    def _refresh_readout(self) -> None:
        if self.temps is None:
            return
        rows = self.measurements.summarise(self.temps)
        self.readout.setRowCount(len(rows))
        for i, (label, value) in enumerate(rows):
            self.readout.setItem(i, 0, QTableWidgetItem(label))
            item = QTableWidgetItem(value)
            if not self.planck.trusted:
                item.setForeground(QColor("#d08030"))
            item.setToolTip(value)
            self.readout.setItem(i, 1, item)

    def closeEvent(self, event) -> None:
        if self.source is not None:
            self.source.stop()
        super().closeEvent(event)


class FlirApplication(QApplication):
    """QApplication that remembers files macOS asks it to open.

    On macOS a file opened from Finder or dropped on the Dock icon arrives as a
    FileOpen event rather than on the command line, and it can arrive before the
    window exists, so early ones are queued.
    """

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self.window: MainWindow | None = None
        self._pending: list[Path] = []

    def attach(self, window: MainWindow) -> None:
        self.window = window
        for path in self._pending:
            window.open_path(path)
        self._pending.clear()

    def open_file(self, path: Path) -> None:
        if self.window is None:
            self._pending.append(path)
        else:
            self.window.open_path(path)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.FileOpen:
            url = event.url()
            if url.isLocalFile():
                self.open_file(Path(url.toLocalFile()))
                return True
        return super().event(event)


def main() -> int:
    app = FlirApplication(sys.argv)
    app.setApplicationName("FLIR One")
    window = MainWindow()
    window.show()
    app.attach(window)
    for argument in sys.argv[1:]:
        candidate = Path(argument)
        if candidate.exists():
            window.open_path(candidate)
        else:
            # Ignoring it silently makes a mistyped path look like a file that
            # opened to nothing. The window still comes up, since the argument
            # is a convenience rather than the point of running the program.
            print(f"flirone: no such file: {argument}", file=sys.stderr)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
