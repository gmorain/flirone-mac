"""Edge registration, against synthetic scenes with a known answer."""

from __future__ import annotations

import numpy as np
import pytest

from flirone import registration as reg


def scene(height=240, width=180, seed=0):
    """A thermal field with aperiodic structure in both orientations."""
    rng = np.random.default_rng(seed)
    field = np.full((height, width), 22.0)
    for _ in range(14):
        h = int(rng.integers(12, 40))
        w = int(rng.integers(12, 40))
        y = int(rng.integers(0, height - h))
        x = int(rng.integers(0, width - w))
        field[y : y + h, x : x + w] += rng.uniform(15, 60)
    return field


def visible_from(field, scale=1.0, dx=0, dy=0):
    """Render the same structure as an RGB frame, shifted and scaled."""
    from PIL import Image

    height, width = field.shape
    normalised = (field - field.min()) / max(np.ptp(field), 1e-9)
    grey = (normalised * 255).astype(np.uint8)
    shifted = np.roll(np.roll(grey, -dy, axis=0), -dx, axis=1)
    image = Image.fromarray(shifted).convert("RGB")
    target = (int(round(width / scale)), int(round(height / scale)))
    return np.asarray(image.resize(target, Image.Resampling.BILINEAR))


def test_contrast_separates_real_scenes_from_flat_ones():
    assert reg.contrast(scene()).usable
    flat = np.full((240, 180), 22.0) + np.random.default_rng(1).normal(0, 0.05, (240, 180))
    assert not reg.contrast(flat).usable


def test_flat_scene_is_refused_rather_than_guessed():
    flat = np.full((240, 180), 22.0) + np.random.default_rng(2).normal(0, 0.05, (240, 180))
    with pytest.raises(reg.InsufficientContrast):
        reg.estimate(flat, visible_from(flat))


@pytest.mark.parametrize("dx,dy", [(0, 0), (6, -9), (-11, 7)])
def test_recovers_a_known_shift(dx, dy):
    field = scene()
    visible = visible_from(field, scale=1.0, dx=dx, dy=dy)
    result = reg.estimate(field, visible, scale_prior=1.0)
    assert result.dx == pytest.approx(dx, abs=1.5)
    assert result.dy == pytest.approx(dy, abs=1.5)


def test_scale_prior_narrows_the_search():
    field = scene()
    visible = visible_from(field, scale=1.0, dx=3, dy=-4)
    result = reg.estimate(field, visible, scale_prior=1.0)
    # Only refined around the prior, never off to the sweep boundary.
    assert 0.96 <= result.scale <= 1.04


def test_score_peaks_at_the_true_offset():
    field = scene()
    edges = reg._gradient_magnitude(field)
    shifted = reg._gradient_magnitude(
        reg._resample_visible(visible_from(field, 1.0, 8, -5), field.shape, 1.0)
    )
    best = reg.score(edges, shifted, 8, -5)
    for wrong in ((0, 0), (20, -5), (8, 15)):
        assert reg.score(edges, shifted, *wrong) < best


def test_no_visible_image_is_an_error():
    with pytest.raises(ValueError):
        reg.estimate(scene(), np.array([]))
