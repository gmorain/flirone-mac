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
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import external
from .alignment import Alignment
from .calibration import ABSOLUTE_ZERO_C, Conditions, Planck, Trust
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


def save_radiometric(
    path: Path,
    frame: DecodedFrame,
    planck: Planck,
    conditions: Conditions,
    template: bytes,
    alignment: Alignment | None = None,
) -> Path:
    """Write a radiometric JPEG that FLIR's own tools can read.

    `template` is the FFF payload of a real file from the same camera. Its
    records are reused wholesale except for the raw plane and the camera-info
    block, so the five record types nobody here has parsed survive untouched.

    Counts are put on the reference plane before writing. A live frame carries
    14-bit counts where the Planck constants in the same file describe a 16-bit
    plane, and any reader, ours or FLIR's, would otherwise apply those constants
    to counts four times too small.
    """
    from . import fff

    raw = frame.raw
    if frame.scale is not None:
        raw = frame.scale.validated().to_reference(raw)
    raw = np.asarray(raw)
    if raw.ndim != 2:
        raise FlirJpegError("raw plane must be two-dimensional")
    counts = np.clip(np.rint(raw), 0, 65535).astype(np.uint16)

    payload = fff.read_app1(b"\xff\xd8" + template) if template[:2] != b"FFF\x00"[:2] else template
    old = {r.type: r for r in fff.records(payload)}
    for needed in (fff.RECORD_RAW_DATA, fff.RECORD_CAMERA_INFO):
        if needed not in old:
            raise FlirJpegError(f"template has no 0x{needed:02x} record")

    # Raw plane: the record's own 32-byte header, with our dimensions, followed
    # by the counts as a 16-bit PNG, which is how FLIR stores them.
    head = bytearray(fff.body(payload, old[fff.RECORD_RAW_DATA])[:32])
    height, width = counts.shape
    struct.pack_into("<HH", head, 2, width, height)
    struct.pack_into("<II", head, 12, width - 1, height - 1)
    buffer = io.BytesIO()
    # No mode argument: Pillow deprecated it and drops it in version 13. A
    # uint16 array already maps to I;16, which is how FLIR stores the plane.
    Image.fromarray(counts).save(buffer, format="PNG", optimize=True)
    new_raw = bytes(head) + buffer.getvalue()

    # Camera info: this camera's constants and the conditions in force.
    info = bytearray(fff.body(payload, old[fff.RECORD_CAMERA_INFO]))
    endian = "<" if info[:2] == b"\x02\x00" else ">"
    for offset, value in (
        (fff.PLANCK_R1, planck.r1),
        (fff.PLANCK_B, planck.b),
        (fff.PLANCK_F, planck.f),
        (fff.PLANCK_R2, planck.r2),
        (fff.EMISSIVITY, conditions.emissivity),
        (fff.OBJECT_DISTANCE, conditions.distance_m),
        (fff.REFLECTED_TEMPERATURE, conditions.reflected_c - ABSOLUTE_ZERO_C),
        (fff.ATMOSPHERIC_TEMPERATURE, conditions.atmospheric_c - ABSOLUTE_ZERO_C),
        (fff.RELATIVE_HUMIDITY, conditions.humidity),
    ):
        struct.pack_into(f"{endian}f", info, offset, float(value))
    struct.pack_into(f"{endian}i", info, fff.PLANCK_O, int(planck.o))
    if planck.serial:
        serial = planck.serial.encode("ascii", "ignore")[: fff.SERIAL_LENGTH - 1]
        info[fff.SERIAL : fff.SERIAL + fff.SERIAL_LENGTH] = serial.ljust(fff.SERIAL_LENGTH, b"\x00")

    visible = frame.visible if frame.visible is not None else np.zeros((*counts.shape, 3), np.uint8)
    picture = io.BytesIO()
    Image.fromarray(visible).save(picture, format="JPEG", quality=90)

    # The embedded image, which is what a reader shows as the visible frame.
    # Copying the template's here put its scene in every file written.
    embedded_head = bytearray(fff.body(payload, old[fff.RECORD_EMBEDDED_IMAGE])[:32])
    struct.pack_into("<HH", embedded_head, 2, visible.shape[1], visible.shape[0])
    new_embedded = bytes(embedded_head) + picture.getvalue()

    # The alignment relates those two planes, so the template's is wrong for
    # this pair whatever it says. Written when known, dropped when not, since a
    # reader that finds none measures one instead of trusting a stale number.
    replacements: dict[int, bytes | None] = {
        fff.RECORD_RAW_DATA: new_raw,
        fff.RECORD_CAMERA_INFO: bytes(info),
        fff.RECORD_EMBEDDED_IMAGE: new_embedded,
        fff.RECORD_ALIGNMENT: _alignment_record(
            payload, old.get(fff.RECORD_ALIGNMENT), alignment, visible.shape[1], counts.shape[1]
        ),
    }
    rebuilt = fff.rebuild(payload, replacements)
    path = Path(path)
    path.write_bytes(fff.insert(picture.getvalue(), rebuilt))
    return path


def _alignment_record(
    payload: bytes,
    record,
    alignment: Alignment | None,
    visible_width: int,
    thermal_width: int,
) -> bytes | None:
    """The alignment record for this pair of planes, or None to drop it.

    Offsets are stored in visible pixels and signed opposite to ours, which is
    the conversion `alignment_from_tags` undoes on the way in.
    """
    from . import fff

    if record is None or alignment is None or not thermal_width:
        return None
    ratio = float(visible_width) / float(thermal_width)
    out = bytearray(fff.body(payload, record))
    struct.pack_into("<f", out, fff.REAL2IR, float(alignment.scale))
    struct.pack_into("<h", out, fff.OFFSET_X, int(round(-alignment.dx * ratio)))
    struct.pack_into("<h", out, fff.OFFSET_Y, int(round(-alignment.dy * ratio)))
    return bytes(out)
