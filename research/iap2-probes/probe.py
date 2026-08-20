"""Hardware probe: does the FLIR One stream on macOS, and by which start mode?

Prints the descriptor tree, then tries both stream-start strategies and reports
how many bytes and whole frames each one produced.

    uv run python tools/probe.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import usb.core  # noqa: E402
import usb.util  # noqa: E402

from flirone import protocol as proto  # noqa: E402
from flirone.usb_link import FlirOneLink, StartMode, make_backend  # noqa: E402


def dump_descriptors(dev) -> None:
    print(f"  bus {dev.bus} address {dev.address}  {dev.idVendor:#06x}:{dev.idProduct:#06x}")
    try:
        print(f"  manufacturer={dev.manufacturer!r} product={dev.product!r}")
    except Exception as exc:  # descriptor strings need an open handle
        print(f"  (string descriptors unavailable: {exc})")
    for cfg in dev:
        print(f"  configuration {cfg.bConfigurationValue}  ({cfg.bNumInterfaces} interfaces)")
        for intf in cfg:
            eps = " ".join(f"{ep.bEndpointAddress:#04x}" for ep in intf)
            print(
                f"    interface {intf.bInterfaceNumber} alt {intf.bAlternateSetting} "
                f"class {intf.bInterfaceClass:#04x}  endpoints: {eps or '(none)'}"
            )


def try_stream(mode: StartMode, seconds: float, backend) -> dict:
    result = {"mode": mode.value, "bytes": 0, "chunks": 0, "frames": 0, "error": None}
    link = FlirOneLink(start_mode=mode, backend=backend)
    assembler = proto.FrameAssembler()
    try:
        link.open(timeout_s=25.0)
        link.start_stream()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            chunk = link.read_frame_chunk(timeout_ms=200)
            if not chunk:
                continue
            result["bytes"] += len(chunk)
            result["chunks"] += 1
            frames = assembler.feed(chunk)
            result["frames"] += len(frames)
            if frames and result["frames"] == 1:
                f = frames[0]
                result["first_frame"] = {
                    "thermal": len(f.thermal),
                    "jpeg": len(f.jpeg),
                    "status": len(f.status),
                    "geometry": f.geometry,
                    "status_text": f.status[:200].decode("utf-8", "replace"),
                }
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            link.close()
        except Exception:
            pass
    result["desyncs"] = assembler.desync_count
    return result


def main() -> int:
    backend = make_backend()
    print("libusb backend loaded")

    print("\nwaiting for the camera to enumerate (it cycles when idle)...")
    dev = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        dev = usb.core.find(idVendor=proto.VENDOR_ID, idProduct=proto.PRODUCT_ID, backend=backend)
        if dev is not None:
            break
        time.sleep(0.02)
    if dev is None:
        print("FAIL: camera never appeared. Power it on and retry.")
        return 1

    print("\nDESCRIPTORS")
    dump_descriptors(dev)
    usb.util.dispose_resources(dev)

    for mode in (StartMode.ALT_SETTING, StartMode.CONTROL):
        print(f"\n--- start mode: {mode.value} ---")
        result = try_stream(mode, seconds=6.0, backend=backend)
        print(
            f"  chunks={result['chunks']} bytes={result['bytes']} "
            f"frames={result['frames']} desyncs={result['desyncs']}"
        )
        if result["error"]:
            print(f"  error: {result['error']}")
        if "first_frame" in result:
            ff = result["first_frame"]
            print(
                f"  first frame: thermal={ff['thermal']}B jpeg={ff['jpeg']}B "
                f"status={ff['status']}B geometry={ff['geometry']}"
            )
            print(f"  status: {ff['status_text']}")
        if result["frames"]:
            print(f"  >>> {mode.value} WORKS")
            return 0
    print("\nNo start mode produced frames.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
