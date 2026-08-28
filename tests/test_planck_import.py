"""Reading Planck constants out of a radiometric JPEG.

The parser tests need a real FLIR file; the validation tests do not, and those
are the ones that would have caught the bug this module had.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from flirone import external, planck_import
from flirone.calibration import Planck, Trust, serials_match

CANDIDATES = [
    os.environ.get("FLIRONE_TEST_IMAGE"),
    str(Path.home() / "Pictures" / "FLIR One" / "IMG_9400_dist_292mm.JPG"),
    str(Path.home() / "Downloads" / "IMG_3888.JPG"),
]
IMAGE = next((Path(c) for c in CANDIDATES if c and Path(c).exists()), None)

needs_image = pytest.mark.skipif(IMAGE is None, reason="no radiometric JPEG available")
needs_exiftool = pytest.mark.skipif(
    external.find_tool("exiftool") is None, reason="exiftool not installed"
)


# --- validation, which needs no hardware and no file -------------------------

# What the segment parser produced when it read the constants from 0x00D4, the
# camera model string, instead of their real offsets. Every value is positive,
# so a positivity check passed them and the app reported temperatures about a
# hundred degrees low.
MISREAD = {"r1": 216142020608.0, "r2": 1e-12, "b": 3300.9, "f": 6.82254e22, "o": 691150958.0}
REAL = {"r1": 18453.355, "r2": 0.012509181, "b": 1460.6, "f": 1.0, "o": -1731.0}


def test_a_misread_record_is_rejected():
    """Positivity is not enough: these values are positive and impossible."""
    with pytest.raises(ValueError, match="implausible"):
        Planck(**MISREAD).validated()


def test_real_constants_pass_validation():
    assert Planck(**REAL).validated().b == 1460.6


def test_the_reference_unit_still_validates():
    from flirone.calibration import DEFAULT_PLANCK

    assert DEFAULT_PLANCK.validated() is DEFAULT_PLANCK


@pytest.mark.parametrize(
    "field,value",
    [("r1", 0.0), ("r2", 0.0), ("b", 0.0), ("r1", -1.0)],
)
def test_non_positive_constants_are_still_rejected(field, value):
    with pytest.raises(ValueError):
        Planck(**{**REAL, field: value}).validated()


# --- the parser itself -------------------------------------------------------


@needs_image
def test_segment_parser_reports_the_serial():
    """Without it, constants can never be checked against the camera."""
    planck = planck_import.from_flir_segment(IMAGE)
    assert planck.serial


@needs_image
@needs_exiftool
def test_segment_parser_agrees_with_exiftool():
    """The two readers must not disagree; one of them being wrong is the bug."""
    segment = planck_import.from_flir_segment(IMAGE)
    reference = planck_import.from_exiftool(IMAGE)
    for field in ("r1", "r2", "b", "f", "o"):
        expected = getattr(reference, field)
        assert getattr(segment, field) == pytest.approx(expected, rel=1e-6, abs=1e-9)
    assert serials_match(segment.serial, reference.serial)


@needs_image
def test_loaded_constants_are_adopted_not_yet_trusted():
    """A file cannot tell you whose camera is being measured; only a match can."""
    assert planck_import.load(IMAGE).trust is Trust.UNVERIFIED
