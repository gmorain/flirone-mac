"""Bring the vendor interfaces up FIRST, then handshake, then request frames.

Previous attempts talked iAP2 on interface 0 while interfaces 1 and 2 sat in
alternate setting 0, where they expose no endpoints at all. If the camera
starts its frame protocol and finds no pipe to deliver into, resetting is a
plausible response. So open every pipe before asking for video.
"""
from __future__ import annotations
import os, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone import protocol as fproto
from flirone.iap2 import control
from flirone.iap2.session import Iap2Session, find_camera
from flirone.usb_link import make_backend

B = make_backend()
quiet = lambda m: None

dev = find_camera(backend=B)
if dev is None:
    raise SystemExit("no camera")
try:
    dev.reset()
except usb.core.USBError:
    pass
usb.util.dispose_resources(dev)
time.sleep(1.5)

dev = find_camera(backend=B)
if dev is None:
    raise SystemExit("gone after reset")

# Every interface claimed and both streaming interfaces in alt 1, so all pipes
# exist before the camera is asked to use them.
for iface in (0, 1, 2):
    usb.util.claim_interface(dev, iface)
for iface, alt in ((2, 0), (1, 0), (1, 1), (2, 1)):
    dev.set_interface_altsetting(interface=iface, alternate_setting=alt)
print("vendor interfaces up: 0x83 and 0x85 pipes exist")

session = Iap2Session(dev, log=quiet)
if not session.connect():
    raise SystemExit("link handshake failed")
def ctl(m, s):
    session.send_control(m); return session.pump(s)
ctl(control.Message(control.START_IDENTIFICATION), 1.5)
ctl(control.Message(control.IDENTIFICATION_ACCEPTED), 0.8)
ctl(control.Message(control.REQUEST_AUTH_CERTIFICATE), 1.5)
ctl(control.Message(control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                    [control.Parameter(0x0000, os.urandom(20))]), 3.5)
ctl(control.Message(control.AUTH_SUCCEEDED), 0.8)
print(f"authenticated, alive={session.alive}")

EA_LINK = session.session_id(0x02)
ctl(control.start_ea_session(control.EA_PROTOCOL_CONFIG, 1), 1.0)
print(f"config session open, alive={session.alive}")
session.send_control(control.start_ea_session(control.EA_PROTOCOL_FRAME, 2))
print(f"frame session requested")

import subprocess
on_bus = lambda: b"FLIR" in subprocess.run(["ioreg","-p","IOUSB","-w0","-l"],capture_output=True).stdout
bulk_errors: dict[str, int] = {}
asm_ea = fproto.FrameAssembler()
asm_bulk = fproto.FrameAssembler()
ea_bytes = bulk_bytes = 0
frames = []
t0 = time.monotonic()
while time.monotonic() - t0 < 12 and len(frames) < 3:
    # Endpoint 0x85, where the Android variant delivers frames.
    try:
        data = bytes(dev.read(0x85, 1 << 20, 50))
        if data:
            bulk_bytes += len(data)
            frames.extend(asm_bulk.feed(data))
    except usb.core.USBTimeoutError:
        pass
    except usb.core.USBError as exc:
        bulk_errors[str(exc)] = bulk_errors.get(str(exc), 0) + 1
        if bulk_errors[str(exc)] == 1:
            print(f"  t+{time.monotonic()-t0:5.2f} 0x85: {exc}  (on bus: {on_bus()})")
        time.sleep(0.05)
    # And the iAP2 control channel.
    if session.alive:
        chunk = session._read(50)
        if chunk:
            for pkt in session._decode_all(chunk):
                if pkt.payload and pkt.session == EA_LINK:
                    body = pkt.payload[2:]
                    ea_bytes += len(body)
                    frames.extend(asm_ea.feed(body))
                session.ack()
        else:
            session.ack()

print(f"\nalive={session.alive}  0x85 bytes={bulk_bytes}  EA bytes={ea_bytes}  frames={len(frames)}")
print(f"0x85 errors: {bulk_errors}")
print(f"on bus at end: {on_bus()}")
if frames:
    f = frames[0]
    print(f"*** FRAME  thermal={len(f.thermal)}B jpeg={len(f.jpeg)}B status={len(f.status)}B geom={f.geometry}")
    out = Path("/tmp/flir_capture"); out.mkdir(parents=True, exist_ok=True)
    (out/"thermal.bin").write_bytes(f.thermal)
    (out/"visible.jpg").write_bytes(f.jpeg)
    (out/"status.json").write_bytes(f.status)
    print("saved /tmp/flir_capture")
session.close()
