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

Doing that once per session is busywork, so the application remembers. Constants
are kept per camera, filed under the serial they came from, and a camera that
identifies itself gets its own back automatically: readings are absolute from
the first frame, with no action and nothing to re-select. Owning two cameras is
therefore ordinary rather than dangerous, since each is only ever matched with
its own. Constants that carry no serial are never remembered, because there
would be no way to know which camera they belong to. What has been remembered
can be dropped, either for the camera in use or for every camera at once, and
dropping it returns readings to relative rather than leaving a stale claim of
accuracy.

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

The store is keyed by serial and looked up by the same containment match, so
constants filed from an image are found again from the live handshake, which
spells the serial differently. It lives in the platform's own application
state directory. A missing or corrupt store reads as empty rather than raising,
and one damaged entry does not cost the others, since losing a calibration to a
parse error is worse than ignoring it. Writes go through a temporary file and a
rename, so an interrupted save cannot truncate what was already remembered.

A live stream carries no constants at all, so what promotes stored ones to
absolute is the serial reported by the accessory handshake matching the serial
they were filed under. That check is the same one made against an image.

Covered by tests for serial matching across both formats, rejection of
non-physical constants and models, exact recovery from synthetic samples, the
legacy file key, and for the store: cross-format lookup, refusal of unkeyed
constants, isolation between two cameras, and survival of a corrupt file.

## Tasks

- [x] Three-state trust: from this camera, unverified, mismatched
- [x] Serial recorded, matched tolerantly across both known formats
- [x] Range validation of constants on load
- [x] Warning when constants come from a different camera
- [x] Saved calibrations retain the serial
- [x] Constants remembered per camera between sessions
- [x] A camera that identifies itself gets its own constants back automatically
- [x] Forget one camera, or all of them
