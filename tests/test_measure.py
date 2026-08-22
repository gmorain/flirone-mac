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


class TestSpotResolution:
    """A spot on a target smaller than a few detector pixels reads a blend.

    Sizes are judged on the detector grid, not the array: files from the phone
    app are upscaled 4x, which adds no information and would otherwise make
    every target look four times better resolved than it is.
    """

    @staticmethod
    def target(native_px, array=(640, 480), upscale=4, hot=80.0, cold=20.0):
        import numpy as np

        field = np.full(array, cold)
        half = max(native_px * upscale // 2, 1)
        cy, cx = array[0] // 2, array[1] // 2
        field[cy - half : cy + half, cx - half : cx + half] = hot
        return field, cx, cy

    def test_upscale_is_detected(self):
        from flirone.measure import native_upscale

        assert native_upscale((640, 480)) == 4
        assert native_upscale((160, 120)) == 1

    @pytest.mark.parametrize("native_px", [1, 2])
    def test_small_targets_are_flagged(self, native_px):
        from flirone.measure import spot_quality

        field, x, y = self.target(native_px)
        quality = spot_quality(field, x, y)
        assert not quality.resolved
        assert "too small" in quality.describe()

    @pytest.mark.parametrize("native_px", [3, 6])
    def test_marginal_targets_are_noted_but_allowed(self, native_px):
        from flirone.measure import spot_quality

        field, x, y = self.target(native_px)
        quality = spot_quality(field, x, y)
        assert quality.resolved
        assert not quality.comfortable
        assert "caution" in quality.describe()

    def test_large_targets_pass_silently(self):
        from flirone.measure import spot_quality

        field, x, y = self.target(15)
        quality = spot_quality(field, x, y)
        assert quality.resolved and quality.comfortable
        assert quality.describe() == ""

    def test_uniform_field_is_resolved(self):
        import numpy as np

        from flirone.measure import spot_quality

        quality = spot_quality(np.full((640, 480), 25.0), 240, 320)
        assert quality.resolved and quality.comfortable

    def test_distance_converts_the_size_to_millimetres(self):
        from flirone.measure import spot_quality

        field, x, y = self.target(3)
        quality = spot_quality(field, x, y, distance_m=1.0, ifov_mrad=5.05)
        assert quality.footprint_mm == pytest.approx(5.05, rel=1e-3)
        assert quality.feature_mm == pytest.approx(3 * 5.05, rel=0.05)

    def test_without_distance_it_still_judges_resolution(self):
        """Distance only adds millimetres; the pixel test needs no calibration."""
        from flirone.measure import spot_quality

        field, x, y = self.target(1)
        quality = spot_quality(field, x, y)
        assert not quality.resolved
        assert quality.feature_mm is None

    def test_a_cold_target_on_a_warm_field_is_judged_too(self):
        from flirone.measure import spot_quality

        field, x, y = self.target(1, hot=10.0, cold=40.0)
        assert not spot_quality(field, x, y).resolved
