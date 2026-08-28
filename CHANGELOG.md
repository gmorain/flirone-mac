# Changelog

## 0.9.9

The macOS release: everything works there, live and from a file, in absolute
degrees. Linux is 1.0.0.

**Live streaming from a Lightning FLIR One works.** An inline analyser capture
of the official iOS app showed the blocker was self-inflicted: authentication
comes before identification, exactly one accessory session is opened and it is
the configuration one, the camera's file listing must be read and drained, and
the stream is started by a command rather than by opening a session. Earlier
attempts opened a session no real host opens, which the camera answers by
leaving the bus. About 7.5 fps, reproducible, and it reaches the application.

**Live readings are absolute.** The stream carries the Lepton's 14-bit counts
where the plane the Planck constants describe is 16-bit, so converting them is a
multiplication by four and nothing else. Checked against an ice-water slurry and
boiling water, both fixed by physics: 0.35 and 100.19 C. Nothing is calibrated
per camera for this, because a bit depth belongs to the product rather than to a
serial number.

That answer took two wrong turns, both recorded in section 15 of
`docs/hardware-findings.md` rather than quietly dropped. It was first fitted
from the camera's own shutter, which is a thermistor on the mechanism and reads
7.8 C from what the detector sees; and one point cannot separate a factor from
an offset anyway. The two-parameter fit that replaced it then appeared to find
an offset, which turned out to be an artefact of how the scale was
parameterised. It is a factor of four with no offset, which is what the Android
reverse engineering established in 2016.

Calibration constants are now remembered per camera, filed under the serial they
came from, so a camera identified over the accessory handshake gets its own back
automatically and two cameras are never confused. Two menu entries forget what
is stored, for one camera or for all.

Fixed: the FLIR APP1 segment reader took all five Planck constants from one
offset, which is where the camera model string lives. It returned values that
were positive, absurd, and accepted, making readings about a hundred degrees
low. The constants are not contiguous, the serial was never read at all, and
validation now bounds magnitudes rather than only signs.

The stored-image conversion is cross-checked against an independent
implementation of FLIR's published model, agreeing to floating point at zero
range over a whole frame. The only divergence is how the path length enters the
atmospheric term, worth 0.11 C at 1 m; section 16 records it.

`tools/` is now `packaging/`, which is what it holds, and the icon lives beside
the script that installs it. The bundle version is read from `pyproject.toml`
instead of being hardcoded in the plist, where it had already drifted two
releases behind.

## 0.9.5

Zoom and pan on the image, up to 16x, anchored on the pointer. At fit-to-window
the image is drawn at roughly its own size, so a sub-pixel alignment cannot be
judged by eye; this is what makes that possible. Panning is a left-drag in
Cursor mode, so no middle button is needed.

The cursor warns when the target under it is too small to measure. A radiometric
reading is only trustworthy when the target fills several detector elements;
below three the pixel averages target and surroundings and reads low, which is
the commonest way to get a confidently wrong temperature. Sizes are judged on
the detector grid rather than on the file, which arrives upscaled 4x and would
otherwise flatter every target by a factor of four. The threshold comes from the
published optics, 160x120 over 46 by 35 degrees, so 5.05 mrad per pixel. The
check needs no calibration.

Blend and the three alignment values are sliders with their value beside them.
On the two alignment offsets a click beside the handle nudges a single pixel
rather than jumping to where it landed, since a stray click should not discard a
carefully found alignment. Their range narrows from plus or minus 400 to 150:
measured offsets sit within about 25, and the old range put nearly three pixels
under every pixel of slider.

**Removed: distance from parallax.** It shipped in 0.9.0 and has been taken out
rather than finished. It barely affected temperature at the ranges this camera
works at, shifting an 80 C reading by 0.14 C between an assumed 0.3 m and 1 m,
and its other use, turning pixels into millimetres, is served better by the
distance already set in Conditions, which is measured rather than estimated to
plus or minus 14%. Establishing it also needed a per-camera-model constant most
users cannot determine. This removes `flirone.distance`, the
`calibrate_distance` tool and the Setup panel that drove them.

That also settles the 0.9.1 item below: there is no distance uncertainty left to
measure. The work was not wasted, though. It produced the measured field of
view behind the spot-size threshold, and it caught the parallax axis being
horizontal rather than vertical, which the alignment documentation had wrong.

## 0.9.0

First tagged release.

Radiometric images shot with the FLIR One phone app load with the camera's own
Planck constants and the conditions of the shot, so readings are absolute rather
than relative. Spot, box and line measurements, isotherms, auto-tracked
extremes, emissivity and atmospheric correction, alignment recovered from image
content, distance from parallax, captures, and CSV line-profile export carrying
the calibration that produced it.

Runs on macOS and Linux. On Linux the camera needs `usbcore.old_scheme_first=1`
and a udev rule before it will enumerate; both are in the README.

Not in this release:

- **Live streaming from the Lightning unit.** The iAP2 handshake completes and
  the camera authenticates, but opening the video session kills the link. The
  camera accepts the write, answers nothing, and leaves the bus about 2ms later.
  Replaying the stack on Linux reproduces it exactly, so the fault is
  camera-side. Sections 12 and 13 of `docs/hardware-findings.md`.
- The Android/USB-C variant should work as written, but is untested for want of
  the hardware.
- Distance uncertainty is the model evaluated at an assumed 0.3 px registration
  noise, not a measured one. Measuring it against this camera is the 0.9.1 job.
