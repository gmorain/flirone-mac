"""The parallax model and its calibration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flirone.distance import ParallaxModel, calibrate, estimate

TRUE_OFFSET, TRUE_K = 20.0, 5.58


def observed(distance: float) -> float:
    return TRUE_OFFSET + TRUE_K / distance


def test_two_points_recover_the_model_exactly():
    model = calibrate([(0.3, observed(0.3)), (2.0, observed(2.0))])
    assert model.dy_inf == pytest.approx(TRUE_OFFSET)
    assert model.k == pytest.approx(TRUE_K)


def test_least_squares_over_repeats():
    samples = [(z, observed(z)) for z in (0.3, 0.3, 0.5, 2.0, 2.0)]
    model = calibrate(samples)
    assert model.k == pytest.approx(TRUE_K, rel=1e-6)
    assert model.samples == 5


def test_distance_round_trips():
    model = calibrate([(0.3, observed(0.3)), (2.0, observed(2.0))])
    for z in (0.25, 0.5, 1.0, 3.0):
        assert model.distance_at(observed(z)) == pytest.approx(z, rel=1e-6)


def test_uncertainty_grows_quadratically():
    model = ParallaxModel(TRUE_OFFSET, TRUE_K)
    near = model.uncertainty_at(1.0)
    far = model.uncertainty_at(2.0)
    assert far == pytest.approx(near * 4, rel=1e-6)


def test_offset_at_infinity_is_the_boresight():
    model = ParallaxModel(TRUE_OFFSET, TRUE_K)
    assert model.offset_at(1e9) == pytest.approx(TRUE_OFFSET, abs=1e-6)


def test_unphysical_offset_gives_no_distance():
    model = ParallaxModel(TRUE_OFFSET, TRUE_K)
    # An offset below the boresight implies a negative distance.
    assert model.distance_at(TRUE_OFFSET - 5) is None
    assert estimate(model, TRUE_OFFSET - 5) is None


def test_single_sample_is_refused():
    with pytest.raises(ValueError):
        calibrate([(1.0, 25.0)])


def test_non_physical_model_is_rejected_on_load(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"dy_inf": 71.1, "k": -16.5}))
    with pytest.raises(ValueError, match="non-physical"):
        ParallaxModel.from_json(path)


def test_legacy_key_still_loads(tmp_path: Path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"dx_inf": 20.0, "k": 5.58}))
    assert ParallaxModel.from_json(path).dy_inf == pytest.approx(20.0)


def test_very_uncertain_estimates_say_so():
    model = ParallaxModel(TRUE_OFFSET, TRUE_K)
    assert "uncertain" in estimate(model, observed(20.0)).format()
    assert "±" in estimate(model, observed(1.0)).format()
