"""Container templates in the calibration store.

The serial becomes a filename, so most of what is worth testing here is what
happens when the serial is not the alphanumeric string a camera reports.
"""

from __future__ import annotations

import pytest

from flirone.calibration import Planck, Trust
from flirone.calibration_store import CalibrationStore

PAYLOAD = b"FFF\x00" + b"\x00" * 60


def store_with_camera(tmp_path, serial="F02F9T00570"):
    store = CalibrationStore(tmp_path / "calibrations.json")
    store.remember(
        Planck(
            r1=18453.35,
            r2=0.0125,
            b=1460.6,
            f=1.0,
            o=-1731.0,
            trust=Trust.CAMERA,
            serial=serial,
            source="test",
        )
    )
    return store


def test_a_template_comes_back_for_the_camera_that_filed_it(tmp_path):
    store = store_with_camera(tmp_path)
    assert store.remember_template("F02F9T00570", PAYLOAD)
    assert store.template("F02F9T00570") == PAYLOAD


def test_a_template_is_found_through_the_handshake_spelling(tmp_path):
    """The camera reports FLIRONE<serial>A where the file says <serial>."""
    store = store_with_camera(tmp_path)
    store.remember_template("F02F9T00570", PAYLOAD)
    assert store.template("FLIRONEF02F9T00570A") == PAYLOAD


def test_an_unknown_camera_has_no_template(tmp_path):
    store = store_with_camera(tmp_path)
    store.remember_template("F02F9T00570", PAYLOAD)
    assert store.template("F02F9T99999") is None
    assert store.template(None) is None


def test_a_serial_cannot_escape_the_store_directory(tmp_path):
    """Otherwise a crafted serial writes wherever it likes."""
    store = CalibrationStore(tmp_path / "store" / "calibrations.json")
    for serial in ("../escaped", "..", "/etc/passwd", "a/b", "", "x" * 65):
        assert not store.remember_template(serial, PAYLOAD), serial
    assert not list(tmp_path.glob("escaped*"))
    assert not list(tmp_path.glob("*.fff"))


def test_an_empty_payload_is_refused(tmp_path):
    store = store_with_camera(tmp_path)
    assert not store.remember_template("F02F9T00570", b"")
    assert store.template("F02F9T00570") is None


def test_adopt_from_image_files_constants_and_container(tmp_path, monkeypatch):
    """One call, so neither front end knows a template is filed beside them."""
    from flirone import calibration_store

    store = CalibrationStore(tmp_path / "calibrations.json")
    planck = Planck(
        r1=18453.35,
        r2=0.0125,
        b=1460.6,
        f=1.0,
        o=-1731.0,
        trust=Trust.CAMERA,
        serial="F02F9T00570",
        source="test",
    )
    image = tmp_path / "IMG.jpg"
    image.write_bytes(b"\xff\xd8not really")

    monkeypatch.setattr(calibration_store, "PLANCK_LOADER", None, raising=False)
    monkeypatch.setattr("flirone.planck_import.load", lambda path: planck)
    monkeypatch.setattr("flirone.fff.read_app1", lambda data: PAYLOAD)

    filed = calibration_store.adopt_from_image(image, store)

    assert filed.serial == "F02F9T00570"
    assert store.find("F02F9T00570") is not None
    assert store.template("F02F9T00570") == PAYLOAD


def test_adopt_from_image_refuses_another_camera(tmp_path, monkeypatch):
    from flirone import calibration_store

    store = CalibrationStore(tmp_path / "calibrations.json")
    other = Planck(
        r1=18453.35,
        r2=0.0125,
        b=1460.6,
        f=1.0,
        o=-1731.0,
        trust=Trust.CAMERA,
        serial="F02F9T99999",
        source="test",
    )
    monkeypatch.setattr("flirone.planck_import.load", lambda path: other)

    image = tmp_path / "IMG.jpg"
    image.write_bytes(b"\xff\xd8")
    with pytest.raises(ValueError, match="belong to camera"):
        calibration_store.adopt_from_image(image, store, serial="F02F9T00570")
    assert store.find("F02F9T00570") is None


def test_a_calibration_without_a_container_still_files_constants(tmp_path, monkeypatch):
    """A calibration saved as JSON has no container, which only matters later."""
    from flirone import calibration_store

    store = CalibrationStore(tmp_path / "calibrations.json")
    planck = Planck(
        r1=18453.35,
        r2=0.0125,
        b=1460.6,
        f=1.0,
        o=-1731.0,
        trust=Trust.CAMERA,
        serial="F02F9T00570",
        source="test",
    )
    monkeypatch.setattr("flirone.planck_import.load", lambda path: planck)

    image = tmp_path / "saved.json"
    image.write_text("{}")
    calibration_store.adopt_from_image(image, store)

    assert store.find("F02F9T00570") is not None
    assert store.template("F02F9T00570") is None
