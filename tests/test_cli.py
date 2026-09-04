"""The timelapse command line.

No camera and no window. The source and the store are replaced, so what is
under test is the argument handling, the refusals and the exit codes rather
than the recording, which test_timelapse.py already covers.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from flirone import cli, timelapse
from flirone.calibration import Conditions, Planck, Trust
from flirone.decode import DecodedFrame

SERIAL = "F02F9T00570"
TEMPLATE = b"FFF\x00" + b"\x00" * 60


def camera_planck(serial=SERIAL, trust=Trust.CAMERA):
    return Planck(
        r1=18453.35,
        r2=0.0125,
        b=1460.6,
        f=1.0,
        o=-1731.0,
        trust=trust,
        serial=serial,
        source="test",
    )


class FakeSource:
    name = "fake"
    continuous = True

    def __init__(self, serial=SERIAL):
        self.serial = serial
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def latest(self, timeout=0.2):
        time.sleep(min(timeout, 0.01))
        return DecodedFrame(raw=np.zeros((4, 4), np.uint16), visible=None, status={})


class FakeStore:
    def __init__(self, planck=None, template=TEMPLATE):
        self._planck = planck if planck is not None else camera_planck()
        self._template = template

    def find(self, serial):
        return self._planck

    def template(self, serial):
        return self._template


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """A CLI with no camera behind it and nothing actually written."""
    source = FakeSource()
    monkeypatch.setattr(cli, "UsbFrameSource", lambda: source)
    monkeypatch.setattr(timelapse, "save_radiometric", lambda path, *a, **k: path)
    return source


# -- arguments ----------------------------------------------------------------


def test_the_folder_is_required_unless_calibrating():
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 2


def test_a_bad_number_is_a_usage_error_not_a_traceback():
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(["/tmp/x", "--every", "soon"])
    assert exit_info.value.code == 2


def test_conditions_default_to_the_library_defaults():
    args = cli.build_parser().parse_args(["/tmp/x"])
    assert cli.conditions_from(args) == Conditions().validated()


def test_conditions_come_from_the_flags():
    args = cli.build_parser().parse_args(
        [
            "/tmp/x",
            "--emissivity",
            "0.8",
            "--reflected",
            "18",
            "--air",
            "17",
            "--humidity",
            "0.3",
            "--distance",
            "2.5",
        ]
    )
    used = cli.conditions_from(args)
    assert used.emissivity == pytest.approx(0.8)
    assert used.reflected_c == pytest.approx(18.0)
    assert used.atmospheric_c == pytest.approx(17.0)
    assert used.humidity == pytest.approx(0.3)
    assert used.distance_m == pytest.approx(2.5)


def test_a_clamped_condition_is_reported(capsys):
    """It is clamped rather than rejected, so silence would record a typo."""
    args = cli.build_parser().parse_args(["/tmp/x", "--emissivity", "5"])
    used = cli.conditions_from(args)
    captured = capsys.readouterr()

    assert used.emissivity == 1.0
    assert "--emissivity" in captured.err
    assert "out of range" in captured.err


# -- refusals -----------------------------------------------------------------


def test_an_impossible_interval_exits_two(wired, tmp_path, capsys):
    assert cli.main([str(tmp_path), "--every", "0"]) == 2
    assert "interval" in capsys.readouterr().err


def test_constants_from_another_camera_exit_one(monkeypatch, wired, tmp_path, capsys):
    monkeypatch.setattr(
        cli, "CalibrationStore", lambda: FakeStore(camera_planck(trust=Trust.UNVERIFIED))
    )
    assert cli.main([str(tmp_path), "--frames", "1"]) == 1
    assert "did not come from this camera" in capsys.readouterr().err


def test_no_template_exits_one(monkeypatch, wired, tmp_path, capsys):
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore(template=None))
    assert cli.main([str(tmp_path), "--frames", "1"]) == 1
    assert "radiometric image" in capsys.readouterr().err


def test_a_camera_with_no_serial_is_refused(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "UsbFrameSource", lambda: FakeSource(serial=None))
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore())
    assert cli.main([str(tmp_path), "--frames", "1"]) == 1


# -- running ------------------------------------------------------------------


def test_a_frame_limit_run_reports_and_exits_zero(monkeypatch, wired, tmp_path, capsys):
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore())
    code = cli.main([str(tmp_path), "--every", "0.05", "--frames", "3"])
    out = capsys.readouterr().out

    assert code == 0
    assert "3 written" in out
    assert str(tmp_path) in out
    assert wired.stopped, "the camera must be closed on the way out"


def test_an_interrupt_ends_the_run_and_still_summarises(monkeypatch, wired, tmp_path, capsys):
    """Ctrl-C ends the run, not the process, so the summary survives it."""
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore())

    real_record = timelapse.record

    def record_then_interrupt(*args, **kwargs):
        stop = kwargs.get("stop")
        threading.Timer(0.2, stop.set).start()
        return real_record(*args, **kwargs)

    monkeypatch.setattr(cli, "record", record_then_interrupt)
    code = cli.main([str(tmp_path), "--every", "0.05"])
    out = capsys.readouterr().out

    assert code == 0
    assert "written" in out
    assert wired.stopped


class LateSerialSource(FakeSource):
    """A camera as it really behaves: the serial arrives with the handshake.

    Resolving before a frame read None here and refused a camera that was
    simply still coming up, which every fake with a serial set at construction
    was blind to.
    """

    def __init__(self, frames_before_serial=2):
        super().__init__(serial=None)
        self.remaining = frames_before_serial

    def latest(self, timeout=0.2):
        time.sleep(min(timeout, 0.01))
        if self.remaining > 0:
            self.remaining -= 1
            return None
        self.serial = SERIAL
        return DecodedFrame(raw=np.zeros((4, 4), np.uint16), visible=None, status={})


def test_a_camera_whose_serial_arrives_late_still_records(monkeypatch, tmp_path, capsys):
    source = LateSerialSource()
    monkeypatch.setattr(cli, "UsbFrameSource", lambda: source)
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore())
    monkeypatch.setattr(timelapse, "save_radiometric", lambda path, *a, **k: path)

    code = cli.main([str(tmp_path), "--every", "0.05", "--frames", "2"])

    assert code == 0
    assert "F02F9T00570" in capsys.readouterr().out


def test_zero_means_no_limit(monkeypatch, wired, tmp_path):
    """So a run can be left going without naming a number known in advance."""
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore())
    captured = {}

    def fake_record(*args, **kwargs):
        captured["settings"] = args[1]
        return timelapse.Status(folder=args[1].folder)

    monkeypatch.setattr(cli, "record", fake_record)
    cli.main([str(tmp_path), "--frames", "0", "--minutes", "0"])

    assert captured["settings"].max_frames is None
    assert captured["settings"].max_seconds is None


def test_the_terminal_records_no_alignment(monkeypatch, wired, tmp_path):
    """Nothing headless knows one, and a wrong one is worse than none."""
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore())
    captured = {}

    def fake_record(*args, **kwargs):
        captured["alignment"] = kwargs.get("alignment")
        return timelapse.Status(folder=args[1].folder)

    monkeypatch.setattr(cli, "record", fake_record)
    cli.main([str(tmp_path), "--frames", "1"])

    assert captured["alignment"] is None


def test_calibrate_files_constants_and_exits_without_a_folder(monkeypatch, capsys):
    filed = {}

    def fake_adopt(path, store, serial=None):
        filed["path"] = path
        return camera_planck()

    monkeypatch.setattr(cli, "adopt_from_image", fake_adopt)
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore())

    assert cli.main(["--calibrate", "/tmp/IMG.jpg"]) == 0
    assert "F02F9T00570" in capsys.readouterr().out
    assert filed["path"].name == "IMG.jpg"


def test_a_calibrate_that_fails_exits_one(monkeypatch, capsys):
    def explode(path, store, serial=None):
        raise ValueError("not a radiometric JPEG")

    monkeypatch.setattr(cli, "adopt_from_image", explode)
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore())

    assert cli.main(["--calibrate", "/tmp/nope.jpg"]) == 1
    assert "not a radiometric JPEG" in capsys.readouterr().err


def test_calibrate_alone_says_it_was_not_checked(monkeypatch, capsys):
    """There is no camera session to check against, and silence would imply one."""
    monkeypatch.setattr(cli, "adopt_from_image", lambda path, store, serial=None: camera_planck())
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore())

    assert cli.main(["--calibrate", "/tmp/IMG.jpg"]) == 0
    out = capsys.readouterr().out
    assert "Not checked against a camera" in out


def test_calibrate_before_a_run_is_checked_against_the_camera(monkeypatch, wired, tmp_path):
    """A file from another unit has to fail here, not hours into a run."""
    seen = {}

    def fake_adopt(path, store, serial=None):
        seen["serial"] = serial
        return camera_planck()

    monkeypatch.setattr(cli, "adopt_from_image", fake_adopt)
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore())
    monkeypatch.setattr(cli, "record", lambda *a, **k: timelapse.Status(folder=a[1].folder))

    cli.main([str(tmp_path), "--calibrate", "/tmp/IMG.jpg", "--frames", "1"])

    assert seen["serial"] == SERIAL, "the attached camera was not consulted"


def test_calibrating_with_another_cameras_image_stops_the_run(monkeypatch, wired, tmp_path, capsys):
    def mismatch(path, store, serial=None):
        raise ValueError(f"those constants belong to camera F02F9T99999, and this one is {serial}")

    monkeypatch.setattr(cli, "adopt_from_image", mismatch)
    monkeypatch.setattr(cli, "CalibrationStore", lambda: FakeStore())

    assert cli.main([str(tmp_path), "--calibrate", "/tmp/other.jpg", "--frames", "1"]) == 1
    assert "belong to camera" in capsys.readouterr().err
