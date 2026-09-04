"""The FLIR FFF container, as it sits inside a radiometric JPEG's APP1 segments.

Reading and rebuilding are both here because they are the same knowledge. The
reader in `planck_import.from_flir_segment` predates this module and still
carries its own copy of the chunk walk; the offsets it uses are imported from
here so the two cannot drift.

Nothing in this file trusts the bytes it is given. A record index is a set of
offsets and lengths read from a file, and following one without checking it
against the payload size is how a malformed image becomes an out-of-range read.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

FFF_MAGIC = b"FFF\x00"
FLIR_APP1_MAGIC = b"FLIR\x00"

# Offsets inside the 0x20 CameraInfo record. The Planck ones are duplicated in
# planck_import, which imports them from here.
PLANCK_R1 = 0x0058
PLANCK_B = 0x005C
PLANCK_F = 0x0060
PLANCK_O = 0x0308  # signed
PLANCK_R2 = 0x030C
SERIAL = 0x0104
SERIAL_LENGTH = 16

EMISSIVITY = 0x0020
OBJECT_DISTANCE = 0x0024
REFLECTED_TEMPERATURE = 0x0028  # kelvin
ATMOSPHERIC_TEMPERATURE = 0x002C  # kelvin
RELATIVE_HUMIDITY = 0x003C  # fraction, not percent

RECORD_RAW_DATA = 0x01
RECORD_EMBEDDED_IMAGE = 0x0E
RECORD_CAMERA_INFO = 0x20
RECORD_ALIGNMENT = 0x2A

# Inside the 0x2a alignment record, little-endian.
REAL2IR = 0x0000
OFFSET_X = 0x0004
OFFSET_Y = 0x0006

_INDEX_AT = 24
_ENTRY_SIZE = 32
# The largest APP1 body FLIR itself emits. A JPEG marker length field is 16 bits
# and covers itself, so the ceiling is 65533 bytes of segment; FLIR stops short
# of it and there is no reason to be the first to find out why.
_CHUNK_PAYLOAD = 65524


class FffError(RuntimeError):
    """Raised when a payload does not hold together."""


@dataclass(frozen=True)
class Record:
    """One entry of the FFF record index."""

    type: int
    offset: int
    length: int
    header: bytes  # the entry's first 12 bytes, carried through unchanged
    tail: bytes  # the entry's last 12 bytes, likewise


def read_app1(jpeg: bytes) -> bytes:
    """Reassemble the FLIR payload from a JPEG's APP1 segments."""
    if not jpeg.startswith(b"\xff\xd8"):
        raise FffError("not a JPEG")

    chunks: dict[int, bytes] = {}
    pos = 2
    while pos < len(jpeg) - 1:
        if jpeg[pos] != 0xFF:
            pos += 1
            continue
        marker = jpeg[pos + 1]
        if marker in (0xD8, 0xD9):
            pos += 2
            continue
        if marker == 0xDA:  # start of scan, metadata is behind us
            break
        if pos + 4 > len(jpeg):
            break
        length = struct.unpack_from(">H", jpeg, pos + 2)[0]
        if length < 2 or pos + 2 + length > len(jpeg):
            raise FffError("truncated JPEG segment")
        segment = jpeg[pos + 4 : pos + 2 + length]
        if marker == 0xE1 and segment.startswith(FLIR_APP1_MAGIC):
            if len(segment) < 8:
                raise FffError("FLIR APP1 segment too short to carry a chunk index")
            chunks[segment[6]] = segment[8:]
        pos += 2 + length

    if not chunks:
        raise FffError("no FLIR APP1 segments; not a radiometric JPEG")
    missing = set(range(max(chunks) + 1)) - set(chunks)
    if missing:
        raise FffError(f"FLIR APP1 chunks missing: {sorted(missing)}")

    payload = b"".join(chunks[k] for k in sorted(chunks))
    if not payload.startswith(FFF_MAGIC):
        raise FffError("reassembled payload is not an FFF container")
    return payload


def records(payload: bytes) -> list[Record]:
    """The record index, with every offset and length checked against the payload."""
    if len(payload) < _INDEX_AT + 8:
        raise FffError("FFF payload too short to hold an index pointer")
    index_offset, count = struct.unpack_from(">II", payload, _INDEX_AT)
    if count > 4096:
        raise FffError(f"implausible record count {count}")
    end = index_offset + count * _ENTRY_SIZE
    if index_offset < _INDEX_AT or end > len(payload):
        raise FffError("record index lies outside the payload")

    found = []
    for i in range(count):
        at = index_offset + i * _ENTRY_SIZE
        entry = payload[at : at + _ENTRY_SIZE]
        rec_type = struct.unpack_from(">H", entry, 0)[0]
        offset, length = struct.unpack_from(">II", entry, 12)
        if rec_type == 0:
            continue  # an empty slot, of which real files carry several
        if offset + length > len(payload):
            raise FffError(f"record 0x{rec_type:02x} runs past the end of the payload")
        found.append(
            Record(
                type=rec_type,
                offset=offset,
                length=length,
                header=entry[:12],
                tail=entry[20:],
            )
        )
    return found


def body(payload: bytes, record: Record) -> bytes:
    """The bytes of one record."""
    return payload[record.offset : record.offset + record.length]


def rebuild(payload: bytes, replacements: dict[int, bytes | None]) -> bytes:
    """Return a payload with the named records replaced, offsets recomputed.

    A replacement of None drops the record and empties its index slot, which is
    how a file says it has no such thing rather than carrying the template's.
    Records not named are copied byte for byte.
    """
    existing = records(payload)
    unknown = set(replacements) - {r.type for r in existing}
    if unknown:
        raise FffError(f"no such record to replace: {sorted(unknown)}")

    index_offset, count = struct.unpack_from(">II", payload, _INDEX_AT)
    header = bytearray(payload[: index_offset + count * _ENTRY_SIZE])

    out = bytearray(header)
    # Records start where they started in the source, so the header and index
    # keep the size the camera gave them.
    first = min(r.offset for r in existing)
    if first < len(out):
        raise FffError("record data overlaps the index")
    out.extend(b"\x00" * (first - len(out)))

    entries: dict[int, tuple[int, int]] = {}
    dropped = {t for t, data in replacements.items() if data is None}
    for record in existing:
        if record.type in dropped:
            continue
        data = replacements.get(record.type) or body(payload, record)
        entries[record.type] = (len(out), len(data))
        out.extend(data)
        if len(out) % 2:
            out.append(0)  # records sit on even offsets in every file seen

    for i in range(count):
        at = index_offset + i * _ENTRY_SIZE
        rec_type = struct.unpack_from(">H", out, at)[0]
        if rec_type in dropped:
            struct.pack_into(">H", out, at, 0)
            struct.pack_into(">II", out, at + 12, 0, 0)
        elif rec_type in entries:
            offset, length = entries[rec_type]
            struct.pack_into(">II", out, at + 12, offset, length)
    return bytes(out)


def to_app1_segments(payload: bytes) -> bytes:
    """Wrap a payload back into numbered APP1 segments."""
    pieces = [payload[i : i + _CHUNK_PAYLOAD] for i in range(0, len(payload), _CHUNK_PAYLOAD)]
    if len(pieces) > 256:
        raise FffError("payload needs more than 256 APP1 chunks")
    last = len(pieces) - 1

    out = bytearray()
    for i, piece in enumerate(pieces):
        head = FLIR_APP1_MAGIC + bytes([0x01, i, last])
        segment = head + piece
        out += b"\xff\xe1" + struct.pack(">H", len(segment) + 2) + segment
    return bytes(out)


def insert(jpeg: bytes, payload: bytes) -> bytes:
    """Put a FLIR payload into a JPEG, replacing any FLIR segments already there."""
    if not jpeg.startswith(b"\xff\xd8"):
        raise FffError("not a JPEG")

    out = bytearray(b"\xff\xd8")
    out += to_app1_segments(payload)

    pos = 2
    while pos < len(jpeg) - 1:
        if jpeg[pos] != 0xFF:
            raise FffError("lost JPEG segment alignment")
        marker = jpeg[pos + 1]
        if marker == 0xDA:  # scan data, and everything after it, goes as-is
            out += jpeg[pos:]
            break
        length = struct.unpack_from(">H", jpeg, pos + 2)[0]
        if length < 2 or pos + 2 + length > len(jpeg):
            raise FffError("truncated JPEG segment")
        segment = jpeg[pos + 4 : pos + 2 + length]
        if not (marker == 0xE1 and segment.startswith(FLIR_APP1_MAGIC)):
            out += jpeg[pos : pos + 2 + length]
        pos += 2 + length
    else:
        raise FffError("JPEG has no scan")
    return bytes(out)
