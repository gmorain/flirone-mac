"""Radiance conversion, checked by inverting it."""

from __future__ import annotations

import numpy as np
import pytest

from flirone.calibration import (
    DEFAULT_PLANCK,
    Conditions,
    Planck,
    Trust,
    atmospheric_transmission,
    raw_to_celsius,
    temperature_to_raw,
)

# The constants a FLIR One Gen 2 records for itself, from IMG_3888.
GEN2 = Planck(
    r1=18453.355,
    r2=0.012509181,
    b=1460.6,
    f=1.0,
    o=-1731.0,
    trust=Trust.CAMERA,
    serial="F02F9T00570",
)

IDEAL = Conditions(emissivity=1.0, distance_m=0.0)


@pytest.mark.parametrize("celsius", [-20.0, 0.0, 22.0, 60.0, 150.0, 400.0])
def test_round_trips_through_planck(celsius):
    raw = temperature_to_raw(celsius, GEN2)
    back = raw_to_celsius(np.array([raw]), GEN2, IDEAL)
    assert back[0] == pytest.approx(celsius, abs=1e-6)


def test_monotonic_in_raw_counts():
    counts = np.arange(12000, 65000, 500)
    temps = raw_to_celsius(counts, GEN2, IDEAL)
    assert np.all(np.diff(temps) > 0)


def test_emissivity_below_one_raises_the_reading():
    """A dull object emitting the same signal must be hotter than a black one."""
    raw = np.array([temperature_to_raw(60.0, GEN2)])
    black = raw_to_celsius(raw, GEN2, Conditions(emissivity=1.0, reflected_c=20.0, distance_m=0.0))
    dull = raw_to_celsius(raw, GEN2, Conditions(emissivity=0.7, reflected_c=20.0, distance_m=0.0))
    assert dull[0] > black[0]


def test_reflected_temperature_matters_more_at_low_emissivity():
    raw = np.array([temperature_to_raw(60.0, GEN2)])

    def reading(emissivity, reflected):
        return raw_to_celsius(
            raw, GEN2, Conditions(emissivity=emissivity, reflected_c=reflected, distance_m=0.0)
        )[0]

    shiny = abs(reading(0.5, 40.0) - reading(0.5, 0.0))
    matte = abs(reading(0.95, 40.0) - reading(0.95, 0.0))
    assert shiny > matte


def test_atmospheric_transmission_falls_with_distance():
    near = atmospheric_transmission(Conditions(distance_m=0.0))
    far = atmospheric_transmission(Conditions(distance_m=50.0))
    assert near == pytest.approx(1.0, abs=1e-6)
    assert 0.0 < far < near


def test_per_camera_constants_change_the_answer():
    """Why the app refuses to call another camera's constants calibrated."""
    raw = np.array([30000.0])
    mine = raw_to_celsius(raw, GEN2, IDEAL)[0]
    other = raw_to_celsius(raw, DEFAULT_PLANCK, IDEAL)[0]
    assert abs(mine - other) > 1.0


def test_out_of_domain_counts_do_not_raise():
    temps = raw_to_celsius(np.array([0.0, 1.0, 65535.0]), GEN2, IDEAL)
    assert temps.shape == (3,)


def test_constants_from_the_same_file_are_absolute():
    assert GEN2.trust is Trust.CAMERA
    assert GEN2.trusted


def test_adopting_from_the_same_camera_stays_absolute():
    # The handshake spells the serial differently from EXIF; same unit.
    adopted = GEN2.adopted_for("FLIRONEF02F9T00570A")
    assert adopted.trust is Trust.CAMERA
    assert adopted.trusted


def test_adopting_from_another_camera_is_not_absolute():
    adopted = GEN2.adopted_for("F0ZZZZZ99999")
    assert adopted.trust is Trust.MISMATCH
    assert not adopted.trusted


def test_adopting_with_an_unknown_target_is_unverified():
    adopted = GEN2.adopted_for(None)
    assert adopted.trust is Trust.UNVERIFIED
    assert not adopted.trusted


def test_reference_constants_are_never_absolute():
    assert DEFAULT_PLANCK.trust is Trust.REFERENCE
    assert not DEFAULT_PLANCK.trusted


@pytest.mark.parametrize(
    "r1,r2,b",
    [(0.0, 0.0125, 1460.6), (18453.0, 0.0, 1460.6), (18453.0, 0.0125, -1.0)],
)
def test_non_physical_constants_are_rejected(r1, r2, b):
    with pytest.raises(ValueError, match="non-physical"):
        Planck(r1=r1, r2=r2, b=b, f=1.0, o=-1731.0).validated()


def test_short_serials_never_match():
    from flirone.calibration import serials_match

    assert not serials_match("AB12", "AB12")
    assert serials_match("F02F9T00570", "FLIRONEF02F9T00570A")
