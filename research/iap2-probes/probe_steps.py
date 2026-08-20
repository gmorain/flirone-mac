"""Step-by-step init probe: report exactly which USB operation fails.

The camera re-enumerates every few seconds when idle, so each attempt races to
grab it and the whole sequence is retried.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import usb.core  # noqa: E402
import usb.util  # noqa: E402

from flirone import protocol as proto  # noqa: E402
from flirone.usb_link import make_backend  # noqa: E402

BACKEND = make_backend()


def step(label, fn):
    try:
        value = fn()
        print(f"    OK   {label}" + (f" -> {value}" if value is not None else ""))
        return True
    except Exception as exc:
        print(f"    FAIL {label}: {type(exc).__name__}: {exc}")
        return False


def grab(timeout_s=25.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        dev = usb.core.find(idVendor=proto.VENDOR_ID, idProduct=proto.PRODUCT_ID, backend=BACKEND)
        if dev is not None:
            return dev
        time.sleep(0.01)
    return None


def attempt(n: int) -> bool:
    print(f"\n=== attempt {n} ===")
    dev = grab()
    if dev is None:
        print("    camera did not appear")
        return False

    def current_cfg():
        try:
            return dev.get_active_configuration().bConfigurationValue
        except Exception as exc:
            return f"<unreadable: {exc}>"

    print(f"    active configuration before: {current_cfg()}")
    step("set_configuration(3)", lambda: dev.set_configuration(proto.CONFIGURATION))
    print(f"    active configuration after:  {current_cfg()}")

    for iface in (0, 1, 2):
        step(f"claim_interface({iface})", lambda i=iface: usb.util.claim_interface(dev, i))

    # Only interfaces 1 and 2 have a non-empty alt 1.
    step("set_altsetting(iface=1, alt=1)",
         lambda: dev.set_interface_altsetting(interface=1, alternate_setting=1))
    step("set_altsetting(iface=2, alt=1)",
         lambda: dev.set_interface_altsetting(interface=2, alternate_setting=1))

    print("    reading EP 0x85 for 4s...")
    total = chunks = 0
    asm = proto.FrameAssembler()
    frames = 0
    deadline = time.monotonic() + 4.0
    err = None
    while time.monotonic() < deadline:
        try:
            data = dev.read(proto.EP_FRAME_IN, 65536, 200)
        except usb.core.USBTimeoutError:
            continue
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            break
        total += len(data)
        chunks += 1
        frames += len(asm.feed(bytes(data)))
    print(f"    chunks={chunks} bytes={total} frames={frames} desyncs={asm.desync_count}")
    if err:
        print(f"    read error: {err}")

    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass
    return frames > 0 or total > 0


def main() -> int:
    for n in range(1, 4):
        if attempt(n):
            print("\n>>> data received")
            return 0
    print("\n>>> no data on any attempt")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
