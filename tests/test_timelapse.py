"""The timelapse recorder, driven without a camera.

Writing is stubbed in most of these: what is being tested is the timer, the
numbering and the refusals, and a real radiometric JPEG per tick would make the
suite slow without testing anything test_radiometric_write.py does not.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from flirone import timelapse
from flirone.calibration import Conditions, Planck, Trust
from flirone.decode import DecodedFrame

TEMPLATE = b"FFF\x00" + b"\x00" * 60


def planck(serial="F02F9T00570", trust=Trust.CAMERA):
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


def frame():
    return DecodedFrame(raw=np.zeros((4, 4), np.uint16), visible=None, status={})


@pytest.fixture
def written(monkeypatch):
    """Record the paths a run would write, without building JPEGs."""
    paths = []

    def fake_save(path, *args, **kwargs):
        Path(path).write_bytes(b"not really a jpeg")
        paths.append(Path(path))
        return Path(path)

    monkeypatch.setattr(timelapse, "save_radiometric", fake_save)
    return paths


def run(recorder, seconds, feed=True):
    """Drive a recorder for a while, offering frames unless told not to."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and recorder.status.running:
        if feed:
            recorder.offer(frame())
        time.sleep(0.01)
    recorder.stop()


def test_a_frame_limit_ends_the_run(tmp_path, written):
    settings = timelapse.Settings(tmp_path, interval_s=0.05, max_frames=4)
    rec = timelapse.Recorder(settings, planck(), Conditions(), TEMPLATE)
    rec.start()
    run(rec, 3.0)

    assert rec.status.written == 4
    assert len(written) == 4


def test_a_time_limit_ends_the_run(tmp_path, written):
    settings = timelapse.Settings(tmp_path, interval_s=0.05, max_seconds=0.3)
    rec = timelapse.Recorder(settings, planck(), Conditions(), TEMPLATE)
    rec.start()
    run(rec, 3.0)

    assert not rec.status.running
    assert rec.status.elapsed_s < 2.0


def test_a_tick_with_no_frame_leaves_a_hole_in_the_numbering(tmp_path, written):
    """A stalled camera has to be visible in a file listing."""
    settings = timelapse.Settings(tmp_path, interval_s=0.05)
    rec = timelapse.Recorder(settings, planck(), Conditions(), TEMPLATE)
    rec.start()

    rec.offer(frame())
    time.sleep(0.12)  # one tick writes, the next finds nothing
    time.sleep(0.12)
    rec.offer(frame())
    time.sleep(0.12)
    rec.stop()

    numbers = sorted(int(p.stem.split("-")[1]) for p in written)
    assert rec.status.skipped >= 1
    assert numbers, "nothing was written at all"
    # The numbers used are not consecutive, because a skipped tick spent one.
    assert numbers[-1] > len(numbers)


def test_names_are_fixed_width_and_sort_in_order(tmp_path, written):
    settings = timelapse.Settings(tmp_path, interval_s=0.05, max_frames=3)
    rec = timelapse.Recorder(settings, planck(), Conditions(), TEMPLATE)
    rec.start()
    run(rec, 3.0)

    names = [p.name for p in written]
    assert names == sorted(names)
    assert all(len(n) == len(names[0]) for n in names)


def test_a_write_error_stops_the_run_and_is_reported(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(timelapse, "save_radiometric", explode)
    settings = timelapse.Settings(tmp_path, interval_s=0.05)
    rec = timelapse.Recorder(settings, planck(), Conditions(), TEMPLATE)
    rec.start()
    run(rec, 1.0)

    assert not rec.status.running
    assert "no space left" in (rec.status.error or "")
    assert rec.status.written == 0


def test_constants_from_another_camera_refuse_to_start(tmp_path):
    rec = timelapse.Recorder(
        timelapse.Settings(tmp_path), planck(trust=Trust.UNVERIFIED), Conditions(), TEMPLATE
    )
    with pytest.raises(timelapse.TimelapseError, match="did not come from this camera"):
        rec.start()


def test_a_different_camera_streaming_refuses_to_start(tmp_path):
    rec = timelapse.Recorder(timelapse.Settings(tmp_path), planck(), Conditions(), TEMPLATE)
    with pytest.raises(timelapse.TimelapseError, match="belongs to camera"):
        rec.start(camera_serial="F02F9T99999")


def test_no_template_refuses_to_start(tmp_path):
    rec = timelapse.Recorder(timelapse.Settings(tmp_path), planck(), Conditions(), b"")
    with pytest.raises(timelapse.TimelapseError, match="radiometric image"):
        rec.start()


def test_an_impossible_interval_is_refused(tmp_path):
    rec = timelapse.Recorder(
        timelapse.Settings(tmp_path, interval_s=0.0), planck(), Conditions(), TEMPLATE
    )
    with pytest.raises(timelapse.TimelapseError, match="interval"):
        rec.start()


def test_output_stays_inside_the_chosen_folder(tmp_path):
    rec = timelapse.Recorder(timelapse.Settings(tmp_path), planck(), Conditions(), TEMPLATE)
    path = rec._path_for(7, tmp_path)
    assert path.parent == tmp_path.resolve()
    assert path.name == "flirone-000007.jpg"


class OneFrameSource:
    """A source that has nothing ready for a moment, then produces frames."""

    def __init__(self, quiet_for=0.0):
        self.ready_at = time.monotonic() + quiet_for

    def latest(self, timeout=0.2):
        if time.monotonic() < self.ready_at:
            time.sleep(min(timeout, 0.02))
            return None
        return frame()


def test_record_waits_for_a_frame_before_starting_the_clock(tmp_path, written):
    """The first tick fires at once, so a slow camera used to record a gap.

    A hole in the numbering is supposed to mean the camera stalled. Starting
    the clock before the first frame arrived made every run begin with one.
    """
    source = OneFrameSource(quiet_for=0.15)
    status = timelapse.record(
        source,
        timelapse.Settings(tmp_path, interval_s=0.05, max_frames=3),
        planck(),
        Conditions(),
        TEMPLATE,
    )

    assert status.written == 3
    assert status.skipped == 0
    numbers = sorted(int(p.stem.split("-")[1]) for p in written)
    assert numbers == [1, 2, 3]


def test_record_gives_up_when_no_frame_ever_arrives(tmp_path, written):
    source = OneFrameSource(quiet_for=1e6)
    with pytest.raises(timelapse.TimelapseError, match="no frame"):
        timelapse.record(
            source,
            timelapse.Settings(tmp_path, interval_s=0.05),
            planck(),
            Conditions(),
            TEMPLATE,
            first_frame_timeout_s=0.2,
        )
    assert not written


class SlowWriteSource:
    """Always has a frame ready."""

    def latest(self, timeout=0.2):
        return frame()


def test_a_run_that_cannot_keep_up_says_so(tmp_path, monkeypatch):
    """Ticks are timed from the start, so drift would otherwise be silent."""

    def slow_save(path, *args, **kwargs):
        time.sleep(0.15)
        Path(path).write_bytes(b"x")
        return Path(path)

    monkeypatch.setattr(timelapse, "save_radiometric", slow_save)
    rec = timelapse.Recorder(
        timelapse.Settings(tmp_path, interval_s=0.05, max_frames=4),
        planck(),
        Conditions(),
        TEMPLATE,
    )
    rec.start()
    run(rec, 3.0)

    assert rec.status.written == 4
    assert rec.status.late >= 1
    assert rec.status.worst_late_s > 0.05


def test_a_run_that_keeps_up_reports_no_lateness(tmp_path, written):
    rec = timelapse.Recorder(
        timelapse.Settings(tmp_path, interval_s=0.1, max_frames=3),
        planck(),
        Conditions(),
        TEMPLATE,
    )
    rec.start()
    run(rec, 3.0)

    assert rec.status.written == 3
    assert rec.status.late == 0


def test_elapsed_time_freezes_when_the_run_ends(tmp_path, written):
    """It kept counting after a run stopped, so a finished run aged on screen."""
    rec = timelapse.Recorder(
        timelapse.Settings(tmp_path, interval_s=0.05, max_frames=2),
        planck(),
        Conditions(),
        TEMPLATE,
    )
    rec.start()
    run(rec, 2.0)

    assert not rec.status.running
    settled = rec.status.elapsed_s
    time.sleep(0.3)
    assert rec.status.elapsed_s == settled


def test_elapsed_time_freezes_after_a_write_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        timelapse, "save_radiometric", lambda *a, **k: (_ for _ in ()).throw(OSError("full"))
    )
    rec = timelapse.Recorder(
        timelapse.Settings(tmp_path, interval_s=0.05), planck(), Conditions(), TEMPLATE
    )
    rec.start()
    run(rec, 1.0)

    settled = rec.status.elapsed_s
    time.sleep(0.2)
    assert rec.status.elapsed_s == settled


def test_check_refuses_without_starting_a_thread(tmp_path):
    rec = timelapse.Recorder(
        timelapse.Settings(tmp_path), planck(trust=Trust.UNVERIFIED), Conditions(), TEMPLATE
    )
    with pytest.raises(timelapse.TimelapseError):
        rec.check()
    assert not rec.status.running
