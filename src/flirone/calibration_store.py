"""Remember each camera's Planck constants between sessions.

Constants are calibrated per unit, so a user with two cameras needs two sets and
must not have one silently applied to the other. Keeping them in a file keyed by
serial means a camera calibrated once stays calibrated, and swapping cameras
picks up the right constants instead of the last ones loaded.

Lookup is `calibration.serials_match`, not a dictionary hit: the same unit is
written `F02F9T00570` in EXIF and `FLIRONEF02F9T00570A` over the accessory
handshake, so a stored entry has to be findable from either. Entries without a
serial are not stored at all, since there would be no way to know which camera
they belong to and adopting them automatically is exactly the mistake this
application exists to avoid.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .calibration import Planck, Trust, serials_match

APP_DIR_NAME = "flirone"
STORE_NAME = "calibrations.json"


def default_directory() -> Path:
    """Where this platform keeps small application state."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config) if config else Path.home() / ".config"
    return base / APP_DIR_NAME


@dataclass(frozen=True)
class StoredCalibration:
    """One camera's constants, as written to disk."""

    serial: str
    planck: Planck

    @property
    def label(self) -> str:
        return f"{self.serial} (from {self.planck.source})"


class CalibrationStore:
    """Planck constants for every camera this installation has seen."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_directory() / STORE_NAME

    # -- disk ---------------------------------------------------------------

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            # A missing or corrupt store is not an error worth stopping for;
            # it just means nothing is remembered yet.
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write and replace, so an interrupted save cannot truncate the store.
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True))
        temporary.replace(self.path)

    # -- contents -----------------------------------------------------------

    def entries(self) -> list[StoredCalibration]:
        """Everything remembered, in the order the file happens to hold it."""
        found = []
        for serial, record in self._read().items():
            try:
                found.append(StoredCalibration(serial, _planck_from(record, serial)))
            except (KeyError, TypeError, ValueError):
                continue  # skip a damaged entry rather than lose the rest
        return found

    def remember(self, planck: Planck) -> bool:
        """Store these constants. False if they carry no serial to key them by."""
        if not planck.serial:
            return False
        data = self._read()
        data[planck.serial] = {
            "PlanckR1": planck.r1,
            "PlanckR2": planck.r2,
            "PlanckB": planck.b,
            "PlanckF": planck.f,
            "PlanckO": planck.o,
            "source": planck.source,
            "serial": planck.serial,
        }
        self._write(data)
        return True

    def find(self, serial: str | None) -> Planck | None:
        """Constants for this camera, marked as belonging to it.

        The serial having matched is what promotes them out of UNVERIFIED: it
        is the same check `_adopt_calibration` makes against an image.
        """
        if not serial:
            return None
        for entry in self.entries():
            if serials_match(entry.serial, serial):
                return Planck(
                    r1=entry.planck.r1,
                    r2=entry.planck.r2,
                    b=entry.planck.b,
                    f=entry.planck.f,
                    o=entry.planck.o,
                    trust=Trust.CAMERA,
                    source=entry.planck.source,
                    serial=entry.serial,
                )
        return None

    def forget(self, serial: str | None) -> bool:
        """Drop the entry matching this camera. False if nothing matched."""
        if not serial:
            return False
        data = self._read()
        for key in [k for k in data if serials_match(k, serial)]:
            del data[key]
            self._write(data)
            return True
        return False

    def forget_all(self) -> int:
        """Empty the store, returning how many entries were dropped."""
        count = len(self._read())
        if count:
            self._write({})
        return count


def _planck_from(record: dict, serial: str) -> Planck:
    return Planck(
        r1=float(record["PlanckR1"]),
        r2=float(record["PlanckR2"]),
        b=float(record["PlanckB"]),
        f=float(record["PlanckF"]),
        o=float(record["PlanckO"]),
        trust=Trust.UNVERIFIED,
        source=str(record.get("source", "stored")),
        serial=str(record.get("serial", serial)),
    ).validated()
