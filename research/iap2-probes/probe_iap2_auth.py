"""Complete the full iAP2 handshake, then check whether video unlocks.

We are the device side. In MFi the accessory proves itself to us, so we issue
the challenge and decide to accept the answer. No Apple-issued key material is
involved on this side, and none is produced.
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone import protocol as fproto
from flirone.iap2 import control
from flirone.iap2.session import open_session

session = open_session()
if session is None:
    raise SystemExit("could not bring the iAP2 link up")
dev = session.dev

def step(label, message, seconds=2.5):
    print(f"\n--- {label} ---")
    session.send_control(message)
    return session.pump(seconds)

# 1. Identification
messages = step("StartIdentification", control.Message(control.START_IDENTIFICATION))
info = next((m for m in messages if m.identifier == control.IDENTIFICATION_INFORMATION), None)
if info is not None:
    name = (info.parameter(0x0000) or b"").split(b"\x00")[0].decode("ascii", "ignore")
    serial = (info.parameter(0x0003) or b"").split(b"\x00")[0].decode("ascii", "ignore")
    firmware = (info.parameter(0x0004) or b"").split(b"\x00")[0].decode("ascii", "ignore")
    print(f"    identified: {name}  serial {serial}  firmware {firmware}")
    step("IdentificationAccepted", control.Message(control.IDENTIFICATION_ACCEPTED), 1.5)

# 2. Authentication: fetch the accessory's certificate.
messages = step("RequestAuthenticationCertificate",
                control.Message(control.REQUEST_AUTH_CERTIFICATE))
cert = next((m.parameter(0x0000) for m in messages
             if m.identifier == control.AUTH_CERTIFICATE), None)
if cert:
    Path("/tmp/flir_auth_cert.der").write_bytes(cert)
    print(f"    certificate: {len(cert)} bytes, saved to /tmp/flir_auth_cert.der")

# 3. Challenge the coprocessor. 20 bytes, the SHA-1 digest size the spec uses.
challenge = os.urandom(20)
print(f"    challenge: {challenge.hex()}")
messages = step("RequestAuthenticationChallengeResponse",
                control.Message(control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                                [control.Parameter(0x0000, challenge)]), 4.0)
response = next((m.parameter(0x0000) for m in messages
                 if m.identifier == control.AUTH_RESPONSE), None)
if response:
    print(f"    signed response: {len(response)} bytes  {response[:32].hex(' ')}")

# 4. Accept it.
step("AuthenticationSucceeded", control.Message(control.AUTH_SUCCEEDED), 3.0)

# 5. Does the vendor video interface work now?
print("\n--- does EP 0x85 stream now? ---")
try:
    for iface in (1, 2):
        usb.util.claim_interface(dev, iface)
    for iface, alt in ((2, 0), (1, 0), (1, 1), (2, 1)):
        dev.set_interface_altsetting(interface=iface, alternate_setting=alt)
    print("    vendor interfaces up")
except Exception as exc:
    print(f"    vendor interface setup failed: {type(exc).__name__}: {exc}")

asm = fproto.FrameAssembler()
total = frames = 0
end = time.monotonic() + 6.0
while time.monotonic() < end:
    try:
        data = bytes(dev.read(0x85, 65536, 100))
    except usb.core.USBTimeoutError:
        session.pump(0.05)
        continue
    except usb.core.USBError as exc:
        print(f"    0x85 error: {exc}")
        break
    total += len(data)
    frames += len(asm.feed(data))
print(f"    0x85: {total} bytes, {frames} frames")
print(f"\nsurvived {time.monotonic() - session.t0:.2f}s")
