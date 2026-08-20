"""Read radiometric JPEGs produced by the FLIR One phone app.

These carry everything the live stream would have given us and one thing it
would not: this camera's own Planck constants. So measurements taken from them
are properly calibrated rather than relative.

exiftool does the container parsing. It is the only practical reader for FLIR's
APP1 layout and is already a dependency of the calibration path.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import external
from .alignment import Alignment
from .calibration import Conditions, Planck, Trust
from .decode import DecodedFrame

# Files arrive by drag and drop from anywhere, and both the container and the
# embedded planes are decoded by Pillow. Cap the decoded size so a crafted or
# corrupt image cannot exhaust memory; a FLIR frame is a few hundred kilopixels,
# so this is orders of magnitude above anything legitimate.
Image.MAX_IMAGE_PIXELS = 64_000_000


class FlirJpegError(RuntimeError):
    pass


@dataclass
class RadiometricImage:
    """A decoded radiometric JPEG."""

    raw: np.ndarray  # uint16 sensor counts
    visible: np.ndarray | None
    planck: Planck
    conditions: Conditions
    tags: dict
    alignment: Alignment | None = None  # as recorded by the camera

    def as_frame(self) -> DecodedFrame:
        return DecodedFrame(
            raw=self.raw, visible=self.visible, status={"source": "radiometric jpeg"}
        )


def _exiftool(args: list[str], binary: bool = False):
    try:
        return external.exiftool(args, binary=binary)
    except (external.ToolNotFound, RuntimeError) as exc:
        raise FlirJpegError(str(exc)) from exc


def alignment_from_tags(tags: dict) -> Alignment | None:
    """The alignment the camera recorded, converted to our convention.

    FLIR stores Real2IR as a plain scale factor, but OffsetX and OffsetY are in
    **visible-image pixels** and are signed opposite to ours, so they need
    dividing by the visible-to-thermal grid ratio and negating.

    Only the axis perpendicular to the lens baseline is trustworthy. The other
    carries parallax, which the camera derives from the user-set object distance
    and is therefore only as good as that setting. Use this as a starting point
    and refine it with registration.estimate.
    """
    scale = tags.get("Real2IR")
    offset_x, offset_y = tags.get("OffsetX"), tags.get("OffsetY")
    if scale is None or offset_x is None or offset_y is None:
        return None
    thermal_width = tags.get("RawThermalImageWidth")
    visible_width = tags.get("EmbeddedImageWidth")
    if not thermal_width or not visible_width:
        return None
    ratio = float(visible_width) / float(thermal_width)
    return Alignment(
        scale=float(scale),
        dx=-float(offset_x) / ratio,
        dy=-float(offset_y) / ratio,
    )


def _tags(path: Path) -> dict:
    records = json.loads(
        _exiftool(
            [
                "-j",
                "-n",
                "-Real2IR",
                "-OffsetX",
                "-OffsetY",
                "-EmbeddedImageWidth",
                "-Planck*",
                "-RawThermalImage*",
                "-Emissivity",
                "-ReflectedApparentTemperature",
                "-AtmosphericTemperature",
                "-RelativeHumidity",
                "-SubjectDistance",
                "-ObjectDistance",
                "-AtmosphericTrans*",
                "-CameraTemperatureRange*",
                "-CameraModel",
                "-CameraSerialNumber",
                str(path),
            ]
        )
    )
    if not records:
        raise FlirJpegError("exiftool returned no records")
    return records[0]


def _thermal_array(path: Path, tags: dict, planck: Planck, conditions: Conditions) -> np.ndarray:
    """Extract the raw 16-bit plane, resolving its byte order physically.

    FLIR writes little-endian samples into the big-endian PNG container, so the
    plane usually needs swapping, but not always. Rather than guess, both orders
    are converted to temperature and scored against the camera's own stated
    measurement range: the correct one puts nearly every pixel inside it.
    """
    blob = _exiftool(["-b", "-RawThermalImage", str(path)], binary=True)
    if not blob:
        raise FlirJpegError("no RawThermalImage; this is not a radiometric JPEG")
    with Image.open(io.BytesIO(blob)) as img:
        array = np.array(img)
    if array.dtype != np.uint16:
        array = array.astype(np.uint16)

    if str(tags.get("RawThermalImageType", "")).upper() != "PNG":
        return array

    from .calibration import raw_to_celsius

    lo = float(tags.get("CameraTemperatureRangeMin", -40.0))
    hi = float(tags.get("CameraTemperatureRangeMax", 120.0))

    def plausibility(candidate: np.ndarray) -> float:
        with np.errstate(all="ignore"):
            temps = raw_to_celsius(candidate, planck, conditions)
        finite = np.isfinite(temps)
        if not finite.any():
            return -1.0
        return float(((temps >= lo) & (temps <= hi) & finite).mean())

    swapped = array.byteswap()
    return swapped if plausibility(swapped) > plausibility(array) else array


def load(path: str | Path) -> RadiometricImage:
    path = Path(path)
    tags = _tags(path)

    missing = [
        k for k in ("PlanckR1", "PlanckR2", "PlanckB", "PlanckF", "PlanckO") if k not in tags
    ]
    if missing:
        raise FlirJpegError(f"image carries no {', '.join(missing)}; not radiometric")

    planck = Planck(
        r1=float(tags["PlanckR1"]),
        r2=float(tags["PlanckR2"]),
        b=float(tags["PlanckB"]),
        f=float(tags["PlanckF"]),
        o=float(tags["PlanckO"]),
        trust=Trust.CAMERA,
        source=f"{path.name} ({tags.get('CameraSerialNumber', 'unknown serial')})",
        serial=str(tags["CameraSerialNumber"]) if tags.get("CameraSerialNumber") else None,
    )

    distance = tags.get("SubjectDistance", tags.get("ObjectDistance", 1.0))
    humidity = float(tags.get("RelativeHumidity", 50.0))
    defaults = Conditions()
    conditions = Conditions(
        emissivity=float(tags.get("Emissivity", 0.95)),
        reflected_c=float(tags.get("ReflectedApparentTemperature", 20.0)),
        atmospheric_c=float(tags.get("AtmosphericTemperature", 20.0)),
        # exiftool reports humidity as a percentage.
        humidity=humidity / 100.0 if humidity > 1.0 else humidity,
        distance_m=float(distance),
        # The camera records the atmospheric model it used; prefer it.
        atm_alpha1=float(tags.get("AtmosphericTransAlpha1", defaults.atm_alpha1)),
        atm_alpha2=float(tags.get("AtmosphericTransAlpha2", defaults.atm_alpha2)),
        atm_beta1=float(tags.get("AtmosphericTransBeta1", defaults.atm_beta1)),
        atm_beta2=float(tags.get("AtmosphericTransBeta2", defaults.atm_beta2)),
        atm_x=float(tags.get("AtmosphericTransX", defaults.atm_x)),
    )

    visible = None
    try:
        blob = _exiftool(["-b", "-EmbeddedImage", str(path)], binary=True)
        if blob:
            with Image.open(io.BytesIO(blob)) as img:
                visible = np.asarray(img.convert("RGB"))
    except (FlirJpegError, OSError):
        pass

    return RadiometricImage(
        raw=_thermal_array(path, tags, planck, conditions),
        visible=visible,
        planck=planck,
        conditions=conditions,
        tags=tags,
        alignment=alignment_from_tags(tags),
    )
