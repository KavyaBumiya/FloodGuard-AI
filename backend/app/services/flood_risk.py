"""
FLOODGUARD AI — Flood Risk Assessment Engine
=============================================
Explainable, rule-based flood risk scoring for the Mithi River corridor (BKC → Kurla).

This is a prototype decision model — not a scientifically validated hydraulic model.
Weights and thresholds are starting assumptions requiring local calibration.
The interface must state this clearly.

Risk Levels: LOW, MODERATE, HIGH, SEVERE
(SEVERE maps to CRITICAL in UI alerts)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.core.config import get_settings
from app.core.project_config import get_risk_config

# ──────────────────────────────────────────────────────────────────────────────
# THRESHOLDS — Mithi River Corridor (BKC → Kurla)
# Loaded from config/risk_thresholds.json (single source of truth).
# These are starting assumptions. Real thresholds need local calibration.
# ──────────────────────────────────────────────────────────────────────────────
_CFG = get_risk_config()
_TH = _CFG.get("thresholds", {})
_W = _CFG.get("weights", {})
_BANDS = _CFG.get("bands", {})
_DEFAULTS = _CFG.get("defaults", {})
_FW = _DEFAULTS.get("forecast_weights", {}) if isinstance(_DEFAULTS.get("forecast_weights"), dict) else {}


def _b(scope: str, key: str, default: float) -> float:
    try:
        return float(_TH.get(scope, {}).get(key, default))
    except (TypeError, ValueError):
        return default


def _w(key: str, default: float) -> float:
    try:
        return float(_W.get(key, default))
    except (TypeError, ValueError):
        return default


# Water level thresholds (cm above channel bed / sensor reference)
WATER_LEVEL_LOW_CM = _b("water_level_cm", "low", 50.0)
WATER_LEVEL_MODERATE_CM = _b("water_level_cm", "moderate", 100.0)
WATER_LEVEL_HIGH_CM = _b("water_level_cm", "high", 150.0)
WATER_LEVEL_SEVERE_CM = _b("water_level_cm", "severe", 200.0)

# Rainfall intensity thresholds (mm/hr)
RAINFALL_LOW = _b("rainfall_mm_hr", "low", 5.0)
RAINFALL_MODERATE = _b("rainfall_mm_hr", "moderate", 20.0)
RAINFALL_HIGH = _b("rainfall_mm_hr", "high", 50.0)
RAINFALL_SEVERE = _b("rainfall_mm_hr", "severe", 80.0)

# Rise rate thresholds (cm per 5-minute interval)
RISE_RATE_LOW = _b("rise_rate_cm_per_interval", "low", 1.0)
RISE_RATE_MODERATE = _b("rise_rate_cm_per_interval", "moderate", 5.0)
RISE_RATE_HIGH = _b("rise_rate_cm_per_interval", "high", 15.0)
RISE_RATE_SEVERE = _b("rise_rate_cm_per_interval", "severe", 30.0)

# Soil saturation (0-1, where available)
SOIL_LOW = _b("soil_saturation", "low", 0.3)
SOIL_MODERATE = _b("soil_saturation", "moderate", 0.5)
SOIL_HIGH = _b("soil_saturation", "high", 0.7)
SOIL_SEVERE = _b("soil_saturation", "severe", 0.85)

# Forecast rainfall contribution thresholds (mm/hr predicted)
FORECAST_RAIN_LOW = _b("forecast_rainfall_mm_hr", "low", 5.0)
FORECAST_RAIN_MODERATE = _b("forecast_rainfall_mm_hr", "moderate", 15.0)
FORECAST_RAIN_HIGH = _b("forecast_rainfall_mm_hr", "high", 35.0)
FORECAST_RAIN_SEVERE = _b("forecast_rainfall_mm_hr", "severe", 60.0)

# Terrain elevation relative to water surface (m)
ELEVATION_HIGH_RISK_M = float(_CFG.get("terrain_elevation_m", 1.2)) + 2.0
ELEVATION_MOD_RISK_M = float(_CFG.get("terrain_elevation_m", 1.2)) + 0.5

# Stale device detection
DEVICE_STALE_AFTER_MINUTES = float(_DEFAULTS.get("device_stale_after_minutes", _CFG.get("device_stale_after_minutes", 5.0)))

# Default soil saturation when sensor unavailable
DEFAULT_SOIL_SATURATION = float(_DEFAULTS.get("soil_saturation", 0.4))
DEFAULT_SOIL_AVAILABLE = bool(_DEFAULTS.get("soil_available", False))

# Score bands
BAND_SEVERE = float(_BANDS.get("severe", 0.75))
BAND_HIGH = float(_BANDS.get("high", 0.50))
BAND_MODERATE = float(_BANDS.get("moderate", 0.25))


def map_physical_to_mithi(physical_level_cm: float) -> float:
    """Map the physical probe reading onto the Mithi simulation stage scale.

    Both scales are ASCENDING depth: 0 cm = dry on the probe, and the low end
    of the Mithi stage. The probe rises to DEMO_INPUT_PHYSICAL_MAX_CM (30 cm)
    when fully submerged, which maps to the top of the Mithi stage.
    This is the single source of truth for the mapping — the forecast service
    and the alert engine's thresholds both use it.
    """
    settings = get_settings()
    pmin = settings.demo_input_physical_min_cm
    pmax = settings.demo_input_physical_max_cm
    if pmax <= pmin:
        raise ValueError("DEMO_INPUT_PHYSICAL_MAX_CM must exceed MIN_CM")
    fraction = (physical_level_cm - pmin) / (pmax - pmin)
    fraction = max(0.0, min(1.0, fraction))
    return settings.demo_input_mithi_min_cm + fraction * (
        settings.demo_input_mithi_max_cm - settings.demo_input_mithi_min_cm
    )


def map_mithi_to_physical(mithi_level_cm: float) -> float:
    """Inverse of map_physical_to_mithi: Mithi stage cm -> physical probe cm."""
    settings = get_settings()
    mmin = settings.demo_input_mithi_min_cm
    mmax = settings.demo_input_mithi_max_cm
    if mmax <= mmin:
        raise ValueError("DEMO_INPUT_MITHI_MAX_CM must exceed MIN_CM")
    fraction = (mithi_level_cm - mmin) / (mmax - mmin)
    fraction = max(0.0, min(1.0, fraction))
    return settings.demo_input_physical_min_cm + fraction * (
        settings.demo_input_physical_max_cm - settings.demo_input_physical_min_cm
    )


def water_level_thresholds(*, probe_domain: bool = True) -> tuple[float, float, float, float]:
    """Band boundaries for the requested water-level domain.

    Both domains are ASCENDING depth, so the raw config bounds (0 = dry,
    30 = submerged) apply directly to physical probe readings. Mithi-stage
    readings need the same boundaries mapped onto the Mithi scale, in the
    same order.
    """
    bounds = (
        WATER_LEVEL_LOW_CM,
        WATER_LEVEL_MODERATE_CM,
        WATER_LEVEL_HIGH_CM,
        WATER_LEVEL_SEVERE_CM,
    )
    if probe_domain:
        return bounds
    return (
        map_physical_to_mithi(bounds[0]),
        map_physical_to_mithi(bounds[1]),
        map_physical_to_mithi(bounds[2]),
        map_physical_to_mithi(bounds[3]),
    )


# ──────────────────────────────────────────────────────────────────────────────
# RISK WEIGHTS (loaded from config — starting assumptions, must be calibrated)
# ──────────────────────────────────────────────────────────────────────────────
WEIGHT_WATER_LEVEL = _w("water_level", 0.35)
WEIGHT_RISE_RATE = _w("rise_rate", 0.30)
WEIGHT_RAINFALL = _w("rainfall", 0.20)
WEIGHT_SOIL = _w("soil", 0.15)

# Forecast horizon weights (for predicted risk)
WEIGHT_FORECAST_RAIN = float(_FW.get("forecast_rainfall", 0.25))
WEIGHT_CURRENT_RISK = float(_FW.get("current_risk", 0.75))

RISK_MODEL_NAME = str(_DEFAULTS.get("model_name", "rule_based_v1"))
RISK_MODEL_VERSION = str(_DEFAULTS.get("model_version", "1.0.0"))
RISK_ASSUMPTIONS = list(
    _DEFAULTS.get(
        "assumptions",
        [
            "Risk weights: 35% water level, 30% rise rate, 20% rainfall, 15% soil saturation",
            "Thresholds are prototype assumptions - not calibrated for Mithi River",
            "Model: rule_based_v1 (not ML-validated)",
        ],
    )
)


@dataclass
class RiskComponent:
    """Single risk factor with its contribution."""
    name: str
    value: float
    normalized: float
    weight: float
    contribution: float
    band: str


@dataclass
class FloodRiskAssessment:
    """
    Structured result of applying flood risk scoring to one reading/forecast.
    """

    risk_level: str           # "LOW" | "MODERATE" | "HIGH" | "SEVERE"
    risk_score: float         # 0.0 - 1.0
    horizon_minutes: int      # 0 = current, 15/30/60 = forecast
    components: list[RiskComponent] = field(default_factory=list)
    predicted_level_cm: float | None = None
    primary_driver: str = ""
    generated_at: str = ""
    model: str = RISK_MODEL_NAME
    model_version: str = RISK_MODEL_VERSION
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "risk_level": self.risk_level,
            "risk_score": round(self.risk_score, 3),
            "horizon_minutes": self.horizon_minutes,
            "components": [
                {
                    "name": c.name,
                    "value": c.value,
                    "normalized": round(c.normalized, 3),
                    "weight": c.weight,
                    "contribution": round(c.contribution, 3),
                    "band": c.band,
                }
                for c in self.components
            ],
            "predicted_level_cm": self.predicted_level_cm,
            "primary_driver": self.primary_driver,
            "generated_at": self.generated_at,
            "model": self.model,
            "model_version": self.model_version,
            "assumptions": self.assumptions,
        }


def score_to_level(total_score: float) -> str:
    """Map a 0-1 risk score onto the four configured bands."""
    if total_score >= BAND_SEVERE:
        return "SEVERE"
    if total_score >= BAND_HIGH:
        return "HIGH"
    if total_score >= BAND_MODERATE:
        return "MODERATE"
    return "LOW"


def _normalize_linear(
    value: float,
    low: float,
    moderate: float,
    high: float,
    severe: float,
    *,
    inverse: bool = False,
) -> tuple[str, float]:
    """
    Normalize a value to 0-1 scale with 4 bands.
    Returns (band_name, normalized_score 0.0-1.0).
    """
    if inverse:
        if value >= low:
            return "LOW", 0.0
        if value >= moderate:
            return "MODERATE", 0.33
        if value >= high:
            return "HIGH", 0.67
        return "SEVERE", 1.0
    else:
        if value >= severe:
            return "SEVERE", 1.0
        if value >= high:
            return "HIGH", 0.67
        if value >= moderate:
            return "MODERATE", 0.33
        if value >= low:
            return "LOW", 0.0
        return "LOW", 0.0


def assess_current_risk(
    *,
    water_level_cm: float,
    rainfall_mm_hr: float,
    rise_rate_cm_per_interval: float,
    soil_saturation: Optional[float] = None,
    soil_available: bool = False,
    terrain_elevation_m: Optional[float] = None,
    device_timestamp: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    probe_domain: bool = True,
) -> FloodRiskAssessment:
    """
    Assess current flood risk from live sensor readings.

    Args:
        water_level_cm: Current water level in cm above the sensor datum
            (0 = dry, 30 = submerged)
        rainfall_mm_hr: Current rainfall intensity in mm/hr
        rise_rate_cm_per_interval: Water level rise rate (cm per sampling interval)
        soil_saturation: Soil saturation 0-1 (optional)
        soil_available: Whether soil sensor is working
        terrain_elevation_m: Ground elevation at sensor (optional)
        device_timestamp: ISO timestamp from device
        lat, lng: Sensor location
        probe_domain: True for physical probe cm (uses the raw config bands),
            False for Mithi-stage cm (bands mapped onto the Mithi scale).

    Returns:
        FloodRiskAssessment with risk_level, score, and explainable components.
    """
    components: list[RiskComponent] = []
    assumptions: list[str] = list(RISK_ASSUMPTIONS)

    # 1. Water level component. Both domains ascend with severity, so the
    # normalising comparator is never inverted.
    wl_band, wl_norm = _normalize_linear(
        water_level_cm,
        *water_level_thresholds(probe_domain=probe_domain),
    )
    wl_contrib = wl_norm * WEIGHT_WATER_LEVEL
    components.append(RiskComponent(
        name="water_level",
        value=water_level_cm,
        normalized=wl_norm,
        weight=WEIGHT_WATER_LEVEL,
        contribution=wl_contrib,
        band=wl_band,
    ))

    # 2. Rise rate component
    rr_band, rr_norm = _normalize_linear(
        rise_rate_cm_per_interval,
        RISE_RATE_LOW,
        RISE_RATE_MODERATE,
        RISE_RATE_HIGH,
        RISE_RATE_SEVERE,
    )
    rr_contrib = rr_norm * WEIGHT_RISE_RATE
    components.append(RiskComponent(
        name="rise_rate",
        value=rise_rate_cm_per_interval,
        normalized=rr_norm,
        weight=WEIGHT_RISE_RATE,
        contribution=rr_contrib,
        band=rr_band,
    ))

    # 3. Rainfall component
    rf_band, rf_norm = _normalize_linear(
        rainfall_mm_hr,
        RAINFALL_LOW,
        RAINFALL_MODERATE,
        RAINFALL_HIGH,
        RAINFALL_SEVERE,
    )
    rf_contrib = rf_norm * WEIGHT_RAINFALL
    components.append(RiskComponent(
        name="rainfall",
        value=rainfall_mm_hr,
        normalized=rf_norm,
        weight=WEIGHT_RAINFALL,
        contribution=rf_contrib,
        band=rf_band,
    ))

    # 4. Soil saturation component
    if soil_available and soil_saturation is not None:
        soil_val = soil_saturation
        soil_avail = True
    else:
        soil_val = DEFAULT_SOIL_SATURATION
        soil_avail = False
        assumptions.append(f"Soil saturation unavailable — using default {DEFAULT_SOIL_SATURATION}")

    soil_band, soil_norm = _normalize_linear(
        soil_val,
        SOIL_LOW,
        SOIL_MODERATE,
        SOIL_HIGH,
        SOIL_SEVERE,
    )
    soil_contrib = soil_norm * WEIGHT_SOIL
    components.append(RiskComponent(
        name="soil_saturation",
        value=soil_val,
        normalized=soil_norm,
        weight=WEIGHT_SOIL,
        contribution=soil_contrib,
        band=soil_band,
    ))

    # 5. Terrain elevation modifier (optional)
    if terrain_elevation_m is not None:
        if terrain_elevation_m <= ELEVATION_HIGH_RISK_M:
            # Very low lying — bump risk score slightly
            assumptions.append(f"Low terrain elevation ({terrain_elevation_m:.1f}m) increases risk")
        elif terrain_elevation_m <= ELEVATION_MOD_RISK_M:
            assumptions.append(f"Moderate terrain elevation ({terrain_elevation_m:.1f}m)")

    # Calculate total score
    total_score = sum(c.contribution for c in components)
    total_score = min(1.0, total_score)

    # Worst-driver floor. The weighted sum can only reach 1.0 when every driver
    # saturates, so a single extreme input stayed quiet: 2 cm of water (SEVERE
    # band) scored 0.35 x 1.0 = 0.35 and reported MODERATE. The overall level
    # must never read lower than the worst single driver, because that is the
    # band the explanation names.
    worst_normalized = max((c.normalized for c in components), default=0.0)
    if worst_normalized > total_score:
        total_score = worst_normalized
        assumptions.append(
            "Overall level raised to the worst single driver "
            f"({max(components, key=lambda c: c.normalized).band})"
        )

    # Determine risk level from configured bands
    risk_level = score_to_level(total_score)

    # Find primary driver
    primary = max(components, key=lambda c: c.contribution)
    primary_driver = primary.name.replace("_", " ").title()

    # Add driver-specific reasoning
    if primary.name == "water_level":
        assumptions.append(f"Primary driver: Water level at {water_level_cm:.0f} cm ({wl_band})")
    elif primary.name == "rise_rate":
        assumptions.append(f"Primary driver: Rapid rise at {rise_rate_cm_per_interval:.1f} cm/interval ({rr_band})")
    elif primary.name == "rainfall":
        assumptions.append(f"Primary driver: Heavy rainfall at {rainfall_mm_hr:.1f} mm/hr ({rf_band})")
    else:
        assumptions.append(f"Primary driver: Soil saturation ({soil_band})")

    return FloodRiskAssessment(
        risk_level=risk_level,
        risk_score=round(total_score, 3),
        horizon_minutes=0,
        components=components,
        predicted_level_cm=water_level_cm,
        primary_driver=primary_driver,
        generated_at=datetime.now(timezone.utc).isoformat(),
        assumptions=assumptions,
    )


def assess_forecast_risk(
    *,
    current_risk: FloodRiskAssessment,
    forecast_rainfall_mm_hr: float,
    forecast_horizon_minutes: int,
    predicted_level_cm: float,
) -> FloodRiskAssessment:
    """
    Assess flood risk for a forecast horizon.

    Combines current risk trajectory with forecast rainfall.
    """
    components = list(current_risk.components)
    assumptions = list(current_risk.assumptions)

    # Add forecast rainfall component
    fc_band, fc_norm = _normalize_linear(
        forecast_rainfall_mm_hr,
        FORECAST_RAIN_LOW,
        FORECAST_RAIN_MODERATE,
        FORECAST_RAIN_HIGH,
        FORECAST_RAIN_SEVERE,
    )
    fc_contrib = fc_norm * WEIGHT_FORECAST_RAIN
    components.append(RiskComponent(
        name="forecast_rainfall",
        value=forecast_rainfall_mm_hr,
        normalized=fc_norm,
        weight=WEIGHT_FORECAST_RAIN,
        contribution=fc_contrib,
        band=fc_band,
    ))

    assumptions.append(f"Forecast rainfall: {forecast_rainfall_mm_hr:.1f} mm/hr ({fc_band})")
    assumptions.append(f"Predicted water level at +{forecast_horizon_minutes}min: {predicted_level_cm:.0f} cm")

    # Combine: weighted blend of current risk and forecast rain
    current_score = current_risk.risk_score
    combined_score = (WEIGHT_CURRENT_RISK * current_score) + fc_contrib
    combined_score = min(1.0, combined_score)

    # Same worst-driver floor as the current-state assessment: WEIGHT_CURRENT_RISK
    # is a damping factor, so it must not hide a component that is already
    # SEVERE on its own.
    worst_normalized = max((c.normalized for c in components), default=0.0)
    if worst_normalized > combined_score:
        combined_score = worst_normalized

    risk_level = score_to_level(combined_score)

    # Primary driver
    primary = max(components, key=lambda c: c.contribution)
    primary_driver = primary.name.replace("_", " ").title()

    return FloodRiskAssessment(
        risk_level=risk_level,
        risk_score=round(combined_score, 3),
        horizon_minutes=forecast_horizon_minutes,
        components=components,
        predicted_level_cm=predicted_level_cm,
        primary_driver=primary_driver,
        generated_at=datetime.now(timezone.utc).isoformat(),
        assumptions=assumptions,
    )


def assess_scenario_risk(
    *,
    scenario_rainfall_mm_hr: float,
    scenario_initial_level_cm: float,
    scenario_drainage_pct: float,
    forecast_horizon_minutes: int,
    predicted_level_cm: float,
) -> FloodRiskAssessment:
    """
    Assess risk for a what-if scenario.
    Uses simplified assumptions (stated in output).
    """
    assumptions = [
        "SCENARIO MODE — Simplified what-if assumptions:",
        f"  Rainfall intensity: {scenario_rainfall_mm_hr} mm/hr",
        f"  Initial water level: {scenario_initial_level_cm} cm",
        f"  Drainage capacity: {scenario_drainage_pct}%",
        "  Runoff coefficient: 0.7 (assumed)",
        "  Level rise per mm excess rain: 0.84 cm (assumed)",
        "  Not a hydraulic model — elevation-based inundation only",
    ]

    # Simplified: treat scenario as "current" with modified inputs
    # Effective rainfall considers drainage
    effective_rain = scenario_rainfall_mm_hr * (1.0 - scenario_drainage_pct / 100.0) * 0.7

    # Rise rate estimate from scenario
    rise_rate = effective_rain * 0.84 / (60 / 5)  # cm per 5-min interval

    return assess_current_risk(
        water_level_cm=predicted_level_cm,
        rainfall_mm_hr=effective_rain,
        rise_rate_cm_per_interval=rise_rate,
        terrain_elevation_m=None,
        device_timestamp=None,
        # Scenario levels come from Mithi-stage presets (100-165 cm).
        probe_domain=False,
    )


def calculate_rise_rate(
    current_level: float,
    previous_level: float,
    interval_seconds: float = 300.0,
) -> float:
    """
    Calculate rise rate in cm per 5-minute interval.

    Every water-level domain is ascending depth (0 = dry), so a rising level
    is a rising number. There is no direction flag to get wrong.
    """
    if interval_seconds <= 0:
        return 0.0
    intervals = interval_seconds / 300.0  # normalize to 5-min intervals
    return max(0.0, (current_level - previous_level) / intervals)


def get_risk_color(risk_level: str) -> str:
    """Return CSS color class for risk level."""
    colors = {
        "LOW": "risk-low",
        "MODERATE": "risk-moderate",
        "HIGH": "risk-high",
        "SEVERE": "risk-severe",
    }
    return colors.get(risk_level, "risk-low")


def get_risk_label(risk_level: str) -> str:
    """Return human-readable label for risk level."""
    labels = {
        "LOW": "Low",
        "MODERATE": "Moderate",
        "HIGH": "High",
        "SEVERE": "Severe",
    }
    return labels.get(risk_level, "Unknown")