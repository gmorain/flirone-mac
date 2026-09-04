"""The timelapse controls in the window.

The refusals matter more than the layout: a run that starts uncalibrated
produces a folder of files that claim absolute temperatures and are wrong by
degrees, and the only thing standing between the user and that is this button.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from flirone.calibration import Planck, Trust
from flirone.ui import app as appmod


class FakeLiveSource:
    """A source that names a camera, which is what timelapse needs."""

    name = "fake"
    continuous = True
    serial = "F02F9T00570"
    status = "streaming"
    error = None

    def latest(self, timeout=0.0):
        return None


def _camera_planck():
    return Planck(
        r1=18453.35,
        r2=0.0125,
        b=1460.6,
        f=1.0,
        o=-1731.0,
        trust=Trust.CAMERA,
        serial="F02F9T00570",
        source="test",
    )


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(
        appmod.QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args[-1])
    )
    win = appmod.MainWindow()
    win.timelapse_dir = tmp_path
    win.warnings = warnings
    yield win
    if win.recorder is not None:
        win.recorder.stop()


def test_starting_uncalibrated_refuses_and_says_why(window):
    """A fresh window runs on reference constants from another unit."""
    window.source = FakeLiveSource()  # so the calibration is what is wrong
    window._toggle_timelapse()

    assert window.recorder is None
    assert window.warnings, "the user was told nothing"
    # Specific: a readiness message also mentions the camera, and would let
    # this pass while the calibration check never ran.
    assert "did not come from this camera" in window.warnings[-1]


def test_the_button_reads_start_when_idle(window):
    assert window.timelapse_button.text() == "Start recording"
    assert window.timelapse_interval.isEnabled()


def test_no_limit_is_the_default_for_both_limits(window):
    """Zero means no limit, and shows as such rather than as a zero."""
    assert window.timelapse_frames.value() == 0
    assert window.timelapse_minutes.value() == 0.0
    assert window.timelapse_frames.specialValueText() == "no limit"
    assert window.timelapse_minutes.specialValueText() == "no limit"


def test_the_interval_cannot_be_set_below_what_the_recorder_accepts(window):
    window.timelapse_interval.setValue(0.0)
    assert window.timelapse_interval.value() >= 0.05


def test_the_status_line_updates_when_no_frame_arrives(window, tmp_path, monkeypatch):
    """A stalled camera is exactly when the panel has something to say.

    The recorder used to be offered frames after the early return for a missing
    frame, so the one moment skipped ticks mattered was the one moment the
    label stopped moving.
    """
    import flirone.timelapse as tl

    monkeypatch.setattr(tl, "save_radiometric", lambda path, *a, **k: Path(path))
    recorder = tl.Recorder(
        tl.Settings(tmp_path, interval_s=0.05), _camera_planck(), appmod.Conditions(), b"FFF\x00"
    )
    recorder.start()
    window.recorder = recorder
    try:
        time.sleep(0.2)  # ticks pass with nothing offered
        window.source = None
        window._tick()
        assert "skipped" in window.timelapse_label.text()
        assert str(tmp_path) in window.timelapse_label.text()
    finally:
        recorder.stop()


def test_a_run_cannot_start_before_the_first_frame(window, tmp_path, monkeypatch):
    """Every run from the window used to open with a phantom gap.

    The first tick fires at once, so starting before a frame had arrived spent
    number 000001 on nothing and the sequence began at 000002.
    """
    window.planck = _camera_planck()
    window.source = FakeLiveSource()
    window.timelapse_dir = tmp_path
    monkeypatch.setattr(window.calibrations, "find", lambda serial: _camera_planck())
    monkeypatch.setattr(window.calibrations, "template", lambda serial: b"FFF\x00")
    window.frame = None

    window._toggle_timelapse()

    assert window.recorder is None
    assert "live" in window.warnings[-1]


def test_recording_needs_a_camera_rather_than_an_opened_image(window, tmp_path):
    """It used to blame the missing template, which is not what is wrong."""
    window.planck = _camera_planck()
    window.timelapse_dir = tmp_path
    window.source = None

    window._toggle_timelapse()

    assert window.recorder is None
    assert "No camera is streaming" in window.warnings[-1]


def test_the_panel_shows_a_home_path_as_a_tilde(window, tmp_path):
    """Covered as a unit in test_paths.py; this is that it is wired up."""
    window.timelapse_dir = Path.home() / "Pictures" / "run"
    window._update_timelapse_label()
    assert window.timelapse_label.text() == "~/Pictures/run"
