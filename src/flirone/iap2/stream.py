"""Bring a Lightning FLIR One up the way the iOS app does, and stream from it.

The sequence here is not designed, it is copied. It was recovered from an
inline USB analyser capture of the official app streaming from this camera, and
section 14 of docs/hardware-findings.md carries the trace. Four details matter,
and all four were got wrong before the capture existed:

- Authentication comes first, identification second. Doing it the other way
  round leaves the camera in a state where the next step kills the link.
- Exactly one External Accessory session is opened, for protocol 0 (config).
  The fileio and frame protocols are never opened by a real host; asking for
  either makes the camera leave the bus about 2ms later.
- `CameraFiles.zip` has to be opened, read, and then drained off endpoint 0x83.
  Leaving that pipe full stalls the camera: the stream request draws no reply
  at all.
- Streaming is switched on by `setOption stream=true` over the config session,
  not by opening a session. Frames then arrive as ordinary bulk transfers on
  endpoint 0x85.

A camera left mid-session from a previous run enumerates but says nothing, and
its control pipe returns EIO on the first read. Completing the handshake also
stops the camera's watchdog for good, so it never re-enumerates by itself
afterwards; close() therefore resets the device, which is what makes a second
session possible without unplugging anything.

open() decides whether a camera is usable by claiming it, not by watching the
bus. Presence proves nothing: a camera being held by another process looks
identical to a free one, and the reset does not reliably produce a visible gap.
Only one process can hold the camera, so the application and a probe cannot run
at once, and that is by far the most common reason a claim fails.
"""

from __future__ import annotations

import json
import os
import re
import time

import usb.core
import usb.util

from .. import protocol as proto
from .. import rosebud
from ..usb_link import FlirUsbError, find_device, make_backend
from . import control
from .session import Iap2Session

EA_SESSION = 1

# The camera answers a stream request within about a second, but only once the
# file has been opened and drained. These are generous rather than tuned.
_AUTH_WAIT = 4.0
_STEP_WAIT = 2.0
_DRAIN_QUIET = 1.0
# How long the frame endpoint is given to produce its first byte once the stream
# has been asked for. A working camera answers immediately; a powered-off one
# never does, and says nothing about it.
_FIRST_FRAME_WAIT = 6.0
# How long a present camera is given to disappear before it is assumed stuck.
_PRESENT_SETTLE = 8.0
# How long to allow for re-enumeration after resetting a stuck camera.
_RESET_SETTLE = 3.0


class Iap2StreamError(RuntimeError):
    pass


def _json_bodies(chunks: list[bytes]):
    """FLIR's replies are NUL-terminated JSON behind a 16-byte 0xCC header."""
    for chunk in chunks:
        for match in re.finditer(rb"\{.*?\}\x00", chunk, re.S):
            try:
                yield json.loads(match.group(0)[:-1].decode())
            except (ValueError, UnicodeDecodeError):
                continue


class Iap2StreamLink:
    """An authenticated iAP2 link to the camera, streaming frames on 0x85."""

    def __init__(self, backend=None) -> None:
        self._backend = backend or make_backend()
        self.session: Iap2Session | None = None
        self.dev = None
        self.sled: dict | None = None  # the camera's own description of itself
        self.stream_id: int | None = None
        self._claimed: list[int] = []
        # Bytes read while confirming the stream started. They are frame data
        # and must reach the assembler, so they are handed back first.
        self._pending = b""

    # -- lifecycle ----------------------------------------------------------

    def _appear(self, deadline: float) -> usb.core.Device | None:
        while time.monotonic() < deadline:
            dev = find_device(self._backend)
            if dev is not None:
                return dev
            time.sleep(0.001)
        return None

    def _wait_for_fresh_attach(self, timeout_s: float) -> usb.core.Device:
        """Get a camera that will actually talk, not one left mid-session.

        An idle camera watchdog-resets every few seconds, so waiting for it to
        vanish and come back costs nothing and guarantees one that announces
        itself. A camera whose handshake completed has no watchdog left, so it
        never vanishes; that one is reset in place instead, since the
        alternative is asking for the cable to be pulled.

        The reset does not always produce a visible gap on the bus, so after it
        the test becomes whether the device can be claimed, retried until the
        deadline. That covers the case the bus cannot distinguish: a camera held
        by another process looks exactly like a free one, and a camera plugged
        back in by hand satisfies the claim on its first attempt.
        """
        deadline = time.monotonic() + timeout_s

        if find_device(self._backend) is None:
            dev = self._appear(deadline)
            if dev is not None:
                return dev
            raise FlirUsbError("No FLIR One appeared. Check that it is connected.")

        settle = min(deadline, time.monotonic() + _PRESENT_SETTLE)
        while time.monotonic() < settle and find_device(self._backend) is not None:
            time.sleep(0.02)

        stuck = find_device(self._backend)
        if stuck is None:
            dev = self._appear(deadline)
            if dev is not None:
                return dev
            raise FlirUsbError("The FLIR One went away and did not come back.")

        try:
            stuck.reset()
        except usb.core.USBError:
            pass
        usb.util.dispose_resources(stuck)

        # The reset may or may not free it. Whether a device is usable is
        # decided by claiming it, not by watching the bus, so keep trying until
        # a claim succeeds or the deadline passes. A camera reconnected by hand
        # in the meantime satisfies this on its first attempt.
        while time.monotonic() < deadline:
            dev = self._appear(deadline)
            if dev is None:
                break
            try:
                usb.util.claim_interface(dev, 0)
            except usb.core.USBError:
                usb.util.dispose_resources(dev)
                time.sleep(0.2)
                continue
            usb.util.release_interface(dev, 0)
            return dev
        raise FlirUsbError(
            "No FLIR One could be claimed. Only one process can hold the camera, "
            "so the application or another probe is probably already using it. "
            "Close whatever has it open, or unplug the camera and reconnect."
        )

    def _send(self, header: bytes, body: bytes) -> None:
        """One FLIR command: the phone writes header and body separately."""
        assert self.session is not None
        self.session.send_ea(EA_SESSION, header)
        self.session.send_ea(EA_SESSION, body)

    def _step(self, message: control.Message, wait: float = _STEP_WAIT) -> None:
        assert self.session is not None
        self.session.send_control(message)
        self.session.pump(wait)
        if not self.session.alive:
            raise Iap2StreamError(f"link died sending {message}")

    def _collect(self, seconds: float) -> list[dict]:
        assert self.session is not None
        self.session.pump(seconds)
        pending, self.session.ea_pending = self.session.ea_pending, []
        return list(_json_bodies(pending))

    def open(self, timeout_s: float = 30.0) -> None:
        dev = self._wait_for_fresh_attach(timeout_s)
        for interface in (0, proto.IFACE_FILEIO, proto.IFACE_FRAME):
            usb.util.claim_interface(dev, interface)
            self._claimed.append(interface)

        session = Iap2Session(dev, log=lambda _m: None)
        if not session.connect():
            raise Iap2StreamError("iAP2 link never established")
        self.session, self.dev = session, dev

        # Authenticate, then identify. This order is the phone's.
        self._step(control.Message(control.REQUEST_AUTH_CERTIFICATE))
        self._step(
            control.Message(
                control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                [control.Parameter(0x0000, os.urandom(20))],
            ),
            _AUTH_WAIT,
        )
        self._step(control.Message(control.AUTH_SUCCEEDED), 0.5)
        self._step(control.Message(control.START_IDENTIFICATION))
        self._step(control.Message(control.IDENTIFICATION_ACCEPTED), 0.5)

        # One session, config only.
        self._step(control.start_ea_session(control.EA_PROTOCOL_CONFIG, EA_SESSION), 1.5)

        dev.set_interface_altsetting(interface=proto.IFACE_FILEIO, alternate_setting=1)
        for message in self._collect(0.5):
            if message.get("type") == "sledInformation":
                self.sled = message["data"]

        self._send(*rosebud.open_file("CameraFiles.zip"))
        for message in self._collect(_STEP_WAIT):
            if message.get("type") == "sledInformation":
                self.sled = message["data"]
            elif message.get("type") == "openFileStatus":
                self.stream_id = message["data"].get("streamIdentifier")
        if self.stream_id is None:
            raise Iap2StreamError("camera never returned a streamIdentifier")

        # Read the file and empty the pipe, or the stream request is ignored.
        self._send(*rosebud.read_file(self.stream_id))
        self._collect(0.3)
        self._send(*rosebud.read_file(self.stream_id))
        self._drain_fileio()

        dev.set_interface_altsetting(interface=proto.IFACE_FRAME, alternate_setting=1)
        self._send(*rosebud.set_option("stream", True))
        self._collect(1.0)
        if not session.alive:
            raise Iap2StreamError("link died requesting the stream")

        # The reply says nothing useful. `setOptionStatus` comes back with
        # value -1 whether the stream starts or not, so the only evidence that
        # it did is bytes on the frame endpoint. Without this check a camera
        # that never streams looks identical to one that does: the session
        # stays alive, the status stays "streaming", and no frame ever arrives.
        self._pending = self._await_first_frame_bytes()
        if not self._pending:
            raise Iap2StreamError(
                "the camera accepted the stream request and sent no frames. "
                "The sled controller answers the handshake whether or not the "
                "imager is powered, so this is what an unpowered imager looks "
                "like. Press the button on the camera and try again."
            )

    def _await_first_frame_bytes(self, seconds: float = _FIRST_FRAME_WAIT) -> bytes:
        """Whatever the frame endpoint produces first, or b'' if it stays quiet."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            chunk = self.read_frame_chunk(100)
            if chunk:
                return chunk
            self.keepalive()
            if not self.alive:
                raise Iap2StreamError("link died waiting for the first frame")
        return b""

    def _drain_fileio(self) -> int:
        """Empty CameraFiles.zip off 0x83 until it goes quiet."""
        assert self.dev is not None and self.session is not None
        total = 0
        quiet = time.monotonic()
        while time.monotonic() - quiet < _DRAIN_QUIET:
            try:
                chunk = bytes(self.dev.read(proto.EP_FILEIO_IN, 1 << 16, 100))
            except usb.core.USBTimeoutError:
                chunk = b""
            except usb.core.USBError:
                break
            if chunk:
                total += len(chunk)
                quiet = time.monotonic()
            self.session.pump(0.02)
        return total

    # -- streaming ----------------------------------------------------------

    def read_frame_chunk(self, timeout_ms: int = 100) -> bytes:
        """One bulk chunk from the frame endpoint. b'' on timeout."""
        assert self.dev is not None
        if self._pending:
            chunk, self._pending = self._pending, b""
            return chunk
        try:
            return bytes(self.dev.read(proto.EP_FRAME_IN, 1 << 16, timeout_ms))
        except usb.core.USBTimeoutError:
            return b""

    def keepalive(self, seconds: float = 0.02) -> None:
        """Answer whatever the camera sends, so the link is not left unacked."""
        if self.session is not None and self.session.alive:
            self.session.pump(seconds)
            self.session.ea_pending.clear()

    @property
    def alive(self) -> bool:
        return self.session is not None and self.session.alive

    def close(self) -> None:
        if self.session is not None and self.session.alive:
            # Leave the camera idle. A camera left streaming never re-attaches,
            # and the next open() waits for an attach that never comes.
            try:
                self._send(*rosebud.set_option("stream", False))
                self.session.pump(0.3)
            except (usb.core.USBError, AssertionError):
                pass
        if self.dev is not None:
            for interface in self._claimed:
                try:
                    usb.util.release_interface(self.dev, interface)
                except usb.core.USBError:
                    pass
            # A completed handshake stops the camera's watchdog for good (see
            # section 5), so it never re-enumerates on its own and the next
            # open() would wait forever for an attach. Reset it to put it back
            # in the pre-handshake state where it announces itself again.
            try:
                self.dev.reset()
            except usb.core.USBError:
                pass
            usb.util.dispose_resources(self.dev)
        self._claimed.clear()
        if self.session is not None:
            self.session.alive = False
        self.session = self.dev = None

    def __enter__(self) -> Iap2StreamLink:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
