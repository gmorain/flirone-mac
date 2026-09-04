"""The library must not depend on the interface.

AGENTS.md has said so since the project started and nothing checked it. The
rule exists so every capability works from a script, a notebook or a terminal,
and the timelapse command line is the first thing that would break silently if
a Qt import crept into the recording path.
"""

from __future__ import annotations

import ast
from pathlib import Path

LIBRARY = Path(__file__).resolve().parents[1] / "src" / "flirone"
FORBIDDEN = ("PySide6", "flirone.ui")


def library_modules():
    for path in sorted(LIBRARY.rglob("*.py")):
        if "ui" not in path.relative_to(LIBRARY).parts:
            yield path


def imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative: ".ui" is level 1, "..ui" level 2
                names.add("." * node.level + (node.module or ""))
            elif node.module:
                names.add(node.module)
    return names


def violations(path: Path) -> list[str]:
    """Interface imports in one module. The check and its self-check share it."""
    return [
        name
        for name in imported_names(path)
        if any(name.startswith(bad) for bad in FORBIDDEN) or name.lstrip(".") == "ui"
    ]


def test_no_library_module_imports_the_interface():
    offenders = [
        f"{path.relative_to(LIBRARY)} imports {name}"
        for path in library_modules()
        for name in violations(path)
    ]
    assert not offenders, "library depends on the interface: " + "; ".join(offenders)


def test_the_check_would_notice_a_violation():
    """A test that cannot fail is worse than no test.

    The first version of this asserted a condition the real check does not use,
    so it passed while the real check was matching nothing.
    """
    assert violations(LIBRARY / "ui" / "app.py")


def test_recording_from_a_terminal_pulls_in_no_qt():
    import subprocess
    import sys

    code = "import sys, flirone.cli; print(any(m.startswith('PySide6') for m in sys.modules))"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False", "importing the command line loaded Qt"
