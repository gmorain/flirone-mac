"""Radiometric calibration: sensor counts to temperature.

The FLIR One streams 16-bit counts proportional to received radiance, not
temperature. Converting them needs the camera's Planck constants, which are
per-unit and are NOT carried in the USB stream. Get them from the camera's own
CameraFiles.zip (see camerafiles.py) or with exiftool from one radiometric JPEG
shot with the phone app:

    exiftool -Planck* shot.jpg

Using another camera's constants produces numbers that look plausible and are
wrong, so DEFAULT_PLANCK is deliberately marked untrusted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum

import numpy as np

ABSOLUTE_ZERO_C = -273.15


# Serials are not written the same way everywhere: EXIF reports F02F9T00570
# while the accessory handshake reports FLIRONEF02F9T00570A, the same unit with
# a prefix and a suffix. So containment, not equality.
MIN_SERIAL_LENGTH = 6


def serials_match(a: str | None, b: str | None) -> bool:
    """Whether two serial numbers identify the same camera."""
    if not a or not b:
        return False
    left = "".join(c for c in a.upper() if c.isalnum())
    right = "".join(c for c in b.upper() if c.isalnum())
    if len(left) < MIN_SERIAL_LENGTH or len(right) < MIN_SERIAL_LENGTH:
        return False
    return left in right or right in left


class Trust(StrEnum):
    """Where constants came from, relative to the data being measured.

    A boolean is not enough here. "Not from this camera" and "cannot tell"
    are different claims, and conflating them either overstates confidence or
    cries wolf on the ordinary case of calibrating a live camera.
    """

    CAMERA = "camera"  # read from the same file, or the serial matches
    UNVERIFIED = "unverified"  # adopted, and the serials cannot be compared
    MISMATCH = "mismatch"  # adopted from a demonstrably different camera
    REFERENCE = "reference"  # built-in constants from someone else's unit

    @property
    def absolute(self) -> bool:
        """Whether readings may be presented as absolute temperatures."""
        return self is Trust.CAMERA

    def describe(self) -> str:
        return {
            Trust.CAMERA: "this camera's own constants",
            Trust.UNVERIFIED: "adopted constants, camera not verified",
            Trust.MISMATCH: "constants from a DIFFERENT camera",
            Trust.REFERENCE: "reference constants from another unit",
        }[self]


@dataclass(frozen=True)
class Planck:
    """Per-camera radiometric constants, from FLIR EXIF tags of the same name.

    These are calibrated per unit at the factory, so constants from one camera
    give wrong temperatures on another. `serial` records which camera they came
    from so that can be checked rather than assumed.
    """

    r1: float
    r2: float
    b: float
    f: float
    o: float
    trust: Trust = Trust.UNVERIFIED
    source: str = "unknown"
    serial: str | None = None

    @property
    def trusted(self) -> bool:
        """True only when these constants belong to the camera being measured."""
        return self.trust.absolute

    def validated(self) -> Planck:
        """Reject constants that cannot describe a real sensor."""
        if not (self.r1 > 0 and self.r2 > 0 and self.b > 0):
            raise ValueError(
                f"non-physical Planck constants (R1={self.r1}, R2={self.r2}, B={self.b}); "
                "all three must be positive"
            )
        return self

    def adopted_for(self, target_serial: str | None) -> Planck:
        """Re-label these constants for the camera they are about to measure.

        Constants read from a file describe the camera that wrote it. Applying
        them to anything else is only sound when the serials agree, and that is
        a comparison, not an assumption.
        """
        if self.serial and target_serial:
            trust = Trust.CAMERA if serials_match(self.serial, target_serial) else Trust.MISMATCH
        else:
            trust = Trust.UNVERIFIED
        return replace(self, trust=trust)


# Constants published with the fnoop/flirone-v4l2 reference driver, read off one
# specific Gen 2 unit. Fine for relative thermography, not for absolute numbers.
DEFAULT_PLANCK = Planck(
    r1=16528.178,
    r2=0.012258549,
    b=1427.5,
    f=1.0,
    o=-1307.0,
    trust=Trust.REFERENCE,
    source="fnoop/flirone-v4l2 reference unit",
)


@dataclass(frozen=True)
class Conditions:
    """Measurement conditions applied on top of the Planck constants."""

    emissivity: float = 0.95
    reflected_c: float = 20.0  # reflected apparent temperature
    atmospheric_c: float = 20.0  # air temperature along the path
    humidity: float = 0.50  # relative, 0..1
    distance_m: float = 1.0

    # Atmospheric transmission coefficients. These are the standard FLIR
    # values, but radiometric JPEGs carry their own, so they are overridable.
    atm_alpha1: float = 0.006569
    atm_alpha2: float = 0.012620
    atm_beta1: float = -0.002276
    atm_beta2: float = -0.006670
    atm_x: float = 1.9

    def validated(self) -> Conditions:
        return replace(
            self,
            emissivity=min(max(self.emissivity, 0.01), 1.0),
            humidity=min(max(self.humidity, 0.0), 1.0),
            distance_m=max(self.distance_m, 0.0),
        )


def atmospheric_transmission(conditions: Conditions) -> float:
    """Path transmission tau for the given air temperature, humidity and range."""
    if conditions.distance_m <= 0:
        return 1.0
    t = conditions.atmospheric_c
    # Partial water vapour pressure, FLIR's empirical polynomial.
    omega = conditions.humidity * math.exp(
        1.5587 + 0.06939 * t - 0.00027816 * t**2 + 0.00000068455 * t**3
    )
    sqrt_d = math.sqrt(conditions.distance_m)
    sqrt_w = math.sqrt(omega)
    x = conditions.atm_x
    tau = x * math.exp(-sqrt_d * (conditions.atm_alpha1 + conditions.atm_beta1 * sqrt_w)) + (
        1 - x
    ) * math.exp(-sqrt_d * (conditions.atm_alpha2 + conditions.atm_beta2 * sqrt_w))
    return min(max(tau, 1e-3), 1.0)


def temperature_to_raw(temp_c: float, planck: Planck) -> float:
    """Inverse Planck: the raw count a blackbody at temp_c would produce."""
    return (
        planck.r1 / (planck.r2 * (math.exp(planck.b / (temp_c - ABSOLUTE_ZERO_C)) - planck.f))
        - planck.o
    )


def raw_to_celsius(
    raw: np.ndarray,
    planck: Planck = DEFAULT_PLANCK,
    conditions: Conditions | None = None,
) -> np.ndarray:
    """Convert raw sensor counts to degrees Celsius.

    Applies the full FLIR object-radiance correction: the measured signal is the
    sum of object emission, reflected ambient radiation and atmospheric
    self-emission, so those last two are subtracted before inverting Planck.
    """
    conditions = (conditions or Conditions()).validated()
    eps = conditions.emissivity
    tau = atmospheric_transmission(conditions)

    raw_reflected = temperature_to_raw(conditions.reflected_c, planck)
    raw_atmosphere = temperature_to_raw(conditions.atmospheric_c, planck)

    raw_f = np.asarray(raw, dtype=np.float64)
    raw_object = (raw_f - (1.0 - eps) * tau * raw_reflected - (1.0 - tau) * raw_atmosphere) / (
        eps * tau
    )

    # Values outside the sensor's Planck domain make the log undefined; clamp to
    # a tiny positive argument and let them surface as extreme temperatures
    # rather than NaN warnings mid-render.
    denom = planck.r2 * (raw_object + planck.o)
    with np.errstate(divide="ignore", invalid="ignore"):
        arg = np.where(denom > 0, planck.r1 / np.where(denom > 0, denom, 1.0) + planck.f, np.nan)
        celsius = planck.b / np.log(arg) + ABSOLUTE_ZERO_C
    return celsius
