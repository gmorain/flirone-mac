"""Paths as they are shown.

Both front ends print them, and both used to print the home directory in full.
That line is what ends up in screenshots, issues and pasted terminal output.
"""

from __future__ import annotations

from pathlib import Path

from flirone.paths import short


def test_a_path_under_home_becomes_a_tilde():
    assert short(Path.home() / "Pictures" / "run") == "~/Pictures/run"


def test_the_home_directory_itself_is_a_bare_tilde():
    assert short(Path.home()) == "~"


def test_no_user_name_survives():
    assert str(Path.home()) not in short(Path.home() / "Pictures" / "FLIR One" / "timelapse")


def test_a_path_outside_home_is_left_alone():
    assert short("/var/tmp/run") == "/var/tmp/run"


def test_a_sibling_of_home_is_not_mistaken_for_it():
    """String prefixes match more than they should: /Users/gil vs /Users/gilles."""
    impostor = str(Path.home()) + "-backup/run"
    assert short(impostor) == impostor


def test_nothing_shows_as_nothing():
    assert short(None) == ""
