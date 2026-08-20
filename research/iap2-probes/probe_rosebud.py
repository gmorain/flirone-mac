"""Full stack: iAP2 handshake, EA config session, framed FLIR commands, frames."""
from __future__ import annotations
import os, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from flirone import protocol as fproto, rosebud
from flirone.iap2 import control
from flirone.iap2.session import open_session

quiet = lambda m: None
session = open_session(log=quiet)
if session is None:
    raise SystemExit("could not bring the iAP2 link up")

def t(): return time.monotonic() - session.t0
def ctl(msg, secs=1.5):
    session.send_control(msg)
    for m in session.pump(secs):
        print(f"  t+{t():5.2f}  <<< {m}")

ctl(control.Message(control.START_IDENTIFICATION), 2.0)
ctl(control.Message(control.IDENTIFICATION_ACCEPTED), 1.0)
ctl(control.Message(control.REQUEST_AUTH_CERTIFICATE), 2.0)
ctl(control.Message(control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                    [control.Parameter(0x0000, os.urandom(20))]), 4.0)
ctl(control.Message(control.AUTH_SUCCEEDED), 1.0)
EA_LINK = session.session_id(0x02)
print(f"authenticated t+{t():.2f}s, EA link session {EA_LINK}")

CONFIG_EA = 1
ctl(control.start_ea_session(control.EA_PROTOCOL_CONFIG, CONFIG_EA), 1.5)
print(f"config EA session open, alive={session.alive}")

asm = fproto.FrameAssembler()
frames, ea_totals = [], {}

def drain(seconds, label=""):
    end = time.monotonic() + seconds
    while time.monotonic() < end and session.alive:
        data = session._read(100)
        if not data:
            session.ack(); continue
        for packet in session._decode_all(data):
            if not packet.payload:
                continue
            if packet.session == EA_LINK:
                ea_id = struct.unpack_from(">H", packet.payload, 0)[0]
                body = packet.payload[2:]
                ea_totals[ea_id] = ea_totals.get(ea_id, 0) + len(body)
                preview = body[:100]
                printable = preview.decode("utf-8", "replace")
                print(f"  t+{t():5.2f}  ea[{ea_id}] {len(body):6d}B  {printable!r}")
                frames.extend(asm.feed(body))
            session.ack()

SEQUENCE = [
    ("openFile CameraFiles.zip", rosebud.open_file("CameraFiles.zip")),
    ("readFile stream 10",       rosebud.read_file(10)),
]
for label, (header, body) in SEQUENCE:
    if not session.alive: break
    print(f"\n>>> {label}")
    session.send_ea(CONFIG_EA, header)
    session.send_ea(CONFIG_EA, body)
    drain(3.0, label)

print(f"\n>>> opening frame EA session")
if session.alive:
    ctl(control.start_ea_session(control.EA_PROTOCOL_FRAME, 2), 1.0)
    drain(8.0, "frames")

print(f"\nalive={session.alive}  ea bytes={ea_totals}  frames={len(frames)}  t+{t():.2f}s")
if frames:
    f = frames[0]
    print(f"FRAME thermal={len(f.thermal)}B jpeg={len(f.jpeg)}B status={len(f.status)}B geom={f.geometry}")
    out = Path("/tmp/flir_capture"); out.mkdir(parents=True, exist_ok=True)
    (out/"thermal.bin").write_bytes(f.thermal)
    (out/"visible.jpg").write_bytes(f.jpeg)
    (out/"status.json").write_bytes(f.status)
    print("saved /tmp/flir_capture")
