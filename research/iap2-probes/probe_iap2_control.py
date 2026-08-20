"""Establish the link, then drive the control session and log the replies."""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone.iap2 import control
from flirone.iap2.session import Iap2Session
from flirone.usb_link import make_backend

B = make_backend()

def pounce(t=30.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        d = usb.core.find(idVendor=0x09CB, idProduct=0x1996, backend=B)
        if d is not None: return d
        time.sleep(0.001)
    return None

dev = pounce()
if dev is None:
    raise SystemExit("no device")
usb.util.claim_interface(dev, 0)
session = Iap2Session(dev)

if not session.connect():
    raise SystemExit("link handshake failed")

print("\n--- listening before we say anything ---")
session.pump(1.5)

print("\n--- StartIdentification ---")
session.send_control(control.Message(control.START_IDENTIFICATION))
session.pump(2.5)

print("\n--- RequestAuthenticationCertificate ---")
session.send_control(control.Message(control.REQUEST_AUTH_CERTIFICATE))
session.pump(3.0)

print(f"\nsurvived {time.monotonic() - session.t0:.2f}s")
try: usb.util.dispose_resources(dev)
except Exception: pass
