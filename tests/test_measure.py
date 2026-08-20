"""Measurement tools over a field with known values."""

from __future__ import annotations

import numpy as np
import pytest

from flirone.measure import Box, Delta, Line, MeasurementSet, Spot, coldspot, hotspot


@pytest.fixture
def field():
    temps = np.full((100, 120), 20.0)
    temps[40:60, 50:70] = 80.0  # a hot patch
    temps[5, 5] = -10.0  # a cold pixel
    return temps


def test_spot_reads_its_pixel(field):
    assert Spot(55, 45).measure(field).mean == pytest.approx(80.0)
    assert Spot(5, 90).measure(field).mean == pytest.approx(20.0)


def test_box_statistics(field):
    stats = Box(50, 40, 70, 60).measure(field)
    assert stats.minimum == pytest.approx(80.0)
    assert stats.maximum == pytest.approx(80.0)
    assert stats.stddev == pytest.approx(0.0)


def test_box_spanning_the_edge_of_the_patch(field):
    stats = Box(40, 30, 80, 70).measure(field)
    assert stats.minimum == pytest.approx(20.0)
    assert stats.maximum == pytest.approx(80.0)
    assert 20.0 < stats.mean < 80.0


def test_box_normalises_reversed_corners(field):
    assert Box(70, 60, 50, 40).measure(field).mean == pytest.approx(
        Box(50, 40, 70, 60).measure(field).mean
    )


def test_line_samples_across_the_patch(field):
    distance, values = Line(0, 50, 119, 50).samples(field)
    assert len(distance) == len(values) > 0
    assert values.max() == pytest.approx(80.0)
    assert values.min() == pytest.approx(20.0)


def test_hot_and_cold_spots(field):
    x, y, value = hotspot(field)
    assert value == pytest.approx(80.0)
    assert 50 <= x < 70 and 40 <= y < 60
    x, y, value = coldspot(field)
    assert (x, y) == (5, 5)
    assert value == pytest.approx(-10.0)


def test_delta_is_signed(field):
    hot, cool = Spot(55, 45), Spot(5, 90)
    assert Delta(hot, cool).measure(field) == pytest.approx(60.0)
    assert Delta(cool, hot).measure(field) == pytest.approx(-60.0)


def test_summary_lists_everything(field):
    measurements = MeasurementSet()
    measurements.spots.append(Spot(55, 45))
    measurements.boxes.append(Box(50, 40, 70, 60))
    measurements.lines.append(Line(0, 50, 119, 50))
    measurements.track_hotspot = True
    rows = measurements.summarise(field)
    names = [name for name, _ in rows]
    assert "Spot 1" in names and "Box 1" in names and "Line 1" in names
    assert "Hotspot" in names


def test_clear_removes_everything(field):
    measurements = MeasurementSet()
    measurements.spots.append(Spot(1, 1))
    measurements.boxes.append(Box(0, 0, 5, 5))
    measurements.clear()
    assert not measurements.spots and not measurements.boxes
