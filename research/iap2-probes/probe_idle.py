"""Control experiment: after authentication, do nothing but keep the link alive.

If it dies at a fixed interval regardless, the problem is keepalive or power
negotiation. If it survives, then opening the EA session is what kills it.
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from flirone.iap2 import control
from flirone.iap2.session import open_session

OPEN_EA = "--ea" in sys.argv
PROTOCOL = int(sys.argv[sys.argv.index("--protocol") + 1]) if "--protocol" in sys.argv else 2
quiet = lambda m: None

session = open_session(log=quiet)
if session is None:
    raise SystemExit("could not bring the iAP2 link up")

def step(label, message, seconds):
    session.send_control(message)
    msgs = session.pump(seconds)
    for m in msgs:
        print(f"  t+{time.monotonic()-session.t0:5.2f}  <<< {m}")
    return msgs

step("id", control.Message(control.START_IDENTIFICATION), 2.0)
step("idacc", control.Message(control.IDENTIFICATION_ACCEPTED), 1.0)
step("cert", control.Message(control.REQUEST_AUTH_CERTIFICATE), 2.0)
step("chal", control.Message(control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                             [control.Parameter(0x0000, os.urandom(20))]), 4.0)
step("ok", control.Message(control.AUTH_SUCCEEDED), 1.0)
t_auth = time.monotonic() - session.t0
print(f"\nauthenticated at t+{t_auth:.2f}s")

if OPEN_EA:
    names = {0: "config", 1: "fileio", 2: "frame"}
    print(f"opening EA session for protocol {PROTOCOL} ({names.get(PROTOCOL, '?')})")
    step("ea", control.start_ea_session(PROTOCOL, 1), 1.0)

print("now idling, acking only" + (" (EA session open)" if OPEN_EA else ""))
last_rx = time.monotonic()
while session.alive and time.monotonic() - session.t0 < 25:
    data = session._read(100)
    if data:
        last_rx = time.monotonic()
        for packet in session._decode_all(data):
            if packet.payload:
                try:
                    print(f"  t+{time.monotonic()-session.t0:5.2f}  <<< {control.decode(packet.payload)}")
                except control.ControlError:
                    print(f"  t+{time.monotonic()-session.t0:5.2f}  <<< data {len(packet.payload)}B "
                          f"session={packet.session} {packet.payload[:24].hex(' ')}")
            session.ack()
    else:
        session.ack()   # cumulative-ack keepalive

died = time.monotonic() - session.t0
print(f"\ndied/ended at t+{died:.2f}s   ({died - t_auth:.2f}s after authentication)")
print(f"last inbound traffic {time.monotonic()-last_rx:.2f}s before that")
