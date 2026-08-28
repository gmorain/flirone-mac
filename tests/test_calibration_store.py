"""Remembering per-camera constants across sessions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flirone.calibration import Planck, Trust
from flirone.calibration_store import CalibrationStore, default_directory

# The two ways this unit's serial is written: EXIF, and the accessory handshake.
EXIF_SERIAL = "F02F9T00570"
HANDSHAKE_SERIAL = "FLIRONEF02F9T00570A"


def make_planck(serial: str | None = EXIF_SERIAL, source: str = "IMG_3888.JPG") -> Planck:
    return Planck(
        r1=17096.453125,
        r2=0.040624,
        b=1428.0,
        f=1.0,
        o=-56.0,
        trust=Trust.UNVERIFIED,
        source=source,
        serial=serial,
    )


@pytest.fixture
def store(tmp_path: Path) -> CalibrationStore:
    return CalibrationStore(tmp_path / "calibrations.json")


def test_a_camera_stays_calibrated_across_sessions(store: CalibrationStore, tmp_path: Path):
    assert store.remember(make_planck()) is True
    reopened = CalibrationStore(tmp_path / "calibrations.json")
    found = reopened.find(EXIF_SERIAL)
    assert found is not None
    assert found.b == 1428.0


def test_found_by_either_spelling_of_the_serial(store: CalibrationStore):
    """Stored from an image, looked up from the live handshake."""
    store.remember(make_planck(serial=EXIF_SERIAL))
    assert store.find(HANDSHAKE_SERIAL) is not None


def test_a_match_promotes_the_constants_to_trusted(store: CalibrationStore):
    """The serial matching is what makes them absolute rather than adopted."""
    store.remember(make_planck())
    found = store.find(HANDSHAKE_SERIAL)
    assert found.trust is Trust.CAMERA
    assert found.trusted is True


def test_another_camera_gets_nothing(store: CalibrationStore):
    """The whole point: one camera's constants must not reach another."""
    store.remember(make_planck())
    assert store.find("F99ZZ9Z99999") is None


def test_two_cameras_are_kept_apart(store: CalibrationStore):
    store.remember(make_planck(serial=EXIF_SERIAL, source="a.jpg"))
    store.remember(make_planck(serial="F44K4T00999", source="b.jpg"))
    assert store.find(EXIF_SERIAL).source == "a.jpg"
    assert store.find("F44K4T00999").source == "b.jpg"
    assert len(store.entries()) == 2


def test_constants_without_a_serial_are_refused(store: CalibrationStore):
    """Unkeyed constants could be applied to the wrong camera later."""
    assert store.remember(make_planck(serial=None)) is False
    assert store.entries() == []


def test_recalibrating_replaces_the_entry(store: CalibrationStore):
    store.remember(make_planck(source="old.jpg"))
    store.remember(make_planck(source="new.jpg"))
    assert len(store.entries()) == 1
    assert store.find(EXIF_SERIAL).source == "new.jpg"


def test_forget_one_camera(store: CalibrationStore):
    store.remember(make_planck())
    store.remember(make_planck(serial="F44K4T00999"))
    assert store.forget(HANDSHAKE_SERIAL) is True
    assert store.find(EXIF_SERIAL) is None
    assert store.find("F44K4T00999") is not None


def test_forget_reports_when_nothing_matched(store: CalibrationStore):
    store.remember(make_planck())
    assert store.forget("F99ZZ9Z99999") is False
    assert len(store.entries()) == 1


def test_forget_all_reports_how_many_went(store: CalibrationStore):
    store.remember(make_planck())
    store.remember(make_planck(serial="F44K4T00999"))
    assert store.forget_all() == 2
    assert store.entries() == []


def test_a_missing_store_is_empty_not_an_error(tmp_path: Path):
    assert CalibrationStore(tmp_path / "nope.json").entries() == []


def test_a_corrupt_store_does_not_stop_the_application(tmp_path: Path):
    path = tmp_path / "calibrations.json"
    path.write_text("{ this is not json")
    assert CalibrationStore(path).entries() == []
    assert CalibrationStore(path).find(EXIF_SERIAL) is None


def test_one_damaged_entry_does_not_lose_the_others(tmp_path: Path):
    path = tmp_path / "calibrations.json"
    path.write_text(
        json.dumps(
            {
                "BROKEN000001": {"PlanckR1": 0.0, "PlanckR2": 0.0, "PlanckB": 0.0},
                EXIF_SERIAL: {
                    "PlanckR1": 17096.0,
                    "PlanckR2": 0.04,
                    "PlanckB": 1428.0,
                    "PlanckF": 1.0,
                    "PlanckO": -56.0,
                    "serial": EXIF_SERIAL,
                },
            }
        )
    )
    store = CalibrationStore(path)
    assert [e.serial for e in store.entries()] == [EXIF_SERIAL]


def test_default_directory_is_per_platform():
    directory = default_directory()
    assert directory.is_absolute()
    assert directory.name == "flirone"
