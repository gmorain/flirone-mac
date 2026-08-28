# 001 — Getting images in

Status:  shipped

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

Streaming from a Lightning model is started the way the iOS app starts it,
which an analyser capture of a working session settled: authenticate first and
identify second, open one accessory session for configuration only, read the
camera's file listing and empty the pipe it arrives on, then ask for the stream
by command. Frames are then ordinary bulk transfers. Opening an accessory
session for video or file access is what earlier attempts did and no real host
does; the camera answers that by leaving the bus.

Completing the handshake stops the camera's watchdog permanently, so a camera
that has streamed never re-announces itself and must be reset before it will
talk again. Whether a camera is usable is therefore decided by claiming it, not
by watching the bus: only one process can hold it, and a camera held elsewhere
looks identical to a free one.

**Unverified — no hardware.** The vendor protocol path has never been exercised
against a camera that reaches it, because the only camera available is a
Lightning model, which does not use it.

Live counts are not on the plane the constants assume. The stream is the
Lepton's 14-bit output where that plane is 16-bit, so counts are four times
lower. A frame records which plane it is on, and a capture records it too, so
that a live capture reopened later is not converted as though it came from a
stored image. The factor is a constant rather than a per-camera setting: a bit
depth belongs to the product, not to a serial number, and nothing needs
calibrating before a camera reads absolute.

The full evidence, including what was excluded and how, is in
`docs/hardware-findings.md`; the scripts are in `research/iap2-probes/`.

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
- [x] Accessory session and command sequence that make a Lightning unit stream
- [x] Recovery from a camera whose handshake left it without a watchdog
- [x] Live counts converted to absolute temperature
- [x] The count scale carried on the frame and into the capture
- [ ] The Lightning bring-up exercised on Linux, which is 1.0.0
