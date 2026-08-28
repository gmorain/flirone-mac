"""The reconnect loop, driven by fake links rather than a camera.

The counter these test exists because the real trace disagreed with it: a cable
pulled mid-stream recovered correctly and `reconnects` stayed where it was,
because the count came off the exception type and a dropped iAP2 link raises the
same error as a camera that was never there.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from flirone.decode import DecodedFrame
from flirone.sources import UsbFrameSource
from flirone.usb_link import FlirUsbError


class FakeLink:
    """Yields one frame, then fails the way a pulled cable does."""

    def __init__(self, frames_before_drop: int = 1) -> None:
        self.remaining = frames_before_drop
        self.alive = True
        self.closed = False

    def read_frame_chunk(self, timeout_ms: int = 100) -> bytes:
        if self.remaining <= 0:
            self.alive = False
            raise FlirUsbError("iAP2 link dropped")
        self.remaining -= 1
        return b"chunk"

    def keepalive(self, seconds: float = 0.02) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def run_briefly(source: UsbFrameSource, seconds: float = 0.6) -> None:
    source.start()
    time.sleep(seconds)
    source.stop()


@pytest.fixture
def patched(monkeypatch):
    """Make the source produce frames without a camera or a decoder."""
    frame = DecodedFrame(raw=np.zeros((2, 2)), visible=None, status={})

    class OneFrameAssembler:
        desync_count = 0

        def reset(self):
            pass

        def feed(self, chunk):
            return [object()]

    monkeypatch.setattr("flirone.protocol.FrameAssembler", OneFrameAssembler)
    monkeypatch.setattr("flirone.sources.decode", lambda _raw: frame)
    return frame


def test_a_drop_mid_stream_counts_as_a_reconnect(patched, monkeypatch):
    links = []

    def open_link(_self):
        links.append(FakeLink())
        return links[-1]

    monkeypatch.setattr(UsbFrameSource, "_open_link", open_link)
    source = UsbFrameSource()
    run_briefly(source)

    assert source.frames_seen >= 1, "the fake link should have produced frames"
    assert source.reconnects >= 1, "a session that streamed and stopped is a reconnect"
    assert all(link.closed for link in links), "every link must be closed"


def test_a_camera_that_never_arrives_is_not_a_reconnect(patched, monkeypatch):
    """Otherwise an idle host counts reconnects forever and the number is noise."""

    def never(_self):
        raise FlirUsbError("No FLIR One appeared.")

    monkeypatch.setattr(UsbFrameSource, "_open_link", never)
    source = UsbFrameSource()
    run_briefly(source)

    assert source.reconnects == 0
    assert source.status == "no camera"
