"""Recover this camera's Planck constants.

The USB stream carries no calibration, and the constants are per-unit, so a
viewer using another camera's numbers reports confident nonsense. Two sources
work offline:

  1. A radiometric JPEG shot with the FLIR One phone app. The constants sit in
     its FLIR APP1 segment, readable with exiftool or by parsing the segment
     directly (no external tool needed).
  2. A JSON file previously saved by this module.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from . import external
from .calibration import Planck, Trust

_FIELDS = ("PlanckR1", "PlanckR2", "PlanckB", "PlanckF", "PlanckO")


def from_json(path: Path) -> Planck:
    data = json.loads(Path(path).read_text())
    return Planck(
        r1=float(data["PlanckR1"]),
        r2=float(data["PlanckR2"]),
        b=float(data["PlanckB"]),
        f=float(data["PlanckF"]),
        o=float(data["PlanckO"]),
        trust=Trust.UNVERIFIED,
        source=str(data.get("source", path)),
        serial=str(data["serial"]) if data.get("serial") else None,
    ).validated()


def to_json(planck: Planck, path: Path) -> None:
    Path(path).write_text(
        json.dumps(
            {
                "PlanckR1": planck.r1,
                "PlanckR2": planck.r2,
                "PlanckB": planck.b,
                "PlanckF": planck.f,
                "PlanckO": planck.o,
                "source": planck.source,
                "serial": planck.serial,
            },
            indent=2,
        )
    )


def from_exiftool(image: Path) -> Planck:
    """Read the constants using exiftool, if it is installed."""
    records = json.loads(external.exiftool(["-j", "-Planck*", "-CameraSerialNumber", str(image)]))
    if not records:
        raise RuntimeError("exiftool returned no records")
    tags = records[0]
    missing = [f for f in _FIELDS if f not in tags]
    if missing:
        raise RuntimeError(f"image carries no {', '.join(missing)}; is it radiometric?")
    return Planck(
        r1=float(tags["PlanckR1"]),
        r2=float(tags["PlanckR2"]),
        b=float(tags["PlanckB"]),
        f=float(tags["PlanckF"]),
        o=float(tags["PlanckO"]),
        trust=Trust.UNVERIFIED,
        source=f"exiftool:{image.name}",
        serial=str(tags["CameraSerialNumber"]) if tags.get("CameraSerialNumber") else None,
    ).validated()


def from_flir_segment(image: Path) -> Planck:
    """Parse the FLIR APP1 segment directly, so exiftool is not required.

    FLIR splits its metadata across numbered APP1 chunks that carry the magic
    'FLIR\\x00'. Reassembled, the payload is a small record table; record type
    0x20 is the camera-info block holding the Planck constants at fixed offsets.
    """
    data = Path(image).read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise RuntimeError("not a JPEG")

    chunks: dict[int, bytes] = {}
    pos = 2
    while pos < len(data) - 1:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0xD9):
            pos += 2
            continue
        if marker == 0xDA:  # start of scan, metadata is behind us
            break
        length = struct.unpack_from(">H", data, pos + 2)[0]
        segment = data[pos + 4 : pos + 2 + length]
        if marker == 0xE1 and segment.startswith(b"FLIR\x00"):
            # FLIR\0 <1 byte unused> <chunk index> <chunk count>
            index = segment[6]
            chunks[index] = segment[8:]
        pos += 2 + length

    if not chunks:
        raise RuntimeError("no FLIR APP1 segments; not a radiometric JPEG")
    payload = b"".join(chunks[k] for k in sorted(chunks))

    if not payload.startswith(b"FFF\x00"):
        raise RuntimeError("unexpected FLIR payload header")
    index_offset, record_count = struct.unpack_from(">II", payload, 24)
    for i in range(record_count):
        entry = index_offset + i * 32
        if entry + 32 > len(payload):
            break
        rec_type, _, _, _, rec_offset, rec_len = struct.unpack_from(">HHIIII", payload, entry)
        if rec_type != 0x20:  # CameraInfo
            continue
        rec = payload[rec_offset : rec_offset + rec_len]
        # Byte order of the record is given by its own leading marker.
        endian = "<" if rec[:2] == b"\x02\x00" else ">"
        r1, b_, f_, o_, r2 = struct.unpack_from(f"{endian}f f f i f", rec, 0x00D4)
        return Planck(
            r1=float(r1),
            r2=float(r2),
            b=float(b_),
            f=float(f_),
            o=float(o_),
            trust=Trust.UNVERIFIED,
            source=f"flir-segment:{Path(image).name}",
        )
    raise RuntimeError("no CameraInfo record in the FLIR payload")


def load(image_or_json: Path) -> Planck:
    """Best-effort load: JSON, then the embedded segment, then exiftool."""
    path = Path(image_or_json)
    if path.suffix.lower() == ".json":
        return from_json(path)
    errors = []
    for reader in (from_flir_segment, from_exiftool):
        try:
            return reader(path)
        except Exception as exc:
            errors.append(f"{reader.__name__}: {exc}")
    raise RuntimeError("could not recover Planck constants -> " + "; ".join(errors))
