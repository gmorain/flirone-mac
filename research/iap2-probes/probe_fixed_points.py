"""Check the live count scale against temperatures known from physics.

The application uses a fixed scale, four with no offset, which is the Lepton's
14 bits sitting on a 16-bit plane. Nothing here is stored or fed back: this
exists to test that constant against a camera, and would be how a unit that
disagrees with it gets found.

Section 15 fits the live count scale from one point, the camera's own shutter.
One point cannot separate a gain from an offset, and the camera checking itself
is weak evidence anyway. Two physical fixed points settle both:

  ice-water slurry   0.00 C exactly, while ice and water coexist
  boiling water      100 C at sea level, less about 0.3 C per 100 m

Neither needs a calibrated instrument, which is the point: they are defined by
physics rather than measured.

Run it interactively:

    uv run python research/iap2-probes/probe_fixed_points.py --altitude 120

To add thermometer readings later, against fixed points already captured:

    uv run python research/iap2-probes/probe_fixed_points.py \
        --fit-from fixed_points.json

Then follow the prompts. Fill the frame with each target, look nearly straight
down but not dead-on, since water mirrors in LWIR at grazing angles and shows
the camera back to itself at normal incidence.

Counts are what matter here, not temperatures. The application's conversion is
the thing under test, so its reading would be circular.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np

from flirone import decode
from flirone import protocol as proto
from flirone.iap2.stream import Iap2StreamLink

SAMPLE_FRAMES = 20
# Sample the middle of the frame only. Edges catch the rim of a mug, the bench
# behind it, and the vignetting at the corners of the sensor.
CENTRE = 0.4


WATER_EMISSIVITY = 0.96
ROOM_C = 22.0


def solve_scale(planck, points, eps=WATER_EMISSIVITY, room=ROOM_C):
    """Factor and offset placing live counts on the reference plane.

        reference_raw = counts * factor + offset

    Two temperatures are the minimum that separates those: one point cannot
    tell a factor from an offset, which is how section 15 went wrong the first
    time. Expect a factor near 4, the Lepton's 14 bits on a 16-bit plane, and an
    offset near zero. A fit far from that is a bad measurement, not a discovery.

    The reflected term is included because the runtime conversion includes it.
    Fitting on uncorrected counts and then converting with the correction
    applied counts it twice, which moves the fixed points by over a degree.
    """
    reference = planck.r1 / planck.r2

    def reference_raw(tc):
        e = math.exp(planck.b / (tc + 273.15)) - planck.f
        e_room = math.exp(planck.b / (room + 273.15)) - planck.f
        return reference * (eps / e + (1 - eps) / e_room) - planck.o

    rows = np.array([[c, 1.0] for _, c in points])
    wanted = np.array([reference_raw(tc) for tc, _ in points])
    (factor, offset), *_ = np.linalg.lstsq(rows, wanted, rcond=None)
    return float(factor), float(offset)


def live_celsius(planck, factor: float, offset: float, counts: float) -> float:
    """Through the same path the application uses, so this tests what ships."""
    from flirone.calibration import Conditions, CountScale, raw_to_celsius

    scale = CountScale(factor=factor, offset=offset, source="fixed points")
    conditions = Conditions(
        emissivity=WATER_EMISSIVITY, reflected_c=ROOM_C, atmospheric_c=ROOM_C, distance_m=0.3
    )
    return float(raw_to_celsius(np.array([[counts]]), planck, conditions, scale)[0, 0])


def camera_planck(serial: str | None):
    """This camera's constants, from whatever the application already stored."""
    from flirone.calibration_store import CalibrationStore

    if "--planck" in sys.argv:
        from flirone import planck_import

        return planck_import.load(Path(sys.argv[sys.argv.index("--planck") + 1]))
    return CalibrationStore().find(serial)


def boiling_point(altitude_m: float) -> float:
    """Water boils lower up a hill. Linear is plenty over a few hundred metres."""
    return 100.0 - 0.0033 * altitude_m


def centre_of(frame_thermal: bytes) -> np.ndarray:
    raw = decode.decode_thermal(frame_thermal, proto.geometry_for(len(frame_thermal)))
    h, w = raw.shape
    dh, dw = int(h * (1 - CENTRE) / 2), int(w * (1 - CENTRE) / 2)
    return raw[dh : h - dh, dw : w - dw].astype(float)


def status_of(frame_status: bytes) -> dict:
    try:
        return json.loads(frame_status.decode().rstrip("\x00 ").strip() or "{}")
    except ValueError:
        return {}


def collect(link, assembler, seconds: float, want: int = 0):
    """Read for a while, returning (centre patches, last status)."""
    patches, status = [], {}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and link.alive:
        chunk = link.read_frame_chunk(100)
        link.keepalive()
        if not chunk:
            continue
        for frame in assembler.feed(chunk):
            patches.append(centre_of(frame.thermal))
            status = status_of(frame.status) or status
            if want and len(patches) >= want:
                return patches, status
    return patches, status


def sample(link, assembler, label: str, true_c: float) -> dict | None:
    input(f"\n  Fill the frame with {label}, then press Enter: ")
    collect(link, assembler, 1.5)  # discard what was queued while you moved
    patches, status = collect(link, assembler, 20.0, want=SAMPLE_FRAMES)
    if not patches:
        print("  no frames arrived")
        return None
    stack = np.stack(patches)
    mean, std = float(stack.mean()), float(stack.std())
    drift = float(stack.mean(axis=(1, 2)).std())
    print(f"  {len(patches)} frames: counts {mean:.1f}  spread {std:.1f}  drift {drift:.1f}")
    if std > 200:
        print("  spread is high; the target may not be filling the frame")
    return dict(
        label=label,
        true_c=true_c,
        counts=mean,
        spread=std,
        drift=drift,
        shutter_k=status.get("shutterTemperature"),
        ffc=status.get("ffcState"),
    )


def main() -> int:
    altitude = 0.0
    if "--altitude" in sys.argv:
        altitude = float(sys.argv[sys.argv.index("--altitude") + 1])
    boiling = boiling_point(altitude)

    print("Opening the camera. Unplug and reconnect it if it has been streaming.")
    link = Iap2StreamLink()
    link.open(timeout_s=120.0)
    print(f"  streaming, camera {(link.sled or {}).get('serialNumberBoard')}")

    assembler = proto.FrameAssembler()
    print("  waiting for the flat-field correction to finish...")
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline and link.alive:
        _, status = collect(link, assembler, 2.0)
        if status.get("ffcState") == "FFC_VALID_RAD":
            print("  radiometry valid")
            break
    else:
        print("  never reached FFC_VALID_RAD; readings would not be radiometric")

    if "--fit-from" in sys.argv:
        # Reuse fixed points captured earlier and take only measured ones now.
        # Worth knowing when reading the result: a disagreement then means
        # either the relationship is not linear or the counts drifted between
        # sessions, and one point cannot tell those apart.
        prior = Path(sys.argv[sys.argv.index("--fit-from") + 1])
        results = [r for r in json.loads(prior.read_text()) if not r.get("measured")]
        print(f"\n  reusing {len(results)} fixed points from {prior.name}:")
        for r in results:
            print(f"    {r['true_c']:6.1f} C  ->  {r['counts']:8.1f} counts")
        print("  these came from another session, so drift and non-linearity")
        print("  cannot be told apart if a measured point disagrees")
    else:
        targets = [
            ("an ice-water slurry, stirred and settled", 0.0),
            (f"boiling water (taken as {boiling:.1f} C at {altitude:.0f} m)", boiling),
        ]
        results = [r for label, t in targets if (r := sample(link, assembler, label, t))]

    # Extra points, measured with a thermometer. Two points force a straight
    # line through themselves and cannot test whether the relationship is
    # actually linear; a third somewhere in the middle can.
    while link.alive:
        answer = input("\n  Measured temperature of another target in C (blank to finish): ")
        if not answer.strip():
            break
        try:
            measured = float(answer.replace(",", "."))
        except ValueError:
            print("  not a number")
            continue
        point = sample(link, assembler, f"that target, measured at {measured:.1f} C", measured)
        if point:
            point["measured"] = True
            results.append(point)
    link.close()

    print("\n" + "=" * 62)
    for r in results:
        print(f"  {r['true_c']:6.1f} C  ->  {r['counts']:8.1f} counts   (spread {r['spread']:.1f})")
    if len(results) == 2 and "--fit-from" not in sys.argv:
        (a, b) = results
        span_c = b["true_c"] - a["true_c"]
        span_counts = b["counts"] - a["counts"]
        print(f"\n  {span_counts:.1f} counts across {span_c:.1f} C")
        if abs(span_counts) < 100:
            print("  that is too little to fit anything; check the targets filled the frame")
    fixed = [r for r in results if not r.get("measured")]
    planck = camera_planck((link.sled or {}).get("serialNumberBoard"))
    if len(fixed) == 2 and planck is not None:
        factor, offset = solve_scale(planck, [(r["true_c"], r["counts"]) for r in fixed])
        print(f"\n  live scale: reference_raw = {factor:.4f} x counts {offset:+.1f}")
        print(f"  the default, from the bit depths, is 4.0000 x counts +0.0")
        print(f"  ({100 * (factor / 4.0 - 1):+.1f}% on the factor)")
        extras = [r for r in results if r.get("measured")]
        if extras:
            print("\n  predicted against measured, for points not used in the fit:")
            for r in extras:
                got = live_celsius(planck, factor, offset, r["counts"])
                print(f"    measured {r['true_c']:6.1f} C  ->  predicted {got:6.2f} C  "
                      f"(off by {got - r['true_c']:+.2f})")
        else:
            print("\n  no independent point, so linearity is assumed rather than shown")
    elif planck is None:
        print("\n  no stored constants for this camera; pass --planck <radiometric.jpg>")

    out = Path("fixed_points.json" if "--fit-from" not in sys.argv else "fixed_points_extra.json")
    out.write_text(
        json.dumps(
            {"cross_session": "--fit-from" in sys.argv, "points": results}
            if "--fit-from" in sys.argv
            else results,
            indent=2,
        )
    )
    print(f"\n  written to {out.resolve()}")
    return 0 if len(fixed) == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
