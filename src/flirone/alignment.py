"""How the visible image is placed onto the thermal one.

Its own module because both the renderer and the registration code need it, and
the file reader needs it too: leaving it in the renderer made reading metadata
depend on drawing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Alignment:
    """Parallax correction between the visible and thermal cameras.

    Offsets are in **thermal pixels** and are fractional: sub-pixel refinement
    exists precisely to produce fractions, and rounding them here would discard
    it. This is the same convention FLIR records in EXIF, once its
    visible-pixel offsets have been converted, so the recorded and the measured
    alignment are directly comparable.
    """

    scale: float = 1.0
    dx: float = 0.0
    dy: float = 0.0

    def __str__(self) -> str:
        return f"scale {self.scale:.4f}  dx {self.dx:+.1f}  dy {self.dy:+.1f}"
