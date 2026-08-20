"""Init variants x read strategies, to find a combination that yields frames."""
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

def grab(timeout=25.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = usb.core.find(idVendor=proto.VENDOR_ID, idProduct=proto.PRODUCT_ID, backend=B)
        if d: return d
        time.sleep(0.005)
    return None

def attempt(fileio_ep, read_size, do_clear_halt, reset_first):
    tag = f"fileio_ep={fileio_ep} read={read_size} clear_halt={do_clear_halt} reset={reset_first}"
    dev = grab()
    if dev is None:
        print(f"  {tag}: no device"); return False
    try:
        if reset_first:
            try:
                dev.reset()
            except Exception:
                pass
            usb.util.dispose_resources(dev)
            time.sleep(1.0)
            dev = grab()
            if dev is None:
                print(f"  {tag}: gone after reset"); return False
        for i in (0, 1, 2):
            usb.util.claim_interface(dev, i)
        dev.set_interface_altsetting(interface=2, alternate_setting=0)
        dev.set_interface_altsetting(interface=1, alternate_setting=0)
        dev.set_interface_altsetting(interface=1, alternate_setting=1)
        if fileio_ep:
            for payload in (HDR_OPEN, CMD_OPEN, HDR_READ, CMD_READ):
                dev.write(fileio_ep, payload, 1000)
        dev.set_interface_altsetting(interface=2, alternate_setting=1)
        if do_clear_halt:
            for ep in (0x85, 0x06):
                try: dev.clear_halt(ep)
                except Exception as e: print(f"      clear_halt({ep:#04x}): {e}")

        asm = proto.FrameAssembler()
        chunks = total = frames = 0
        errs = []
        end = time.monotonic() + 5.0
        while time.monotonic() < end:
            try:
                data = bytes(dev.read(0x85, read_size, 300))
            except usb.core.USBTimeoutError:
                continue
            except usb.core.USBError as e:
                errs.append(str(e))
                if len(errs) > 5: break
                continue
            chunks += 1; total += len(data)
            frames += len(asm.feed(data))
        status = f"chunks={chunks} bytes={total} frames={frames}"
        if errs: status += f" errors={errs[0]} (x{len(errs)})"
        print(f"  {tag}: {status}")
        return total > 0
    except Exception as e:
        print(f"  {tag}: INIT FAIL {type(e).__name__}: {e}")
        return False
    finally:
        try: usb.util.dispose_resources(dev)
        except Exception: pass

for fileio_ep, read_size, ch, rst in [
    (0x02, 512,   False, False),
    (0x02, 16384, True,  False),
    (None, 512,   False, False),
    (None, 16384, True,  False),
    (0x02, 512,   True,  True),
    (None, 512,   True,  True),
]:
    if attempt(fileio_ep, read_size, ch, rst):
        print("  >>> DATA RECEIVED"); break
