"""
FLOODGUARD AI — Scenarios API
==============================
What-if scenario management: create, run, compare, and list scenarios.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.database import get_db
from app.models.floodguard import Scenario, Forecast, FloodLayer
from app.services.scenario_service import scenario_service, SCENARIO_PRESETS

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


@router.get("/presets")
async def get_scenario_presets(
    _: dict = Depends(get_current_user),
):
    """Get available scenario presets and assumptions."""
    presets = await scenario_service.get_presets()
    return presets


@router.post("/run")
async def run_scenario(
    name: str | None = None,
    preset: str | None = None,
    rainfall_mm_hr: float = 50.0,
    initial_level_cm: float = 150.0,
    drainage_capacity_pct: float = 70.0,
    duration_minutes: int = 60,
    _: dict = Depends(get_current_user),
):
    """Create and run a new what-if scenario."""
    if preset and preset not in SCENARIO_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {preset}")

    scenario = await scenario_service.create_scenario(
        name=name,
        preset=preset,
        rainfall_mm_hr=rainfall_mm_hr,
        initial_level_cm=initial_level_cm,
        drainage_capacity_pct=drainage_capacity_pct,
        duration_minutes=duration_minutes,
    )

    return {
        "scenario_id": scenario.id,
        "status": scenario.status,
        "message": "Scenario started. Poll for completion or subscribe to WebSocket.",
    }


@router.get("/{scenario_id}")
async def get_scenario(
    scenario_id: int,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get scenario details with forecasts and flood layers."""
    scenario = await scenario_service.get_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Get forecasts
    result = await db.execute(
        select(Forecast)
        .where(Forecast.scenario_id == scenario_id)
        .order_by(Forecast.node_id, Forecast.horizon_minutes)
    )
    forecasts = result.scalars().all()

    # Get flood layers
    layer_result = await db.execute(
        select(FloodLayer)
        .where(FloodLayer.scenario_id == scenario_id)
        .order_by(FloodLayer.time_step_minutes, FloodLayer.layer_type)
    )
    layers = layer_result.scalars().all()

    return {
        "id": scenario.id,
        "name": scenario.name,
        "preset": scenario.preset,
        "rainfall_mm_hr": scenario.rainfall_mm_hr,
        "initial_level_cm": scenario.initial_level_cm,
        "drainage_capacity_pct": scenario.drainage_capacity_pct,
        "duration_minutes": scenario.duration_minutes,
        "assumptions": scenario.assumptions,
        "status": scenario.status,
        "error_message": scenario.error_message,
        "created_at": scenario.created_at.isoformat(),
        "completed_at": scenario.completed_at.isoformat() if scenario.completed_at else None,
        "forecasts": [
            {
                "node_id": f.node_id,
                "horizon_minutes": f.horizon_minutes,
                "predicted_level_cm": f.predicted_level_cm,
                "lower_bound_cm": f.lower_bound_cm,
                "upper_bound_cm": f.upper_bound_cm,
                "target_time": f.target_time.isoformat(),
                "status": f.status,
            }
            for f in forecasts
        ],
        "flood_layers": [
            {
                "time_step_minutes": l.time_step_minutes,
                "layer_type": l.layer_type,
                "max_depth_m": l.max_depth_m,
                "flooded_area_m2": l.flooded_area_m2,
                "geojson": l.geojson,
            }
            for l in layers
        ],
    }


@router.get("/{scenario_id}/compare")
async def compare_scenario(
    scenario_id: int,
    _: dict = Depends(get_current_user),
):
    """Compare scenario with live baseline."""
    comparison = await scenario_service.compare_with_baseline(scenario_id)
    return comparison


@router.get("")
async def list_scenarios(
    limit: int = Query(20, ge=1, le=100),
    _: dict = Depends(get_current_user),
):
    """List recent scenarios."""
    scenarios = await scenario_service.list_scenarios(limit)
    return {
        "scenarios": [
            {
                "id": s.id,
                "name": s.name,
                "preset": s.preset,
                "rainfall_mm_hr": s.rainfall_mm_hr,
                "initial_level_cm": s.initial_level_cm,
                "drainage_capacity_pct": s.drainage_capacity_pct,
                "duration_minutes": s.duration_minutes,
                "status": s.status,
                "created_at": s.created_at.isoformat(),
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            }
            for s in scenarios
        ]
    }


@router.delete("/{scenario_id}")
async def delete_scenario(
    scenario_id: int,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a scenario and its associated data."""
    scenario = await db.get(Scenario, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")

    await db.delete(scenario)
    await db.commit()
    return {"message": "Scenario deleted"}