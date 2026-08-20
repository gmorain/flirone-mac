"""Combine the thermal and visible images into what gets displayed.

The two cameras sit a couple of centimetres apart, so their images differ by a
parallax shift that changes with distance. There is no factory alignment table
available over USB, so the offset and scale are exposed as user controls rather
than pretended away.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from .alignment import Alignment
from .palettes import apply_palette, normalise

MODE_THERMAL = "Thermal"
MODE_VISIBLE = "Visible"
MODE_BLEND = "Blend"
MODE_EDGES = "Thermal + edges"
MODES = (MODE_THERMAL, MODE_VISIBLE, MODE_BLEND, MODE_EDGES)


def _resample(
    image: np.ndarray, size: tuple[int, int], align: Alignment, upscale: int = 1
) -> np.ndarray:
    """Scale and shift the visible image onto the output grid.

    Offsets arrive in thermal pixels, so they are scaled by the same factor the
    output grid was upscaled by.
    """
    width, height = size
    pil = Image.fromarray(image)
    target = (max(int(width * align.scale), 1), max(int(height * align.scale), 1))
    pil = pil.resize(target, Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    canvas.paste(
        pil,
        (
            int((width - target[0]) / 2 + align.dx * upscale),
            int((height - target[1]) / 2 + align.dy * upscale),
        ),
    )
    return np.asarray(canvas)


def _edges(gray: np.ndarray) -> np.ndarray:
    """Sobel magnitude, normalised to 0..1."""
    g = gray.astype(np.float32)
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    mag = np.hypot(gx, gy)
    peak = float(mag.max())
    return mag / peak if peak > 0 else mag


def compose(
    temps: np.ndarray,
    visible: np.ndarray | None,
    mode: str,
    palette: str,
    vmin: float | None = None,
    vmax: float | None = None,
    blend: float = 0.5,
    align: Alignment | None = None,
    upscale: int | None = None,
) -> np.ndarray:
    """Produce the RGB image to display."""
    align = align or Alignment()
    thermal_rgb = apply_palette(normalise(temps, vmin, vmax), palette)

    if mode == MODE_VISIBLE and visible is not None:
        return visible
    if mode == MODE_THERMAL or visible is None:
        return thermal_rgb

    # Work at the visible camera's resolution so its detail survives.
    height, width = temps.shape
    if upscale is None:
        # Work near the visible camera's resolution without needlessly
        # enlarging a thermal plane the phone app already upscaled.
        upscale = max(1, min(4, round(visible.shape[1] / width)))
    out_size = (width * upscale, height * upscale)
    thermal_big = np.asarray(
        Image.fromarray(thermal_rgb).resize(out_size, Image.Resampling.BILINEAR)
    )
    visible_aligned = _resample(visible, out_size, align, upscale)

    if mode == MODE_BLEND:
        a = float(np.clip(blend, 0.0, 1.0))
        return (thermal_big * a + visible_aligned * (1.0 - a)).astype(np.uint8)

    if mode == MODE_EDGES:
        gray = visible_aligned.astype(np.float32).mean(axis=2)
        strength = _edges(gray)[..., None] * float(np.clip(blend, 0.0, 1.0))
        # Burn the visible structure in as dark contours over the thermal image.
        return np.clip(thermal_big * (1.0 - strength), 0, 255).astype(np.uint8)

    return thermal_rgb
