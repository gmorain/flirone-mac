"""Turn a generated icon image into a macOS .icns.

Image generators produce a rounded tile on a white background with a baked
drop shadow, which is not what macOS wants: the system draws its own shadow and
expects Apple's own corner geometry on a transparent canvas.

So this crops the tile out, re-masks it to Apple's shape, and lays it out on the
standard grid: a 1024 canvas with the tile occupying the centre 824, which is
what gives an icon the right optical size next to the system ones.

    uv run python tools/make_icon.py source.png [-o FLIROne.icns]
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

CANVAS = 1024
TILE = 824  # Apple's content size within a 1024 icon
SUPERELLIPSE_EXPONENT = 5.0  # approximates the continuous-curvature squircle

# Where the lens aperture sits, as fractions of the canvas. Measured from the
# artwork rather than assumed, so a redrawn reticle lands on the real lens.
APERTURE_CX = 0.4976
APERTURE_CY = 0.4946
APERTURE_R = 0.2451

# Below this, the artwork's own reticle is thinner than a pixel and vanishes.
RETICLE_REDRAW_BELOW = 48

RETICLE_COLOUR = (226, 240, 255, 255)


def redraw_reticle(icon: Image.Image, size: int, supersample: int = 4) -> Image.Image:
    """Render a small size with a reticle heavy enough to survive.

    The artwork's crosshair is drawn for 1024 px. Scaled to 16 or 32 its arms
    fall below one pixel and disappear into the thermal bloom, leaving a shape
    that could be any camera app. The icns format carries separate artwork per
    size precisely so this can be fixed; here the same tile and bloom are kept
    and only the reticle is restated at a weight that reads.
    """
    n = size * supersample
    image = icon.resize((n, n), Image.Resampling.LANCZOS).convert("RGBA")
    overlay = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    cx, cy = APERTURE_CX * n, APERTURE_CY * n
    radius = APERTURE_R * n
    # Weight is not simply proportional: 16 px needs a floor to stay visible,
    # while 32 px looks clotted if the arms scale linearly from it.
    stroke = max(size * 0.046, 1.15) * supersample
    width = max(int(round(stroke)), 1)

    # A generous centre gap keeps the hot core showing through; without it the
    # reticle reads as a rifle scope rather than a thermal camera.
    inner = radius * 0.46
    outer = radius * 1.03
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        draw.line(
            [cx + dx * inner, cy + dy * inner, cx + dx * outer, cy + dy * outer],
            fill=RETICLE_COLOUR,
            width=width,
        )

    dot = max(stroke * 0.5, supersample * 0.5)
    draw.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=RETICLE_COLOUR)

    return Image.alpha_composite(image, overlay).resize((size, size), Image.Resampling.LANCZOS)


def render_size(icon: Image.Image, size: int) -> Image.Image:
    if size < RETICLE_REDRAW_BELOW:
        return redraw_reticle(icon, size)
    return icon.resize((size, size), Image.Resampling.LANCZOS)


# The sizes iconutil expects, as (pixel size, filename).
ICONSET = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]


def squircle_mask(size: int, exponent: float = SUPERELLIPSE_EXPONENT) -> Image.Image:
    """Apple-style rounded square as an 8-bit alpha mask, anti-aliased."""
    supersample = 4
    n = size * supersample
    axis = (np.arange(n) - (n - 1) / 2.0) / ((n - 1) / 2.0)
    x, y = np.meshgrid(axis, axis)
    inside = np.abs(x) ** exponent + np.abs(y) ** exponent <= 1.0
    mask = Image.fromarray((inside * 255).astype(np.uint8), mode="L")
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def crop_tile(image: Image.Image, tolerance: int = 26) -> Image.Image:
    """Cut the icon tile out of its background.

    Uses the alpha channel when there is one. Otherwise assumes a light
    background and keeps pixels that differ from the corner colour, then trims
    a little more to drop the soft drop shadow the generator baked in.
    """
    rgba = image.convert("RGBA")
    array = np.asarray(rgba)

    if array[..., 3].min() < 250:
        # Find the solid tile, not its shadow: generators render the drop
        # shadow into the alpha channel too, and a low threshold would include
        # it, pulling the bounding box down and off-centring the crop.
        occupied = array[..., 3] > 200
    else:
        corner = array[0, 0, :3].astype(int)
        distance = np.abs(array[..., :3].astype(int) - corner).sum(axis=2)
        occupied = distance > tolerance

    rows = np.flatnonzero(occupied.any(axis=1))
    cols = np.flatnonzero(occupied.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return rgba
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1

    # The detected box includes the drop shadow, which is wider at the bottom.
    # Take the largest centred square inside it and inset slightly.
    height, width = bottom - top, right - left
    side = int(min(height, width) * 0.985)
    cx, cy = (left + right) // 2, (top + bottom) // 2
    half = side // 2
    return rgba.crop((cx - half, cy - half, cx + half, cy + half))


def build(source: Path) -> Image.Image:
    tile = crop_tile(Image.open(source)).resize((TILE, TILE), Image.Resampling.LANCZOS)
    # The silhouette comes from Apple's mask alone. The source alpha only
    # describes the generator's own tile and shadow, and often plateaus just
    # below 255, which would leave the whole icon faintly translucent.
    tile = Image.alpha_composite(Image.new("RGBA", tile.size, (0, 0, 0, 255)), tile)
    tile.putalpha(squircle_mask(TILE))

    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    offset = (CANVAS - TILE) // 2
    canvas.paste(tile, (offset, offset), tile)
    return canvas


def write_icns(icon: Image.Image, destination: Path) -> None:
    with tempfile.TemporaryDirectory() as directory:
        iconset = Path(directory) / "icon.iconset"
        iconset.mkdir()
        for size, name in ICONSET:
            render_size(icon, size).save(iconset / name)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(destination)],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("resources/FLIROne.icns"))
    parser.add_argument("--preview", type=Path, help="also write the masked 1024 PNG here")
    args = parser.parse_args()

    icon = build(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_icns(icon, args.output)
    print(f"wrote {args.output}")
    if args.preview:
        icon.save(args.preview)
        print(f"wrote {args.preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
