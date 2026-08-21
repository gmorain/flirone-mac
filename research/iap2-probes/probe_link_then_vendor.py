"""Link sync only, no authentication, then the reference vendor start sequence.

Section 10 found the vendor command channel inert after the iAP2 handshake, and
guessed the cause: "after the iAP2 handshake the camera is in iAP2 mode on EP
0x02 and ignores raw vendor bytes there". If that mode switch happens at
identification rather than at link sync, then answering the SYN to stop the
4.69s watchdog and then speaking plain vendor protocol should work.

That combination has never been tried. probe_iap2_link.py syncs and idles.
probe_stream3.py speaks vendor with no link at all, so the watchdog kills it.
Everything else authenticates first.

The vendor steps mirror probe_stream.py, which mirrors the reference driver:
alt 2->0, 1->0, 1->1, the 0xCC-framed openFile/readFile on EP 0x04, then
alt 2->1.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import usb.core
import usb.util

from flirone import protocol as fproto
from flirone import rosebud
from flirone.iap2.session import open_session

VERBOSE = "-v" in sys.argv
CMD_EP = 0x02 if "--ep02" in sys.argv else 0x04


def log(msg):
    if VERBOSE:
        print(msg)


def drain(dev):
    for ep in (0x81, 0x83):
        try:
            dev.read(ep, 4096, 10)
        except (usb.core.USBError, ValueError):
            pass


def main() -> int:
    session = open_session(log=log)
    if session is None or not session.established:
        print("no link established")
        return 1
    print(f"link established, no authentication sent (alive={session.alive})")

    dev = session.dev
    session.pump(1.0)
    if not session.alive:
        print("FAIL: link died immediately after sync, before any vendor traffic")
        return 1
    print(f"held the link for 1s with acks only (alive={session.alive})")

    for iface in (1, 2):
        try:
            usb.util.claim_interface(dev, iface)
        except usb.core.USBError as exc:
            print(f"FAIL: claim interface {iface}: {exc}")
            return 1
    print("claimed interfaces 1 and 2")

    # Reference driver order.
    for iface, alt in ((2, 0), (1, 0), (1, 1)):
        try:
            dev.set_interface_altsetting(interface=iface, alternate_setting=alt)
        except usb.core.USBError as exc:
            print(f"FAIL: alt {iface}->{alt}: {exc}")
            return 1
        drain(dev)
        print(f"  alt {iface} -> {alt}   link alive={session.alive}")

    for label, (header, body) in (
        ("openFile", rosebud.open_file("CameraFiles.zip")),
        ("readFile", rosebud.read_file()),
    ):
        try:
            dev.write(CMD_EP, header, 1000)
            dev.write(CMD_EP, body, 1000)
            print(f"  wrote {label} to EP 0x{CMD_EP:02x}   link alive={session.alive}")
        except usb.core.USBError as exc:
            print(f"  {label} to EP 0x{CMD_EP:02x} FAILED: {exc}")

    try:
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        print(f"  alt 2 -> 1   link alive={session.alive}")
    except usb.core.USBError as exc:
        print(f"FAIL: alt 2->1: {exc}")
        return 1

    # Listen on the fileio IN as well as the frame IN. A reply to openFile
    # would arrive on 0x83, and discarding it would look like silence.
    assembler = fproto.FrameAssembler()
    seen = {0x83: 0, 0x85: 0}
    first = {}
    frames = 0
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        for ep in (0x83, 0x85):
            try:
                chunk = bytes(dev.read(ep, 65536, 50))
            except usb.core.USBTimeoutError:
                continue
            except usb.core.USBError as exc:
                print(f"0x{ep:02x} read failed: {exc}")
                deadline = 0
                break
            if not chunk:
                continue
            seen[ep] += len(chunk)
            first.setdefault(ep, chunk[:64])
            if ep == 0x85:
                frames += len(assembler.feed(chunk))
        session.pump(0.05)
        if not session.alive:
            print("link died while listening")
            break

    print("")
    for ep, count in seen.items():
        print(f"EP 0x{ep:02x} bytes: {count}")
        if ep in first:
            print(f"   first bytes: {first[ep].hex(' ')}")
    print(f"frames:      {frames}   desyncs: {assembler.desync_count}")
    print(f"link alive:  {session.alive}")
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
