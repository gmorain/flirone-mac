"""Is the 0.9s disappearance a crash, or a deliberate re-enumeration?

Snapshot the descriptors, request the frame session, wait for the device to
come back, snapshot again and diff. Then see whether video flows on the way up.
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone import protocol as fproto
from flirone.iap2 import control
from flirone.iap2.session import find_camera, open_session

TYPES = {0: "CTRL", 1: "ISOC", 2: "BULK", 3: "INTR"}

def snapshot(dev) -> str:
    lines = [f"bcdDevice={dev.bcdDevice:#06x} configs={dev.bNumConfigurations}"]
    for cfg in dev:
        lines.append(f"config {cfg.bConfigurationValue} ifaces={cfg.bNumInterfaces}")
        for intf in cfg:
            eps = " ".join(f"{ep.bEndpointAddress:#04x}/{TYPES.get(ep.bmAttributes & 3)}"
                           for ep in intf)
            lines.append(f"  iface {intf.bInterfaceNumber} alt {intf.bAlternateSetting}: {eps or '(none)'}")
    return "\n".join(lines)

quiet = lambda m: None
session = open_session(log=quiet)
if session is None:
    raise SystemExit("could not bring the iAP2 link up")

before = snapshot(session.dev)
print("=== descriptors BEFORE ===")
print(before)

def ctl(msg, secs):
    session.send_control(msg)
    session.pump(secs)

ctl(control.Message(control.START_IDENTIFICATION), 1.5)
ctl(control.Message(control.IDENTIFICATION_ACCEPTED), 0.8)
ctl(control.Message(control.REQUEST_AUTH_CERTIFICATE), 1.5)
ctl(control.Message(control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                    [control.Parameter(0x0000, os.urandom(20))]), 3.5)
ctl(control.Message(control.AUTH_SUCCEEDED), 0.8)
print(f"\nauthenticated, alive={session.alive}")

print("requesting the frame session...")
session.send_control(control.start_ea_session(control.EA_PROTOCOL_FRAME, 1))
t_req = time.monotonic()
while session.alive and time.monotonic() - t_req < 5.0:
    if not session._read(50):
        session.ack()
print(f"link ended {time.monotonic()-t_req:.2f}s after the request")
session.close()

print("\nwaiting for it to come back...")
dev = find_camera(timeout=20.0)
if dev is None:
    raise SystemExit("it did not come back")
print(f"back after {time.monotonic()-t_req:.2f}s")

after = snapshot(dev)
print("\n=== descriptors AFTER ===")
print(after)
print("\ndescriptors identical:", before == after)

# Whatever mode it is in, look for video on the vendor endpoint straight away.
print("\nchecking EP 0x85 immediately on return...")
try:
    for iface in (0, 1, 2):
        usb.util.claim_interface(dev, iface)
    for iface, alt in ((2, 0), (1, 0), (1, 1), (2, 1)):
        dev.set_interface_altsetting(interface=iface, alternate_setting=alt)
    asm = fproto.FrameAssembler()
    total = frames = 0
    end = time.monotonic() + 6.0
    while time.monotonic() < end:
        try:
            data = bytes(dev.read(0x85, 65536, 100))
        except usb.core.USBTimeoutError:
            continue
        except usb.core.USBError as exc:
            print(f"  0x85: {exc}")
            break
        total += len(data)
        frames += len(asm.feed(data))
    print(f"  0x85: {total} bytes, {frames} frames")
except Exception as exc:
    print(f"  setup failed: {type(exc).__name__}: {exc}")
finally:
    try: usb.util.dispose_resources(dev)
    except Exception: pass
