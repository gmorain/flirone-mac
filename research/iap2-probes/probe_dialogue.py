"""Log everything the camera says on 0x81/0x83 during one full window,
and see whether it answers JSON commands sent on 0x02."""
from __future__ import annotations
import sys, time, binascii
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone.usb_link import make_backend

B = make_backend()

def pounce(timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = usb.core.find(idVendor=0x09CB, idProduct=0x1996, backend=B)
        if d is not None: return d
        time.sleep(0.001)
    return None

def show(tag, data, t):
    printable = "".join(chr(c) if 32 <= c < 127 else "." for c in data[:120])
    print(f"  t+{t:5.2f} {tag} {len(data):5d}B  {binascii.hexlify(data[:32]).decode()}")
    if any(32 <= c < 127 for c in data):
        print(f"                        ascii: {printable}")

dev = pounce()
if dev is None:
    raise SystemExit("no device")
t0 = time.monotonic()
for i in (0, 1, 2):
    usb.util.claim_interface(dev, i)
for iface, alt in ((2, 0), (1, 0), (1, 1), (2, 1)):
    dev.set_interface_altsetting(interface=iface, alternate_setting=alt)
print(f"init at t+{time.monotonic()-t0:.3f}")

# Commands to try on the control OUT endpoint, with the reference driver's
# 16-byte framing header (magic, seq, length, checksum).
def framed(json_bytes, checksum):
    import struct
    return struct.pack("<IIII", 0x000001CC, 1, len(json_bytes), checksum), json_bytes

CMDS = [
    ("openFile CameraFiles.zip",
     bytes.fromhex("cc0100000100000041000000f8b3f700"),
     b'{"type":"openFile","data":{"mode":"r","path":"CameraFiles.zip"}}\x00'),
    ("readFile stream 10",
     bytes.fromhex("cc0100000100000033000000efdbc1c1"),
     b'{"type":"readFile","data":{"streamIdentifier":10}}\x00'),
]

seen = 0
next_cmd = 0
sent_at = {}
while time.monotonic() - t0 < 6.0:
    t = time.monotonic() - t0
    # Fire one command a second so responses are attributable.
    if next_cmd < len(CMDS) and t > 0.5 + next_cmd * 1.5:
        label, hdr, body = CMDS[next_cmd]
        try:
            dev.write(0x02, hdr, 500)
            dev.write(0x02, body, 500)
            print(f"  t+{t:5.2f} SENT -> {label}")
        except Exception as e:
            print(f"  t+{t:5.2f} SEND FAIL {label}: {e}")
        next_cmd += 1
    for ep, tag in ((0x81, "0x81"), (0x83, "0x83"), (0x85, "0x85")):
        try:
            data = bytes(dev.read(ep, 65536, 10))
        except usb.core.USBTimeoutError:
            continue
        except (usb.core.USBError, ValueError) as e:
            if "No such device" in str(e):
                print(f"  t+{t:5.2f} DEVICE GONE")
                raise SystemExit(0)
            continue
        if data:
            seen += 1
            if seen < 40:
                show(tag, data, t)
print("window ended without disconnect")
