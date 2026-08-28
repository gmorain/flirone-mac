"""Replay the iPhone's exact bring-up, recovered from the Cynthion capture.

Three things differ from every earlier probe, and all three came out of
research/Cynthion-captures/FlirOne_Streaming.pcapng:

1. The phone authenticates FIRST and identifies afterwards. We had it the other
   way round.
2. It opens ONE External Accessory session, for protocol 0 (config). It never
   opens fileio or frame. Opening those is what was killing our link, and no
   real host ever does it.
3. Streaming is started by a command, not by a session: setOption stream=true
   over the config session. Video then arrives as raw bulk on 0x85.

Timeline from the capture, relative to the first packet:

    106 ms  RequestAuthenticationCertificate
    242 ms  RequestAuthenticationChallengeResponse
   2353 ms  AuthenticationSucceeded
   2353 ms  StartIdentification
   2512 ms  IdentificationAccepted
   2529 ms  StartEASession(protocol 0, session 1)
   2536 ms  SET_INTERFACE iface 1 -> alt 1
   2907 ms  openFile CameraFiles.zip
   2912 ms  openFileStatus -> streamIdentifier 10
   2916 ms  readFile
   3006 ms  SET_INTERFACE iface 2 -> alt 1
   3011 ms  setOption stream=true
   4161 ms  first video on 0x85
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import usb.core
import usb.util

from flirone import protocol as fproto
from flirone import rosebud
from flirone.iap2 import control
from flirone.iap2.session import open_session

EA_SESSION = 1
SAVE = Path(sys.argv[sys.argv.index("--save") + 1]) if "--save" in sys.argv else None


def stamp(label: str) -> None:
    print(f"  t+{time.monotonic() - START:6.2f}s  {label}", flush=True)


def pump(session, seconds: float):
    """Read, acknowledge, and split control messages from EA session data."""
    messages, ea = [], []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and session.alive:
        data = session._read()
        if not data:
            continue
        for packet in session._decode_all(data):
            if not packet.payload:
                continue
            try:
                messages.append(control.decode(packet.payload))
            except control.ControlError:
                ea.append(packet.payload[2:])  # strip the EA session id
            session.ack()
    return messages, ea


def json_bodies(chunks: list[bytes]):
    for chunk in chunks:
        for match in re.finditer(rb"\{.*?\}\x00", chunk, re.S):
            try:
                yield json.loads(match.group(0)[:-1].decode())
            except ValueError:
                continue


def send_command(session, header: bytes, body: bytes) -> None:
    """The phone sends the 0xCC header and the JSON body as two EA writes."""
    session.send_ea(EA_SESSION, header)
    session.send_ea(EA_SESSION, body)


def main() -> int:
    global START
    START = time.monotonic()

    if "--no-reset" in sys.argv:
        # open_session resets the camera first. The phone never does: its
        # capture opens with an ordinary plug-in enumeration. If the reset is
        # what stops the camera coming back, catch it on a watchdog cycle.
        from flirone.iap2.session import Iap2Session, find_camera

        # Wait for a FRESH attach, not whatever is currently on the bus. A
        # camera left half-open from a previous run is present but mute, and
        # grabbing it just reproduces the dead pipe.
        from flirone.usb_link import find_device, make_backend

        backend = make_backend()
        if find_device(backend) is not None:
            print("camera is present; unplug it now (waiting up to 60s)...", flush=True)
            gone = time.monotonic() + 60.0
            while time.monotonic() < gone and find_device(backend) is not None:
                time.sleep(0.05)
            if find_device(backend) is not None:
                print("still present, giving up")
                return 1
        print("unplugged. plug it back in (waiting up to 120s)...", flush=True)
        dev = find_camera(timeout=120.0)
        if dev is None:
            print("camera never came back")
            return 1
        stamp("fresh attach caught")
        try:
            usb.util.claim_interface(dev, 0)
        except usb.core.USBError as exc:
            print(f"claim 0 failed: {exc}")
            return 1
        session = Iap2Session(dev, log=lambda m: None)
        session = session if session.connect() else None
    else:
        session = open_session(log=lambda m: None)
    if session is None or not session.established:
        print("no link")
        return 1
    stamp("link established")
    dev = session.dev

    for iface in (1, 2):
        try:
            usb.util.claim_interface(dev, iface)
        except usb.core.USBError as exc:
            print(f"claim {iface} failed: {exc}")
            return 1

    # --- authenticate first, identify second: the phone's order ---
    session.send_control(control.Message(control.REQUEST_AUTH_CERTIFICATE))
    pump(session, 2.0)
    stamp("certificate requested")

    session.send_control(
        control.Message(
            control.REQUEST_AUTH_CHALLENGE_RESPONSE,
            [control.Parameter(0x0000, os.urandom(20))],
        )
    )
    pump(session, 3.5)
    session.send_control(control.Message(control.AUTH_SUCCEEDED))
    pump(session, 0.5)
    stamp(f"authenticated, alive={session.alive}")
    if not session.alive:
        return 1

    session.send_control(control.Message(control.START_IDENTIFICATION))
    pump(session, 2.0)
    session.send_control(control.Message(control.IDENTIFICATION_ACCEPTED))
    pump(session, 0.5)
    stamp(f"identified, alive={session.alive}")
    if not session.alive:
        return 1

    # --- one EA session, protocol 0 (config). Never fileio, never frame. ---
    session.send_control(control.start_ea_session(control.EA_PROTOCOL_CONFIG, EA_SESSION))
    pump(session, 1.5)
    stamp(f"config EA session open, alive={session.alive}")
    if not session.alive:
        return 1

    dev.set_interface_altsetting(interface=1, alternate_setting=1)
    stamp("iface 1 -> alt 1")
    pump(session, 0.4)

    send_command(session, *rosebud.open_file("CameraFiles.zip"))
    _, ea = pump(session, 1.5)
    stream_id = None
    for message in json_bodies(ea):
        if message.get("type") == "openFileStatus":
            stream_id = message["data"].get("streamIdentifier")
        print(f"       <- {message}")
    if stream_id is None:
        print("no streamIdentifier; aborting")
        return 1
    stamp(f"file open, streamIdentifier={stream_id}")

    # The phone sends readFile twice and drains CameraFiles.zip off 0x83
    # before asking for the stream. Leaving that pipe full stalls the camera's
    # state machine, which is likely why setOption drew no reply.
    send_command(session, *rosebud.read_file(stream_id))
    pump(session, 0.3)
    send_command(session, *rosebud.read_file(stream_id))

    fileio = 0
    quiet = time.monotonic()
    while time.monotonic() - quiet < 1.0:
        try:
            chunk = bytes(dev.read(fproto.EP_FILEIO_IN, 1 << 16, 100))
        except usb.core.USBTimeoutError:
            chunk = b""
        except usb.core.USBError as exc:
            print(f"0x83 read failed: {exc}")
            break
        if chunk:
            fileio += len(chunk)
            quiet = time.monotonic()
        pump(session, 0.02)
    stamp(f"drained {fileio} B off 0x83, alive={session.alive}")

    dev.set_interface_altsetting(interface=2, alternate_setting=1)
    stamp("iface 2 -> alt 1")

    send_command(session, *rosebud.set_option("stream", True))
    _, ea = pump(session, 1.0)
    for message in json_bodies(ea):
        print(f"       <- {message}")
    stamp(f"stream requested, alive={session.alive}")

    # --- video should now arrive as raw bulk on 0x85 ---
    assembler = fproto.FrameAssembler()
    total = frames = 0
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and session.alive:
        try:
            chunk = bytes(dev.read(fproto.EP_FRAME_IN, 1 << 16, 100))
        except usb.core.USBTimeoutError:
            chunk = b""
        except usb.core.USBError as exc:
            print(f"0x85 read failed: {exc}")
            break
        if chunk:
            total += len(chunk)
            got = assembler.feed(chunk)
            if got and not frames:
                stamp("*** FIRST FRAME ***")
                if SAVE:
                    f = got[0]
                    SAVE.mkdir(parents=True, exist_ok=True)
                    (SAVE / "frame.thermal").write_bytes(f.thermal)
                    (SAVE / "frame.jpeg").write_bytes(f.jpeg)
                    (SAVE / "frame.status").write_bytes(f.status)
                    stamp(f"saved thermal {len(f.thermal)}B jpeg {len(f.jpeg)}B to {SAVE}")
            frames += len(got)
        pump(session, 0.02)

    print("")
    print(f"0x85 bytes: {total}")
    print(f"frames:     {frames}   desyncs: {assembler.desync_count}")
    print(f"link alive: {session.alive}")
    # Leave the camera idle, or it stays streaming and never re-attaches.
    if session.alive:
        try:
            send_command(session, *rosebud.set_option("stream", False))
            pump(session, 0.5)
        except Exception as exc:
            print(f"stop-stream failed: {exc}")
    session.close()
    return 0 if frames else 1


if __name__ == "__main__":
    raise SystemExit(main())
