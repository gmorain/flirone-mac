"""Sweep the link parameters we advertise, then ask for video."""
from __future__ import annotations
import os, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone import protocol as fproto
from flirone.iap2 import control
from flirone.iap2.link import LinkSync
from flirone.iap2.session import Iap2Session, find_camera
from flirone.usb_link import make_backend

B = make_backend()

def trial(label, sync):
    dev = find_camera(backend=B)
    if dev is None:
        print(f"{label}: no camera"); return
    try: dev.reset()
    except usb.core.USBError: pass
    usb.util.dispose_resources(dev)
    time.sleep(1.5)
    dev = find_camera(backend=B)
    if dev is None:
        print(f"{label}: gone"); return
    try:
        usb.util.claim_interface(dev, 0)
    except usb.core.USBError as e:
        print(f"{label}: claim failed {e}"); return

    session = Iap2Session(dev, log=lambda m: None, our_sync=sync)
    if not session.connect():
        print(f"{label}: link failed"); session.close(); return
    def ctl(m, s):
        session.send_control(m); return session.pump(s)
    ctl(control.Message(control.START_IDENTIFICATION), 1.5)
    ctl(control.Message(control.IDENTIFICATION_ACCEPTED), 0.8)
    ctl(control.Message(control.REQUEST_AUTH_CERTIFICATE), 1.5)
    ctl(control.Message(control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                        [control.Parameter(0x0000, os.urandom(20))]), 3.5)
    ctl(control.Message(control.AUTH_SUCCEEDED), 0.8)
    if not session.alive:
        print(f"{label}: died in auth"); session.close(); return

    EA_LINK = session.session_id(0x02)
    session.send_control(control.start_ea_session(control.EA_PROTOCOL_FRAME, 1))
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
                ea_bytes += len(body)
                frames.extend(asm.feed(body))
            session.ack()
    print(f"{label}: alive={session.alive} ea_bytes={ea_bytes} frames={len(frames)}")
    if frames:
        f = frames[0]
        print(f"  *** FRAME thermal={len(f.thermal)}B jpeg={len(f.jpeg)}B geom={f.geometry}")
        out = Path("/tmp/flir_capture"); out.mkdir(parents=True, exist_ok=True)
        (out/"thermal.bin").write_bytes(f.thermal)
        (out/"visible.jpg").write_bytes(f.jpeg)
        (out/"status.json").write_bytes(f.status)
    session.close()
    time.sleep(0.8)
    return bool(frames)

for label, out_pkts, pkt_len in [
    ("echo   (127 x 65535)", 127, 65535),
    ("modest (  8 x  4096)",   8,  4096),
    ("small  (  4 x  1024)",   4,  1024),
    ("tiny   (  1 x   512)",   1,   512),
]:
    sync = LinkSync(max_outstanding_packets=out_pkts, max_packet_length=pkt_len)
    if trial(label, sync):
        print(">>> VIDEO"); break
