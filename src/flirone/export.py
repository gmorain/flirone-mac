"""Saving captures.

A capture is a directory holding the dual-mode pair plus everything needed to
reprocess it later: the raw counts, the calibrated field, and the constants and
conditions that produced it. Radiometry that cannot be recomputed is not worth
much six months on.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from .calibration import Conditions, Planck
from .decode import DecodedFrame
from .measure import MeasurementSet
from .palettes import apply_palette, normalise


def timestamp_name(prefix: str = "flirone") -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def render(
    temps: np.ndarray, palette: str, vmin: float | None = None, vmax: float | None = None
) -> np.ndarray:
    return apply_palette(normalise(temps, vmin, vmax), palette)


def save_capture(
    directory: Path,
    frame: DecodedFrame,
    temps: np.ndarray,
    planck: Planck,
    conditions: Conditions,
    palette: str = "Iron",
    measurements: MeasurementSet | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
    write_geotiff: bool = False,
) -> Path:
    """Write one capture. Returns the directory created."""
    out = Path(directory) / timestamp_name()
    out.mkdir(parents=True, exist_ok=True)

    # Visible camera, unmodified.
    if frame.visible is not None:
        Image.fromarray(frame.visible).save(out / "visible.jpg", quality=95)

    # Colourised thermal, as shown on screen.
    Image.fromarray(render(temps, palette, vmin, vmax)).save(out / "thermal.png")

    # Raw counts and the calibrated field, both lossless.
    import tifffile

    tifffile.imwrite(out / "raw_counts.tiff", frame.raw.astype(np.uint16))
    tifffile.imwrite(out / "temperature_celsius.tiff", temps.astype(np.float32))

    # A flat CSV, because that is what usually gets opened first.
    np.savetxt(out / "temperature_celsius.csv", temps, delimiter=",", fmt="%.3f")

    # Everything needed to replay or recompute.
    if frame.visible is not None:
        np.savez_compressed(out / "capture.npz", raw=frame.raw, visible=frame.visible)
    else:
        np.savez_compressed(out / "capture.npz", raw=frame.raw)

    meta = {
        "captured_at": datetime.now().astimezone().isoformat(),
        "sensor": {"width": int(temps.shape[1]), "height": int(temps.shape[0])},
        "planck": {
            "R1": planck.r1,
            "R2": planck.r2,
            "B": planck.b,
            "F": planck.f,
            "O": planck.o,
            "trusted": planck.trusted,
            "source": planck.source,
        },
        "conditions": asdict(conditions),
        "palette": palette,
        "scale": {"vmin": vmin, "vmax": vmax},
        "statistics": {
            "min_c": float(np.nanmin(temps)),
            "max_c": float(np.nanmax(temps)),
            "mean_c": float(np.nanmean(temps)),
        },
        "camera_status": frame.status,
    }
    if measurements is not None:
        meta["measurements"] = [
            {"label": label, "value": value} for label, value in measurements.summarise(temps)
        ]
    if not planck.trusted:
        meta["warning"] = (
            "Planck constants are not from this camera. Temperatures are "
            "relative only and must not be quoted as absolute."
        )
    (out / "metadata.json").write_text(json.dumps(meta, indent=2))

    if write_geotiff:
        _write_geotiff(out / "temperature_celsius_geo.tiff", temps)

    return out


def _write_geotiff(path: Path, temps: np.ndarray) -> None:
    """Write the temperature field as a GeoTIFF on a pixel grid.

    There is no georeferencing on a handheld thermal frame, so this writes an
    identity transform. It exists so the raster drops straight into an existing
    rasterio/GDAL workflow, not to imply the pixels are located on the ground.
    """
    try:
        import rasterio
        from rasterio.transform import Affine
    except ImportError as exc:
        raise RuntimeError("GeoTIFF export needs the 'geo' extra: uv pip install rasterio") from exc

    height, width = temps.shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        transform=Affine.identity(),
        nodata=float("nan"),
    ) as dst:
        dst.write(temps.astype(np.float32), 1)
        dst.update_tags(units="degrees_celsius", note="pixel grid, not georeferenced")
