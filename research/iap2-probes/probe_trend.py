"""Repeat the same request and watch how long the camera survives each time.

If time-to-death shortens across trials, the camera is running its battery
down and the cause is power. If it is flat and identical, that points at a
deterministic device-side refusal instead.
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core
from flirone.iap2 import control
from flirone.iap2.session import find_camera, open_session

quiet = lambda m: None
results = []

for trial in range(1, 6):
    session = open_session(log=quiet)
    if session is None:
        print(f"trial {trial}: could not establish link")
        results.append(None)
        continue

    def ctl(msg, secs):
        session.send_control(msg)
        session.pump(secs)

    ctl(control.Message(control.START_IDENTIFICATION), 1.5)
    ctl(control.Message(control.IDENTIFICATION_ACCEPTED), 0.8)
    ctl(control.Message(control.REQUEST_AUTH_CERTIFICATE), 1.5)
    ctl(control.Message(control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                        [control.Parameter(0x0000, os.urandom(20))]), 3.5)
    ctl(control.Message(control.AUTH_SUCCEEDED), 0.8)
    if not session.alive:
        print(f"trial {trial}: died during authentication")
        results.append(0.0)
        session.close()
        continue

    t_auth = time.monotonic()
    session.send_control(control.start_ea_session(control.EA_PROTOCOL_FRAME, 1))
    while session.alive and time.monotonic() - t_auth < 10.0:
        if not session._read(100):
            session.ack()
    survived = time.monotonic() - t_auth
    results.append(survived)

    # How quickly does it come back? A brownout reset returns in about a second.
    session.close()
    gone_at = time.monotonic()
    back = find_camera(timeout=15.0)
    recovery = time.monotonic() - gone_at if back is not None else float("nan")
    print(f"trial {trial}: survived {survived:5.2f}s after requesting frames, "
          f"reappeared after {recovery:4.1f}s")

good = [r for r in results if r is not None]
print(f"\nsurvival times: {[f'{r:.2f}' for r in good]}")
if len(good) >= 3:
    first, last = good[0], good[-1]
    trend = "shortening (battery draining)" if last < first * 0.6 else \
            "flat (deterministic refusal)" if abs(last - first) < 1.0 else "mixed"
    print(f"trend: {trend}")
