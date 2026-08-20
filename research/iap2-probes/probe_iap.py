"""Does the camera react to a well-formed iAP1 reply?

It emits FF 55 02 00 EE 10 (General lingo) and reboots after ~4.7s. If it is
waiting on the host side of Apple's accessory handshake, a valid reply should
change something: a different packet, or a longer survival time.
"""
from __future__ import annotations
import sys, time, collections
sys.path.insert(0, "/Users/gilles/Documents/Projects_Code/flirone-mac/src")
import usb.core, usb.util
from flirone.usb_link import make_backend

B = make_backend()

def iap1(payload: bytes) -> bytes:
    """Frame an iAP1 packet: FF 55, length, payload, checksum."""
    length = len(payload)
    checksum = (-(length + sum(payload))) & 0xFF
    return b"\xff\x55" + bytes([length]) + payload + bytes([checksum])

REPLIES = [
    ("no reply (baseline)", None),
    ("ACK ok -> cmd 0xEE",  iap1(bytes([0x00, 0x02, 0x00, 0xEE]))),
    ("RequestIdentify",     iap1(bytes([0x00, 0x00]))),
    ("echo its own packet", bytes.fromhex("ff550200ee10")),
]

def pounce(t=30.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        d = usb.core.find(idVendor=0x09CB, idProduct=0x1996, backend=B)
        if d is not None: return d
        time.sleep(0.001)
    return None

for label, reply in REPLIES:
    dev = pounce()
    if dev is None:
        print(f"{label}: no device"); continue
    t0 = time.monotonic()
    try:
        usb.util.claim_interface(dev, 0)
    except Exception as e:
        print(f"{label}: claim failed {e}"); continue

    seen = collections.Counter()
    replied = 0
    death = None
    while time.monotonic() - t0 < 9.0:
        try:
            data = bytes(dev.read(0x81, 4096, 50))
        except usb.core.USBTimeoutError:
            continue
        except usb.core.USBError:
            death = time.monotonic() - t0
            break
        if not data:
            continue
        seen[data.hex()] += 1
        if reply is not None and replied < 8:
            try:
                dev.write(0x02, reply, 200)
                replied += 1
            except usb.core.USBError as e:
                if replied == 0:
                    print(f"    write failed: {e}")
                break
    distinct = ", ".join(f"{k}(x{v})" for k, v in seen.most_common(4)) or "nothing"
    print(f"{label}:")
    print(f"    replies sent {replied}  died at {death if death else '>9'}s")
    print(f"    heard: {distinct}")
    try: usb.util.dispose_resources(dev)
    except Exception: pass
