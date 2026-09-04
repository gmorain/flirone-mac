"""Record a timelapse from a terminal.

A wrapper. Beyond parsing arguments and printing lines it calls
`timelapse.record`, the same loop the window drives, so the two cannot disagree
about what a run is.

Nothing here imports Qt, which is what makes recording possible over ssh and on
a machine with no display. A test asserts it.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path
from typing import TextIO

from .calibration import DEFAULT_PLANCK, Conditions
from .calibration_store import CalibrationStore, adopt_from_image
from .paths import short as short_path
from .sources import UsbFrameSource
from .timelapse import (
    Recorder,
    Settings,
    Status,
    TimelapseError,
    calibration_for,
    record,
    wait_for_frame,
)

BRING_UP_TIMEOUT_S = 90.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flirone-timelapse",
        description="Record a FLIR One to radiometric JPEGs, one per interval.",
    )
    parser.add_argument(
        "folder", type=Path, nargs="?", default=None, help="where to write the files"
    )
    parser.add_argument("--every", type=float, default=1.0, metavar="S", help="seconds per frame")
    parser.add_argument(
        "--frames", type=int, default=0, metavar="N", help="stop after N files, 0 for no limit"
    )
    parser.add_argument(
        "--minutes", type=float, default=0.0, metavar="M", help="stop after M minutes, 0 for none"
    )
    parser.add_argument(
        "--calibrate",
        type=Path,
        default=None,
        metavar="IMAGE",
        help="file this camera's constants from a radiometric JPEG, then record",
    )

    defaults = Conditions()
    group = parser.add_argument_group("measurement conditions, recorded in every file")
    group.add_argument("--emissivity", type=float, default=defaults.emissivity, metavar="E")
    group.add_argument(
        "--reflected", type=float, default=defaults.reflected_c, metavar="C", help="apparent, C"
    )
    group.add_argument("--air", type=float, default=defaults.atmospheric_c, metavar="C")
    group.add_argument(
        "--humidity", type=float, default=defaults.humidity, metavar="H", help="0 to 1"
    )
    group.add_argument("--distance", type=float, default=defaults.distance_m, metavar="M")
    return parser


def conditions_from(args: argparse.Namespace, out: TextIO | None = None) -> Conditions:
    """The conditions to record, reporting any the library clamped.

    `Conditions.validated` clamps rather than rejects, which is right behind a
    slider and silent behind a typed argument: a mistyped emissivity would be
    written into every file as though it had been chosen.
    """
    asked = Conditions(
        emissivity=args.emissivity,
        reflected_c=args.reflected,
        atmospheric_c=args.air,
        humidity=args.humidity,
        distance_m=args.distance,
    )
    # Resolved here rather than in the signature: a default argument binds at
    # import, so a caller that replaces sys.stderr later would be ignored.
    out = sys.stderr if out is None else out
    used = asked.validated()
    for field, flag in (
        ("emissivity", "--emissivity"),
        ("humidity", "--humidity"),
        ("distance_m", "--distance"),
    ):
        before, after = getattr(asked, field), getattr(used, field)
        if before != after:
            print(f"{flag} {before:g} is out of range, recording {after:g}", file=out)
    return used


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    store = CalibrationStore()
    if args.calibrate is None and args.folder is None:
        parser.error("a folder is required unless --calibrate is given")

    if args.calibrate is not None and args.folder is None:
        # No camera session to check against here. Filing anyway is safe, since
        # the serial is compared again at record time and constants only count
        # as this camera's when it matches. Saying so is the point.
        try:
            planck = adopt_from_image(args.calibrate, store)
        except Exception as exc:
            print(f"flirone-timelapse: {exc}", file=sys.stderr)
            return 1
        print(f"Filed constants for {planck.serial} from {args.calibrate.name}")
        print("Not checked against a camera. That happens when you record.")
        return 0

    try:
        settings = Settings(
            folder=args.folder,
            interval_s=args.every,
            # Zero is no limit, so a run can be left going without naming a
            # number nobody knows in advance.
            max_frames=args.frames or None,
            max_seconds=args.minutes * 60.0 or None,
        ).validated()
    except TimelapseError as exc:
        print(f"flirone-timelapse: {exc}", file=sys.stderr)
        return 2

    conditions = conditions_from(args)
    source = UsbFrameSource()

    print("Bringing the camera up.", flush=True)
    source.start()
    try:
        # After a frame, not before. The serial arrives with the accessory
        # handshake, which is seconds after start, so asking immediately gets
        # None and the run refuses a camera that is simply still coming up.
        first = wait_for_frame(source, BRING_UP_TIMEOUT_S)
        if first is None:
            print(
                f"flirone-timelapse: no frame within {BRING_UP_TIMEOUT_S:.0f} s. "
                "Check the camera's own power button.",
                file=sys.stderr,
            )
            return 1

        if args.calibrate is not None:
            # Now there is a camera to check against, so a file from a
            # different unit fails here rather than at record time.
            try:
                planck = adopt_from_image(args.calibrate, store, source.serial)
            except Exception as exc:
                print(f"flirone-timelapse: {exc}", file=sys.stderr)
                return 1
            print(f"Filed constants for {planck.serial} from {args.calibrate.name}")

        camera = calibration_for(source, store)
        recorder = Recorder(
            settings, camera.planck or DEFAULT_PLANCK, conditions, camera.template or b""
        )
        try:
            recorder.check(camera.serial)
        except TimelapseError as exc:
            print(f"flirone-timelapse: {exc}", file=sys.stderr)
            return 1

        stopping = threading.Event()

        def interrupt(_signum, _frame):
            # Ctrl-C ends the run rather than the process, so the summary is
            # printed and the camera is closed on the way out.
            stopping.set()

        previous = signal.signal(signal.SIGINT, interrupt)
        try:
            print(f"Recording {camera.serial} to {short_path(settings.folder)}", flush=True)
            status = record(
                source,
                settings,
                recorder.planck,
                conditions,
                recorder.template,
                camera_serial=camera.serial,
                first_frame_timeout_s=BRING_UP_TIMEOUT_S,
                on_change=_progress,
                stop=stopping,
                first_frame=first,
            )
        except TimelapseError as exc:
            print(f"flirone-timelapse: {exc}", file=sys.stderr)
            return 1
        finally:
            signal.signal(signal.SIGINT, previous)

        _summarise(status)
        return 1 if status.error else 0
    finally:
        source.stop()


def _progress(status: Status) -> None:
    line = f"  {status.written} written"
    if status.skipped:
        line += f", {status.skipped} skipped"
    if status.late:
        line += f", {status.late} late"
    print(f"{line}, {status.elapsed_s:.0f} s", flush=True)


def _summarise(status: Status) -> None:
    print(
        f"{status.written} written, {status.skipped} skipped, "
        f"{status.late} late in {status.elapsed_s:.0f} s"
    )
    print(f"in {short_path(status.folder)}")
    if status.worst_late_s:
        print(f"worst tick was {status.worst_late_s:.1f} s behind")
    if status.error:
        print(f"stopped: {status.error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
