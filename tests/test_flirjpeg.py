"""Radiometric JPEG reading.

These need a real FLIR file and exiftool, so they skip when either is missing.
Point FLIRONE_TEST_IMAGE at a radiometric JPEG to run them.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from flirone import external, flirjpeg
from flirone.calibration import raw_to_celsius

CANDIDATES = [
    os.environ.get("FLIRONE_TEST_IMAGE"),
    str(Path.home() / "Downloads" / "IMG_3888.JPG"),
]
IMAGE = next((Path(c) for c in CANDIDATES if c and Path(c).exists()), None)

pytestmark = [
    pytest.mark.skipif(IMAGE is None, reason="no radiometric JPEG available"),
    pytest.mark.skipif(external.find_tool("exiftool") is None, reason="exiftool not installed"),
]


@pytest.fixture(scope="module")
def image():
    return flirjpeg.load(IMAGE)


def test_carries_its_own_calibration(image):
    assert image.planck.trusted
    assert image.planck.r1 > 0 and image.planck.b > 0


def test_thermal_plane_is_plausible(image):
    temps = raw_to_celsius(image.raw, image.planck, image.conditions)
    assert image.raw.dtype == np.uint16
    # Byte order is chosen by plausibility; the wrong one puts most of the
    # frame far outside the camera's range.
    inside = ((temps >= -40) & (temps <= 200)).mean()
    assert inside > 0.95


def test_byte_order_choice_is_the_smooth_one(image):
    """The correct order is far smoother; the swapped one is high-frequency noise."""

    def roughness(a):
        a = a.astype(np.float64)
        return float(np.mean(np.abs(np.diff(a, axis=1))) + np.mean(np.abs(np.diff(a, axis=0))))

    assert roughness(image.raw) < roughness(image.raw.byteswap()) / 10


def test_conditions_come_from_the_file(image):
    assert 0.0 < image.conditions.emissivity <= 1.0
    assert 0.0 <= image.conditions.humidity <= 1.0
    assert image.conditions.distance_m > 0


def test_alignment_is_converted_from_exif(image):
    alignment = image.alignment
    assert alignment is not None
    # Real2IR is a little over 1: the visible frame is wider than the thermal.
    assert 1.1 < alignment.scale < 1.4
    # Offsets are small, in thermal pixels, not the raw visible-pixel values.
    assert abs(alignment.dx) < 100 and abs(alignment.dy) < 100


def test_visible_image_is_present(image):
    assert image.visible is not None
    assert image.visible.ndim == 3 and image.visible.shape[2] == 3


def test_rejects_a_non_radiometric_jpeg(tmp_path):
    from PIL import Image

    plain = tmp_path / "plain.jpg"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(plain)
    with pytest.raises(flirjpeg.FlirJpegError):
        flirjpeg.load(plain)
