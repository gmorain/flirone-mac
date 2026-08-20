"""Estimating subject distance from the thermal/visible parallax.

The two lenses are a few millimetres apart, so a point at distance Z appears
displaced between them by

    dx(Z) = dy_inf + K / Z

where dy_inf is the fixed boresight offset (the disparity at infinity) and
K = f * b, the focal length in pixels times the baseline in metres.

One image cannot give Z, because a single measured dx has two unknowns behind
it. Two shots at known distances pin dy_inf and K, and every later image then
yields a distance from its measured offset alone.

Precision degrades quadratically: dZ = Z^2 * sigma_dx / K. With a baseline of a
few millimetres this is useful up close and worthless across a room, so
`estimate` returns an uncertainty and refuses to pretend otherwise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class ParallaxModel:
    """Calibrated relationship between horizontal offset and distance."""

    dy_inf: float  # offset at infinity, thermal pixels, on the parallax axis
    k: float  # f_px * baseline_m, pixel-metres
    residual: float = 0.0  # RMS fit residual, thermal pixels
    samples: int = 0

    def offset_at(self, distance_m: float) -> float:
        return self.dy_inf + self.k / max(distance_m, 1e-6)

    def distance_at(self, dx: float) -> float | None:
        """Distance implied by a measured offset, or None if unphysical."""
        denominator = dx - self.dy_inf
        if self.k == 0 or denominator == 0:
            return None
        distance = self.k / denominator
        return distance if distance > 0 else None

    def uncertainty_at(self, distance_m: float, sigma_dx: float = 0.3) -> float:
        """Standard error of a distance estimate, in metres."""
        if self.k == 0:
            return float("inf")
        return distance_m**2 * sigma_dx / abs(self.k)

    def to_json(self, path: Path) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "dy_inf": self.dy_inf,
                    "k": self.k,
                    "residual": self.residual,
                    "samples": self.samples,
                },
                indent=2,
            )
        )

    @property
    def physical(self) -> bool:
        """K is focal length times baseline, so it cannot be zero or negative."""
        return self.k > 0

    @classmethod
    def from_json(cls, path: Path) -> ParallaxModel:
        data = json.loads(Path(path).read_text())
        # "dx_inf" is the old spelling, from before the parallax axis was known.
        offset = data.get("dy_inf", data.get("dx_inf"))
        model = cls(
            float(offset),
            float(data["k"]),
            float(data.get("residual", 0.0)),
            int(data.get("samples", 0)),
        )
        if not model.physical:
            raise ValueError(
                f"stored parallax model is non-physical (K={model.k:.3f}); recalibrate"
            )
        return model


def calibrate(samples: list[tuple[float, float]]) -> ParallaxModel:
    """Fit the model to (distance_m, measured_dx) pairs.

    Two pairs are the minimum; more are fitted by least squares. Spread the
    distances widely, since the model is linear in 1/Z and two nearby distances
    barely constrain it.
    """
    if len(samples) < 2:
        raise ValueError("need at least two (distance, offset) samples")
    distances = np.array([s[0] for s in samples], dtype=float)
    offsets = np.array([s[1] for s in samples], dtype=float)
    if np.any(distances <= 0):
        raise ValueError("distances must be positive")

    design = np.column_stack([np.ones_like(distances), 1.0 / distances])
    (dy_inf, k), *_ = np.linalg.lstsq(design, offsets, rcond=None)
    residual = float(np.sqrt(np.mean((design @ [dy_inf, k] - offsets) ** 2)))
    return ParallaxModel(float(dy_inf), float(k), residual, len(samples))


@dataclass(frozen=True)
class DistanceEstimate:
    metres: float
    sigma: float

    def format(self) -> str:
        if not np.isfinite(self.metres):
            return "unknown"
        relative = self.sigma / self.metres if self.metres else float("inf")
        if relative > 0.5:
            return f"~{self.metres:.1f} m (very uncertain)"
        return f"{self.metres:.2f} ± {self.sigma:.2f} m"


def estimate(model: ParallaxModel, dx: float, sigma_dx: float = 0.3) -> DistanceEstimate | None:
    distance = model.distance_at(dx)
    if distance is None:
        return None
    return DistanceEstimate(distance, model.uncertainty_at(distance, sigma_dx))
