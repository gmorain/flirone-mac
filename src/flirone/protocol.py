"""FLIR One USB frame protocol.

Wire format reverse-engineered by the EEVblog thermal-imaging community; the
reference implementation is fnoop/flirone-v4l2 (GPL-2.0). This module only
parses the byte layout, it performs no I/O.

Frame layout on bulk endpoint 0x85, reassembled from arbitrary-sized chunks:

    offset  0  u32   magic 0x0000BEEF (bytes EF BE 00 00)
    offset  8  u32   frame_size   payload bytes following the 28-byte header
    offset 12  u32   thermal_size
    offset 16  u32   jpeg_size
    offset 20  u32   status_size
    offset 28        thermal section, then JPEG section, then status JSON
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

VENDOR_ID = 0x09CB
PRODUCT_ID = 0x1996

# The camera exposes three vendor-specific interfaces in configuration 3.
CONFIGURATION = 3
IFACE_CONTROL = 0
IFACE_FILEIO = 1
IFACE_FRAME = 2

EP_CONTROL_IN = 0x81
EP_FILEIO_OUT = 0x02
EP_FILEIO_IN = 0x83
EP_FRAME_IN = 0x85

# Streaming is gated by the standard SET_INTERFACE request: alternate setting 0
# is idle, 1 is running. See usb_link for why that matters on macOS.
ALT_IDLE = 0
ALT_STREAMING = 1

MAGIC = b"\xef\xbe\x00\x00"
HEADER_LEN = 28

# Thermal sections are row-padded. A 160x120 sensor ships 164 u16 per row:
# 80 pixels, 2 pad words, 80 pixels, 2 pad words, after a 4-byte section prelude.
Geometry = tuple[int, int, int, int]  # width, height, stride_words, prelude_bytes

_GEOMETRY_BY_SIZE: dict[int, Geometry] = {
    39364: (160, 120, 164, 4),  # FLIR One Gen 2 / Gen 3 / Pro
    10084: (80, 60, 84, 4),  # FLIR One Pro LT (unverified, same padding scheme)
}


def geometry_for(thermal_size: int) -> Geometry | None:
    """Map a thermal section size to sensor geometry, or None if unrecognised."""
    return _GEOMETRY_BY_SIZE.get(thermal_size)


@dataclass(frozen=True)
class RawFrame:
    """One assembled frame, still undecoded."""

    thermal: bytes
    jpeg: bytes
    status: bytes

    @property
    def geometry(self) -> Geometry | None:
        return geometry_for(len(self.thermal))


class FrameAssembler:
    """Reassembles bulk chunks into whole frames.

    Feed it every chunk read from endpoint 0x85. It yields a RawFrame each time
    a complete one is available. Chunks arriving before the first magic marker
    are discarded rather than guessed at.
    """

    def __init__(self, max_frame_bytes: int = 1 << 21) -> None:
        self._buf = bytearray()
        self._max = max_frame_bytes
        self.desync_count = 0

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, chunk: bytes) -> list[RawFrame]:
        if not chunk:
            return []

        # A chunk starting with the magic marker begins a new frame, so anything
        # still buffered was a torn frame and is dropped.
        if chunk.startswith(MAGIC) or len(self._buf) + len(chunk) > self._max:
            self._buf.clear()

        self._buf += chunk

        if not self._buf.startswith(MAGIC):
            self._buf.clear()
            self.desync_count += 1
            return []

        frames = []
        while (frame := self._take_one()) is not None:
            frames.append(frame)
        return frames

    def _take_one(self) -> RawFrame | None:
        if len(self._buf) < HEADER_LEN:
            return None

        frame_size, thermal_size, jpeg_size, status_size = struct.unpack_from("<4I", self._buf, 8)
        total = HEADER_LEN + frame_size
        if total > self._max or len(self._buf) < total:
            return None

        # The three sections must fit inside the frame, otherwise the header is
        # garbage and resyncing on the next magic marker beats trusting offsets.
        if thermal_size + jpeg_size + status_size > frame_size:
            self._buf.clear()
            self.desync_count += 1
            return None

        base = HEADER_LEN
        thermal = bytes(self._buf[base : base + thermal_size])
        base += thermal_size
        jpeg = bytes(self._buf[base : base + jpeg_size])
        base += jpeg_size
        status = bytes(self._buf[base : base + status_size])

        del self._buf[:total]
        return RawFrame(thermal=thermal, jpeg=jpeg, status=status)
