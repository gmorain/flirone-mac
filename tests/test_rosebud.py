"""FLIR's command framing, checked against the reference driver's constants."""

from __future__ import annotations

import zlib

from flirone import rosebud

# The two headers hardcoded in fnoop/flirone-v4l2, with the bodies that produce them.
REFERENCE = [
    ("cc0100000100000041000000f8b3f700", rosebud.open_file("CameraFiles.zip")),
    ("cc0100000100000033000000efdbc1c1", rosebud.read_file(10)),
]


def test_headers_match_the_reference_driver():
    for expected, (header, _body) in REFERENCE:
        assert header.hex() == expected


def test_declared_length_matches_the_body():
    for _expected, (header, body) in REFERENCE:
        assert int.from_bytes(header[8:12], "little") == len(body)


def test_checksum_covers_the_header_not_the_body():
    header, _body = rosebud.set_option("autoFFC", True)
    assert int.from_bytes(header[12:16], "little") == zlib.crc32(header[:12])


def test_bodies_are_nul_terminated_json():
    _header, body = rosebud.set_option("autoFFC", True)
    assert body.endswith(b"\x00")
    assert b'"autoFFC"' in body
