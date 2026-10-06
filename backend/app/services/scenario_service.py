"""
FLOODGUARD AI — Scenario Service
=================================
What-if scenario simulator for flood prediction.
Allows users to adjust rainfall, initial water level, drainage capacity
and see predicted flood extent.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import structlog
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.floodguard import (
    SensorNode,
    TelemetryReading,
    Scenario,
    Forecast,
    RiskAssessment,
    SourceType,
)
from app.services.forecast_service import forecast_service
from app.services.flood_simulation import flood_simulation_engine
from app.services.flood_risk import assess_scenario_risk

logger = structlog.get_logger()

# Scenario presets
SCENARIO_PRESETS = {
    "normal_monsoon": {
        "name": "Normal Monsoon Day",
        "rainfall_mm_hr": 25.0,
        "initial_level_cm": 100.0,
        "drainage_capacity_pct": 90.0,
        "duration_minutes": 60,
    },
    "heavy_rain": {
        "name": "Heavy Rain",
        "rainfall_mm_hr": 50.0,
        "initial_level_cm": 150.0,
        "drainage_capacity_pct": 70.0,
        "duration_minutes": 60,
    },
    "extreme_rain": {
        "name": "Extreme Rainfall with Poor Drainage",
        "rainfall_mm_hr": 85.0,
        "initial_level_cm": 165.0,
        "drainage_capacity_pct": 35.0,
        "duration_minutes": 60,
    },
}

# Simplified hydraulic assumptions (stated in UI)
SCENARIO_ASSUMPTIONS = {
    "runoff_coefficient": 0.7,
    "level_rise_per_mm_excess_rain_cm": 0.84,
    "max_level_cm": 300.0,
    "description": (
        "Simplified what-if assumptions (not a hydraulic model):\n"
        "1. Runoff coefficient: 0.7 (70% of rainfall becomes runoff)\n"
        "2. Excess rainfall = rainfall × (1 - drainage_capacity/100) × runoff_coefficient\n"
        "3. Water level rise = excess_rainfall × 0.84 cm/mm\n"
        "4. Level capped at 300 cm\n"
        "5. Flood extent from elevation-based inundation, not hydraulic routing"
    ),
}


def _component(risk, name: str) -> float:
    """Extract one component's 0-1 contribution from a risk assessment."""
    for c in risk.components:
        if c.name == name:
            return round(float(c.normalized), 4)
    return 0.0


class ScenarioService:
    """
    Manages what-if scenario creation, execution, and comparison.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    async def create_scenario(
        self,
        name: Optional[str],
        preset: Optional[str],
        rainfall_mm_hr: float,
        initial_level_cm: float,
        drainage_capacity_pct: float,
        duration_minutes: int,
    ) -> Scenario:
        """Create and run a new what-if scenario."""
        # Use preset if provided
        if preset and preset in SCENARIO_PRESETS:
            p = SCENARIO_PRESETS[preset]
            rainfall_mm_hr = p["rainfall_mm_hr"]
            initial_level_cm = p["initial_level_cm"]
            drainage_capacity_pct = p["drainage_capacity_pct"]
            duration_minutes = p["duration_minutes"]
            preset_name = preset
        else:
            preset_name = None

        # Build assumptions dict
        assumptions = {
            **SCENARIO_ASSUMPTIONS,
            "rainfall_mm_hr": rainfall_mm_hr,
            "initial_level_cm": initial_level_cm,
            "drainage_capacity_pct": drainage_capacity_pct,
            "duration_minutes": duration_minutes,
            "preset": preset_name,
        }

        # Create scenario record
        async with AsyncSessionLocal() as db:
            scenario = Scenario(
                name=name or f"Scenario {datetime.now(timezone.utc).strftime('%H:%M')}",
                preset=preset_name,
                rainfall_mm_hr=rainfall_mm_hr,
                initial_level_cm=initial_level_cm,
                drainage_capacity_pct=drainage_capacity_pct,
                duration_minutes=duration_minutes,
                assumptions=assumptions,
                status="running",
            )
            db.add(scenario)
            await db.commit()
            await db.refresh(scenario)

        # Run scenario asynchronously
        asyncio.create_task(self._run_scenario(scenario.id))

        return scenario

    async def _run_scenario(self, scenario_id: int) -> None:
        """Execute scenario simulation in background."""
        try:
            async with AsyncSessionLocal() as db:
                scenario = await db.get(Scenario, scenario_id)
                if not scenario:
                    return

                # Generate scenario forecasts
                forecasts = await self._generate_scenario_forecasts(scenario)
                
                # Run flood simulation
                sim_results = await flood_simulation_engine.run_for_time_steps(
                    forecasts_by_node=forecasts,
                    scenario_id=scenario.id,
                    input_source="scenario",
                )

                # Update scenario status
                scenario.status = "completed"
                scenario.completed_at = datetime.now(timezone.utc)
                await db.commit()

                # Publish completion event
                await self._publish_scenario_event(scenario.id, "completed", sim_results)

        except Exception as exc:
            logger.error("scenario_run_failed", scenario_id=scenario_id, error=str(exc))
            async with AsyncSessionLocal() as db:
                scenario = await db.get(Scenario, scenario_id)
                if scenario:
                    scenario.status = "failed"
                    scenario.error_message = str(exc)
                    await db.commit()
            
            await self._publish_scenario_event(scenario_id, "failed", {"error": str(exc)})

    async def _generate_scenario_forecasts(self, scenario: Scenario) -> dict[int, list[Forecast]]:
        """
        Generate forecast water levels for scenario.
        Uses simplified physics: initial_level + accumulated_rise_over_time.
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SensorNode).where(SensorNode.is_active == True)
            )
            nodes = result.scalars().all()

        # Calculate effective rainfall
        drainage_factor = 1.0 - (scenario.drainage_capacity_pct / 100.0)
        effective_rain = scenario.rainfall_mm_hr * drainage_factor * SCENARIO_ASSUMPTIONS["runoff_coefficient"]
        
        # Level rise per minute
        rise_per_min = effective_rain * SCENARIO_ASSUMPTIONS["level_rise_per_mm_excess_rain_cm"] / 60.0

        forecasts_by_node = {}
        
        for node in nodes:
            node_forecasts = []
            
            for horizon in [0, 15, 30, 60]:
                if horizon == 0:
                    predicted = scenario.initial_level_cm
                else:
                    predicted = scenario.initial_level_cm + (rise_per_min * horizon)
                    predicted = min(predicted, SCENARIO_ASSUMPTIONS["max_level_cm"])

                # Assess risk for this horizon
                risk = assess_scenario_risk(
                    scenario_rainfall_mm_hr=scenario.rainfall_mm_hr,
                    scenario_initial_level_cm=scenario.initial_level_cm,
                    scenario_drainage_pct=scenario.drainage_capacity_pct,
                    forecast_horizon_minutes=horizon,
                    predicted_level_cm=predicted,
                )

                forecast = Forecast(
                    node_id=node.id,
                    model_id=None,
                    scenario_id=scenario.id,
                    made_at=datetime.now(timezone.utc),
                    target_time=datetime.now(timezone.utc) + timedelta(minutes=horizon),
                    horizon_minutes=horizon,
                    predicted_level_cm=round(predicted, 1),
                    lower_bound_cm=round(predicted - 10, 1),
                    upper_bound_cm=round(predicted + 10, 1),
                    input_source="scenario",
                    status="ok",
                )
                # Forecasts MUST be persisted here: the caller reads them back
                # for scenario detail, baseline comparison and predicted-risk
                # alerts. Previously they were constructed and silently dropped.
                db.add(forecast)
                await db.flush()

                db.add(
                    RiskAssessment(
                        node_id=node.id,
                        forecast_id=forecast.id,
                        scenario_id=scenario.id,
                        horizon_minutes=horizon,
                        risk_score=risk.risk_score,
                        risk_level=risk.risk_level,
                        predicted_level_cm=round(predicted, 1),
                        level_component=_component(risk, "water_level"),
                        rise_rate_component=_component(risk, "rise_rate"),
                        rainfall_component=_component(risk, "rainfall"),
                        soil_component=_component(risk, "soil_saturation"),
                        primary_driver=risk.primary_driver,
                    )
                )
                node_forecasts.append(forecast)

            forecasts_by_node[node.id] = node_forecasts

        await db.commit()
        return forecasts_by_node

    async def compare_with_baseline(
        self, scenario_id: int
    ) -> dict:
        """Compare scenario results with live baseline."""
        async with AsyncSessionLocal() as db:
            scenario = await db.get(Scenario, scenario_id)
            if not scenario:
                return {"error": "Scenario not found"}

            # Get baseline (live) forecasts
            baseline_forecasts = {}
            result = await db.execute(
                select(SensorNode).where(SensorNode.is_active == True)
            )
            nodes = result.scalars().all()

            for node in nodes:
                result = await db.execute(
                    select(Forecast)
                    .where(
                        Forecast.node_id == node.id,
                        Forecast.scenario_id.is_(None),
                        Forecast.status == "ok",
                    )
                    .order_by(desc(Forecast.made_at))
                    .limit(4)  # 0, 15, 30, 60
                )
                forecasts = result.scalars().all()
                if forecasts:
                    baseline_forecasts[node.id] = list(reversed(forecasts))

            # Get scenario forecasts
            scenario_forecasts = {}
            for node in nodes:
                result = await db.execute(
                    select(Forecast)
                    .where(
                        Forecast.node_id == node.id,
                        Forecast.scenario_id == scenario_id,
                    )
                    .order_by(Forecast.horizon_minutes)
                )
                forecasts = result.scalars().all()
                if forecasts:
                    scenario_forecasts[node.id] = forecasts

            # Compare
            comparison = self._compute_comparison(baseline_forecasts, scenario_forecasts)
            
            return comparison

    def _compute_comparison(
        self, baseline: dict[int, list[Forecast]], scenario: dict[int, list[Forecast]]
    ) -> dict:
        """Compute comparison metrics between baseline and scenario."""
        node_comparisons = []
        total_additional_flooded_area = 0
        total_additional_roads = 0
        total_additional_buildings = 0
        max_risk_baseline = "low"
        max_risk_scenario = "low"

        for node_id in set(baseline.keys()) | set(scenario.keys()):
            base = {f.horizon_minutes: f for f in baseline.get(node_id, [])}
            scen = {f.horizon_minutes: f for f in scenario.get(node_id, [])}

            peak_base = max((f.predicted_level_cm for f in base.values()), default=0)
            peak_scen = max((f.predicted_level_cm for f in scen.values()), default=0)
            
            node_comparisons.append({
                "node_id": node_id,
                "peak_level_baseline_cm": peak_base,
                "peak_level_scenario_cm": peak_scen,
                "level_difference_cm": round(peak_scen - peak_base, 1),
            })

            # Risk comparison (simplified)
            risk_order = {"low": 0, "moderate": 1, "high": 2, "severe": 3}
            # Would need flood layers to compute actual flooded area difference

        # Generate summary
        if node_comparisons:
            max_level_diff = max(c["level_difference_cm"] for c in node_comparisons)
            avg_level_diff = sum(c["level_difference_cm"] for c in node_comparisons) / len(node_comparisons)
        else:
            max_level_diff = 0
            avg_level_diff = 0

        summary = (
            f"Under the scenario, peak water levels rise by up to {max_level_diff:.0f} cm "
            f"(average {avg_level_diff:.0f} cm across nodes). "
        )

        # Corridor roll-ups derived from the same per-node peaks, so they cannot
        # disagree with `node_comparisons`. The console's BASELINE vs SCENARIO
        # tiles read exactly these keys; they were absent from the response, so
        # all four tiles rendered an em dash even after a run had genuinely
        # succeeded. Derived, never invented: None when there is nothing to
        # roll up, which the UI renders as a dash rather than a zero.
        baseline_peaks = [c["peak_level_baseline_cm"] for c in node_comparisons]
        scenario_peaks = [c["peak_level_scenario_cm"] for c in node_comparisons]
        baseline_peak = max(baseline_peaks) if baseline_peaks else None
        scenario_peak = max(scenario_peaks) if scenario_peaks else None
        baseline_mean = (
            round(sum(baseline_peaks) / len(baseline_peaks), 1) if baseline_peaks else None
        )
        scenario_mean = (
            round(sum(scenario_peaks) / len(scenario_peaks), 1) if scenario_peaks else None
        )

        return {
            "node_comparisons": node_comparisons,
            "baseline_peak_level_cm": baseline_peak,
            "baseline_mean_level_cm": baseline_mean,
            "scenario_peak_level_cm": scenario_peak,
            "scenario_mean_level_cm": scenario_mean,
            # Never computed: the inundation engine reports area per
            # flood_layer row and no scenario roll-up exists. Declared as null
            # so the UI omits the tile instead of rendering "0.000 km²".
            "scenario_flooded_area_m2": None,
            "peak_level_difference_cm": round(max_level_diff, 1),
            "average_level_difference_cm": round(avg_level_diff, 1),
            "summary": summary,
            "assumptions": SCENARIO_ASSUMPTIONS,
        }

    async def get_scenario(self, scenario_id: int) -> Optional[Scenario]:
        """Get scenario by ID."""
        async with AsyncSessionLocal() as db:
            return await db.get(Scenario, scenario_id)

    async def list_scenarios(self, limit: int = 20) -> list[Scenario]:
        """List recent scenarios."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Scenario)
                .order_by(desc(Scenario.created_at))
                .limit(limit)
            )
            return list(result.scalars().all())

    async def get_presets(self) -> dict:
        """Get available scenario presets."""
        return {
            "presets": SCENARIO_PRESETS,
            "assumptions": SCENARIO_ASSUMPTIONS,
        }

    async def _publish_scenario_event(
        self, scenario_id: int, status: str, data: dict
    ) -> None:
        """Publish scenario event for real-time updates."""
        try:
            event = {
                "event": "flood.scenario.updated",
                "payload": {
                    "scenario_id": scenario_id,
                    "status": status,
                    "data": data,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "scenario_service",
            }
            from app.services.websocket import publish_realtime_event

            await publish_realtime_event(event)
        except Exception as exc:
            logger.warning("scenario_publish_failed", error=str(exc))


# Global instance
scenario_service = ScenarioService()