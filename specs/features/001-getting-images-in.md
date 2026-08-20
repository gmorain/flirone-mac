# 001 — Getting images in

Status:  in-progress

---

## User

Images reach the application two ways, and everything downstream works the same
regardless of which.

A user opens a picture shot with the FLIR One phone app and gets everything the
camera recorded: the full-resolution thermal field, the visible photograph, the
camera's own calibration, and the conditions of the shot, pre-filled and still
adjustable. Images arrive by whatever route suits — dropped on the window,
dropped on the application icon, opened from the dialog, or named on the command
line. A saved calibration or a folder of past captures can be dropped the same
way, and dropping a calibration alongside an image applies it first so the image
renders correctly the first time. After an import the user sees a summary of
what was read from the file against what was measured from it, so the two can be
compared rather than silently merged.

Alternatively a camera is attached and streams live, with every tool working
against the moving image exactly as against a still. A FLIR One does not simply
appear as a webcam: Lightning models expect the host to identify itself the way
an iPhone would, and reset every few seconds when nothing answers, so a user
plugging one into a Mac sees it appear and vanish repeatedly. The application
answers, so the camera settles, stays connected, and reports its model, serial
and firmware. That serial is what allows a borrowed calibration to be checked
against the camera actually in use.

With no camera and no file, a synthetic scene keeps every tool usable, so the
application is never inert for want of hardware.

## Tech

FLIR's APP1 metadata is parsed by shelling out to exiftool, the only reliable
reader for that layout. The raw plane is a 16-bit PNG whose byte order is
ambiguous in practice, resolved by physical plausibility rather than assumption;
see decision 002.

Over USB the camera exposes vendor-specific interfaces in a non-default
configuration. Frames are bulk chunks reassembled on a magic marker; the thermal
plane is row-padded and split per row, undone by a vectorised deinterleave.
Streaming is gated by a SET_INTERFACE request that must go through the normal
API, not a raw control transfer; see decision 004. Read buffers match the
negotiated maximum packet, since an undersized bulk read overflows and reports
an I/O error indistinguishable from a disconnect.

Lightning models first require Apple's iAP2 accessory handshake. Its framing was
recovered from the wire and is validated in the tests against a captured packet.
Authentication runs in the direction that makes this feasible: in MFi the
accessory proves itself to the device, so this side issues a challenge and
inspects the answer, never producing Apple-signed material.

Dropped files are treated as untrusted: only images, calibrations and
directories are accepted, and Pillow's decode size is bounded so a crafted or
corrupt image cannot exhaust memory.

**Unverified — no hardware.** The vendor protocol path has never been exercised
against a camera that reaches it, because the only camera available is a
Lightning model, blocked below.

**Known gap.** On Lightning models, requesting the video session resets the
camera deterministically. Every host-side explanation has been ruled out by
experiment and the hardware is proven healthy, so the difference lies inside
Apple's iAP2 implementation, whose specification is under NDA. Settling it needs
an analyser capture of a working session. The full evidence, including what was
excluded and how, is in `docs/hardware-findings.md`; the scripts are in
`research/iap2-probes/`.

## Tasks

- [x] Read calibration, conditions and the raw thermal plane from radiometric JPEGs
- [x] Resolve raw byte order by plausibility
- [x] Drag and drop, Dock, dialog and command-line entry points
- [x] Import summary card comparing recorded against measured
- [x] USB discovery, configuration, and streaming start that works on macOS
- [x] Frame reassembly with resynchronisation, deinterleave and JPEG decode
- [x] iAP2 handshake through authentication, yielding the camera serial
- [x] Interchangeable sources: camera, file, replay folder, synthetic
- [x] Bounded decode of untrusted files
- [ ] External Accessory session for video — blocked, see above
