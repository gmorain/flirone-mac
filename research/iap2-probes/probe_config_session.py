"""Open the EA config session and talk to it, then try to bring up frames."""
from __future__ import annotations
import os, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from flirone import protocol as fproto
from flirone.iap2 import control
from flirone.iap2.session import open_session

quiet = lambda m: None
session = open_session(log=quiet)
if session is None:
    raise SystemExit("could not bring the iAP2 link up")
EA_LINK = None

def t():
    return time.monotonic() - session.t0

def ctl(message, seconds=1.5):
    session.send_control(message)
    for m in session.pump(seconds):
        print(f"  t+{t():5.2f}  <<< {m}")

ctl(control.Message(control.START_IDENTIFICATION), 2.0)
ctl(control.Message(control.IDENTIFICATION_ACCEPTED), 1.0)
ctl(control.Message(control.REQUEST_AUTH_CERTIFICATE), 2.0)
ctl(control.Message(control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                    [control.Parameter(0x0000, os.urandom(20))]), 4.0)
ctl(control.Message(control.AUTH_SUCCEEDED), 1.0)
EA_LINK = session.session_id(0x02)
print(f"authenticated at t+{t():.2f}s, EA link session {EA_LINK}")

CONFIG_EA = 1
print("\nopening EA config session")
ctl(control.start_ea_session(control.EA_PROTOCOL_CONFIG, CONFIG_EA), 1.5)
if not session.alive:
    raise SystemExit("died opening config session")

# The reference driver's command vocabulary, now over the EA config session.
COMMANDS = [
    b'{"type":"getOptions"}\x00',
    b'{"type":"setOption","data":{"option":"autoFFC","value":true}}\x00',
    b'{"type":"startFrameStream"}\x00',
]

asm = fproto.FrameAssembler()
frames = []

def drain(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end and session.alive:
        data = session._read(100)
        if not data:
            session.ack()
            continue
        for packet in session._decode_all(data):
            if not packet.payload:
                continue
            if packet.session == EA_LINK:
                ea_id = struct.unpack_from(">H", packet.payload, 0)[0]
                body = packet.payload[2:]
                text = body[:120].decode("utf-8", "replace")
                print(f"  t+{t():5.2f}  ea[{ea_id}] {len(body)}B  {text!r}")
                frames.extend(asm.feed(body))
            else:
                try:
                    print(f"  t+{t():5.2f}  <<< {control.decode(packet.payload)}")
                except control.ControlError:
                    pass
            session.ack()

for cmd in COMMANDS:
    if not session.alive:
        break
    print(f"\nsending on config session: {cmd[:60]!r}")
    session.send_ea(CONFIG_EA, cmd)
    drain(2.5)

print(f"\nalive={session.alive}  frames={len(frames)}  ended t+{t():.2f}s")
