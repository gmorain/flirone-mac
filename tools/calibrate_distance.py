"""Fit the parallax model from images shot at known distances.

    uv run python tools/calibrate_distance.py near.jpg=0.3 far.jpg=2.0 [more...]

Shoot the same kind of edge-rich scene at two or more accurately measured
distances, spread as widely as possible: the model is linear in 1/Z, so two
nearby distances barely constrain it. The result is written to
~/.flirone/parallax.json and picked up by the app.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from flirone import flirjpeg, registration  # noqa: E402
from flirone.calibration import raw_to_celsius  # noqa: E402
from flirone.distance import calibrate  # noqa: E402

OUTPUT = Path.home() / ".flirone" / "parallax.json"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    samples = []
    for argument in argv:
        if "=" not in argument:
            print(f"expected image=distance, got {argument!r}")
            return 2
        filename, _, distance_text = argument.rpartition("=")
        path = Path(filename)
        try:
            distance = float(distance_text)
        except ValueError:
            print(f"bad distance in {argument!r}")
            return 2

        image = flirjpeg.load(path)
        if image.visible is None:
            print(f"{path.name}: no visible image, skipping")
            continue
        temps = raw_to_celsius(image.raw, image.planck, image.conditions)

        quality = registration.contrast(temps)
        prior = image.alignment.scale if image.alignment is not None else None
        try:
            result = registration.estimate(temps, image.visible, scale_prior=prior)
        except registration.InsufficientContrast:
            print(f"{path.name:28s} {distance:5.2f} m  ->  UNUSABLE: {quality}")
            print("    The thermal image is nearly flat, so there is no structure to")
            print("    match. Shoot a scene with real thermal relief; see the README.")
            continue

        print(f"{path.name:28s} {distance:5.2f} m  ->  {result}")
        print(f"    thermal {quality}")
        if quality.weak:
            print("    WARNING: marginal thermal contrast, the offset may be unreliable")
        if result.confidence < 0.15:
            print("    WARNING: weak edge match, use a scene with more shared structure")
        # Parallax is on the axis of the lens baseline, which in portrait is
        # the image's vertical axis.
        samples.append((distance, result.dy))

    if len(samples) < 2:
        print("need at least two usable images")
        return 1

    model = calibrate(samples)
    print(
        f"\ndy_inf {model.dy_inf:+.2f} px   K {model.k:.3f} px.m   "
        f"residual {model.residual:.2f} px over {model.samples} samples"
    )

    # How well the geometry constrains K. The fit is linear in 1/Z, so the
    # spread in 1/Z is the lever arm, and it is set almost entirely by the
    # nearest shot: 1/0.25 is 4.0, 1/3 is only 0.33.
    inverse = [1.0 / z for z, _ in samples]
    lever = max(inverse) - min(inverse)
    if lever <= 1e-6:
        print("ERROR: every shot is at the same distance, so K cannot be")
        print("       separated from the fixed offset. Use two distances.")
        return 1
    sigma_k = 0.3 * (2**0.5) / lever
    relative = 100 * sigma_k / abs(model.k) if model.k else float("inf")
    print(f"lever arm {lever:.2f} 1/m  ->  K uncertain to ~{sigma_k:.3f} px.m ({relative:.0f}%)")

    nearest = min(z for z, _ in samples)
    if nearest > 0.4:
        print(
            f"NOTE: the nearest shot is {nearest:.2f} m. The lever arm is set almost\n"
            "      entirely by the closest distance. Reshooting it at 0.25-0.3 m would\n"
            "      sharpen K several-fold; moving the far shot beyond 3 m gains almost\n"
            "      nothing (3 m to infinity is worth only about 0.2%)."
        )
    if model.k <= 0:
        print(
            "\nREJECTED: K came out negative, which is physically impossible - it is\n"
            "          focal length times baseline, both positive. The offsets did not\n"
            "          vary with distance the way parallax must, which means the\n"
            "          registration did not lock onto real structure. Nothing saved.\n"
            "          Reshoot with a scene carrying strong thermal relief."
        )
        return 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    model.to_json(OUTPUT)
    print(f"saved {OUTPUT}")
    print("\nprecision from this fit:")
    for z in (0.3, 0.5, 1.0, 2.0, 5.0):
        print(f"   at {z:4.1f} m  ->  +/- {model.uncertainty_at(z):.2f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
