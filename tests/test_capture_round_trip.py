"""A live capture must reopen at the temperature it was taken at.

A capture stores raw counts, and live counts are four times below the plane the
Planck constants describe. If the scale does not survive the write, a live
capture reopened later converts as though it came from a stored image and reads
about a hundred degrees low. Nothing in the interface would show that; it is a
plausible number, which is the dangerous kind.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from flirone.calibration import FLIR_ONE_LIVE_SCALE, Conditions, Planck, Trust, raw_to_celsius
from flirone.decode import DecodedFrame
from flirone.export import save_capture
from flirone.sources import ReplayFrameSource

CAMERA = Planck(
    r1=18453.355,
    r2=0.012509181,
    b=1460.6,
    f=1.0,
    o=-1731.0,
    trust=Trust.CAMERA,
    serial="F02F9T00570",
)


def live_frame() -> DecodedFrame:
    """A room-temperature field with an ice-water patch, on the live plane."""
    counts = np.full((120, 160), 3025.0)
    counts[40:80, 60:100] = 2246.3
    return DecodedFrame(raw=counts, visible=None, status={}, scale=FLIR_ONE_LIVE_SCALE)


@pytest.fixture
def written(tmp_path: Path) -> tuple[Path, np.ndarray]:
    frame = live_frame()
    conditions = Conditions()
    temps = raw_to_celsius(frame.raw, CAMERA, conditions, frame.scale)
    return save_capture(tmp_path, frame, temps, CAMERA, conditions), temps


def test_a_live_capture_replays_at_the_same_temperature(written):
    directory, temps = written
    source = ReplayFrameSource(directory, loop=False, fps=60)
    source.start()
    frame = source.latest(timeout=5.0)
    source.stop()

    assert frame is not None, "the capture did not replay"
    assert frame.scale is not None, "the capture came back with no scale"
    replayed = raw_to_celsius(frame.raw, CAMERA, Conditions(), frame.scale)
    assert np.nanmax(np.abs(replayed - temps)) < 1e-9


def test_dropping_the_scale_would_be_caught(written):
    """What the test above is protecting against, so the margin is visible."""
    directory, temps = written
    with np.load(directory / "capture.npz", allow_pickle=False) as data:
        unscaled = raw_to_celsius(data["raw"], CAMERA, Conditions())
    assert np.nanmax(unscaled) < -50
    assert np.nanmax(np.abs(unscaled - temps)) > 50


def test_a_capture_from_a_stored_image_carries_no_scale(tmp_path: Path):
    """It is already on the reference plane, and an identity scale would be a
    different claim from no conversion at all."""
    frame = DecodedFrame(raw=np.full((120, 160), 12269.0), visible=None, status={})
    conditions = Conditions()
    temps = raw_to_celsius(frame.raw, CAMERA, conditions)
    directory = save_capture(tmp_path, frame, temps, CAMERA, conditions)
    with np.load(directory / "capture.npz", allow_pickle=False) as data:
        assert "live_factor" not in data
