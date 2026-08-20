"""Reference-driver-faithful loop: poll 0x85, 0x81 and 0x83 together.

The camera drives a small state machine over the control endpoint; if 0x81 and
0x83 are never drained it can stall before it ever starts sending frames.
"""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone import protocol as proto
from flirone.usb_link import make_backend

B = make_backend()
HDR_OPEN = bytes.fromhex("cc0100000100000041000000f8b3f700")
CMD_OPEN = b'{"type":"openFile","data":{"mode":"r","path":"CameraFiles.zip"}}\x00'
HDR_READ = bytes.fromhex("cc0100000100000033000000efdbc1c1")
CMD_READ = b'{"type":"readFile","data":{"streamIdentifier":10}}\x00'
OUT = Path("/tmp/flir_capture")

def grab(timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = usb.core.find(idVendor=0x09CB, idProduct=0x1996, backend=B)
        if d: return d
        time.sleep(0.005)
    raise SystemExit("camera never appeared")

def rd(dev, ep, size, ms):
    try:
        return bytes(dev.read(ep, size, ms))
    except usb.core.USBTimeoutError:
        return b""
    except usb.core.USBError as e:
        return f"ERR:{e}"

dev = grab()
for i in (0, 1, 2):
    usb.util.claim_interface(dev, i)
dev.set_interface_altsetting(interface=2, alternate_setting=0)
dev.set_interface_altsetting(interface=1, alternate_setting=0)
dev.set_interface_altsetting(interface=1, alternate_setting=1)
for payload in (HDR_OPEN, CMD_OPEN, HDR_READ, CMD_READ):
    dev.write(0x02, payload, 1000)
dev.set_interface_altsetting(interface=2, alternate_setting=1)
# The reference driver repeats the "start frame" request with a 2-byte data
# stage after the interface is already running.
try:
    dev.ctrl_transfer(0x01, 0x0B, 1, 2, b"\x00\x00", 200)
except usb.core.USBError as e:
    print(f"start-frame control transfer: {e}")
print("init done, polling 0x85 / 0x81 / 0x83 for 15s")

asm = proto.FrameAssembler()
frames = []
stats = {"0x85": 0, "0x81": 0, "0x83": 0}
errors = {}
end = time.monotonic() + 15.0
while time.monotonic() < end and len(frames) < 5:
    for ep, name, size, ms in ((0x85, "0x85", 65536, 100), (0x81, "0x81", 4096, 10), (0x83, "0x83", 4096, 10)):
        r = rd(dev, ep, size, ms)
        if isinstance(r, str):
            errors[name] = r
            continue
        if not r:
            continue
        stats[name] += len(r)
        if name == "0x85":
            frames.extend(asm.feed(r))
        elif name == "0x81" and stats[name] < 200:
            print(f"  0x81 <- {r[:48].hex()}")
        elif name == "0x83" and stats[name] < 400:
            print(f"  0x83 <- {r[:80]!r}")

print(f"bytes: {stats}  frames={len(frames)} desyncs={asm.desync_count}")
for k, v in errors.items():
    print(f"  {k} error: {v}")
if frames:
    f = frames[0]
    print(f"FRAME: thermal={len(f.thermal)} jpeg={len(f.jpeg)} status={len(f.status)} geom={f.geometry}")
    print(f"  jpeg magic {f.jpeg[:3].hex()}  status {f.status[:250].decode('utf-8','replace')}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "thermal.bin").write_bytes(f.thermal)
    (OUT / "visible.jpg").write_bytes(f.jpeg)
    (OUT / "status.json").write_bytes(f.status)
    print(f"  saved {OUT}")
usb.util.dispose_resources(dev)
