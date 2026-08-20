"""Drives an iAP2 session over the camera's control endpoints.

We play the role of the Apple device. That matters for authentication: in MFi
the accessory proves itself to the device, so this side issues challenges and
inspects answers rather than producing any Apple-signed material.
"""

from __future__ import annotations

import struct
import time
from collections.abc import Callable

import usb.core
import usb.util

from . import control
from .link import ACK, SYN, LinkError, LinkSync, Packet, decode, split_stream

EP_IN = 0x81
EP_OUT = 0x02

# The camera negotiates a 65535-byte maximum iAP2 packet, and a single video
# frame is close to that on its own (~39KB thermal + ~25KB JPEG). A USB transfer
# larger than the buffer we hand libusb overflows and is reported as an I/O
# error, which is indistinguishable from the device vanishing. Read generously.
READ_BUFFER = 1 << 20

# The camera opens in iAP1 and only offers iAP2 once the host writes to it.
IAP1_HELLO = bytes.fromhex("ff550200ee10")


def find_camera(timeout: float = 25.0, backend=None):
    """Poll hard until the camera enumerates."""
    from ..usb_link import make_backend

    backend = backend or make_backend()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dev = usb.core.find(idVendor=0x09CB, idProduct=0x1996, backend=backend)
        if dev is not None:
            return dev
        time.sleep(0.001)
    return None


def open_session(log: Callable[[str], None] = print, backend=None) -> Iap2Session | None:
    """Reset the camera, catch it on the way up, and bring the iAP2 link up.

    The reset is not optional. A camera left in a half-open session from a
    previous run stops emitting anything at all, and only a USB reset clears it.
    It then retransmits its SYN roughly once a second and gives up after three
    tries, so the handshake has to be waiting for it.
    """
    dev = find_camera(backend=backend)
    if dev is None:
        return None
    try:
        dev.reset()
    except usb.core.USBError as exc:
        log(f"  reset failed: {exc}")
    usb.util.dispose_resources(dev)
    time.sleep(1.5)

    dev = find_camera(backend=backend)
    if dev is None:
        return None
    # A handle left open by a previous run makes macOS refuse the claim, so
    # give it a moment to be reaped rather than failing outright.
    for attempt in range(5):
        try:
            usb.util.claim_interface(dev, 0)
            break
        except usb.core.USBError as exc:
            if attempt == 4:
                log(f"  claim failed after retries: {exc}")
                return None
            usb.util.dispose_resources(dev)
            time.sleep(0.8)
            dev = find_camera(backend=backend)
            if dev is None:
                return None
    session = Iap2Session(dev, log=log)
    return session if session.connect() else None


class Iap2Session:
    def __init__(
        self, dev, log: Callable[[str], None] = print, our_sync: LinkSync | None = None
    ) -> None:
        self.dev = dev
        self.log = log
        # What we advertise in our SYN|ACK. Echoing the accessory's own values
        # back is not automatically safe: a small device may size its buffers
        # from what the host claims it can send.
        self.our_sync = our_sync
        self.our_seq = 0x00
        self.their_seq = 0x00
        self.sync: LinkSync | None = None
        self.established = False
        self.alive = True
        self.t0 = time.monotonic()

    # -- plumbing -----------------------------------------------------------

    def _now(self) -> float:
        return time.monotonic() - self.t0

    def _read(self, timeout_ms: int = 50) -> bytes:
        try:
            return bytes(self.dev.read(EP_IN, READ_BUFFER, timeout_ms))
        except usb.core.USBTimeoutError:
            return b""
        except usb.core.USBError as exc:
            self.log(f"  t+{self._now():5.2f}  read failed: {exc}")
            self.alive = False
            return b""

    def _write(self, data: bytes) -> None:
        if not self.alive:
            return
        try:
            self.dev.write(EP_OUT, data, 500)
        except usb.core.USBError as exc:
            self.log(f"  t+{self._now():5.2f}  write failed: {exc}")
            self.alive = False

    def send(self, packet: Packet) -> None:
        self.log(f"  t+{self._now():5.2f}  TX {packet}")
        self._write(packet.encode())

    def session_id(self, kind: int) -> int:
        if self.sync is None:
            return 1
        spec = self.sync.session_for(kind)
        return spec.identifier if spec else 1

    # -- handshake ----------------------------------------------------------

    def connect(self, timeout: float = 8.0) -> bool:
        """Nudge the camera into iAP2 and complete the link handshake."""
        nudged = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.established:
            data = self._read()
            if not data:
                continue
            if data == IAP1_HELLO:
                if not nudged:
                    self._write(IAP1_HELLO)
                    nudged = True
                    self.log(f"  t+{self._now():5.2f}  iAP1 hello, nudging to iAP2")
                continue
            for packet in self._decode_all(data):
                if packet.is_syn and not packet.is_ack:
                    self.sync = LinkSync.decode(packet.payload)
                    self.log(f"           accessory sync: {self.sync}")
                    reply_sync = self.our_sync
                    if reply_sync is None:
                        reply_sync = self.sync
                    else:
                        # Keep the session list the accessory offered; only our
                        # own buffering limits differ.
                        reply_sync.sessions = self.sync.sessions
                    self.log(f"           our sync:       {reply_sync}")
                    self.send(Packet(SYN | ACK, self.our_seq, packet.seq, 0, reply_sync.encode()))
                    self.our_seq = (self.our_seq + 1) & 0xFF
                elif packet.is_ack and self.sync is not None:
                    self.established = True
                    self.log(f"  t+{self._now():5.2f}  link established")
        return self.established

    def _decode_all(self, data: bytes) -> list[Packet]:
        packets = []
        for raw in split_stream(data):
            try:
                packet = decode(raw)
            except LinkError as exc:
                self.log(f"  t+{self._now():5.2f}  undecodable: {exc}  {raw[:32].hex()}")
                continue
            self.log(f"  t+{self._now():5.2f}  RX {packet}")
            self.their_seq = packet.seq
            packets.append(packet)
        return packets

    # -- control session ----------------------------------------------------

    def send_control(self, message: control.Message) -> None:
        session = self.session_id(0x00)
        packet = Packet(ACK, self.our_seq, self.their_seq, session, message.encode())
        self.log(f"  t+{self._now():5.2f}  TX control {message}")
        self.send(packet)
        self.our_seq = (self.our_seq + 1) & 0xFF

    def send_ea(self, ea_session_id: int, data: bytes) -> None:
        """Send payload on an External Accessory session.

        EA data rides the EA link session, prefixed with the 2-byte identifier
        of the EA protocol session it belongs to.
        """
        payload = struct.pack(">H", ea_session_id) + data
        packet = Packet(ACK, self.our_seq, self.their_seq, self.session_id(0x02), payload)
        self.log(f"  t+{self._now():5.2f}  TX ea[{ea_session_id}] {len(data)}B {data[:60]!r}")
        self.send(packet)
        self.our_seq = (self.our_seq + 1) & 0xFF

    def close(self) -> None:
        """Release the device so the next session can claim it."""
        self.alive = False
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass

    def ack(self) -> None:
        self._write(Packet(ACK, self.our_seq, self.their_seq, 0).encode())

    def pump(self, seconds: float) -> list[control.Message]:
        """Read for a while, acknowledging and parsing control messages."""
        messages: list[control.Message] = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and self.alive:
            data = self._read()
            if not data:
                continue
            for packet in self._decode_all(data):
                if not packet.payload:
                    continue
                try:
                    message = control.decode(packet.payload)
                except control.ControlError as exc:
                    self.log(
                        f"           not a control message ({exc}): {packet.payload[:48].hex(' ')}"
                    )
                    continue
                self.log(f"           >>> {message}")
                for parameter in message.parameters:
                    self.log(f"               {parameter}")
                messages.append(message)
                self.ack()
        return messages
