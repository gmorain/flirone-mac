"""Locating external command-line tools.

An app bundle launched from Finder inherits a minimal PATH that does not
include Homebrew, so tools that resolve fine in a terminal are invisible to the
GUI. Everything here therefore resolves absolute paths rather than trusting the
environment.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

_SEARCH_PATHS = (
    "/opt/homebrew/bin",  # Homebrew, Apple silicon
    "/usr/local/bin",  # Homebrew, Intel
    "/opt/local/bin",  # MacPorts
    "/usr/bin",
)


class ToolNotFound(RuntimeError):
    pass


@lru_cache(maxsize=8)
def find_tool(name: str) -> str | None:
    """Absolute path to an external tool, or None if it is not installed."""
    found = shutil.which(name)
    if found:
        return found
    for directory in _SEARCH_PATHS:
        candidate = Path(directory) / name
        if candidate.is_file():
            return str(candidate)
    return None


def require_tool(name: str, install_hint: str) -> str:
    path = find_tool(name)
    if path is None:
        raise ToolNotFound(f"{name} not found. Install it with: {install_hint}")
    return path


def exiftool(args: list[str], binary: bool = False):
    """Run exiftool and return its stdout."""
    executable = require_tool("exiftool", "brew install exiftool")
    proc = subprocess.run([executable, *args], capture_output=True, check=False)
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(message or "exiftool failed")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")
