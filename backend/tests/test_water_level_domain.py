"""Regression tests for the water-level domain rules.

Two scales exist and they are not interchangeable:

* the physical probe is ASCENDING depth -- 0 cm is dry, 30 cm is fully
  submerged -- so risk rises as the reading rises;
* the Mithi simulation stage is also ASCENDING -- higher means more water.

Both are ascending now. They used to disagree: the probe was INVERTED (0 cm
flooded, 30 cm dry), which meant a dry sensor scored the maximum severity and
the dashboard showed SEVERE while the device logged a normal channel. Three
distinct bugs came out of that disagreement and each one is guarded here:

* `_normalize_linear(inverse=True)` compared `value <= low`, which mapped every
  reading below the first boundary to LOW and everything above it to SEVERE;
* the ascending Mithi band edges were passed through unshifted, so a 10 cm
  (MODERATE) probe reading arriving as 185 cm reported LOW -- every Mithi band
  sat one step too low;
* the probe and the Mithi stage ran in opposite directions, so whichever way
  the code guessed, one of the two screens was wrong.

Every value that crosses between the scales goes through
`map_physical_to_mithi` / `map_mithi_to_physical`.

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

# Bands on the probe domain: 0-20 cm is normal, 20-25 cm escalates, 25+ is
# critical. Mirrors config/risk_thresholds.json.
PROBE_LOW, PROBE_MODERATE, PROBE_HIGH, PROBE_SEVERE = water_level_thresholds(
    probe_domain=True
)


def _band(water_level_cm: float, *, probe_domain: bool) -> str:
    assessment = assess_current_risk(
        water_level_cm=water_level_cm,
        rainfall_mm_hr=0.0,
        rise_rate_cm_per_interval=0.0,
        soil_available=False,
        probe_domain=probe_domain,
    )
    return str(assessment.components[0].band)


# ── mapping ───────────────────────────────────────────────────────────────────


def test_dry_probe_maps_to_the_mithi_minimum():
    """0 cm is dry on both scales, so it must land on the lowest Mithi stage."""
    assert map_physical_to_mithi(0.0) == pytest.approx(MITHI_MIN)


def test_submerged_probe_maps_to_the_mithi_maximum():
    assert map_physical_to_mithi(PHYSICAL_MAX) == pytest.approx(MITHI_MAX)


def test_mapping_is_strictly_increasing():
    """Both scales ascend, so more water on one means more water on the other."""
    probe_readings = (0.0, 10.0, 20.0, 30.0)
    stages = [map_physical_to_mithi(v) for v in probe_readings]
    assert stages == sorted(stages), (
        "as the probe reading rises, the Mithi stage must rise"
    )


@pytest.mark.parametrize("probe_cm", [0.0, 1.5, 6.0, 12.0, 23.75, 30.0])
def test_mapping_round_trips(probe_cm: float):
    back = map_mithi_to_physical(map_physical_to_mithi(probe_cm))
    assert back == pytest.approx(probe_cm, abs=1e-6)


def test_out_of_range_probe_values_clamp_instead_of_extrapolating():
    """A probe outside its calibrated range must saturate, never overshoot."""
    assert map_physical_to_mithi(-50.0) == pytest.approx(MITHI_MIN)
    assert map_physical_to_mithi(PHYSICAL_MAX + 50.0) == pytest.approx(MITHI_MAX)


# ── bands ────────────────────────────────────────────────────────────────────


def test_probe_band_edges_ascend_with_severity():
    """A descending edge list silently inverts every comparison downstream."""
    edges = water_level_thresholds(probe_domain=True)
    assert list(edges) == sorted(edges), "probe edges must ascend"
    assert edges[0] <= edges[1] <= edges[2] <= edges[3]


def test_mithi_band_edges_are_the_mapped_probe_edges():
    """Each Mithi edge is the same physical boundary, on the Mithi scale.

    The old code passed map(low), map(moderate), map(high), map(severe) but
    dropped one and prepended a different boundary, shifting every Mithi band
    one step. With both domains ascending the edges map one-for-one.
    """
    probe_edges = water_level_thresholds(probe_domain=True)
    mithi_edges = water_level_thresholds(probe_domain=False)

    assert list(mithi_edges) == sorted(mithi_edges), "edges must be ascending"
    assert mithi_edges == tuple(map_physical_to_mithi(e) for e in probe_edges)


@pytest.mark.parametrize(
    "probe_cm, expected",
    [(0.0, "LOW"), (1.5, "LOW"), (12.0, "LOW"), (19.9, "LOW"),
     (20.0, "MODERATE"), (21.0, "MODERATE"),
     (22.0, "HIGH"), (24.0, "HIGH"),
     (25.0, "SEVERE"), (28.0, "SEVERE"), (30.0, "SEVERE")],
)
def test_band_matches_the_probe_scale(probe_cm: float, expected: str):
    assert _band(probe_cm, probe_domain=True) == expected


@pytest.mark.parametrize(
    "probe_cm", [0.0, 6.0, 12.0, 19.0, 20.0, 21.0, 22.0, 24.0, 25.0, 30.0]
)
def test_the_same_state_reads_the_same_in_both_scales(probe_cm: float):
    """Interior states must survive the crossing to the Mithi scale intact."""
    mithi = map_physical_to_mithi(probe_cm)
    assert _band(mithi, probe_domain=False) == _band(probe_cm, probe_domain=True)


def test_band_severity_never_exceeds_its_own_explanation():
    """A SEVERE component must not report a milder overall level.

    The weighted sum can only reach 1.0 when every driver saturates, so a
    shallow reading scored 0.35 and reported MODERATE while its own text said
    SEVERE.
    """
    assessment = assess_current_risk(
        water_level_cm=PROBE_SEVERE,
        rainfall_mm_hr=0.0,
        rise_rate_cm_per_interval=0.0,
        soil_available=False,
        probe_domain=True,
    )
    assert assessment.components[0].band == "SEVERE"
    assert assessment.risk_level == "SEVERE"


def test_benign_reading_stays_low():
    """A dry channel is the case that used to report SEVERE."""
    assessment = assess_current_risk(
        water_level_cm=0.0,
        rainfall_mm_hr=0.0,
        rise_rate_cm_per_interval=0.0,
        soil_available=False,
        probe_domain=True,
    )
    assert assessment.components[0].band == "LOW"
    assert assessment.risk_level == "LOW"


def test_dry_channel_never_escalates_however_it_is_scaled():
    """The original production bug, stated as a guard.

    A dry sensor reads 0 cm. On the inverted scale that sat below every band
    edge and scored SEVERE while the device logged a normal channel.
    """
    assert PROBE_LOW == 0.0, "the dry reading must be inside the LOW band"
    assert _band(0.0, probe_domain=True) == "LOW"


# ── rise rate ────────────────────────────────────────────────────────────────


def test_rising_reading_is_a_rising_water_level():
    assert calculate_rise_rate(10.0, 5.0, 300.0) == pytest.approx(5.0)


def test_falling_reading_is_not_a_rise():
    """Water going down is never a rise, on any scale."""
    assert calculate_rise_rate(5.0, 10.0, 300.0) == pytest.approx(0.0)


def test_rise_rate_scales_to_five_minute_intervals():
    """1 cm of rise in 30 s is 10 cm per 5-minute interval."""
    per_5min = calculate_rise_rate(10.0, 9.0, 30.0)
    assert per_5min == pytest.approx(10.0)


def test_rise_rate_is_never_negative():
    assert calculate_rise_rate(20.0, 5.0, 300.0) >= 0.0
    assert calculate_rise_rate(5.0, 20.0, 300.0) >= 0.0


def test_non_positive_interval_is_ignored():
    assert calculate_rise_rate(5.0, 10.0, 0.0) == 0.0