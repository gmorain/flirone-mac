"""Pounce on the camera the moment it enumerates, run the paced init, stream.

The camera watchdogs itself every ~5.7s until a host drives it, so we poll at
1ms and start the handshake immediately, pacing the steps the way the reference
driver does (its init runs across separate main-loop iterations, with 0x81 and
0x83 drained between each).
"""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone import protocol as proto
from flirone.usb_link import make_backend

B = make_backend()
OUT = Path("/tmp/flir_capture")

def pounce(timeout=30.0):
    """Return a device handle as soon as one appears."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = usb.core.find(idVendor=0x09CB, idProduct=0x1996, backend=B)
        if d is not None:
            return d
        time.sleep(0.001)
    return None

def drain(dev):
    for ep, size in ((0x81, 4096), (0x83, 4096)):
        try:
            dev.read(ep, size, 10)
        except (usb.core.USBError, ValueError):
            # ValueError: the endpoint does not exist in the current alt setting.
            pass

def session(seconds=20.0):
    dev = pounce()
    if dev is None:
        print("  no device"); return 0, None
    t_open = time.monotonic()
    try:
        for i in (0, 1, 2):
            usb.util.claim_interface(dev, i)
        # Paced init: one step per iteration, endpoints drained between steps.
        for iface, alt in ((2, 0), (1, 0), (1, 1)):
            dev.set_interface_altsetting(interface=iface, alternate_setting=alt)
            drain(dev)
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        drain(dev)
        try:
            dev.ctrl_transfer(0x01, 0x0B, 1, 2, b"\x00\x00", 200)
        except usb.core.USBError:
            pass
        print(f"  init done at t+{time.monotonic()-t_open:.3f}s")
    except usb.core.USBError as e:
        print(f"  init failed at t+{time.monotonic()-t_open:.3f}s: {e}")
        usb.util.dispose_resources(dev)
        return 0, None

    asm = proto.FrameAssembler()
    frames = []
    nbytes = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            data = bytes(dev.read(0x85, 65536, 100))
            if data:
                nbytes += len(data)
                frames.extend(asm.feed(data))
        except usb.core.USBTimeoutError:
            pass
        except usb.core.USBError as e:
            print(f"  0x85 died at t+{time.monotonic()-t_open:.2f}s after {nbytes}B: {e}")
            break
        drain(dev)
        if len(frames) >= 30:
            break
    alive = time.monotonic() - t_open
    print(f"  survived {alive:.2f}s  0x85 bytes={nbytes} frames={len(frames)}")
    try: usb.util.dispose_resources(dev)
    except Exception: pass
    return nbytes, frames[0] if frames else None

for attempt in range(1, 7):
    print(f"attempt {attempt}")
    nbytes, frame = session()
    if frame is not None:
        print("\n>>> GOT A FRAME")
        print(f"thermal={len(frame.thermal)}B jpeg={len(frame.jpeg)}B status={len(frame.status)}B geom={frame.geometry}")
        print(f"jpeg magic={frame.jpeg[:3].hex()} status={frame.status[:250].decode('utf-8','replace')}")
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT/"thermal.bin").write_bytes(frame.thermal)
        (OUT/"visible.jpg").write_bytes(frame.jpeg)
        (OUT/"status.json").write_bytes(frame.status)
        print(f"saved to {OUT}")
        break
    if nbytes:
        print(">>> bytes flowed on 0x85 but no complete frame"); break
