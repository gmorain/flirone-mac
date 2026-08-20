"""Full init sequence via IOKit alt-settings, then read real frames."""
from __future__ import annotations

import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone import protocol as proto
from flirone.usb_link import make_backend

B = make_backend()
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/flir_capture")
SEND_FILEIO = "--nofileio" not in sys.argv

# 16-byte framing header preceding each file-IO JSON command:
# magic 0xcc, u32 seq, u32 len(json)+1, u32 checksum. Reused verbatim from the
# reference driver since we send byte-identical command strings.
HDR_OPEN = bytes.fromhex("cc0100000100000041000000f8b3f700")
CMD_OPEN = b'{"type":"openFile","data":{"mode":"r","path":"CameraFiles.zip"}}\x00'
HDR_READ = bytes.fromhex("cc0100000100000033000000efdbc1c1")
CMD_READ = b'{"type":"readFile","data":{"streamIdentifier":10}}\x00'


def grab(timeout=25.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = usb.core.find(idVendor=proto.VENDOR_ID, idProduct=proto.PRODUCT_ID, backend=B)
        if d: return d
        time.sleep(0.005)
    raise SystemExit("camera never appeared")


def init(dev):
    for i in (0, 1, 2):
        usb.util.claim_interface(dev, i)
    # Sync IOKit's view of both interfaces to idle before starting them. Doing
    # this first is what makes SetAlternateInterface succeed.
    dev.set_interface_altsetting(interface=2, alternate_setting=0)
    dev.set_interface_altsetting(interface=1, alternate_setting=0)
    dev.set_interface_altsetting(interface=1, alternate_setting=1)
    if SEND_FILEIO:
        dev.write(0x04, HDR_OPEN, 1000)
        dev.write(0x04, CMD_OPEN, 1000)
        dev.write(0x04, HDR_READ, 1000)
        dev.write(0x04, CMD_READ, 1000)
    dev.set_interface_altsetting(interface=2, alternate_setting=1)


def main():
    dev = grab()
    print(f"got device (fileio commands: {SEND_FILEIO})")
    init(dev)
    print("init sequence completed without error")

    asm = proto.FrameAssembler()
    frames, chunks, total = [], 0, 0
    end = time.monotonic() + 8.0
    while time.monotonic() < end and len(frames) < 12:
        try:
            data = bytes(dev.read(0x85, 65536, 200))
        except usb.core.USBTimeoutError:
            continue
        except usb.core.USBError as e:
            print(f"read error: {e}")
            break
        chunks += 1
        total += len(data)
        frames.extend(asm.feed(data))

    print(f"chunks={chunks} bytes={total} frames={len(frames)} desyncs={asm.desync_count}")
    if frames:
        f = frames[0]
        print(f"frame 0: thermal={len(f.thermal)}B jpeg={len(f.jpeg)}B status={len(f.status)}B")
        print(f"geometry: {f.geometry}")
        print(f"jpeg magic: {f.jpeg[:4].hex()}")
        txt = f.status.decode('utf-8', 'replace').strip('\x00').strip()
        print(f"status: {txt[:400]}")
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "thermal.bin").write_bytes(f.thermal)
        (OUT / "visible.jpg").write_bytes(f.jpeg)
        (OUT / "status.json").write_text(txt)
        print(f"saved to {OUT}")
    usb.util.dispose_resources(dev)
    return 0 if frames else 1


raise SystemExit(main())
