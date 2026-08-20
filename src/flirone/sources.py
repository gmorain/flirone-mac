"""Frame sources.

The UI talks to a FrameSource and never to libusb, so the camera, a recorded
capture and a synthetic scene are interchangeable. That matters here: the
hardware only streams under conditions we do not fully control, and the rest of
the application has to be developed and tested regardless.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Protocol

import numpy as np

from . import protocol as proto
from .decode import DecodedFrame, decode
from .usb_link import FlirOneLink, FlirUsbError, StartMode

log = logging.getLogger(__name__)


class FrameSource(Protocol):
    name: str

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def latest(self, timeout: float = 0.5) -> DecodedFrame | None: ...


class _ThreadedSource:
    """Common plumbing: a producer thread and a one-slot mailbox."""

    name = "source"

    def __init__(self) -> None:
        self._queue: queue.Queue[DecodedFrame] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.status: str = "idle"
        self.error: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _publish(self, frame: DecodedFrame) -> None:
        # Keep only the newest frame; a stalled UI must not build up latency.
        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            pass

    def latest(self, timeout: float = 0.5) -> DecodedFrame | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class UsbFrameSource(_ThreadedSource):
    """Live camera.

    An idle FLIR One watchdog-resets every few seconds, so this reconnects in a
    loop rather than failing once: it waits for enumeration, runs the handshake
    immediately, streams until the device goes away, then repeats.
    """

    name = "usb"

    def __init__(self, start_mode: StartMode = StartMode.ALT_SETTING) -> None:
        super().__init__()
        self.start_mode = start_mode
        self.frames_seen = 0
        self.reconnects = 0

    def _run(self) -> None:
        import usb.core

        assembler = proto.FrameAssembler()
        while not self._stop.is_set():
            link = FlirOneLink(start_mode=self.start_mode)
            try:
                self.status = "waiting for camera"
                link.open(timeout_s=10.0)
                link.start_stream()
                assembler.reset()
                self.status = "streaming"
                self.error = None
                while not self._stop.is_set():
                    chunk = link.read_frame_chunk(timeout_ms=100)
                    link.drain()
                    if not chunk:
                        continue
                    for raw in assembler.feed(chunk):
                        try:
                            self._publish(decode(raw))
                            self.frames_seen += 1
                        except ValueError as exc:
                            log.debug("undecodable frame: %s", exc)
            except FlirUsbError as exc:
                self.status = "no camera"
                self.error = str(exc)
            except usb.core.USBError as exc:
                self.status = "disconnected"
                self.error = str(exc)
                self.reconnects += 1
            finally:
                try:
                    link.close()
                except Exception:
                    pass
            if not self._stop.is_set():
                time.sleep(0.2)


class ReplayFrameSource(_ThreadedSource):
    """Replay a directory of captures saved by export.save_capture."""

    name = "replay"

    def __init__(self, directory: Path, fps: float = 8.7, loop: bool = True) -> None:
        super().__init__()
        self.directory = Path(directory)
        self.fps = fps
        self.loop = loop
        self._files = sorted(self.directory.glob("*.npz"))

    def _run(self) -> None:
        if not self._files:
            self.status = "no captures"
            self.error = f"no .npz files in {self.directory}"
            return
        self.status = "replaying"
        period = 1.0 / max(self.fps, 0.1)
        while not self._stop.is_set():
            for path in self._files:
                if self._stop.is_set():
                    break
                with np.load(path, allow_pickle=False) as data:
                    visible = data["visible"] if "visible" in data else None
                    self._publish(DecodedFrame(raw=data["raw"], visible=visible, status={}))
                time.sleep(period)
            if not self.loop:
                break


class SyntheticFrameSource(_ThreadedSource):
    """A physically plausible fake scene, for building the UI without hardware.

    Produces raw counts in the same range the sensor emits, so the calibration
    and measurement paths are exercised exactly as they are on real data.
    """

    name = "synthetic"

    def __init__(self, width: int = 160, height: int = 120, fps: float = 8.7) -> None:
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps

    def _scene(self, t: float) -> np.ndarray:
        h, w = self.height, self.width
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        # Ambient gradient, a warm wall and a drifting hot source.
        celsius = 19.0 + 3.0 * (yy / h)
        celsius += 6.0 * np.exp(-(((xx - w * 0.28) / 26) ** 2 + ((yy - h * 0.62) / 20) ** 2))
        cx = w * (0.5 + 0.22 * np.sin(t * 0.7))
        cy = h * (0.42 + 0.14 * np.cos(t * 0.5))
        celsius += 62.0 * np.exp(-(((xx - cx) / 7.5) ** 2 + ((yy - cy) / 7.5) ** 2))
        celsius += np.random.default_rng(int(t * 1000) % 2**31).normal(0, 0.09, (h, w))

        from .calibration import DEFAULT_PLANCK, temperature_to_raw

        # Invert the calibration so the synthetic frame carries real counts.
        lut_c = np.linspace(-30.0, 260.0, 1600)
        lut_raw = np.array([temperature_to_raw(c, DEFAULT_PLANCK) for c in lut_c])
        raw = np.interp(celsius, lut_c, lut_raw)
        return np.clip(raw, 0, 65535).astype(np.uint16)

    def _visible(self, t: float) -> np.ndarray:
        h, w = self.height * 4, self.width * 4
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[..., 0] = (60 + 40 * np.sin(xx / 60 + t)).clip(0, 255)
        img[..., 1] = (70 + 30 * np.cos(yy / 55)).clip(0, 255)
        img[..., 2] = 90
        return img

    def _run(self) -> None:
        self.status = "synthetic"
        period = 1.0 / max(self.fps, 0.1)
        t0 = time.monotonic()
        while not self._stop.is_set():
            t = time.monotonic() - t0
            self._publish(
                DecodedFrame(
                    raw=self._scene(t), visible=self._visible(t), status={"synthetic": True}
                )
            )
            time.sleep(period)


class StillFrameSource(_ThreadedSource):
    """Republishes a single frame, so a still behaves like any other source."""

    name = "still"

    def __init__(self, frame: DecodedFrame, label: str = "still") -> None:
        super().__init__()
        self.frame = frame
        self.label = label

    def _run(self) -> None:
        self.status = self.label
        while not self._stop.is_set():
            self._publish(self.frame)
            time.sleep(0.25)
