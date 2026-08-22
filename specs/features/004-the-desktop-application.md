# 004 — The desktop application

Status:  shipped

Depends: 002-temperature-and-alignment

---

## User

A normal macOS application: it appears in the Dock with its own icon, accepts
files dropped onto it, and opens ready for work rather than showing a
placeholder scene.

**Display.** The temperature field is drawn with a choice of palettes, including
the iron palette thermographers expect and a grayscale for print. A user can
show the thermal field alone, the visible photograph alone, a blend of the two
at an adjustable weight, or the thermal field with the visible image's edges
over it, which keeps temperatures readable while making the scene recognisable.
Colour mapping defaults to the 1st–99th percentile, because a handful of
saturated pixels would otherwise consume most of the palette and flatten
everything else; the true range, manual limits and a locked range are all
available.

**Zoom.** The image magnifies up to 16x under the scroll wheel, anchored on the
pointer so the feature being examined stays put. Panning is a left-drag in
Cursor mode, which is otherwise an idle gesture, so it needs no middle button. This
is what makes a sub-pixel judgement possible: at fit-to-window the image is
drawn at roughly its own size, so alignment cannot be checked closely.

**Measurement.** Tools are placed directly on the image and read absolute
temperatures. Spot meters read a point. Region boxes report minimum, mean,
maximum and spread. A line produces a temperature profile plotted beneath it,
which is how a thermal bridge or a gradient across a surface is actually
assessed. Any two spots pair into a difference, so a rise over ambient is read
directly. The hottest and coldest points are tracked and labelled continuously,
which matters on a live image where the peak moves. Every reading appears in a
table, labelled and in degrees, and is shown as untrustworthy when the
calibration in use did not come from the camera that took the picture. Labels
are drawn on their own backing so they stay legible over a saturated hot region.

**Spot validity.** A radiometric reading is only trustworthy when the target
fills several detector elements; below three the pixel averages target and
surroundings and reads low. The cursor warns when the feature under it is too
small, judged on the detector grid rather than on the upscaled file, so an
upsampled image is not flattered. The check needs no calibration; a distance set
in Conditions additionally gives the size in millimetres.

**Saving.** Line profiles export as CSV, one row per pixel step, carrying the
pixel coordinates and the calibration and conditions they were computed under, so
a curve can be reprocessed elsewhere and each row traced back to a place in the
image. Hovering the plot marks that pixel on the image and names the row.

A capture stores everything needed to reconstruct the measurement
later: raw counts, the temperature field, the visible photograph, the rendered
image, and the calibration and conditions in force. So a capture reopened months
later can be reprocessed under corrected assumptions and produce the
temperatures that should have been recorded. Temperature grids also export as
CSV or TIFF, and a folder of captures replays through the application as a
source.

Controls are split by how often they are touched: input, palette, mode, tools
and the readout are immediately to hand, while conditions, alignment,
calibration and output location sit behind a second tab. The window title states
sensor resolution, frame rate, and whether the current calibration is
trustworthy.

## Tech

PySide6, over a library that carries no UI dependency: every capability is
usable from a script, and the application is a view onto it. Frame sources run
on their own thread and publish to the UI, which polls, so the interface stays
responsive while a camera streams or an alignment is computed.

Measurement tools operate on the temperature field, never on rendered colours,
so results are independent of palette and scaling.

Captures are compressed NumPy archives with metadata alongside, keeping raw
counts exact without inventing a file format. TIFF holds temperatures as 32-bit
float, which round-trips without quantisation. GeoTIFF sits behind an optional
extra, since the geospatial stack is heavy and irrelevant to most users.

The bundle is assembled by a shell script rather than a packaging framework:
Info.plist, an icns built from artwork, document type declarations, and a
launcher. JPEG is registered as an alternate handler rather than the default, so
the system image viewer is not displaced. External tools are resolved by
absolute path, because a bundle launched from Finder inherits no shell
environment.

**Verified manually — no test coverage.** Rendering, export and the interface are
exercised by driving the application headlessly during development and checking
the result by eye, not asserted.

## Tasks

- [x] Palettes, display modes and robust percentile scaling
- [x] Spots, regions, line profiles, differences and extreme tracking
- [x] Warning when a spot's target is too small to measure
- [x] Readout table with calibration state surfaced
- [x] Legible labels over any background
- [x] Captures with everything needed to reprocess
- [x] Line profile CSV export with pixel coordinates and provenance
- [x] Cursor and profile hover reporting pixel coordinates, cross-linked
- [x] CSV and float TIFF export, optional GeoTIFF, replayable folders
- [x] Zoom and pan, with every coordinate conversion going through one rect
- [x] Two-tab layout split by frequency of use
- [x] App bundle with icon and document types
