# Changelog

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
