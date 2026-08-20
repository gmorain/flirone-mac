"""Measurement tools over a calibrated temperature field.

Every tool takes a (h, w) float array of degrees Celsius and returns plain
dataclasses, so the UI and any batch analysis share the same code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Stats:
    """Summary of a set of temperature samples."""

    count: int
    minimum: float
    maximum: float
    mean: float
    median: float
    stddev: float
    min_at: tuple[int, int]  # (x, y)
    max_at: tuple[int, int]

    def format(self, unit: str = "°C") -> str:
        """Compact min / mean / max, sized for a narrow readout column."""
        return (
            f"{self.minimum:.1f} / {self.mean:.1f} / {self.maximum:.1f} {unit}"
            f"  \u03c3{self.stddev:.1f}"
        )


def _stats_from(window: np.ndarray, origin: tuple[int, int]) -> Stats:
    finite = np.isfinite(window)
    if not finite.any():
        nan = float("nan")
        return Stats(0, nan, nan, nan, nan, nan, (-1, -1), (-1, -1))
    masked = np.where(finite, window, np.nan)
    flat_min = int(np.nanargmin(masked))
    flat_max = int(np.nanargmax(masked))
    h, w = window.shape
    ox, oy = origin
    return Stats(
        count=int(finite.sum()),
        minimum=float(np.nanmin(masked)),
        maximum=float(np.nanmax(masked)),
        mean=float(np.nanmean(masked)),
        median=float(np.nanmedian(masked)),
        stddev=float(np.nanstd(masked)),
        min_at=(ox + flat_min % w, oy + flat_min // w),
        max_at=(ox + flat_max % w, oy + flat_max // w),
    )


@dataclass
class Spot:
    """A point measurement, averaged over a small window to suppress noise."""

    x: int
    y: int
    radius: int = 1
    label: str = ""

    def measure(self, temps: np.ndarray) -> Stats:
        h, w = temps.shape
        x0 = max(0, self.x - self.radius)
        y0 = max(0, self.y - self.radius)
        x1 = min(w, self.x + self.radius + 1)
        y1 = min(h, self.y + self.radius + 1)
        return _stats_from(temps[y0:y1, x0:x1], (x0, y0))


@dataclass
class Box:
    """A rectangular region. Coordinates are inclusive of x0,y0 and exclusive of x1,y1."""

    x0: int
    y0: int
    x1: int
    y1: int
    label: str = ""

    def normalised(self, shape: tuple[int, int]) -> tuple[int, int, int, int]:
        h, w = shape
        x0, x1 = sorted((self.x0, self.x1))
        y0, y1 = sorted((self.y0, self.y1))
        return (
            max(0, min(x0, w - 1)),
            max(0, min(y0, h - 1)),
            max(1, min(x1, w)),
            max(1, min(y1, h)),
        )

    def measure(self, temps: np.ndarray) -> Stats:
        x0, y0, x1, y1 = self.normalised(temps.shape)
        return _stats_from(temps[y0:y1, x0:x1], (x0, y0))


@dataclass
class Line:
    """A line profile between two points."""

    x0: int
    y0: int
    x1: int
    y1: int
    label: str = ""

    def samples(self, temps: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (distance_px, temperature) sampled at one point per pixel step."""
        n = max(int(round(np.hypot(self.x1 - self.x0, self.y1 - self.y0))), 1) + 1
        xs = np.linspace(self.x0, self.x1, n)
        ys = np.linspace(self.y0, self.y1, n)
        h, w = temps.shape
        xi = np.clip(np.round(xs).astype(int), 0, w - 1)
        yi = np.clip(np.round(ys).astype(int), 0, h - 1)
        distance = np.hypot(xs - self.x0, ys - self.y0)
        return distance, temps[yi, xi]

    def measure(self, temps: np.ndarray) -> Stats:
        _, values = self.samples(temps)
        return _stats_from(values.reshape(1, -1), (0, 0))


Region = Spot | Box | Line


@dataclass
class Delta:
    """Difference between two regions, the usual way thermal faults are called."""

    a: Region
    b: Region
    label: str = ""

    def measure(self, temps: np.ndarray) -> float:
        return self.a.measure(temps).mean - self.b.measure(temps).mean


def hotspot(temps: np.ndarray) -> tuple[int, int, float]:
    """Location and value of the hottest pixel."""
    idx = int(np.nanargmax(np.where(np.isfinite(temps), temps, -np.inf)))
    h, w = temps.shape
    return idx % w, idx // w, float(temps.flat[idx])


def coldspot(temps: np.ndarray) -> tuple[int, int, float]:
    idx = int(np.nanargmin(np.where(np.isfinite(temps), temps, np.inf)))
    h, w = temps.shape
    return idx % w, idx // w, float(temps.flat[idx])


@dataclass
class Isotherm:
    """Highlight pixels inside, above or below a temperature band."""

    mode: str = "above"  # above | below | between
    low: float = 0.0
    high: float = 100.0
    colour: tuple[int, int, int] = (255, 0, 0)
    enabled: bool = False

    def mask(self, temps: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            if self.mode == "above":
                m = temps >= self.low
            elif self.mode == "below":
                m = temps <= self.high
            else:
                m = (temps >= self.low) & (temps <= self.high)
        return np.where(np.isfinite(temps), m, False)


@dataclass
class MeasurementSet:
    """Everything the user has drawn on the current scene."""

    spots: list[Spot] = field(default_factory=list)
    boxes: list[Box] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    deltas: list[Delta] = field(default_factory=list)
    isotherm: Isotherm = field(default_factory=Isotherm)
    track_hotspot: bool = True
    track_coldspot: bool = False

    def clear(self) -> None:
        self.spots.clear()
        self.boxes.clear()
        self.lines.clear()
        self.deltas.clear()

    def summarise(self, temps: np.ndarray) -> list[tuple[str, str]]:
        """Rows of (label, formatted value) for the readout panel."""
        rows: list[tuple[str, str]] = []
        for i, spot in enumerate(self.spots, 1):
            s = spot.measure(temps)
            rows.append((spot.label or f"Spot {i}", f"{s.mean:.1f} °C"))
        for i, box in enumerate(self.boxes, 1):
            s = box.measure(temps)
            rows.append((box.label or f"Box {i}", s.format()))
        for i, line in enumerate(self.lines, 1):
            s = line.measure(temps)
            rows.append((line.label or f"Line {i}", s.format()))
        for i, delta in enumerate(self.deltas, 1):
            rows.append((delta.label or f"Delta {i}", f"{delta.measure(temps):+.1f} K"))
        if self.track_hotspot:
            x, y, v = hotspot(temps)
            rows.append(("Hotspot", f"{v:.1f} °C at ({x}, {y})"))
        if self.track_coldspot:
            x, y, v = coldspot(temps)
            rows.append(("Coldspot", f"{v:.1f} °C at ({x}, {y})"))
        return rows
