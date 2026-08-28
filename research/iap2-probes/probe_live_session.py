"""Exercise the three live paths that have never been run, in one camera session.

Nothing here is known broken. Nothing here has been run either, which is the
point: capture, replay of a live capture, mid-session recovery and stream
survival have only ever been reasoned about.

    uv run python research/iap2-probes/probe_live_session.py --minutes 5

It goes through `UsbFrameSource`, the same source the application uses, rather
than driving the link directly, so what passes here is what the application
does. Quit the application first: only one process can hold the camera.

Three things are checked.

1. **Capture round trip.** A live frame is written with `save_capture` and read
   back the way a replay reads it. A live capture stores raw counts, so if the
   scale did not travel with it the reopened frame would convert as though it
   came from a stored image and read about a hundred degrees low. This is the
   check that would catch that.

2. **Recovery from a drop.** Halfway through, unplug the camera and plug it back
   in. `UsbFrameSource` reconnects in a loop and that loop now has to reset a
   camera whose completed handshake left it without a watchdog.

3. **Survival.** Frames and gaps are logged throughout. One earlier run dropped
   after 36 frames and others ran for minutes, and nobody has established what
   the difference depends on, so this records rather than concludes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

import numpy as np

from flirone.calibration import Conditions, raw_to_celsius
from flirone.calibration_store import CalibrationStore
from flirone.decode import DecodedFrame
from flirone.export import save_capture
from flirone.measure import Line, MeasurementSet
from flirone.sources import UsbFrameSource

# A gap longer than this is worth naming. Frames arrive at about 7.5 fps, so
# anything past a second is a stall rather than jitter.
GAP_S = 1.0


def camera_planck(source: UsbFrameSource):
    serial = (source.sled or {}).get("serialNumberBoard")
    planck = CalibrationStore().find(serial)
    if planck is None:
        print(f"  no stored constants for {serial}; load a radiometric JPEG first")
    return planck


def check_capture_round_trip(frame: DecodedFrame, planck, conditions, out: Path) -> bool:
    """Write a live frame and read it back the way a replay would."""
    temps = raw_to_celsius(frame.raw, planck, conditions, frame.scale)
    mid = frame.raw.shape[0] // 2
    measurements = MeasurementSet(lines=[Line(0, mid, frame.raw.shape[1] - 1, mid, label="across")])
    directory = save_capture(out, frame, temps, planck, conditions, measurements=measurements)
    print(f"  written to {directory}")

    with np.load(directory / "capture.npz", allow_pickle=False) as data:
        keys = sorted(data.keys())
        if "live_factor" not in data:
            print(f"  FAIL: capture carries no scale, keys are {keys}")
            return False
        from flirone.calibration import CountScale

        scale = CountScale(float(data["live_factor"]), float(data["live_offset"]), "capture")
        reopened = raw_to_celsius(data["raw"], planck, conditions, scale)

    worst = float(np.nanmax(np.abs(reopened - temps)))
    print(f"  scale recorded: {scale}")
    print(
        f"  live {np.nanmin(temps):.1f} to {np.nanmax(temps):.1f} C, "
        f"reopened {np.nanmin(reopened):.1f} to {np.nanmax(reopened):.1f} C, "
        f"worst difference {worst:.4f} C"
    )
    csv = directory / "temperature_celsius.csv"
    profiles = list(directory.glob("profile_*.csv"))
    print(f"  csv {csv.stat().st_size // 1024} kB, {len(profiles)} line profile(s)")
    return worst < 0.01 and csv.exists()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=5.0)
    parser.add_argument("--out", type=Path, default=Path.home() / "Pictures" / "flirone-probe")
    args = parser.parse_args()

    conditions = Conditions()
    source = UsbFrameSource()
    source.start()
    print("Bringing the camera up. This takes 20 to 30 seconds.")

    deadline = time.monotonic() + 90.0
    frame = None
    while frame is None and time.monotonic() < deadline:
        frame = source.latest(timeout=1.0)
    if frame is None:
        print(f"  no frame in 90 s: {source.status} {source.error or ''}")
        source.stop()
        return 1
    print(f"  streaming, camera {(source.sled or {}).get('serialNumberBoard')}")

    planck = camera_planck(source)
    if planck is None:
        source.stop()
        return 1

    print("\n1. Capture round trip")
    ok = check_capture_round_trip(frame, planck, conditions, args.out)
    print(f"   {'pass' if ok else 'FAIL'}")

    print(f"\n2 and 3. Survival for {args.minutes:.0f} minutes, and recovery")
    print("   Unplug the camera partway through, then plug it back in.")
    end = time.monotonic() + args.minutes * 60
    frames = gaps = 0
    last = time.monotonic()
    longest = 0.0
    reconnects_seen = source.reconnects
    while time.monotonic() < end:
        got = source.latest(timeout=2.0)
        now = time.monotonic()
        if got is None:
            continue
        frames += 1
        gap = now - last
        last = now
        longest = max(longest, gap)
        if gap > GAP_S:
            gaps += 1
            print(f"   {gap:5.1f} s gap after {frames} frames  ({source.status})")
        if source.reconnects != reconnects_seen:
            reconnects_seen = source.reconnects
            print(f"   reconnected, {reconnects_seen} so far, and frames are arriving again")

    elapsed = args.minutes * 60
    print(f"\n   {frames} frames in {elapsed:.0f} s, {frames / elapsed:.1f} fps")
    print(f"   {gaps} gaps over {GAP_S} s, longest {longest:.1f} s")
    print(f"   {source.reconnects} reconnects, {source.frames_seen} frames seen by the source")
    print(f"   final status: {source.status} {source.error or ''}")
    source.stop()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
