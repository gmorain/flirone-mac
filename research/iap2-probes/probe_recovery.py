"""Watch a live session across a cable pull, and time the recovery.

Watches from the first instant rather than from the moment streaming begins.
The earlier version opened its window only once the camera was up, and the first
real attempt pulled the cable during bring-up: the drop was absorbed into a
57.8 s start, the window saw nothing, and the run reported "no drop seen" while
the USB device number showed the replug had happened. A probe that can miss the
event it exists to catch is worse than no probe.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from flirone.sources import UsbFrameSource  # noqa: E402

WATCH_SECONDS = 300.0


def main() -> int:
    source = UsbFrameSource()
    source.start()
    started = time.monotonic()

    print(f"Watching for {WATCH_SECONDS:.0f} s from now.")
    print("Walk over, wait until you have counted 90 s from the start,")
    print("then pull the cable, wait 5 s, and plug it back in.\n", flush=True)

    # (status, reconnects) at each change, with the time it changed.
    marks: list[tuple[float, str, int, int, str | None]] = []
    last = None

    while time.monotonic() - started < WATCH_SECONDS:
        source.latest(timeout=0.5)
        now = (source.status, source.reconnects)
        if now != last:
            t = time.monotonic() - started
            marks.append((t, source.status, source.reconnects, source.frames_seen, source.error))
            line = f"  t+{t:6.1f}s  {source.status:<18} reconnects={source.reconnects} frames={source.frames_seen}"
            if source.error:
                line += f"  [{source.error}]"
            print(line, flush=True)
            last = now

    source.stop()
    print()

    streams = [m for m in marks if m[1] == "streaming"]
    if not streams:
        print("NEVER STREAMED. Nothing to say about recovery.")
        return 1

    first_stream = streams[0][0]
    # A drop is any non-streaming mark after the first time it streamed.
    drop = next((m for m in marks if m[0] > first_stream and m[1] != "streaming"), None)
    if drop is None:
        print(f"NO DROP SEEN after streaming began at t+{first_stream:.1f}s.")
        print("The cable was never pulled inside the window; this run says")
        print("nothing about recovery.")
        return 2

    back = next((m for m in streams if m[0] > drop[0]), None)
    if back is None:
        print(f"DROPPED at t+{drop[0]:.1f}s AND DID NOT COME BACK.")
        print(f"final status: {source.status}  {source.error or ''}")
        return 1

    print(f"streaming from t+{first_stream:.1f}s")
    print(f"DROPPED at t+{drop[0]:.1f}s  [{drop[4] or drop[1]}]")
    print(f"STREAMING AGAIN at t+{back[0]:.1f}s")
    print(f"recovered in {back[0] - drop[0]:.1f}s, unattended")
    print(f"reconnects={source.reconnects}  frames={source.frames_seen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
