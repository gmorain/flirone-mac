"""One clean run to the failure, for capture alongside a working iOS session.

probe_ea_matrix runs five trials with a USB reset between each, which makes an
analyser trace hard to read. This does exactly one: link, identify,
authenticate, then StartEASession for the frame protocol, which is the write
the camera accepts and never answers.

Prints wall-clock timestamps so the trace can be lined up with the capture.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from flirone.iap2 import control
from flirone.iap2.session import open_session

PROTOCOL = control.EA_PROTOCOL_FRAME  # 2; use 0 for the config session that works
SESSION_ID = 1


def stamp(label: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}.{int(time.time() % 1 * 1000):03d}  {label}", flush=True)


def main() -> int:
    stamp("BEGIN, opening link")
    session = open_session(log=lambda m: None)
    if session is None or not session.established:
        print("no link")
        return 1
    stamp("link established")

    def control_message(message, wait):
        session.send_control(message)
        return session.pump(wait)

    for label, message, wait in (
        ("StartIdentification", control.Message(control.START_IDENTIFICATION), 1.5),
        ("IdentificationAccepted", control.Message(control.IDENTIFICATION_ACCEPTED), 0.8),
        ("RequestAuthCertificate", control.Message(control.REQUEST_AUTH_CERTIFICATE), 1.5),
        (
            "RequestAuthChallengeResponse",
            control.Message(
                control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                [control.Parameter(0x0000, os.urandom(20))],
            ),
            3.5,
        ),
        ("AuthenticationSucceeded", control.Message(control.AUTH_SUCCEEDED), 0.8),
    ):
        stamp(f"TX {label}")
        control_message(message, wait)
        if not session.alive:
            stamp(f"died during {label}")
            return 1

    stamp(f"TX StartEASession protocol={PROTOCOL} session={SESSION_ID}  <-- THE WRITE")
    control_message(control.start_ea_session(PROTOCOL, SESSION_ID), 2.0)
    stamp(f"after StartEASession: alive={session.alive}")

    session.pump(3.0)
    stamp(f"END: alive={session.alive}")
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
