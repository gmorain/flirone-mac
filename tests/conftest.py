"""Make the Qt tests run without a display.

They passed on macOS, where Qt always has a window server to talk to, and
aborted the whole session on a headless Linux box: not a failure pytest can
report, a SIGABRT partway through the run. Tests are supposed to run anywhere,
so the platform is chosen here rather than left to whoever remembers to export
it.
"""

from __future__ import annotations

import os
import sys

if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
