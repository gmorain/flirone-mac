"""Experiment battery: why does SetAlternateInterface fail, and what works instead?

Separates three hypotheses:
  A. the device is asleep/detached  -> plain control transfers fail too
  B. the device rejects SET_INTERFACE -> raw control transfer fails
  C. IOKit-only problem            -> raw control succeeds, IOKit call does not
"""
from __future__ import annotations

import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone import protocol as proto
from flirone.usb_link import make_backend

B = make_backend()

def grab(timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = usb.core.find(idVendor=proto.VENDOR_ID, idProduct=proto.PRODUCT_ID, backend=B)
        if d: return d
        time.sleep(0.005)
    return None

def run(label, fn):
    try:
        v = fn()
        print(f"  PASS  {label}" + (f"  -> {v!r}" if v is not None else ""))
        return True, v
    except Exception as e:
        print(f"  FAIL  {label}: {type(e).__name__}: {e}")
        return False, None

def fresh():
    d = grab()
    if d is None:
        print("  (camera absent)")
    return d

print("=" * 62)
print("TEST 1  liveness: standard control transfers")
print("=" * 62)
d = fresh()
if d:
    run("GET_CONFIGURATION (0x80,0x08)", lambda: bytes(d.ctrl_transfer(0x80, 0x08, 0, 0, 1, 1000)))
    run("GET_DESCRIPTOR device (0x80,0x06)", lambda: bytes(d.ctrl_transfer(0x80, 0x06, 0x0100, 0, 18, 1000))[:8])
    run("GET_STATUS device (0x80,0x00)", lambda: bytes(d.ctrl_transfer(0x80, 0x00, 0, 0, 2, 1000)))
    run("string descriptor product", lambda: usb.util.get_string(d, d.iProduct))
    usb.util.dispose_resources(d)

print()
print("=" * 62)
print("TEST 2  raw SET_INTERFACE control transfer (the Linux path)")
print("=" * 62)
d = fresh()
if d:
    for i in (0, 1, 2):
        usb.util.claim_interface(d, i)
    run("ctrl SET_INTERFACE iface2 alt0", lambda: d.ctrl_transfer(0x01, 0x0B, 0, 2, None, 1000))
    run("ctrl SET_INTERFACE iface1 alt0", lambda: d.ctrl_transfer(0x01, 0x0B, 0, 1, None, 1000))
    run("ctrl SET_INTERFACE iface1 alt1", lambda: d.ctrl_transfer(0x01, 0x0B, 1, 1, None, 1000))
    run("ctrl SET_INTERFACE iface2 alt1", lambda: d.ctrl_transfer(0x01, 0x0B, 1, 2, None, 1000))
    run("GET_INTERFACE iface2 (0x81,0x0A)", lambda: bytes(d.ctrl_transfer(0x81, 0x0A, 0, 2, 1, 1000)))
    usb.util.dispose_resources(d)

print()
print("=" * 62)
print("TEST 3  bulk on interface 0 (endpoints exist at alt 0)")
print("=" * 62)
d = fresh()
if d:
    usb.util.claim_interface(d, 0)
    def read81():
        try:
            return bytes(d.read(0x81, 4096, 500))[:32]
        except usb.core.USBTimeoutError:
            return "TIMEOUT (endpoint alive, no data queued)"
    run("bulk read EP 0x81", read81)
    run("bulk write EP 0x02 (16-byte framing hdr)",
        lambda: d.write(0x02, bytes.fromhex("cc0100000100000041000000f8b3f700"), 1000))
    usb.util.dispose_resources(d)

print()
print("=" * 62)
print("TEST 4  IOKit SetAlternateInterface variants")
print("=" * 62)
for desc, claims, steps in [
    ("claim 0,1,2 then alt(2,0) then alt(2,1)", (0,1,2), [(2,0),(2,1)]),
    ("claim 2 only, alt(2,1)", (2,), [(2,1)]),
    ("claim 1,2, alt(1,1) then alt(2,1)", (1,2), [(1,1),(2,1)]),
]:
    print(f"\n {desc}")
    d = fresh()
    if not d: continue
    ok = True
    for i in claims:
        ok &= run(f"  claim({i})", lambda i=i: usb.util.claim_interface(d, i))[0]
    for (i, a) in steps:
        run(f"  set_altsetting({i},{a})",
            lambda i=i, a=a: d.set_interface_altsetting(interface=i, alternate_setting=a))
    usb.util.dispose_resources(d)

print()
print("=" * 62)
print("TEST 5  raw control SET_INTERFACE, then try reading 0x85 anyway")
print("=" * 62)
d = fresh()
if d:
    for i in (0,1,2):
        usb.util.claim_interface(d, i)
    d.ctrl_transfer(0x01, 0x0B, 1, 1, None, 1000)
    d.ctrl_transfer(0x01, 0x0B, 1, 2, None, 1000)
    def read85():
        try:
            return f"{len(bytes(d.read(0x85, 65536, 500)))} bytes"
        except usb.core.USBTimeoutError:
            return "TIMEOUT"
    run("read EP 0x85 after raw control start", read85)
    usb.util.dispose_resources(d)
