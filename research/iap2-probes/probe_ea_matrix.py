"""Which EA session sequence does the camera tolerate?"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from flirone.iap2 import control
from flirone.iap2.session import open_session

def trial(label, steps):
    session = open_session(log=lambda m: None)
    if session is None:
        print(f"{label}: no link"); return
    def ctl(m, s):
        session.send_control(m); return session.pump(s)
    ctl(control.Message(control.START_IDENTIFICATION), 1.5)
    ctl(control.Message(control.IDENTIFICATION_ACCEPTED), 0.8)
    ctl(control.Message(control.REQUEST_AUTH_CERTIFICATE), 1.5)
    ctl(control.Message(control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                        [control.Parameter(0x0000, os.urandom(20))]), 3.5)
    ctl(control.Message(control.AUTH_SUCCEEDED), 0.8)
    if not session.alive:
        print(f"{label}: died during auth"); session.close(); return

    outcome = []
    for proto_id, ea_id, name in steps:
        if not session.alive:
            outcome.append(f"{name}:SKIPPED"); continue
        msgs = ctl(control.start_ea_session(proto_id, ea_id), 2.0)
        acked = session.alive
        outcome.append(f"{name}(ea={ea_id}):{'ok' if acked else 'DIED'}")
        if msgs:
            outcome.append(f"replies={[m.name for m in msgs]}")
    print(f"{label}: {' | '.join(outcome)}")
    session.close()
    time.sleep(1.0)

C, F, R = control.EA_PROTOCOL_CONFIG, control.EA_PROTOCOL_FILEIO, control.EA_PROTOCOL_FRAME
trial("A frame only, id 1        ", [(R, 1, "frame")])
trial("B config then frame       ", [(C, 1, "config"), (R, 2, "frame")])
trial("C frame, high session id  ", [(R, 0x0100, "frame")])
trial("D config, fileio, frame   ", [(C, 1, "config"), (F, 2, "fileio"), (R, 3, "frame")])
trial("E config then fileio      ", [(C, 1, "config"), (F, 2, "fileio")])
