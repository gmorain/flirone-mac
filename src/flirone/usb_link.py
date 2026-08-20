"""USB transport for the FLIR One.

Two macOS-specific details drive the design here.

Starting the stream. The reference Linux driver starts streaming with a raw
control transfer bmRequestType=0x01, bRequest=0x0B. That is the *standard*
SET_INTERFACE request: alternate setting 0 is idle, 1 is running. Linux passes
it straight to the device. macOS/IOKit tracks alternate settings itself and
rebuilds its pipe objects on SetAlternateInterface, so a raw control transfer
changes the device's state while leaving the host's pipes bound to alt 0, and
every subsequent bulk read times out with 0xe0004061. That is the unresolved
failure in libusb issue #729. StartMode.ALT_SETTING goes through the proper
API; StartMode.CONTROL reproduces the Linux path for comparison.

Catching the device. An idle FLIR One re-enumerates every few seconds, so
open() polls hard and claims it the moment it appears.
"""

from __future__ import annotations

import ctypes.util
import logging
import time
from enum import StrEnum
from pathlib import Path

import usb.backend.libusb1
import usb.core
import usb.util

from . import protocol as proto

log = logging.getLogger(__name__)

# Homebrew on Apple Silicon installs outside the loader's default search path,
# so ctypes.util.find_library often misses it.
_LIBUSB_CANDIDATES = (
    "/opt/homebrew/lib/libusb-1.0.dylib",
    "/usr/local/lib/libusb-1.0.dylib",
)

READ_CHUNK = 65536


class StartMode(StrEnum):
    ALT_SETTING = "alt"  # IOKit-aware, expected to work on macOS
    CONTROL = "control"  # raw SET_INTERFACE, as the Linux reference driver does


class FlirUsbError(RuntimeError):
    pass


def find_libusb() -> str | None:
    for candidate in _LIBUSB_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return ctypes.util.find_library("usb-1.0")


def make_backend():
    path = find_libusb()
    if path is None:
        raise FlirUsbError("libusb 1.0 not found. Install it with: brew install libusb")
    backend = usb.backend.libusb1.get_backend(find_library=lambda _: path)
    if backend is None:
        raise FlirUsbError(f"libusb at {path} could not be loaded")
    return backend


def find_device(backend=None):
    return usb.core.find(idVendor=proto.VENDOR_ID, idProduct=proto.PRODUCT_ID, backend=backend)


class FlirOneLink:
    """An open connection to the camera."""

    def __init__(self, start_mode: StartMode = StartMode.ALT_SETTING, backend=None) -> None:
        self.start_mode = start_mode
        self._backend = backend or make_backend()
        self.dev: usb.core.Device | None = None
        self._claimed: list[int] = []

    # -- lifecycle ----------------------------------------------------------

    def wait_for_device(self, timeout_s: float = 20.0, poll_s: float = 0.02):
        """Poll until the camera enumerates. It cycles, so poll fast."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            dev = find_device(self._backend)
            if dev is not None:
                return dev
            time.sleep(poll_s)
        raise FlirUsbError(
            "No FLIR One found. Power the camera on; it re-enumerates every few "
            "seconds when idle, so leave it powered while connecting."
        )

    def open(self, timeout_s: float = 20.0) -> None:
        dev = self.wait_for_device(timeout_s)

        # The camera boots into a configuration that carries no streaming
        # interfaces; configuration 3 is the one with interfaces 0/1/2.
        try:
            if dev.get_active_configuration().bConfigurationValue != proto.CONFIGURATION:
                dev.set_configuration(proto.CONFIGURATION)
        except usb.core.USBError:
            dev.set_configuration(proto.CONFIGURATION)

        for iface in (proto.IFACE_CONTROL, proto.IFACE_FILEIO, proto.IFACE_FRAME):
            usb.util.claim_interface(dev, iface)
            self._claimed.append(iface)

        self.dev = dev
        log.info(
            "opened FLIR One, configuration %d, interfaces %s",
            proto.CONFIGURATION,
            self._claimed,
        )

    def close(self) -> None:
        if self.dev is None:
            return
        try:
            self.stop_stream()
        except usb.core.USBError:
            pass
        for iface in self._claimed:
            try:
                usb.util.release_interface(self.dev, iface)
            except usb.core.USBError:
                pass
        self._claimed.clear()
        usb.util.dispose_resources(self.dev)
        self.dev = None

    def __enter__(self) -> FlirOneLink:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- interface state ----------------------------------------------------

    def _set_alt(self, interface: int, alt: int) -> None:
        assert self.dev is not None
        if self.start_mode is StartMode.ALT_SETTING:
            self.dev.set_interface_altsetting(interface=interface, alternate_setting=alt)
        else:
            # bmRequestType 0x01 = host-to-device, standard, recipient interface.
            self.dev.ctrl_transfer(0x01, 0x0B, alt, interface, None, 200)

    def stop_stream(self) -> None:
        self._set_alt(proto.IFACE_FRAME, proto.ALT_IDLE)

    def drain(self) -> None:
        """Read and discard whatever is queued on the status endpoints.

        The camera runs a small state machine over 0x81/0x83 and can stall if
        those are never emptied, so the init steps are paced around them the
        way the reference driver does.
        """
        assert self.dev is not None
        for ep in (proto.EP_CONTROL_IN, proto.EP_FILEIO_IN):
            try:
                self.dev.read(ep, 4096, 10)
            except (usb.core.USBError, ValueError):
                # ValueError: endpoint absent in the current alternate setting.
                pass

    def start_stream(self) -> None:
        """Run the init handshake and bring endpoint 0x85 up.

        Order matters. Both streaming interfaces must be driven to alt 0 first
        so IOKit's view matches the device, otherwise SetAlternateInterface
        returns kIOReturnNotResponding.
        """
        assert self.dev is not None
        for iface, alt in (
            (proto.IFACE_FRAME, proto.ALT_IDLE),
            (proto.IFACE_FILEIO, proto.ALT_IDLE),
            (proto.IFACE_FILEIO, proto.ALT_STREAMING),
            (proto.IFACE_FRAME, proto.ALT_STREAMING),
        ):
            self._set_alt(iface, alt)
            self.drain()

    # -- I/O ----------------------------------------------------------------

    def read_frame_chunk(self, timeout_ms: int = 200) -> bytes:
        """Read one bulk chunk from the frame endpoint. b'' on timeout."""
        assert self.dev is not None
        try:
            data = self.dev.read(proto.EP_FRAME_IN, READ_CHUNK, timeout_ms)
        except usb.core.USBTimeoutError:
            return b""
        return bytes(data)

    def drain_status(self, timeout_ms: int = 10) -> bytes:
        """Read whatever the camera has queued on the control-in endpoint."""
        assert self.dev is not None
        try:
            return bytes(self.dev.read(proto.EP_CONTROL_IN, READ_CHUNK, timeout_ms))
        except usb.core.USBError:
            return b""

    def send_fileio(self, payload: bytes) -> None:
        """Write one framed command to the file-IO endpoint."""
        assert self.dev is not None
        self.dev.write(proto.EP_FILEIO_OUT, payload, 1000)
