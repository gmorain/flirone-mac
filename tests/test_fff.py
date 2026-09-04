"""The FLIR FFF container, on payloads built here rather than read from a camera.

These run everywhere. The round trip against a real file lives in
test_radiometric_write.py and skips when no such file is present, which is most
machines, so the container itself is covered offline.
"""

from __future__ import annotations

import struct

import pytest

from flirone import fff

FIRST_RECORD = 512


def build(records: dict[int, bytes], slots: int = 8) -> bytes:
    """A minimal but structurally honest FFF payload."""
    header = bytearray(FIRST_RECORD)
    header[0:4] = fff.FFF_MAGIC
    struct.pack_into(">II", header, 24, 64, slots)

    out = bytearray(header)
    placed = {}
    for rec_type, data in records.items():
        placed[rec_type] = (len(out), len(data))
        out.extend(data)
        if len(out) % 2:
            out.append(0)

    for i, (rec_type, (offset, length)) in enumerate(placed.items()):
        at = 64 + i * 32
        struct.pack_into(">H", out, at, rec_type)
        struct.pack_into(">II", out, at + 12, offset, length)
    return bytes(out)


def jpeg_with(payload: bytes) -> bytes:
    body = b"\xff\xdb\x00\x04\x00\x00" + b"\xff\xda\x00\x02" + b"scan data"
    return fff.insert(b"\xff\xd8" + body, payload)


def test_a_payload_survives_the_jpeg_round_trip():
    payload = build({fff.RECORD_RAW_DATA: b"raw" * 100, fff.RECORD_CAMERA_INFO: b"info" * 50})
    assert fff.read_app1(jpeg_with(payload)) == payload


def test_a_payload_larger_than_one_segment_is_split_and_rejoined():
    """A real payload is 276 kB against a 64 kB ceiling on a JPEG segment."""
    payload = build({fff.RECORD_RAW_DATA: bytes(200_000)})
    carrier = jpeg_with(payload)
    assert carrier.count(b"\xff\xe1") >= 4
    assert fff.read_app1(carrier) == payload


def test_replacing_a_record_moves_the_ones_after_it():
    payload = build({fff.RECORD_RAW_DATA: b"a" * 100, fff.RECORD_CAMERA_INFO: b"b" * 100})
    rebuilt = fff.rebuild(payload, {fff.RECORD_RAW_DATA: b"c" * 900})
    found = {r.type: fff.body(rebuilt, r) for r in fff.records(rebuilt)}
    assert found[fff.RECORD_RAW_DATA] == b"c" * 900
    assert found[fff.RECORD_CAMERA_INFO] == b"b" * 100


def test_records_not_named_are_copied_untouched():
    """The five record types nobody has parsed depend on this."""
    payload = build({fff.RECORD_RAW_DATA: b"a" * 10, 0x2A: b"opaque", 0x37: b"also opaque"})
    rebuilt = fff.rebuild(payload, {fff.RECORD_RAW_DATA: b"z" * 40})
    found = {r.type: fff.body(rebuilt, r) for r in fff.records(rebuilt)}
    assert found[0x2A] == b"opaque"
    assert found[0x37] == b"also opaque"


def test_an_index_pointing_outside_the_payload_is_refused():
    payload = bytearray(build({fff.RECORD_RAW_DATA: b"a" * 10}))
    struct.pack_into(">II", payload, 24, 64, 4096)
    with pytest.raises(fff.FffError):
        fff.records(bytes(payload))


def test_a_record_running_past_the_end_is_refused():
    payload = bytearray(build({fff.RECORD_RAW_DATA: b"a" * 10}))
    struct.pack_into(">II", payload, 64 + 12, FIRST_RECORD, 1 << 30)
    with pytest.raises(fff.FffError):
        fff.records(bytes(payload))


def test_a_jpeg_without_flir_segments_is_refused():
    with pytest.raises(fff.FffError):
        fff.read_app1(b"\xff\xd8\xff\xda\x00\x02scan")


def test_a_missing_chunk_is_refused_rather_than_silently_joined():
    payload = build({fff.RECORD_RAW_DATA: bytes(200_000)})
    carrier = bytearray(jpeg_with(payload))
    first = carrier.index(b"\xff\xe1")
    length = struct.unpack_from(">H", carrier, first + 2)[0]
    del carrier[first : first + 2 + length]
    with pytest.raises(fff.FffError):
        fff.read_app1(bytes(carrier))
