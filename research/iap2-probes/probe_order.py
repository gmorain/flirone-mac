"""Does the order of authentication vs identification matter?"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone import protocol as fproto
from flirone.iap2 import control
from flirone.iap2.session import Iap2Session, find_camera
from flirone.usb_link import make_backend

B = make_backend()
C = control

def fresh():
    dev = find_camera(backend=B)
    if dev is None: return None
    try: dev.reset()
    except usb.core.USBError: pass
    usb.util.dispose_resources(dev)
    time.sleep(1.5)
    dev = find_camera(backend=B)
    if dev is None: return None
    try:
        usb.util.claim_interface(dev, 0)
    except usb.core.USBError:
        return None
    s = Iap2Session(dev, log=lambda m: None)
    return s if s.connect() else None

AUTH = [
    ("cert",  C.Message(C.REQUEST_AUTH_CERTIFICATE), 1.5),
    ("chal",  C.Message(C.REQUEST_AUTH_CHALLENGE_RESPONSE,
                        [C.Parameter(0x0000, os.urandom(20))]), 3.5),
    ("ok",    C.Message(C.AUTH_SUCCEEDED), 0.8),
]
IDENT = [
    ("startid", C.Message(C.START_IDENTIFICATION), 1.5),
    ("idacc",   C.Message(C.IDENTIFICATION_ACCEPTED), 0.8),
]

def trial(label, phases):
    session = fresh()
    if session is None:
        print(f"{label}: no link"); return False
    for name, msg, secs in phases:
        if not session.alive:
            print(f"{label}: died before {name}"); session.close(); return False
        session.send_control(msg)
        session.pump(secs)
    if not session.alive:
        print(f"{label}: died during handshake"); session.close(); return False

    EA_LINK = session.session_id(0x02)
    session.send_control(C.start_ea_session(C.EA_PROTOCOL_FRAME, 1))
    asm = fproto.FrameAssembler()
    ea_bytes, frames = 0, []
    t0 = time.monotonic()
    while session.alive and time.monotonic() - t0 < 6 and len(frames) < 2:
        data = session._read(100)
        if not data:
            session.ack(); continue
        for pkt in session._decode_all(data):
            if pkt.payload and pkt.session == EA_LINK:
                body = pkt.payload[2:]
                ea_bytes += len(body); frames.extend(asm.feed(body))
            session.ack()
    print(f"{label}: alive={session.alive} ea_bytes={ea_bytes} frames={len(frames)}")
    if frames:
        f = frames[0]
        print(f"  *** FRAME thermal={len(f.thermal)}B jpeg={len(f.jpeg)}B geom={f.geometry}")
        out = Path("/tmp/flir_capture"); out.mkdir(parents=True, exist_ok=True)
        (out/"thermal.bin").write_bytes(f.thermal)
        (out/"visible.jpg").write_bytes(f.jpeg)
        (out/"status.json").write_bytes(f.status)
    session.close(); time.sleep(0.8)
    return bool(frames)

for label, phases in [
    ("identify then auth (what we did)", IDENT + AUTH),
    ("auth then identify (spec order) ", AUTH + IDENT),
    ("auth only, no identification    ", AUTH),
    ("identification only, no auth    ", IDENT),
]:
    if trial(label, phases):
        print(">>> VIDEO"); break
