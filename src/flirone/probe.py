"""Hardware probe: what does the camera present, and can we claim it?

Prints the descriptor tree read-only. With --open it also brings the link up
through FlirOneLink, so the configuration and claim path is exercised rather
than described.

An idle FLIR One re-enumerates every few seconds, so this polls for the device
instead of failing on the first miss. On Linux it will not appear at all
without the two host settings in the README; section 12 of
docs/hardware-findings.md says why.
"""

from __future__ import annotations

import argparse
import sys
from typing import TextIO

import usb.core
import usb.util

from . import protocol as proto
from .usb_link import FlirOneLink, FlirUsbError

_IFACE_NAMES = {
    proto.IFACE_CONTROL: "control",
    proto.IFACE_FILEIO: "fileio",
    proto.IFACE_FRAME: "frame",
}

_EP_TYPES = {
    usb.util.ENDPOINT_TYPE_CTRL: "control",
    usb.util.ENDPOINT_TYPE_ISO: "iso",
    usb.util.ENDPOINT_TYPE_BULK: "bulk",
    usb.util.ENDPOINT_TYPE_INTR: "interrupt",
}


def _string(dev: usb.core.Device, index: int) -> str | None:
    """A string descriptor, or None.

    Reading one is a control transfer, so it fails without device access. That
    is a permissions problem worth distinguishing from an absent string.
    """
    if not index:
        return None
    try:
        return usb.util.get_string(dev, index)
    except (usb.core.USBError, ValueError):
        return None


def _bcd(value: int) -> str:
    return f"{value >> 8:x}.{(value >> 4) & 0xF:x}{value & 0xF:x}"


def describe_endpoint(ep: usb.core.Endpoint) -> str:
    direction = (
        "in" if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN else "out"
    )
    kind = _EP_TYPES.get(usb.util.endpoint_type(ep.bmAttributes), "?")
    return f"EP 0x{ep.bEndpointAddress:02x} {kind} {direction}, {ep.wMaxPacketSize} bytes"


def describe(dev: usb.core.Device) -> list[str]:
    """The descriptor tree, as lines. Read-only: nothing is claimed or set."""
    lines = [
        f"FLIR One {dev.idVendor:04x}:{dev.idProduct:04x} on bus {dev.bus} address {dev.address}",
        f"  bcdDevice      {_bcd(dev.bcdDevice)}",
        f"  manufacturer   {_string(dev, dev.iManufacturer) or '(unreadable)'}",
        f"  product        {_string(dev, dev.iProduct) or '(unreadable)'}",
        f"  serial         {_string(dev, dev.iSerialNumber) or '(unreadable)'}",
        f"  configurations {dev.bNumConfigurations}",
    ]
    for cfg in dev:
        lines.append("")
        lines.append(f"configuration {cfg.bConfigurationValue}, {cfg.bNumInterfaces} interfaces")
        for intf in cfg:
            name = _IFACE_NAMES.get(intf.bInterfaceNumber, "")
            suffix = f" ({name})" if name else ""
            lines.append(
                f"  interface {intf.bInterfaceNumber} alt {intf.bAlternateSetting}"
                f", class 0x{intf.bInterfaceClass:02x}{suffix}"
            )
            endpoints = list(intf)
            if not endpoints:
                lines.append("    no endpoints")
            for ep in endpoints:
                lines.append(f"    {describe_endpoint(ep)}")
    return lines


def probe(wait_s: float = 20.0, open_link: bool = False, out: TextIO = sys.stdout) -> int:
    """Find the camera and report it. Returns a process exit status."""
    try:
        link = FlirOneLink()
    except FlirUsbError as exc:
        print(str(exc), file=out)
        return 2

    try:
        dev = link.wait_for_device(timeout_s=wait_s)
    except FlirUsbError as exc:
        print(str(exc), file=out)
        return 1

    for line in describe(dev):
        print(line, file=out)

    if not open_link:
        return 0

    print("", file=out)
    try:
        link.open(timeout_s=wait_s)
    except (FlirUsbError, usb.core.USBError) as exc:
        # Claiming is where a missing udev rule shows up, so say so rather than
        # letting a bare errno reach the user.
        print(f"could not open the camera: {exc}", file=out)
        print("On Linux this is usually the udev rule; see the README.", file=out)
        return 1
    print(f"opened: configuration {proto.CONFIGURATION}, interfaces 0/1/2 claimed", file=out)
    link.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report what the FLIR One presents over USB.")
    parser.add_argument(
        "--wait",
        type=float,
        default=20.0,
        metavar="SECONDS",
        help="how long to poll for the camera (it re-enumerates when idle)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        dest="open_link",
        help="also set the configuration and claim the interfaces",
    )
    args = parser.parse_args(argv)
    return probe(wait_s=args.wait, open_link=args.open_link)


if __name__ == "__main__":
    raise SystemExit(main())
