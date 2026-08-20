"""Decode the raw sections of a frame into arrays."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .protocol import RawFrame


class DecodeError(ValueError):
    pass


@dataclass
class DecodedFrame:
    """One frame, decoded but not yet calibrated or colourised."""

    raw: np.ndarray  # uint16 sensor counts, shape (h, w)
    visible: np.ndarray | None  # uint8 RGB from the visible camera
    status: dict

    @property
    def shape(self) -> tuple[int, int]:
        return self.raw.shape


def decode_thermal(data: bytes, geometry: tuple[int, int, int, int]) -> np.ndarray:
    """Unpack the padded 16-bit thermal section into a (h, w) uint16 array.

    Rows are stride_words wide and split in two halves with two pad words after
    each, so the payload is read as a rectangle and the pad columns dropped.
    """
    width, height, stride, prelude = geometry
    half = width // 2
    needed = prelude + height * stride * 2
    if len(data) < needed:
        raise DecodeError(f"thermal section is {len(data)} bytes, need {needed}")

    rows = np.frombuffer(data, dtype="<u2", count=height * stride, offset=prelude)
    rows = rows.reshape(height, stride)
    # Columns [0:half] and [half+2 : 2*half+2]; the two-word gaps are padding.
    left = rows[:, :half]
    right = rows[:, half + 2 : 2 * half + 2]
    return np.hstack((left, right)).astype(np.uint16)


def decode_visible(data: bytes) -> np.ndarray | None:
    """Decode the visible-camera JPEG to an RGB array."""
    if not data or not data.startswith(b"\xff\xd8"):
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            return np.asarray(img.convert("RGB"))
    except OSError:
        return None


def decode_status(data: bytes) -> dict:
    """Parse the status section, which is NUL-padded JSON."""
    text = data.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def decode(frame: RawFrame) -> DecodedFrame:
    geometry = frame.geometry
    if geometry is None:
        raise DecodeError(
            f"unrecognised thermal section size {len(frame.thermal)}; sensor geometry unknown"
        )
    return DecodedFrame(
        raw=decode_thermal(frame.thermal, geometry),
        visible=decode_visible(frame.jpeg),
        status=decode_status(frame.status),
    )
