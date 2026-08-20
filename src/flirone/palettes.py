"""Colour palettes for thermal rendering.

Generated from control points rather than shipped as binary LUTs, so the set is
readable and easy to extend. Each palette is a (256, 3) uint8 array.
"""

from __future__ import annotations

import numpy as np

_CONTROL_POINTS: dict[str, list[tuple[float, tuple[int, int, int]]]] = {
    "Grey": [(0.0, (0, 0, 0)), (1.0, (255, 255, 255))],
    "Grey inverted": [(0.0, (255, 255, 255)), (1.0, (0, 0, 0))],
    "Iron": [
        (0.00, (0, 0, 0)),
        (0.15, (40, 0, 90)),
        (0.30, (120, 0, 130)),
        (0.50, (200, 30, 90)),
        (0.70, (245, 110, 20)),
        (0.85, (255, 190, 0)),
        (1.00, (255, 255, 220)),
    ],
    "Rainbow": [
        (0.00, (0, 0, 90)),
        (0.20, (0, 90, 220)),
        (0.40, (0, 200, 190)),
        (0.60, (120, 220, 60)),
        (0.80, (255, 200, 0)),
        (1.00, (200, 0, 0)),
    ],
    "Lava": [
        (0.00, (0, 0, 0)),
        (0.35, (110, 20, 10)),
        (0.65, (220, 90, 10)),
        (0.85, (255, 180, 40)),
        (1.00, (255, 255, 255)),
    ],
    "Arctic": [
        (0.00, (0, 0, 40)),
        (0.35, (0, 80, 160)),
        (0.60, (120, 190, 230)),
        (0.80, (240, 240, 150)),
        (1.00, (255, 120, 40)),
    ],
}


def _build(points: list[tuple[float, tuple[int, int, int]]]) -> np.ndarray:
    xs = np.linspace(0.0, 1.0, 256)
    stops = np.array([p[0] for p in points])
    lut = np.empty((256, 3), dtype=np.uint8)
    for channel in range(3):
        values = np.array([p[1][channel] for p in points], dtype=float)
        lut[:, channel] = np.interp(xs, stops, values).round().astype(np.uint8)
    return lut


PALETTES: dict[str, np.ndarray] = {name: _build(pts) for name, pts in _CONTROL_POINTS.items()}
DEFAULT_PALETTE = "Iron"


def apply_palette(normalised: np.ndarray, name: str = DEFAULT_PALETTE) -> np.ndarray:
    """Map a 0..1 float array to RGB using the named palette."""
    lut = PALETTES.get(name, PALETTES[DEFAULT_PALETTE])
    idx = np.clip(np.nan_to_num(normalised, nan=0.0) * 255.0, 0, 255).astype(np.uint8)
    return lut[idx]


def normalise(
    values: np.ndarray, vmin: float | None = None, vmax: float | None = None
) -> np.ndarray:
    """Scale to 0..1, ignoring NaNs. Falls back to a flat field if degenerate."""
    finite = np.isfinite(values)
    if not finite.any():
        return np.zeros_like(values, dtype=float)
    lo = float(np.nanmin(values)) if vmin is None else vmin
    hi = float(np.nanmax(values)) if vmax is None else vmax
    if hi <= lo:
        return np.zeros_like(values, dtype=float)
    return np.clip((values - lo) / (hi - lo), 0.0, 1.0)
