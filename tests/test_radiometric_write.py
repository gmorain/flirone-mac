"""Writing a radiometric JPEG, checked against a real one.

Needs a FLIR file and exiftool, so it skips without either. The container
itself is covered offline in test_fff.py; what only a real file can check is
that FLIR's own record layout survives being rebuilt, which is the part no
synthetic payload can prove.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from flirone import external, fff, flirjpeg
from flirone.calibration import raw_to_celsius

CANDIDATES = [
    os.environ.get("FLIRONE_TEST_IMAGE"),
    str(Path.home() / "Downloads" / "IMG_3888.JPG"),
    *(str(p) for p in sorted(Path.home().glob("Pictures/FLIR One/*.JPG"))),
]
IMAGE = next((Path(c) for c in CANDIDATES if c and Path(c).exists()), None)

pytestmark = [
    pytest.mark.skipif(IMAGE is None, reason="no radiometric JPEG available"),
    pytest.mark.skipif(external.find_tool("exiftool") is None, reason="exiftool not installed"),
]


@pytest.fixture(scope="module")
def source():
    return flirjpeg.load(IMAGE)


@pytest.fixture(scope="module")
def template():
    return fff.read_app1(IMAGE.read_bytes())


def test_an_identity_rebuild_reproduces_the_payload(template):
    assert fff.rebuild(template, {}) == template


def test_a_written_file_reads_back_with_the_same_temperatures(tmp_path, source, template):
    out = flirjpeg.save_radiometric(
        tmp_path / "written.jpg", source.as_frame(), source.planck, source.conditions, template
    )
    back = flirjpeg.load(out)

    assert np.array_equal(source.raw, back.raw)
    assert back.planck.serial == source.planck.serial
    before = raw_to_celsius(source.raw, source.planck, source.conditions)
    after = raw_to_celsius(back.raw, back.planck, back.conditions)
    assert np.abs(before - after).max() == 0.0


def test_a_written_file_is_smaller_than_the_phone_app_one(tmp_path, source, template):
    out = flirjpeg.save_radiometric(
        tmp_path / "written.jpg", source.as_frame(), source.planck, source.conditions, template
    )
    assert out.stat().st_size < IMAGE.stat().st_size


def test_live_counts_are_written_on_the_reference_plane(tmp_path, source, template):
    """A live frame carries 14-bit counts where the constants describe 16."""
    from flirone.calibration import CountScale
    from flirone.decode import DecodedFrame

    live = DecodedFrame(
        raw=(source.raw // 4).astype(np.uint16),
        visible=source.visible,
        status={},
        scale=CountScale(factor=4.0, offset=0.0, source="test"),
    )
    out = flirjpeg.save_radiometric(
        tmp_path / "live.jpg", live, source.planck, source.conditions, template
    )
    back = flirjpeg.load(out)
    assert np.array_equal(back.raw, (source.raw // 4) * 4)

    # The point of writing on the reference plane: a reader that knows nothing
    # about the live scale still gets the temperatures the live frame showed.
    live_c = raw_to_celsius(live.raw, source.planck, source.conditions, scale=live.scale)
    read_c = raw_to_celsius(back.raw, back.planck, back.conditions)
    assert np.abs(live_c - read_c).max() == 0.0


def test_conditions_survive_only_to_float32(tmp_path, source, template):
    """The container stores them as float32, so a written run is not bit-exact.

    The counts round trip exactly and the conditions do not: 20.0 C stored as a
    float32 kelvin reads back as 19.9999939. The temperature error grows with
    distance from the reflected temperature, so it was 0.0001 C on a 23 to 25 C
    live scene from the camera and 0.0022 C on this file, which reaches 73 C.
    Pinned so a change that made it worse would not pass unnoticed.
    """
    from flirone.calibration import Conditions

    asked = Conditions(emissivity=0.95, reflected_c=20.0, atmospheric_c=20.0, distance_m=1.0)
    out = flirjpeg.save_radiometric(
        tmp_path / "conditions.jpg", source.as_frame(), source.planck, asked, template
    )
    back = flirjpeg.load(out)

    assert back.conditions.emissivity == pytest.approx(asked.emissivity, abs=1e-6)
    assert back.conditions.reflected_c == pytest.approx(asked.reflected_c, abs=1e-4)
    assert back.conditions.distance_m == pytest.approx(asked.distance_m, abs=1e-6)

    before = raw_to_celsius(source.raw, source.planck, asked)
    after = raw_to_celsius(back.raw, back.planck, back.conditions)
    assert np.abs(before - after).max() < 0.01


def _split_visible(height=120, width=160):
    """A visible frame nothing like any template's, so a stale one is obvious."""
    visible = np.zeros((height, width, 3), np.uint8)
    visible[:, : width // 2] = (220, 30, 30)
    visible[:, width // 2 :] = (30, 200, 60)
    return visible


def _frame_with(source, visible):
    from flirone.decode import DecodedFrame

    height, width = visible.shape[:2]
    return DecodedFrame(raw=source.raw[:height, :width].copy(), visible=visible, status={})


def test_the_embedded_image_is_this_frame_not_the_template(tmp_path, source, template):
    """Every timelapse file carried the template's scene as its visible image.

    The reader takes the visible plane from the embedded image record, which
    was being copied through from the template with everything else unparsed.
    """
    visible = _split_visible()
    out = flirjpeg.save_radiometric(
        tmp_path / "visible.jpg",
        _frame_with(source, visible),
        source.planck,
        source.conditions,
        template,
    )
    back = flirjpeg.load(out)

    assert back.visible is not None
    assert back.visible.shape == visible.shape
    left = back.visible[:, :80].mean(axis=(0, 1))
    right = back.visible[:, 80:].mean(axis=(0, 1))
    assert left[0] > 200 and left[1] < 60, "left half should be red"
    assert right[1] > 150 and right[0] < 60, "right half should be green"


def test_alignment_is_written_when_known(tmp_path, source, template):
    from flirone.alignment import Alignment

    out = flirjpeg.save_radiometric(
        tmp_path / "aligned.jpg",
        _frame_with(source, _split_visible()),
        source.planck,
        source.conditions,
        template,
        alignment=Alignment(scale=1.24, dx=-8.0, dy=2.0),
    )
    back = flirjpeg.load(out)

    assert back.alignment is not None
    assert back.alignment.scale == pytest.approx(1.24, abs=1e-4)
    # Offsets are int16 in visible pixels, so a sub-pixel shift does not survive.
    assert back.alignment.dx == pytest.approx(-8.0, abs=1.0)
    assert back.alignment.dy == pytest.approx(2.0, abs=1.0)


def test_no_alignment_is_claimed_when_none_is_known(tmp_path, source, template):
    """The template's alignment describes its own pair of planes, not ours."""
    out = flirjpeg.save_radiometric(
        tmp_path / "unaligned.jpg",
        _frame_with(source, _split_visible()),
        source.planck,
        source.conditions,
        template,
    )
    assert flirjpeg.load(out).alignment is None
