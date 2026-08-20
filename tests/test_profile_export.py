"""Line profile export: the raw pixel/temperature table."""

from __future__ import annotations

import csv
import io

import numpy as np
import pytest

from flirone.calibration import Conditions, Planck, Trust
from flirone.export import profile_csv, save_profiles
from flirone.measure import Line, MeasurementSet

GEN2 = Planck(
    r1=18453.355,
    r2=0.012509181,
    b=1460.6,
    f=1.0,
    o=-1731.0,
    trust=Trust.CAMERA,
    source="IMG_3888.JPG",
    serial="F02F9T00570",
)


@pytest.fixture
def field():
    temps = np.full((100, 120), 20.0)
    temps[50, 40:80] = 75.0
    return temps


def data_rows(text: str) -> list[dict]:
    body = "\n".join(ln for ln in text.splitlines() if not ln.startswith("#"))
    return list(csv.DictReader(io.StringIO(body)))


def test_one_row_per_sample(field):
    line = Line(0, 50, 119, 50)
    rows = data_rows(profile_csv(line, field))
    assert len(rows) == len(line.samples(field)[1])


def test_columns_are_pixel_and_temperature(field):
    rows = data_rows(profile_csv(Line(0, 50, 119, 50), field))
    assert list(rows[0]) == ["index", "distance_px", "x_px", "y_px", "temperature_c"]


def test_temperatures_match_the_measurement(field):
    line = Line(0, 50, 119, 50)
    rows = data_rows(profile_csv(line, field))
    exported = np.array([float(r["temperature_c"]) for r in rows])
    _, values = line.samples(field)
    assert np.allclose(exported, values, atol=1e-3)


def test_pixel_coordinates_index_back_into_the_image(field):
    """Each row's x,y must actually address the temperature it reports."""
    rows = data_rows(profile_csv(Line(0, 50, 119, 50), field))
    for row in rows:
        x, y = int(row["x_px"]), int(row["y_px"])
        assert field[y, x] == pytest.approx(float(row["temperature_c"]), abs=1e-3)


def test_distance_starts_at_zero_and_increases(field):
    rows = data_rows(profile_csv(Line(0, 50, 119, 50), field))
    distances = [float(r["distance_px"]) for r in rows]
    assert distances[0] == 0.0
    assert all(b > a for a, b in zip(distances[:-1], distances[1:], strict=True))


def test_header_carries_calibration_provenance(field):
    text = profile_csv(Line(0, 50, 119, 50), field, GEN2, Conditions(), "L1")
    assert "IMG_3888.JPG" in text
    assert "emissivity" in text


def test_uncalibrated_export_is_marked(field):
    untrusted = Planck(1.0, 1.0, 1.0, 1.0, 0.0, trust=Trust.MISMATCH, source="other camera")
    text = profile_csv(Line(0, 50, 119, 50), field, untrusted, Conditions())
    assert "WARNING" in text
    assert "not absolute" in text


def test_comment_lines_never_collide_with_data(field):
    text = profile_csv(Line(0, 50, 119, 50), field, GEN2, Conditions())
    widths = {len(ln.split(",")) for ln in text.splitlines() if ln and not ln.startswith("#")}
    assert widths == {5}


def test_diagonal_line_exports(field):
    rows = data_rows(profile_csv(Line(0, 0, 119, 99), field))
    assert len(rows) > 100


def test_save_profiles_writes_one_file_per_line(field, tmp_path):
    measurements = MeasurementSet()
    measurements.lines += [Line(0, 50, 119, 50, label="bed"), Line(60, 0, 60, 99, label="column")]
    written = save_profiles(tmp_path, measurements, field, GEN2, Conditions())
    assert {p.name for p in written} == {"profile_bed.csv", "profile_column.csv"}
    assert all(p.read_text().count("\n") > 50 for p in written)


def test_save_profiles_with_no_lines_writes_nothing(field, tmp_path):
    assert save_profiles(tmp_path, MeasurementSet(), field) == []


class TestAxisMaxLabels:
    """The far-end axis label when several lines of different lengths are drawn."""

    @staticmethod
    def parts(maxima):
        from flirone.ui.profileplot import axis_max_parts

        return axis_max_parts(maxima)

    def test_no_lines_gives_nothing(self):
        assert self.parts([]) == []

    def test_single_line_is_one_muted_label(self):
        assert self.parts([479.0]) == [("479 px", None)]

    def test_equal_lengths_collapse_to_one_label(self):
        """Three lines of the same length should not be written out three times."""
        assert self.parts([200.0, 200.0, 200.0]) == [("200 px", None)]

    def test_differing_lengths_are_named_per_series(self):
        parts = self.parts([479.0, 256.0, 57.0])
        numbers = [(text, idx) for text, idx in parts if idx is not None]
        assert numbers == [("479", 0), ("256", 1), ("57", 2)]

    def test_separators_and_suffix_are_muted(self):
        parts = self.parts([479.0, 256.0])
        muted = [text for text, idx in parts if idx is None]
        assert muted == [" / ", " px"]

    def test_series_index_matches_draw_order(self):
        """The colour index must track position, so it matches the legend."""
        parts = self.parts([10.0, 20.0, 30.0, 40.0])
        assert [idx for _t, idx in parts if idx is not None] == [0, 1, 2, 3]

    def test_near_equal_lengths_collapse(self):
        """Rounding to whole pixels, 200.2 and 199.8 are the same length."""
        assert self.parts([200.2, 199.8]) == [("200 px", None)]


def test_tracked_extremes_label_below_their_marker():
    """Guard a flag that a later rewrite silently dropped.

    Hot and cold markers must pass below=True so their labels clear a user spot
    sitting on the same feature. Losing it puts both labels in the same place,
    which is invisible to the linter and to every other test.
    """
    from pathlib import Path

    source = Path("src/flirone/ui/imageview.py").read_text()
    for tag in ('"max"', '"min"'):
        call = source[
            source.index(f"_HOT, {tag}") if tag == '"max"' else source.index(f"_COLD, {tag}") :
        ][:80]
        assert "below=True" in call, f"{tag} marker no longer labels below its marker"
