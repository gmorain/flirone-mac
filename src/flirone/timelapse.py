"""Record a streaming camera to disk, one radiometric JPEG per interval.

The timer and the writing live here rather than in the interface, so a script
can record without one.

Frames arrive by `offer` rather than being pulled from the source. A source
hands out each frame once, so a recorder polling it would take frames the
interface never sees, and the preview would stutter in proportion to the
recording interval. `record` polls for callers that have no interface.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .alignment import Alignment
from .calibration import Conditions, Planck, Trust, serials_match
from .decode import DecodedFrame
from .flirjpeg import save_radiometric
from .sources import FrameSource

# Six digits is eleven days at one per second, and the numbering is per tick, so
# it has to outlast the run rather than the file count.
_SEQUENCE_DIGITS = 6
_MIN_INTERVAL_S = 0.05


class TimelapseError(RuntimeError):
    """Raised when a run cannot start, or cannot continue."""


@dataclass(frozen=True)
class CameraCalibration:
    """What a camera needs before it can be recorded: constants and a container.

    Both come from a radiometric image that camera produced, and both are found
    by the serial it reports. Resolved here so neither front end has to know
    that a template is filed the same way constants are.
    """

    serial: str | None
    planck: Planck | None
    template: bytes | None

    @property
    def ready(self) -> bool:
        return bool(self.serial and self.planck and self.template)


def wait_for_frame(source, timeout_s: float = 60.0) -> DecodedFrame | None:
    """Block until the camera produces a frame, or the wait runs out.

    A frame is the signal, not a completed handshake: a FLIR One whose imager
    is unpowered finishes the handshake and reports its serial while sending
    nothing. Section 17 of docs/hardware-findings.md.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        frame = source.latest(timeout=0.2)
        if frame is not None:
            return frame
    return None


def calibration_for(source, store) -> CameraCalibration:
    """Constants and container template for whatever camera this source has."""
    serial = getattr(source, "serial", None)
    if not serial:
        return CameraCalibration(None, None, None)
    return CameraCalibration(serial, store.find(serial), store.template(serial))


@dataclass(frozen=True)
class Settings:
    """What to record, how often, and when to stop."""

    folder: Path
    interval_s: float = 1.0
    max_frames: int | None = None
    max_seconds: float | None = None

    def validated(self) -> Settings:
        if self.interval_s < _MIN_INTERVAL_S:
            raise TimelapseError(f"interval must be at least {_MIN_INTERVAL_S} s")
        if self.max_frames is not None and self.max_frames < 1:
            raise TimelapseError("frame limit must be at least one")
        if self.max_seconds is not None and self.max_seconds <= 0:
            raise TimelapseError("time limit must be positive")
        return self


@dataclass
class Status:
    """What a run has done so far."""

    running: bool = False
    ticks: int = 0
    written: int = 0
    skipped: int = 0
    error: str | None = None
    folder: Path | None = None
    started_at: float | None = None
    late: int = 0  # ticks that finished after the next one was already due
    worst_late_s: float = 0.0
    stopped_at: float | None = None

    @property
    def elapsed_s(self) -> float:
        """How long the run lasted, frozen once it ends."""
        if self.started_at is None:
            return 0.0
        end = self.stopped_at if self.stopped_at is not None else time.monotonic()
        return end - self.started_at


@dataclass
class Recorder:
    """Writes one radiometric JPEG per tick from whatever was last offered."""

    settings: Settings
    planck: Planck
    conditions: Conditions
    template: bytes
    alignment: Alignment | None = None
    status: Status = field(default_factory=Status)

    _pending: DecodedFrame | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    # -- lifecycle ----------------------------------------------------------

    def check(self, camera_serial: str | None = None) -> None:
        """Raise if a run would be untrustworthy, without starting one.

        Separate from `start` so a caller can prime the recorder with a frame
        first: the first tick fires immediately, and a caller that started
        before offering would record a gap that is supposed to mean the camera
        stalled.
        """
        if self.status.running:
            raise TimelapseError("already recording")
        self.settings.validated()
        self._check_can_write(self.settings.folder)
        self._check_calibration(camera_serial)
        if not self.template:
            raise TimelapseError(
                "no container template for this camera. Open a radiometric image "
                "from it once, so written files can be built like the ones it makes."
            )

    def start(self, camera_serial: str | None = None) -> None:
        """Begin recording. Raises rather than starting a run that cannot be trusted."""
        self.check(camera_serial)
        settings = self.settings

        self.status = Status(running=True, folder=settings.folder, started_at=time.monotonic())
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="timelapse", daemon=True)
        self._thread.start()

    def stop(self) -> Status:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5.0)
        if self.status.stopped_at is None:
            self.status.stopped_at = time.monotonic()
        self.status.running = False
        return self.status

    def offer(self, frame: DecodedFrame | None) -> None:
        """Hand the recorder the newest frame. The last one before a tick wins."""
        if frame is None:
            return
        with self._lock:
            self._pending = frame

    # -- refusals -----------------------------------------------------------

    def _check_can_write(self, folder: Path) -> None:
        if folder is None:
            raise TimelapseError("choose a folder before recording")
        folder = Path(folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TimelapseError(f"cannot use that folder: {exc}") from exc
        if not folder.is_dir():
            raise TimelapseError(f"{folder} is not a folder")

    def _check_calibration(self, camera_serial: str | None) -> None:
        """Refuse a run whose files would claim temperatures they cannot support."""
        if self.planck.trust is not Trust.CAMERA:
            raise TimelapseError(
                "these constants did not come from this camera, so the files would "
                "record temperatures that are wrong by degrees. Load the camera's "
                "own calibration first."
            )
        if camera_serial and not serials_match(self.planck.serial, camera_serial):
            raise TimelapseError(
                f"the calibration belongs to camera {self.planck.serial}, and the "
                f"camera streaming is {camera_serial}."
            )

    # -- the run ------------------------------------------------------------

    def _path_for(self, tick: int, folder: Path) -> Path:
        """The output path for a tick, refused if it escapes the chosen folder."""
        name = f"flirone-{tick:0{_SEQUENCE_DIGITS}d}.jpg"
        root = folder.resolve()
        candidate = (root / name).resolve()
        if candidate.parent != root:
            raise TimelapseError(f"refusing to write outside {root}")
        return candidate

    def _take_pending(self) -> DecodedFrame | None:
        with self._lock:
            frame, self._pending = self._pending, None
        return frame

    def _limits_reached(self) -> bool:
        settings = self.settings
        if settings.max_frames is not None and self.status.written >= settings.max_frames:
            return True
        if settings.max_seconds is not None and self.status.elapsed_s >= settings.max_seconds:
            return True
        return False

    def _run(self) -> None:
        settings = self.settings
        folder = Path(settings.folder)
        started = time.monotonic()
        tick = 0

        while not self._stop.is_set():
            # Sleep to the tick boundary rather than by the interval, so a slow
            # write does not make every later tick late.
            target = started + tick * settings.interval_s
            delay = target - time.monotonic()
            if delay > 0 and self._stop.wait(delay):
                break

            tick += 1
            self.status.ticks = tick
            frame = self._take_pending()
            if frame is None:
                # The number is spent. A gap in the filenames is how a stalled
                # camera shows up in a directory listing.
                self.status.skipped += 1
            else:
                try:
                    save_radiometric(
                        self._path_for(tick, folder),
                        frame,
                        self.planck,
                        self.conditions,
                        self.template,
                        self.alignment,
                    )
                except Exception as exc:
                    self.status.error = str(exc)
                    break
                self.status.written += 1

            # A write slower than the interval makes the run drift. Ticks are
            # timed from the start so the drift does not compound, but it does
            # mean frames are further apart than asked, and saying nothing
            # would leave the user with a sequence that is quietly not the
            # interval on the panel.
            behind = time.monotonic() - (started + tick * settings.interval_s)
            if behind > settings.interval_s:
                self.status.late += 1
                self.status.worst_late_s = max(self.status.worst_late_s, behind)

            if self._limits_reached():
                break

        self.status.stopped_at = time.monotonic()
        self.status.running = False


def record(
    source: FrameSource,
    settings: Settings,
    planck: Planck,
    conditions: Conditions,
    template: bytes,
    camera_serial: str | None = None,
    first_frame_timeout_s: float = 60.0,
    alignment: Alignment | None = None,
    on_change: Callable[[Status], None] | None = None,
    stop: threading.Event | None = None,
    first_frame: DecodedFrame | None = None,
) -> Status:
    """Record from a source until its limits are reached. For scripts.

    `on_change` is called whenever the counters move, and `stop` ends the run
    early, which is how a terminal reports progress and handles an interrupt
    without writing a second copy of this loop.
    """
    recorder = Recorder(settings, planck, conditions, template, alignment)

    # Wait for a frame before starting the clock. Otherwise the first tick
    # lands before the camera has produced anything and is recorded as a gap,
    # which is supposed to mean the camera stalled.
    first = first_frame or wait_for_frame(source, first_frame_timeout_s)
    if first is None:
        raise TimelapseError(f"no frame from the camera within {first_frame_timeout_s:.0f} s")

    # Offered before the clock starts: the first tick fires immediately, and
    # would otherwise beat this frame to the queue and record a gap.
    recorder.offer(first)
    recorder.start(camera_serial)
    last = None
    try:
        while recorder.status.running and not (stop is not None and stop.is_set()):
            recorder.offer(source.latest(timeout=0.2))
            counters = (recorder.status.written, recorder.status.skipped, recorder.status.late)
            if on_change is not None and counters != last:
                on_change(recorder.status)
                last = counters
    finally:
        recorder.stop()
    return recorder.status
