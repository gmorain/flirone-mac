"""Full handshake, then open the External Accessory session and look for video."""
from __future__ import annotations
import os, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from flirone import protocol as fproto
from flirone.iap2 import control
from flirone.iap2.link import ACK, Packet
from flirone.iap2.session import open_session

QUIET = "-v" not in sys.argv
def log(msg):
    if not QUIET or "control" in msg or ">>>" in msg or "established" in msg:
        print(msg)

session = open_session(log=log)
if session is None:
    raise SystemExit("could not bring the iAP2 link up")

def step(label, message, seconds=2.0):
    print(f"--- {label}")
    session.send_control(message)
    return session.pump(seconds)

messages = step("StartIdentification", control.Message(control.START_IDENTIFICATION), 2.5)
step("IdentificationAccepted", control.Message(control.IDENTIFICATION_ACCEPTED), 1.0)
step("RequestAuthenticationCertificate", control.Message(control.REQUEST_AUTH_CERTIFICATE), 2.0)
step("RequestAuthenticationChallengeResponse",
     control.Message(control.REQUEST_AUTH_CHALLENGE_RESPONSE,
                     [control.Parameter(0x0000, os.urandom(20))]), 4.0)
step("AuthenticationSucceeded", control.Message(control.AUTH_SUCCEEDED), 2.0)

# Open a session for each advertised protocol. The frame one is the target;
# config and fileio are opened too because the camera may need them to start.
EA_LINK_SESSION = session.session_id(0x02)
print(f"\nExternal Accessory link session id = {EA_LINK_SESSION}")
for name, protocol_id, ea_session in (
    ("config", control.EA_PROTOCOL_CONFIG, 1),
    ("fileio", control.EA_PROTOCOL_FILEIO, 2),
    ("frame",  control.EA_PROTOCOL_FRAME,  3),
):
    step(f"StartEASession {name} (protocol {protocol_id}, session {ea_session})",
         control.start_ea_session(protocol_id, ea_session), 1.5)

print("\n--- listening on the EA session for video ---")
asm = fproto.FrameAssembler()
ea_bytes = {}
frames = []
end = time.monotonic() + 12.0
while time.monotonic() < end and len(frames) < 3:
    data = session._read(100)
    if not data:
        continue
    for packet in session._decode_all(data):
        if not packet.payload:
            continue
        if packet.session == EA_LINK_SESSION:
            # EA data is prefixed with its 2-byte session identifier.
            ea_id = struct.unpack_from(">H", packet.payload, 0)[0]
            body = packet.payload[2:]
            ea_bytes[ea_id] = ea_bytes.get(ea_id, 0) + len(body)
            frames.extend(asm.feed(body))
        else:
            try:
                print(f"    control: {control.decode(packet.payload)}")
            except control.ControlError:
                pass
        session.ack()

print(f"\nEA bytes by session: {ea_bytes}")
print(f"frames assembled: {len(frames)}  desyncs: {asm.desync_count}")
if frames:
    f = frames[0]
    print(f"FRAME thermal={len(f.thermal)}B jpeg={len(f.jpeg)}B status={len(f.status)}B geom={f.geometry}")
    out = Path("/tmp/flir_capture"); out.mkdir(parents=True, exist_ok=True)
    (out/"thermal.bin").write_bytes(f.thermal)
    (out/"visible.jpg").write_bytes(f.jpeg)
    (out/"status.json").write_bytes(f.status)
    print(f"saved to {out}")
print(f"survived {time.monotonic() - session.t0:.2f}s")
