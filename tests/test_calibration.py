"""Radiance conversion, checked by inverting it."""

from __future__ import annotations

import numpy as np
import pytest

from flirone.calibration import (
    DEFAULT_PLANCK,
    FLIR_ONE_LIVE_SCALE,
    Conditions,
    CountScale,
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


# --- the live count scale ----------------------------------------------------

# Measured on a FLIR One Gen 2, serial F02F9T00570: an ice-water slurry and
# boiling water, both fixed by physics, plus two targets read by the official
# iOS app on the same camera. Section 15 of docs/hardware-findings.md.
LIVE_CAMERA = Planck(r1=18453.355, r2=0.012509181, b=1460.6, f=1.0, o=-1731.0, serial="F02F9T00570")
# The scale measured for this unit, converted to the reference-plane form. It is
# within one percent of the bit-depth shift below, and the difference is
# degenerate with the emissivity assumed for water.
LIVE_SCALE = CountScale(factor=3.9576, offset=213.7, source="ice and boiling water")
LIVE_POINTS = [(2246.3, 0.0), (7745.8, 99.5), (3167.5, 26.0), (3637.0, 35.0)]
WATER = Conditions(emissivity=0.96, reflected_c=22.0, atmospheric_c=22.0, distance_m=0.3)


def test_live_counts_convert_to_the_measured_temperatures():
    """Within the accuracy of the camera, which is about 3 C."""
    for counts, expected in LIVE_POINTS:
        got = float(raw_to_celsius(np.array([[counts]]), LIVE_CAMERA, WATER, LIVE_SCALE)[0, 0])
        assert got == pytest.approx(expected, abs=1.5)


def test_the_same_counts_without_the_scale_are_absurd():
    """The bug this exists to fix: live counts read about a hundred degrees low."""
    got = float(raw_to_celsius(np.array([[3167.5]]), LIVE_CAMERA, WATER)[0, 0])
    assert got < -50


def test_no_scale_leaves_reference_counts_alone():
    """A radiometric JPEG is already on the reference plane and must not move."""
    counts = np.array([[12269.0]])
    plain = raw_to_celsius(counts, LIVE_CAMERA, WATER)
    assert float(plain[0, 0]) == pytest.approx(22.0, abs=1.0)


def test_mapping_to_the_reference_plane_is_what_it_says():
    counts = np.array([[2000.0, 4000.0]])
    mapped = FLIR_ONE_LIVE_SCALE.to_reference(counts)
    assert list(mapped[0]) == [8000.0, 16000.0]


def test_the_default_is_the_bit_depth_shift():
    """A FLIR One with no calibration of its own still reads absolute."""
    assert (FLIR_ONE_LIVE_SCALE.factor, FLIR_ONE_LIVE_SCALE.offset) == (4.0, 0.0)


def test_the_bare_factor_of_four_matches_what_was_measured():
    """Two fitted parameters buy nothing over the factor the bit depths predict."""
    for counts, expected in LIVE_POINTS:
        got = float(
            raw_to_celsius(np.array([[counts]]), LIVE_CAMERA, WATER, FLIR_ONE_LIVE_SCALE)[0, 0]
        )
        assert got == pytest.approx(expected, abs=2.0)


def test_a_non_positive_factor_is_refused():
    with pytest.raises(ValueError, match="factor"):
        CountScale(factor=0.0, offset=0.0).validated()


def test_the_counts_are_not_linear_in_temperature():
    """Rules out the stream being Lepton TLinear output, which would need no Planck.

    A straight line in temperature gets two free parameters, the same as the
    scale, and is fitted here on all four points rather than two. It still
    cannot follow them, because radiance curves away from temperature over this
    range. Section 15 of docs/hardware-findings.md.
    """
    counts = np.array([c for c, _ in LIVE_POINTS])
    truth = np.array([t for _, t in LIVE_POINTS])
    gain, offset = np.polyfit(truth, counts, 1)
    predicted = (counts - offset) / gain
    assert np.abs(predicted - truth).max() > 3.0


# --- cross-check against an independent implementation ------------------------

# Produced by flirimageextractor's raw2temp, itself a port of Glenn Tattersall's
# Thermimage, on this camera's constants at emissivity 0.95, reflected 22 C,
# atmospheric 20 C, 50% humidity. Independent code for the same published model,
# so it checks the arithmetic rather than the physics. Section 16 of
# docs/hardware-findings.md.
XCHECK_RAW = [12301.0, 15000.0, 17744.0, 20000.0, 23220.0]
XCHECK_C = [22.189184, 36.996657, 50.256895, 60.176828, 73.179077]


def test_conversion_matches_an_independent_implementation():
    """At zero range, where no atmospheric convention is in play, exactly."""
    conditions = Conditions(
        emissivity=0.95, reflected_c=22.0, atmospheric_c=20.0, humidity=0.5, distance_m=0.0
    )
    got = raw_to_celsius(np.array(XCHECK_RAW), LIVE_CAMERA, conditions)
    assert list(got) == pytest.approx(XCHECK_C, abs=1e-5)
