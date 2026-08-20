"""Frame reassembly from the camera's bulk stream."""

from __future__ import annotations

import struct

from flirone.protocol import HEADER_LEN, MAGIC, FrameAssembler, geometry_for


def make_frame(thermal: bytes, jpeg: bytes, status: bytes) -> bytes:
    body = thermal + jpeg + status
    # magic(4) + unknown(4) + four sizes(16) + unknown(4) = 28
    header = (
        MAGIC
        + b"\x00" * 4
        + struct.pack("<4I", len(body), len(thermal), len(jpeg), len(status))
        + b"\x00" * 4
    )
    assert len(header) == HEADER_LEN
    return header + body


def test_single_frame_round_trips():
    raw = make_frame(b"T" * 40, b"J" * 20, b'{"a":1}')
    frames = FrameAssembler().feed(raw)
    assert len(frames) == 1
    assert frames[0].thermal == b"T" * 40
    assert frames[0].jpeg == b"J" * 20
    assert frames[0].status == b'{"a":1}'


def test_frame_split_across_chunks():
    raw = make_frame(b"T" * 100, b"J" * 50, b"S" * 10)
    assembler = FrameAssembler()
    assert assembler.feed(raw[:37]) == []
    assert assembler.feed(raw[37:90]) == []
    frames = assembler.feed(raw[90:])
    assert len(frames) == 1
    assert frames[0].thermal == b"T" * 100


def test_two_frames_in_one_chunk():
    raw = make_frame(b"A" * 8, b"B" * 4, b"C") + make_frame(b"D" * 8, b"E" * 4, b"F")
    frames = FrameAssembler().feed(raw)
    assert [f.thermal for f in frames] == [b"A" * 8, b"D" * 8]


def test_chunk_without_magic_is_discarded():
    assembler = FrameAssembler()
    assert assembler.feed(b"garbage that never syncs") == []
    assert assembler.desync_count == 1
    # It recovers on the next real frame.
    frames = assembler.feed(make_frame(b"T" * 8, b"J" * 4, b"S"))
    assert len(frames) == 1


def test_torn_frame_is_dropped_when_a_new_one_starts():
    assembler = FrameAssembler()
    partial = make_frame(b"T" * 200, b"J" * 100, b"S" * 5)[:60]
    assembler.feed(partial)
    frames = assembler.feed(make_frame(b"X" * 8, b"Y" * 4, b"Z"))
    assert len(frames) == 1
    assert frames[0].thermal == b"X" * 8


def test_inconsistent_section_sizes_resync():
    body = b"T" * 10
    bad = MAGIC + b"\x00" * 4 + struct.pack("<4I", len(body), 999, 999, 999) + b"\x00" * 4 + body
    assembler = FrameAssembler()
    assert assembler.feed(bad) == []
    assert assembler.desync_count == 1


def test_known_geometry():
    assert geometry_for(39364) == (160, 120, 164, 4)
    assert geometry_for(12345) is None
