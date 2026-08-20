"""Is our acknowledgement policy killing the frame session?

ack() sends an empty packet reusing the sequence number of the data packet we
just sent. The camera may read that as a malformed retransmission while it is
busy starting the sensor. Try three policies.
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone import protocol as fproto
from flirone.iap2 import control as C
from flirone.iap2.link import ACK, Packet
from flirone.iap2.session import Iap2Session, find_camera
from flirone.usb_link import make_backend

B = make_backend()

def fresh():
    dev = find_camera(backend=B)
    if dev is None: return None
    try: dev.reset()
    except usb.core.USBError: pass
    usb.util.dispose_resources(dev)
    time.sleep(1.5)
    dev = find_camera(backend=B)
    if dev is None: return None
    try: usb.util.claim_interface(dev, 0)
    except usb.core.USBError: return None
    s = Iap2Session(dev, log=lambda m: None)
    return s if s.connect() else None

def trial(label, policy):
    session = fresh()
    if session is None:
        print(f"{label}: no link"); return False
    def ctl(m, s):
        session.send_control(m); session.pump(s)
    ctl(C.Message(C.START_IDENTIFICATION), 1.5)
    ctl(C.Message(C.IDENTIFICATION_ACCEPTED), 0.8)
    ctl(C.Message(C.REQUEST_AUTH_CERTIFICATE), 1.5)
    ctl(C.Message(C.REQUEST_AUTH_CHALLENGE_RESPONSE, [C.Parameter(0x0000, os.urandom(20))]), 3.5)
    ctl(C.Message(C.AUTH_SUCCEEDED), 0.8)
    if not session.alive:
        print(f"{label}: died in auth"); session.close(); return False

    EA_LINK = session.session_id(0x02)
    session.send_control(C.start_ea_session(C.EA_PROTOCOL_FRAME, 1))

    asm = fproto.FrameAssembler()
    ea_bytes, frames = 0, []
    t0 = time.monotonic()
    while session.alive and time.monotonic() - t0 < 8 and len(frames) < 2:
        data = session._read(100)
        if not data:
            if policy == "silent":
                continue
            if policy == "fresh_seq":
                session._write(Packet(ACK, session.our_seq, session.their_seq, 0).encode())
                session.our_seq = (session.our_seq + 1) & 0xFF
            else:
                session.ack()
            continue
        for pkt in session._decode_all(data):
            if pkt.payload and pkt.session == EA_LINK:
                body = pkt.payload[2:]
                ea_bytes += len(body); frames.extend(asm.feed(body))
            if policy == "fresh_seq":
                session._write(Packet(ACK, session.our_seq, session.their_seq, 0).encode())
                session.our_seq = (session.our_seq + 1) & 0xFF
            elif policy != "silent":
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

for label, policy in [
    ("duplicate seq (what we did)", "dup"),
    ("no acks at all             ", "silent"),
    ("fresh seq per ack          ", "fresh_seq"),
]:
    if trial(label, policy):
        print(">>> VIDEO"); break
