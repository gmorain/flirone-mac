# 003 — Calibrating to a camera

Status:  shipped

Depends: 002-temperature-alignment-and-distance

---

## User

Two things must be learned about a specific camera before its numbers mean
anything, and both are a one-off.

**Radiometric constants.** These are calibrated per unit at the factory, and a
live camera does not transmit its own, so a user takes them from one radiometric
image and applies them to whatever else they are measuring. Because constants
from another unit give temperatures that look plausible and are wrong, the
application checks rather than assumes: both the image and the constants carry a
serial number, and they are compared. Where they agree, readings become
absolute. Where they demonstrably disagree, the user is told which camera each
came from and readings stay marked relative. Where the comparison cannot be made
at all, that is reported as its own state rather than rounded up to confidence
or down to failure. A calibration can be saved and reloaded later, keeping the
serial so the same check still applies.

**The parallax relationship.** Photograph a suitable scene from two accurately
measured distances and run a command, and thereafter any aligned image yields a
distance. The scene matters more than the geometry: it needs real thermal
relief, edges in both bands, a single depth, and no repeating pattern. The tool
refuses images too flat to use and says why, and reports how well the two
distances constrained the result.

## Tech

Trust is three states, not a boolean. "From a different camera" and "cannot
tell" are different claims; conflating them either overstates confidence or
cries wolf on the ordinary case of calibrating a live camera whose serial is not
to hand. Readers return constants marked unverified; only comparison can promote
them.

Serials are written differently in different places: EXIF reports
`F02F9T00570` where the accessory handshake reports `FLIRONEF02F9T00570A` for
the same unit, so matching is containment after normalisation, with a minimum
length so short strings cannot match by accident. Constants are range-checked on
load, since a zero or negative value gives a division by zero rather than a
merely wrong temperature.

The parallax fit is least squares in 1/Z, so the lever arm is dominated by the
nearest shot; the tool reports it and the README tabulates the trade. `K` is
focal length times baseline and cannot be negative: a fit producing one is
rejected rather than saved, and a stored model containing one is refused on
load, because a plausible distance from a broken calibration is worse than none.
`K` belongs to the camera model rather than the unit, so it is established once
per model, while the per-unit boresight offset comes from each image.

Covered by tests for serial matching across both formats, rejection of
non-physical constants and models, exact recovery from synthetic samples, and
the legacy file key.

## Tasks

- [x] Three-state trust: from this camera, unverified, mismatched
- [x] Serial recorded, matched tolerantly across both known formats
- [x] Range validation of constants on load
- [x] Warning when constants come from a different camera
- [x] Saved calibrations retain the serial
- [x] Least-squares parallax calibration with lever-arm reporting
- [x] Rejection of non-physical fits on save and on load
