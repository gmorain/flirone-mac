# Changelog

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
