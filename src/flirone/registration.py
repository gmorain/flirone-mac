"""Estimating the alignment between the thermal and visible images.

The two cameras sit a short distance apart, so the visible image must be scaled
and shifted to sit on top of the thermal one. Radiometric JPEGs record the
alignment the camera itself used (Real2IR, OffsetX, OffsetY) and that is exact,
so it is preferred. This module recovers the same numbers from image content
alone, for the live stream and for checking the recorded values.

The method is edge-based: thermal and visible images share almost no intensity
relationship, but they do share structure, so registration is done on gradient
magnitude rather than on the images themselves. Translation is found by phase
correlation, which is exact and cheap; scale is swept around it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from .alignment import Alignment


class InsufficientContrast(ValueError):
    """The thermal field is too flat to register against."""


# Thresholds from measured frames. A usable scene spans tens of degrees with
# sharp local structure; a room-temperature wall spans under a degree and its
# "edges" are sensor noise, which correlates with anything.
MIN_SPAN_C = 2.0
MIN_LOCAL_GRADIENT = 0.010
WEAK_SPAN_C = 8.0
WEAK_LOCAL_GRADIENT = 0.050


@dataclass
class Contrast:
    """How much real thermal structure a frame carries."""

    span: float  # 1st-99th percentile spread, degrees
    local_gradient: float  # mean absolute neighbour difference, degrees per pixel

    @property
    def usable(self) -> bool:
        return self.span >= MIN_SPAN_C and self.local_gradient >= MIN_LOCAL_GRADIENT

    @property
    def weak(self) -> bool:
        return self.span < WEAK_SPAN_C or self.local_gradient < WEAK_LOCAL_GRADIENT

    def __str__(self) -> str:
        return f"span {self.span:.2f} C, local gradient {self.local_gradient:.4f} C/px"


def contrast(temps: np.ndarray) -> Contrast:
    low, high = np.nanpercentile(temps, (1.0, 99.0))
    gradient = (
        float(np.mean(np.abs(np.diff(temps, axis=1))) + np.mean(np.abs(np.diff(temps, axis=0))))
        / 2.0
    )
    return Contrast(float(high - low), gradient)


@dataclass
class Registration:
    """An alignment recovered from image content, with how well it matched."""

    alignment: Alignment
    confidence: float  # normalised cross-correlation of the edge maps, 0..1

    @property
    def scale(self) -> float:
        return self.alignment.scale

    @property
    def dx(self) -> float:
        return self.alignment.dx

    @property
    def dy(self) -> float:
        return self.alignment.dy

    def __str__(self) -> str:
        return (
            f"scale {self.scale:.4f}  dx {self.dx:+.1f}  dy {self.dy:+.1f}  "
            f"confidence {self.confidence:.3f}"
        )


def _gradient_magnitude(image: np.ndarray) -> np.ndarray:
    """Sobel-like gradient magnitude, mean-removed and variance-normalised."""
    a = image.astype(np.float64)
    if a.ndim == 3:
        a = a.mean(axis=2)
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = a[:, 2:] - a[:, :-2]
    gy[1:-1, :] = a[2:, :] - a[:-2, :]
    mag = np.hypot(gx, gy)
    # Compress the dynamic range: a single very hot edge should not dominate.
    mag = np.log1p(mag)
    mag -= mag.mean()
    std = mag.std()
    return mag / std if std > 0 else mag


def _window(shape: tuple[int, int]) -> np.ndarray:
    """Separable Hann window, to stop frame borders dominating the correlation."""
    h, w = shape
    return np.outer(np.hanning(h), np.hanning(w))


def _phase_correlate(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """Shift of b relative to a, plus a peak-sharpness confidence."""
    win = _window(a.shape)
    fa = np.fft.rfft2(a * win)
    fb = np.fft.rfft2(b * win)
    cross = fa * np.conj(fb)
    magnitude = np.abs(cross)
    cross /= np.where(magnitude > 1e-12, magnitude, 1.0)
    surface = np.fft.irfft2(cross, s=a.shape)

    peak = int(np.argmax(surface))
    py, px = divmod(peak, surface.shape[1])
    value = float(surface[py, px])

    # Sub-pixel refinement by parabolic fit against the neighbouring samples.
    def refine(centre: int, axis: int) -> float:
        size = surface.shape[axis]
        prev = surface[(py - 1) % size, px] if axis == 0 else surface[py, (px - 1) % size]
        nxt = surface[(py + 1) % size, px] if axis == 0 else surface[py, (px + 1) % size]
        denom = prev - 2 * value + nxt
        return centre + (0.5 * (prev - nxt) / denom if abs(denom) > 1e-12 else 0.0)

    fy = refine(py, 0)
    fx = refine(px, 1)
    h, w = surface.shape
    if fy > h / 2:
        fy -= h
    if fx > w / 2:
        fx -= w

    # Confidence: peak height against the background of the correlation surface.
    background = float(np.mean(np.abs(surface)))
    confidence = 0.0 if background <= 0 else min(value / (background * 50.0), 1.0)
    return float(fx), float(fy), confidence


def _resample_visible(visible: np.ndarray, shape: tuple[int, int], scale: float) -> np.ndarray:
    """Scale the visible image about its centre onto the thermal grid."""
    h, w = shape
    target = (max(int(round(w * scale)), 1), max(int(round(h * scale)), 1))
    resized = Image.fromarray(visible).resize(target, Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (w, h), (0, 0, 0))
    canvas.paste(resized, ((w - target[0]) // 2, (h - target[1]) // 2))
    return np.asarray(canvas)


def _shift(image: np.ndarray, dx: float, dy: float) -> tuple[np.ndarray, np.ndarray]:
    """Integer-shift an image, returning it with a validity mask."""
    sx, sy = int(round(dx)), int(round(dy))
    shifted = np.roll(np.roll(image, sy, axis=0), sx, axis=1)
    mask = np.ones(image.shape, dtype=bool)
    if sy > 0:
        mask[:sy, :] = False
    elif sy < 0:
        mask[sy:, :] = False
    if sx > 0:
        mask[:, :sx] = False
    elif sx < 0:
        mask[:, sx:] = False
    return shifted, mask


def score(a: np.ndarray, b: np.ndarray, dx: float, dy: float) -> float:
    """Normalised cross-correlation of two edge maps at a given offset.

    Comparable across candidate scales, which peak height is not: a phase
    correlation peak sharpens as the image loses detail, so selecting on it
    drives the search to the largest scale in the range.
    """
    shifted, mask = _shift(b, dx, dy)
    if mask.sum() < mask.size * 0.25:
        return -1.0
    av = a[mask]
    bv = shifted[mask]
    sa, sb = av.std(), bv.std()
    if sa <= 0 or sb <= 0:
        return -1.0
    return float(np.mean((av - av.mean()) * (bv - bv.mean())) / (sa * sb))


def estimate(
    temps: np.ndarray,
    visible: np.ndarray,
    scales: np.ndarray | None = None,
    refine: bool = True,
    scale_prior: float | None = None,
) -> Registration:
    """Recover scale and offset by matching edges between the two images.

    `temps` is the thermal field and `visible` the RGB frame. Offsets are
    returned in thermal pixels, matching the convention FLIR records in EXIF:
    positive dx shifts the visible image right relative to the thermal one.
    """
    if visible is None or visible.size == 0:
        raise ValueError("no visible image to register against")

    quality = contrast(temps)
    if not quality.usable:
        raise InsufficientContrast(
            f"thermal field is too flat to register ({quality}). Registration "
            "matches structure, so the scene needs real thermal relief rather "
            "than a room-temperature surface."
        )

    thermal_edges = _gradient_magnitude(temps)

    def search(candidates: np.ndarray) -> Registration:
        best = Registration(Alignment(), -1.0)
        for scale in candidates:
            edges = _gradient_magnitude(_resample_visible(visible, temps.shape, float(scale)))
            dx, dy, _ = _phase_correlate(thermal_edges, edges)
            quality = score(thermal_edges, edges, dx, dy)
            if quality > best.confidence:
                best = Registration(Alignment(float(scale), dx, dy), quality)
        return best

    if scales is None:
        if scale_prior is not None:
            # Scale is a fixed ratio of the two fields of view. When the file
            # states it there is nothing to search, only to refine: sweeping
            # wide lets the optimiser walk to the range boundary whenever the
            # true optimum is shallow.
            scales = np.arange(scale_prior * 0.97, scale_prior * 1.03, 0.004)
        else:
            scales = np.arange(1.00, 1.60, 0.02)

    coarse = search(scales)
    if not refine:
        return coarse
    fine = search(np.arange(coarse.scale - 0.02, coarse.scale + 0.021, 0.005))
    return fine if fine.confidence >= coarse.confidence else coarse
