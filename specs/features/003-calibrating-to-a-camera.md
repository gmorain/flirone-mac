# 003 — Calibrating to a camera

Status:  shipped

Depends: 002-temperature-and-alignment

---

## User

One thing must be learned about a specific camera before its numbers mean
anything, and it is a one-off.

These are calibrated per unit at the factory, and a
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

Covered by tests for serial matching across both formats, rejection of
non-physical constants and models, exact recovery from synthetic samples, and
the legacy file key.

## Tasks

- [x] Three-state trust: from this camera, unverified, mismatched
- [x] Serial recorded, matched tolerantly across both known formats
- [x] Range validation of constants on load
- [x] Warning when constants come from a different camera
- [x] Saved calibrations retain the serial
