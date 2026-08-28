"""Parsing of the camera's replies on the config session.

The bring-up itself needs the camera; what is testable offline is the framing:
FLIR's replies are NUL-terminated JSON behind a 16-byte 0xCC header, and they
arrive split across EA payloads in whatever way the link happened to chunk them.
"""

from __future__ import annotations

from flirone import rosebud
from flirone.iap2.stream import _json_bodies

# Captured from the camera during a live session.
SLED = (
    b'{"type":"sledInformation","data":{"serialNumberBoard":"F02F9T00570",'
    b'"thermalWidth":"160","thermalHeight":"120","bigEndianThermal":"0"}}\x00'
)
OPEN_STATUS = (
    b'{"type":"openFileStatus","data":{"path":"CameraFiles.zip",'
    b'"streamIdentifier":10,"mode":"r"}}\x00'
)


def test_reads_a_reply_behind_its_header():
    header, _ = rosebud.command({"ignored": True})
    (message,) = _json_bodies([header + OPEN_STATUS])
    assert message["type"] == "openFileStatus"
    assert message["data"]["streamIdentifier"] == 10


def test_reads_several_replies_from_several_chunks():
    messages = list(_json_bodies([SLED, OPEN_STATUS]))
    assert [m["type"] for m in messages] == ["sledInformation", "openFileStatus"]


def test_camera_declares_its_own_geometry_and_byte_order():
    """sledInformation states what decisions/002 otherwise has to infer."""
    (message,) = _json_bodies([SLED])
    data = message["data"]
    assert (data["thermalWidth"], data["thermalHeight"]) == ("160", "120")
    assert data["bigEndianThermal"] == "0"


def test_a_truncated_reply_is_skipped_not_raised():
    """A chunk boundary mid-JSON must not take the session down."""
    assert list(_json_bodies([SLED[:40]])) == []


def test_non_json_payloads_are_ignored():
    assert list(_json_bodies([b"\x00\x01\xcc\x01\x00\x00", b""])) == []


def test_malformed_json_is_skipped():
    assert list(_json_bodies([b'{"type":"broken",\x00'])) == []


# --- a stream that never starts ----------------------------------------------


class _SilentLink:
    """An opened link whose frame endpoint never produces anything.

    That is what a FLIR One with its power button off looks like: the sled
    controller completes the handshake and answers `setOption`, and the imager
    stays down. The reply is identical either way, so bytes are the only
    evidence.
    """

    alive = True

    def __init__(self, chunks=()):
        self._chunks = list(chunks)
        self.keepalives = 0

    def read_frame_chunk(self, timeout_ms=100):
        return self._chunks.pop(0) if self._chunks else b""

    def keepalive(self, seconds=0.02):
        self.keepalives += 1


def test_a_camera_that_never_streams_is_reported(monkeypatch):
    from flirone.iap2.stream import Iap2StreamLink

    link = Iap2StreamLink.__new__(Iap2StreamLink)
    link._pending = b""
    silent = _SilentLink()
    monkeypatch.setattr(
        type(link), "read_frame_chunk", lambda _s, _t=100: silent.read_frame_chunk()
    )
    monkeypatch.setattr(type(link), "keepalive", lambda _s, _t=0.02: silent.keepalive())
    monkeypatch.setattr(type(link), "alive", property(lambda _s: True))

    assert link._await_first_frame_bytes(seconds=0.3) == b""
    assert silent.keepalives > 0, "the link must be kept answered while waiting"


def test_the_bytes_that_proved_the_stream_are_not_lost(monkeypatch):
    """They are frame data. Dropping them would tear the first frame."""
    from flirone.iap2.stream import Iap2StreamLink

    link = Iap2StreamLink.__new__(Iap2StreamLink)
    link.dev = object()
    link._pending = b"\xef\xbe\x00\x00first"
    assert link.read_frame_chunk() == b"\xef\xbe\x00\x00first"
    assert link._pending == b""
