"""Shortcut labels follow the host platform instead of naming macOS keys."""

from __future__ import annotations

import sys

from flirone.ui.app import (
    SHORTCUT_CAPTURE,
    SHORTCUT_EXPORT_PROFILE,
    SHORTCUT_OPEN_IMAGE,
    shortcut_label,
)

COMMAND = "⌘"


def test_sequences_are_stored_portably():
    """Ctrl is the portable spelling. Qt maps it to Command on macOS itself."""
    for sequence in (SHORTCUT_CAPTURE, SHORTCUT_EXPORT_PROFILE, SHORTCUT_OPEN_IMAGE):
        assert sequence.startswith("Ctrl+")
        assert COMMAND not in sequence


def test_label_is_rendered_for_this_platform():
    """The Capture button used to read a hardcoded U+2318 on every platform."""
    label = shortcut_label(SHORTCUT_CAPTURE)
    assert label
    if sys.platform == "darwin":
        assert COMMAND in label
    else:
        assert COMMAND not in label
        assert "Ctrl" in label


def test_each_shortcut_renders_distinctly():
    labels = {
        shortcut_label(s) for s in (SHORTCUT_CAPTURE, SHORTCUT_EXPORT_PROFILE, SHORTCUT_OPEN_IMAGE)
    }
    assert len(labels) == 3


def test_a_still_source_is_not_reported_as_a_frame_rate():
    """A still republishes one frame, so a rate read as live was meaningless."""
    from flirone.sources import StillFrameSource, SyntheticFrameSource, UsbFrameSource

    assert StillFrameSource.continuous is False
    assert SyntheticFrameSource.continuous is True
    assert UsbFrameSource.continuous is True
