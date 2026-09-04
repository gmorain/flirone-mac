"""Paths as they are shown to someone, rather than as they are used.

Separate from the front ends because both show them, and a path printed by the
terminal and a path drawn in the panel should not differ.
"""

from __future__ import annotations

from pathlib import Path


def short(path: str | Path | None) -> str:
    """A path with the home directory as a tilde.

    Anything displaying a path under the home directory carries a user name,
    and those lines are what end up in screenshots, issues and pasted output.
    """
    if path is None:
        return ""
    text = str(path)
    home = str(Path.home())
    if text == home:
        return "~"
    prefix = home.rstrip("/") + "/"
    return "~/" + text[len(prefix) :] if text.startswith(prefix) else text
