"""FLIR's own command protocol, carried over the iAP2 External Accessory sessions.

"Rosebud" is FLIR's internal name for the camera, visible in the accessory
protocol identifiers it advertises: com.flir.rosebud.config, .fileio and .frame.

Each command is a 16-byte header followed by a NUL-terminated JSON body:

    cc 01 00 00 | 01 00 00 00 | <body length, u32 LE> | <crc32 of the preceding
                                                         12 bytes, u32 LE>

The trailing checksum covers the header itself, not the body. That was
recovered from the two hardcoded headers in the reference driver, both of which
reproduce exactly.
"""

from __future__ import annotations

import json
import struct
import zlib

MAGIC = 0x000001CC


def frame(body: bytes) -> bytes:
    """Build the 16-byte header for a command body."""
    header = struct.pack("<III", MAGIC, 1, len(body))
    return header + struct.pack("<I", zlib.crc32(header))


def command(payload: dict) -> tuple[bytes, bytes]:
    """Encode a command, returning (header, body)."""
    body = json.dumps(payload, separators=(",", ":")).encode() + b"\x00"
    return frame(body), body


def open_file(path: str, mode: str = "r") -> tuple[bytes, bytes]:
    return command({"type": "openFile", "data": {"mode": mode, "path": path}})


def read_file(stream_identifier: int = 10) -> tuple[bytes, bytes]:
    return command({"type": "readFile", "data": {"streamIdentifier": stream_identifier}})


def set_option(option: str, value) -> tuple[bytes, bytes]:
    return command({"type": "setOption", "data": {"option": option, "value": value}})


def start_frame_stream() -> tuple[bytes, bytes]:
    return command({"type": "startFrameStream"})
