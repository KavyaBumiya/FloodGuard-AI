"""Regression tests for the water-level domain rules.

Two scales exist and they are not interchangeable:

* the physical probe is INVERTED -- 0 cm is fully flooded, 30 cm is dry -- so
  risk falls as the reading rises;
* the Mithi simulation stage is ASCENDING -- higher means more water.

Every value that crosses between them goes through `map_physical_to_mithi` /
`map_mithi_to_physical`, and every band comparison must agree with the scale
of the number it is given. Both halves of that were wrong at least once:

* `_normalize_linear(inverse=True)` compared `value <= low`, which mapped every
  reading below the first boundary to LOW and everything above it to SEVERE;
* the ascending Mithi band edges were passed through unshifted, so a 10 cm
  (MODERATE) probe reading arriving as 185 cm reported LOW -- every Mithi band
  sat one step too low.

These tests are pure functions, no database and no fixtures, so they run in CI
in a second.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.services.flood_risk import (  # noqa: E402
    assess_current_risk,
    calculate_rise_rate,
    map_mithi_to_physical,
    map_physical_to_mithi,
    water_level_thresholds,
)

SETTINGS = get_settings()
PHYSICAL_MAX = SETTINGS.demo_input_physical_max_cm
MITHI_MIN = SETTINGS.demo_input_mithi_min_cm
MITHI_MAX = SETTINGS.demo_input_mithi_max_cm


def _band(water_level_cm: float, *, inverted: bool) -> str:
    assessment = assess_current_risk(
        water_level_cm=water_level_cm,
        rainfall_mm_hr=0.0,
        rise_rate_cm_per_interval=0.0,
        soil_available=False,
        inverted=inverted,
    )
    return str(assessment.components[0].band)


# ── mapping ───────────────────────────────────────────────────────────────────


def test_dry_probe_maps_to_the_mithi_minimum():
    """The probe is inverted, so the driest reading must be the lowest stage."""
    assert map_physical_to_mithi(PHYSICAL_MAX) == pytest.approx(MITHI_MIN)


def test_flooded_probe_maps_to_the_mithi_maximum():
    assert map_physical_to_mithi(0.0) == pytest.approx(MITHI_MAX)


def test_mapping_is_strictly_decreasing():
    """More water (a LOWER probe reading) must mean a HIGHER Mithi stage."""
    probe_readings = (30.0, 20.0, 10.0, 0.0)
    stages = [map_physical_to_mithi(v) for v in probe_readings]
    assert stages == sorted(stages), (
        "as the probe reading falls, the Mithi stage must rise"
    )


@pytest.mark.parametrize("probe_cm", [0.0, 1.5, 6.0, 12.0, 23.75, 30.0])
def test_mapping_round_trips(probe_cm: float):
    back = map_mithi_to_physical(map_physical_to_mithi(probe_cm))
    assert back == pytest.approx(probe_cm, abs=1e-6)


def test_out_of_range_probe_values_clamp_instead_of_extrapolating():
    """A probe outside its calibrated range must saturate, never overshoot."""
    assert map_physical_to_mithi(-50.0) == pytest.approx(MITHI_MAX)
    assert map_physical_to_mithi(PHYSICAL_MAX + 50.0) == pytest.approx(MITHI_MIN)


# ── bands ────────────────────────────────────────────────────────────────────


def test_mithi_band_edges_are_shifted_from_the_probe_boundaries():
    """Ascending space needs the NEXT-SEVERER edge, not the same edge.

    Passing map(low), map(moderate), map(high), map(severe) in probe order is
    the band-offset bug: the MODERATE band then starts one step too high.
    """
    probe_edges = water_level_thresholds(inverted=True)
    mithi_edges = water_level_thresholds(inverted=False)

    assert list(mithi_edges) == sorted(mithi_edges), "edges must be ascending"
    assert mithi_edges == (
        map_physical_to_mithi(PHYSICAL_MAX),
        map_physical_to_mithi(probe_edges[0]),
        map_physical_to_mithi(probe_edges[1]),
        map_physical_to_mithi(probe_edges[2]),
    )


@pytest.mark.parametrize(
    "probe_cm, expected",
    [(30.0, "LOW"), (20.0, "LOW"), (10.0, "MODERATE"), (8.0, "MODERATE"),
     (5.0, "HIGH"), (4.0, "HIGH"), (2.0, "SEVERE"), (1.0, "SEVERE")],
)
def test_band_matches_the_probe_scale(probe_cm: float, expected: str):
    assert _band(probe_cm, inverted=True) == expected


@pytest.mark.parametrize("probe_cm", [30.0, 20.0, 10.0, 8.0, 5.0, 4.0, 2.0, 1.0])
def test_the_same_state_reads_the_same_in_both_scales(probe_cm: float):
    """Interior states must survive the crossing to the Mithi scale intact."""
    mithi = map_physical_to_mithi(probe_cm)
    assert _band(mithi, inverted=False) == _band(probe_cm, inverted=True)


def test_band_severity_never_exceeds_its_own_explanation():
    """A SEVERE component must not report a milder overall level.

    The weighted sum can only reach 1.0 when every driver saturates, so 2 cm of
    water scored 0.35 and reported MODERATE while its own text said SEVERE.
    """
    assessment = assess_current_risk(
        water_level_cm=2.0,
        rainfall_mm_hr=0.0,
        rise_rate_cm_per_interval=0.0,
        soil_available=False,
        inverted=True,
    )
    assert assessment.components[0].band == "SEVERE"
    assert assessment.risk_level == "SEVERE"


def test_benign_reading_stays_low():
    assessment = assess_current_risk(
        water_level_cm=PHYSICAL_MAX,
        rainfall_mm_hr=0.0,
        rise_rate_cm_per_interval=0.0,
        soil_available=False,
        inverted=True,
    )
    assert assessment.risk_level == "LOW"


# ── rise rate ────────────────────────────────────────────────────────────────


def test_falling_probe_reading_is_a_rising_water_level():
    """On an inverted probe, water rising means the centimetres go DOWN."""
    assert calculate_rise_rate(5.0, 10.0, 300.0, inverted=True) == pytest.approx(5.0)


def test_rising_probe_reading_is_not_a_rise():
    assert calculate_rise_rate(10.0, 5.0, 300.0, inverted=True) == pytest.approx(0.0)


def test_ascending_scale_keeps_the_original_direction():
    assert calculate_rise_rate(10.0, 5.0, 300.0) == pytest.approx(5.0)
    assert calculate_rise_rate(5.0, 10.0, 300.0) == pytest.approx(0.0)


def test_rise_rate_scales_to_five_minute_intervals():
    """1 cm of decline in 30 s is 10 cm per 5-minute interval."""
    per_5min = calculate_rise_rate(9.0, 10.0, 30.0, inverted=True)
    assert per_5min == pytest.approx(10.0)


def test_rise_rate_is_never_negative():
    assert calculate_rise_rate(20.0, 5.0, 300.0, inverted=True) >= 0.0
    assert calculate_rise_rate(5.0, 20.0, 300.0) >= 0.0


def test_non_positive_interval_is_ignored():
    assert calculate_rise_rate(5.0, 10.0, 0.0, inverted=True) == 0.0