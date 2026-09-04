# 005 — Timelapse capture

Status:  shipped

Depends: 003-calibrating-to-a-camera

---

## User

A camera streaming over USB can record itself to disk unattended, from the
window or from a terminal. The user picks a folder, picks how often to save, and
starts. One image is written per tick until they stop, or until an optional
limit is reached: a number of frames, a length of time, or both. The default is
one per second and no limit.

    flirone-timelapse ~/Pictures/run --every 2 --frames 120 --emissivity 0.92

The measurement conditions are the same five the window offers and are set the
same way from a terminal, defaulting to what the window starts with. They are
recorded in every file and can be corrected afterwards.

A run reports the count written, the count skipped, and where they are going,
and says so when its writes cannot keep up with its interval. It ends on the
limit given, or on Ctrl-C, which still prints the summary.

Each image is a radiometric JPEG carrying the thermal plane, the camera's
calibration constants, the conditions in force and the visible photograph. Files
open here with every measurement tool available, and in FLIR's own software, so
a run can be reprocessed later under corrected emissivity or distance.

Recording will not start when the calibration in use did not come from the
camera being recorded. It also needs one radiometric image from that same
camera to have been opened at least once, since the files it writes are built
from the structure of that one.

Files are numbered by tick. A tick with no new frame writes nothing and takes
its number with it, so a stall shows as a hole in the numbering, visible in any
file listing.

## Tech

- **Ownership.** The interval timer and the writing live in the library. Both
  front ends select a folder and an interval, start, stop and show the counters.
- **Format.** One JPEG per tick: the visible frame as the JPEG payload, FLIR's
  metadata in APP1 segments carrying an FFF record table.
- **Writer.** Patches a template APP1 payload captured from a real file by the
  same camera. It is the inverse of `planck_import.from_flir_segment` and shares
  its offsets. Four records are replaced: the raw plane, the camera info, the
  embedded visible image and the alignment. `0x2b`, `0x34` and `0x37` are copied
  verbatim.
- **Scene records replaced, camera records copied.** The embedded image is what
  a reader shows as the visible frame and the alignment relates it to the
  thermal plane, so a template's are wrong for every other frame. The alignment
  is dropped when none is known, since a reader that finds none measures one.
  Its offsets are int16 in visible pixels, so a sub-pixel one does not survive.
- **Limits.** A frame count, a duration, or neither. The count is of files
  written; the duration is wall clock from the start. Whichever arrives first
  ends the run.
- **Numbering.** Files take the tick index, so a skipped tick leaves a hole.
  Fixed width, so names sort lexically for any plausible run length.
- **Size.** Visible frame at stream size, raw plane at native size: 141 kB for
  640x480 over 160x120. A phone-app file from this camera is 1376 kB, of which
  1106 kB is a 1080x1440 visible JPEG, and it upscales the raw plane 4x to
  480x640 PNG. Compatibility means the same tools open it.
- **Count scale.** Written counts carry the scale they were captured on, or a
  frame reopened later converts as though it came from a stored image.
- **Trust gate.** Starting requires `Trust.CAMERA` against the streaming
  camera's serial.
- **Failure.** A write error stops the run and reports it. A tick finishing
  after the next was due is counted late, with the worst kept, since ticks are
  timed from the start and the drift would otherwise be silent.
- **Verification.** Offline round trip against `IMG_9400` through
  `flirjpeg.load` and ExifTool, asserting the temperature field matches.
- **ExifTool.** The reader shells out to it; the writer cannot, since ExifTool
  does not write FLIR tags.
- **Shared, not duplicated.** The serial comes from the source and the store
  lookup from one library call, so neither front end spells out a handshake
  field name. The layering rule is asserted by a test rather than by review.
- **Command line.** Its own console entry point beside `flirone-probe`, so
  recording imports no Qt. It parses arguments, waits for a frame, reads the
  serial from the handshake the source reports, looks the constants and template
  up in the store, and calls `record`. The wait comes first: the serial arrives
  with the handshake, seconds after the source starts. `--calibrate` files a
  camera's constants and container from a radiometric JPEG, so a machine with
  no window can still be set up. With a folder it is checked against the camera
  streaming; alone there is nothing to check against and it says so. Zero means
  no limit on both limits. Refusals come from `Recorder.check`, printed and
  exited non-zero. Nothing there knows an alignment, so its files record none.
  Ctrl-C stops the run and prints the summary.
- **Conditions from the command line.** One flag per field of `Conditions`,
  defaulting to its own defaults. Values go through `Conditions.validated`,
  which clamps rather than rejects, so a clamped value is reported instead of
  being written silently.

**Security.** Output paths are generated inside the chosen folder, never taken
from file content, and resolved before writing to reject anything outside it.
The folder itself arrives from a terminal argument and is treated as any other
untrusted path: resolved, created if absent, refused if it is not a directory.
The template payload is parsed with bounds checks on every record offset and
length, as any other file read from disk. No network, no secrets, no
authentication surface. PII is whatever the camera is pointed at, which never
leaves the machine.

**Out of this feature.** Video encoding and sequence analysis stay out per
`specs/overview.md`. A radiometric JPEG for a single capture uses the same
writer and is deliberately not specified here. The terminal form records: no
window, no preview, no palette or rendering options.

## Tasks

- [x] Radiometric JPEG writer in the library: raw plane, constants and
      conditions patched into a template APP1 payload, visible frame as the
      JPEG image.
- [x] Keep the APP1 payload of the radiometric JPEG a camera's constants were
      read from, filed under the same serial as the constants.
- [x] Round trip offline against `IMG_9400`: written file reads back through
      `flirjpeg.load` and through ExifTool with the temperature field intact.
- [x] Recorder in the library: interval, optional frame and duration limits,
      tick numbering that leaves holes, counters for written and skipped.
- [x] Refusals at start: no folder, no template for this camera, constants that
      are not this camera's.
- [x] Stop the run and report on a write error.
- [x] Bounds-check every record offset and length in a template before use, and
      resolve generated output paths inside the chosen folder before writing.
- [x] Interface panel: folder, interval, limits, start and stop, live counters.
- [x] Recorder tests against a synthetic source: limits end the run, a missing
      frame leaves a hole in the numbering, a write error stops it.
- [x] Pointer from spec 004, and a README section.

### Amendment 2026-09: Recording from the command line

- [x] `flirone-timelapse` console entry point, importing no Qt.
- [x] Arguments: folder, `--every`, `--frames`, `--minutes`, with a usage error
      rather than a traceback on a bad value.
- [x] A source reports its own camera serial, so no front end reads the
      handshake's field names.
- [x] One library call resolves a live camera's constants and template from the
      store, used by both front ends.
- [x] A flag per measurement condition, defaulting to the library's own, with a
      clamped value reported rather than written silently.
- [x] Refuse through `Recorder.check` with the message and a non-zero exit,
      including a folder that cannot be written.
- [x] Progress printed when it changes, and a closing summary of written,
      skipped, late and where.
- [x] Ctrl-C stops the run, prints the summary and exits without a traceback.
- [x] Tests: argument handling, each refusal's exit code, the interrupt path,
      and that the module imports without Qt.
- [x] A test asserting no module under `flirone/` outside `ui/` imports `ui` or
      PySide6, so the layering rule fails a run rather than a review.
- [x] README section for the command.

## Decisions

- Counts are put on the reference plane before writing, because a file that
  declares FLIR's constants and carries the Lepton's 14-bit counts reads four
  times low in any tool that believes it, ours included.
- Frames reach the recorder by `offer` rather than being pulled from the source,
  because a source hands out each frame once and a second consumer would take
  frames the preview never sees.
- The container template is kept as bytes beside the calibration store rather
  than inside it, because the payload is 276 kB and the store is JSON parsed on
  every lookup.
- A serial that is not alphanumeric is refused rather than sanitised, because it
  becomes a filename and a serial needing repair will not match a camera anyway.
- `record` waits for the first frame before starting the clock, because the
  first tick fires immediately and every run would otherwise open with a gap
  that is supposed to mean the camera stalled.
- `record` grew a progress callback and a stop event rather than the terminal
  keeping its own copy of the offer loop, since two loops would drift.
- Conditions are reported when clamped, because clamping is right behind a
  slider and silent behind a typed argument.
- Copying unparsed records verbatim is right for records that describe the
  camera and wrong for records that describe the scene. The first version
  copied the embedded visible image and the alignment, so every file written
  carried the template's photograph and its geometry.

## Notes

- Issue #4. Reverses the time-lapse exclusion in `specs/overview.md`.
- Run against the camera on macOS from both front ends. The window: 12 files at
  one per second, none skipped, 180 kB each, read back absolute and recognised
  by ExifTool as FLIR files at the native 160x120. The command line: a run whose
  frames open showing the scene they were pointed at, with measurement on them
  correct, which is what settles the embedded-image fix.
- Counts round trip exactly; conditions do not. The container stores them as
  float32, so 20.0 C comes back as 19.9999939 and the temperature error grows
  with distance from the reflected temperature: 0.0001 C on a 23 to 25 C live
  scene, 0.0022 C on a file reaching 73 C. Two orders below anything the camera
  can measure, and pinned by a test.
