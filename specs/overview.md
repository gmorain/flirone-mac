# flirone-mac

## What it is

A desktop application for the FLIR One thermal camera, on macOS and Linux. It shows a live dual-mode
preview of the thermal and visible cameras, opens radiometric images shot with
the FLIR One phone app, and provides measurement tools that read absolute
temperatures: spot meters, region statistics, line profiles and differences,
with emissivity and atmospheric correction applied.

The distinguishing constraint is honesty about calibration. A FLIR One's Planck
constants are calibrated per unit at the factory, so temperatures computed with
another camera's constants are wrong by degrees. The application tracks where
its constants came from, marks readings as uncalibrated when they did not come
from the camera that took the picture, and says so in the window title and in
the readout colour rather than presenting a plausible number.

## Primary users

- **Thermographers and engineers** taking measurements they intend to act on or
  report, who need to know whether a number is trustworthy.
- **Owners of a FLIR One** who want a desktop workflow instead of a phone, for
  analysis, comparison and export.
- **Anyone reprocessing past captures**, adjusting emissivity or reflected
  temperature after the fact and recomputing the whole field.

## In scope

- Reading FLIR radiometric JPEGs, including the raw 16-bit plane, the camera's
  own calibration and the conditions the shot was taken under.
- Converting raw sensor counts to degrees Celsius with the full object-radiance
  correction.
- Measurement tools over the temperature field, with results in absolute units.
- Rendering the thermal field with selectable palettes, and fusing it with the
  visible image.
- Recovering the alignment between the thermal and visible cameras from image
  content.
- Exporting captures in forms that can be reprocessed later.
- Speaking the FLIR One USB protocol, including Apple's iAP2 accessory
  handshake for the Lightning variant.

## Out of scope

- Video recording, time-lapse and sequence analysis.
- Automated fault classification or diagnostic advice.
- Cameras other than the FLIR One family.
- Editing or retouching the visible image.
- Any use of FLIR's proprietary SDK, or distribution of their software.
- Windows.
- Packaged builds anywhere but macOS. Linux runs the application from source.

## Architecture

Python throughout, with a PySide6 desktop front end over a library that has no
UI dependency: every capability below is usable from a script or a notebook, and
the application is a view onto it. NumPy carries the temperature field, Pillow
handles image containers, pyusb drives the camera, and exiftool is shelled out
to for FLIR's APP1 metadata, which no Python library reads reliably.

Frames arrive from interchangeable sources behind one interface: the camera over
USB, a folder of previous captures, a still radiometric image, or a synthetic
scene for development without hardware. That indirection is what allowed the
whole application to be built and tested while the live USB path remained
blocked.

External tools are resolved by absolute path rather than through `PATH`, because
an app bundle launched from Finder does not inherit a shell environment and
would otherwise fail to find Homebrew.
