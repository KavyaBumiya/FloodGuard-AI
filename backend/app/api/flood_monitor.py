"""
FLOODGUARD AI — Flood Monitoring API
=====================================
Endpoints for current conditions, forecasts, flood layers, risk assessments.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, literal, select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from geoalchemy2.elements import WKTElement

from app.auth.dependencies import get_current_user
from app.core.database import get_db
from app.models.floodguard import (
    SensorNode,
    TelemetryReading,
    Forecast,
    FloodLayer,
    FloodImpact,
    RiskAssessment,
    Road,
    Building,
    SourceType,
)
from app.services.forecast_service import forecast_service
from app.services.flood_simulation import flood_simulation_engine
from app.services.flood_risk import (
    DEVICE_STALE_AFTER_MINUTES,
    assess_current_risk,
    get_risk_color,
    get_risk_label,
)
from app.services.telemetry import get_latest_reading, is_recent_physical_reading
from app.core.config import get_settings

router = APIRouter(prefix="/api/flood", tags=["flood"])

# Risk is persisted uppercase by the risk engine. The API contract (and the
# dashboard) uses lowercase, and level comparisons must not depend on casing.
RISK_ORDER = {"low": 0, "moderate": 1, "high": 2, "severe": 3}


def normalize_risk(value: str | None) -> str:
    """Normalise a stored risk level to the lowercase API contract."""
    if not value:
        return "unknown"
    return str(value).strip().lower()


async def _impact_summary_for_layer(
    db: AsyncSession, layer: FloodLayer
) -> dict:
    """Aggregate the per-feature impacts stored against one flood layer."""
    rows = await db.execute(
        select(
            FloodImpact.feature_type,
            FloodImpact.estimated_depth_m,
            FloodImpact.risk_level,
        ).where(FloodImpact.flood_layer_id == layer.id)
    )
    buildings: list[dict] = []
    roads: list[dict] = []
    for feature_type, depth, risk in rows.all():
        entry = {"depth_m": depth, "risk_level": risk}
        (buildings if feature_type == "building" else roads).append(entry)

    depths = [e["depth_m"] for e in buildings + roads if e["depth_m"] is not None]
    return {
        "time_step_minutes": layer.time_step_minutes,
        "flooded_area_m2": layer.flooded_area_m2,
        "max_depth_m": layer.max_depth_m,
        "affected_building_count": len(buildings),
        "affected_road_count": len(roads),
        "depth_range_m": [min(depths), max(depths)] if depths else None,
        "buildings_by_risk": _count_by_risk(buildings),
        "roads_by_risk": _count_by_risk(roads),
        "input_source": layer.input_source,
        "scenario_id": layer.scenario_id,
        "generated_at": layer.generated_at.isoformat(),
        "note": "MODEL ESTIMATE — not an official flood zone.",
    }


def _count_by_risk(entries: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for entry in entries:
        key = entry.get("risk_level") or "none"
        counts[key] = counts.get(key, 0) + 1
    return counts


@router.get("/impact-summary")
async def get_impact_summary(
    scenario_id: Optional[int] = None,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-timestep infrastructure impact for the timeline control.

    Drives the NOW/+15/+30/+60 readout shared by the 2D map, the 3D twin and
    the dashboard. Returns `null` for a timestep with no stored layer yet.
    """
    query = select(FloodLayer).where(FloodLayer.layer_type == "depth")
    if scenario_id is not None:
        query = query.where(FloodLayer.scenario_id == scenario_id)
    else:
        query = query.where(FloodLayer.scenario_id.is_(None))
    query = query.order_by(desc(FloodLayer.generated_at))

    latest: dict[int, FloodLayer] = {}
    for layer in (await db.execute(query)).scalars().all():
        latest.setdefault(layer.time_step_minutes, layer)

    steps = []
    for minutes in sorted(latest):
        steps.append(await _impact_summary_for_layer(db, latest[minutes]))

    return {
        "scenario_id": scenario_id,
        "steps": steps,
        "source": steps[0]["input_source"] if steps else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/nodes")
async def list_flood_nodes(
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all flood sensor nodes with latest readings."""
    # Read coordinates with PostGIS ST_X/ST_Y rather than the GeoAlchemy2
    # .x/.y descriptor: geometry written via ST_GeomFromEWKT has no SRID
    # metadata, so the lazy descriptor raises AttributeError on access.
    result = await db.execute(
        select(
            SensorNode,
            func.ST_Y(SensorNode.location).label("lat"),
            func.ST_X(SensorNode.location).label("lng"),
        )
        .where(
            SensorNode.is_active == True,
            SensorNode.node_type == "physical",
        )
        .order_by(SensorNode.node_code)
    )
    rows = result.all()

    node_data = []
    for node, lat, lng in rows:
        latest = await get_latest_reading(
            db, node.id, sources=[SourceType.IOT.value, SourceType.SIMULATED.value]
        )
        has_live_reading = is_recent_physical_reading(node, latest)
        if not has_live_reading:
            latest = None

        # Get current risk
        risk_result = await db.execute(
            select(RiskAssessment)
            .where(
                RiskAssessment.node_id == node.id,
                RiskAssessment.horizon_minutes == 0,
            )
            .order_by(desc(RiskAssessment.assessed_at))
            .limit(1)
        )
        current_risk = risk_result.scalar_one_or_none() if has_live_reading else None

        # Get forecasts
        forecast_result = await db.execute(
            select(Forecast)
            .where(
                Forecast.node_id == node.id,
                Forecast.scenario_id.is_(None),
                Forecast.status == "ok",
            )
            .order_by(Forecast.horizon_minutes)
        )
        forecasts = forecast_result.scalars().all() if has_live_reading else []

        node_data.append({
            "node_code": node.node_code,
            "name": node.name,
            "node_type": node.node_type,
            "role": node.role,
            "location": {
                "lat": float(lat) if lat is not None else None,
                "lng": float(lng) if lng is not None else None,
            },
            "reference_elevation_m": node.reference_elevation_m,
            "warning_threshold_cm": node.warning_threshold_cm,
            "latest_reading": {
                "water_level_cm": latest.water_level_cm if latest else None,
                "rainfall_mm_hr": latest.rainfall_mm_hr if latest else None,
                "timestamp": latest.recorded_at.isoformat() if latest else None,
                "source": latest.source if latest else None,
            } if latest else None,
            "current_risk": {
                "risk_level": normalize_risk(current_risk.risk_level) if current_risk else "unknown",
                "risk_score": current_risk.risk_score if current_risk else None,
                "primary_driver": current_risk.primary_driver if current_risk else None,
            } if current_risk else None,
            "forecasts": [
                {
                    "horizon_minutes": f.horizon_minutes,
                    "predicted_level_cm": f.predicted_level_cm,
                    "lower_bound_cm": f.lower_bound_cm,
                    "upper_bound_cm": f.upper_bound_cm,
                    "target_time": f.target_time.isoformat(),
                    "status": f.status,
                }
                for f in forecasts
            ],
        })

    physical_input = None
    physical_rows = await db.execute(
        select(SensorNode).where(
            SensorNode.node_type == "physical",
            SensorNode.is_active == True,
        )
    )
    configured_input_code = get_settings().demo_input_node_code.strip().upper()
    for physical_node in physical_rows.scalars().all():
        if configured_input_code and physical_node.node_code != configured_input_code:
            continue
        reading = await get_latest_reading(
            db, physical_node.id, sources=[SourceType.IOT.value]
        )
        if reading is None:
            continue
        if physical_input is None or reading.recorded_at > physical_input[1].recorded_at:
            physical_input = (physical_node, reading)

    settings = get_settings()
    input_node, input_reading = physical_input or (None, None)
    input_is_live = bool(
        input_node is not None
        and input_reading is not None
        and is_recent_physical_reading(input_node, input_reading)
    )
    live_reading = input_reading if input_is_live else None
    mapped_level = (
        forecast_service.map_demo_level(live_reading.water_level_cm)
        if live_reading
        else None
    )
    mapped_forecasts = []
    mapped_risk = None
    if input_node is not None and live_reading is not None:
        forecast_rows = await db.execute(
            select(Forecast)
            .where(
                Forecast.node_id == input_node.id,
                Forecast.input_source == "iot-demo",
                Forecast.scenario_id.is_(None),
                Forecast.status == "ok",
            )
            .order_by(desc(Forecast.made_at))
        )
        seen_horizons: set[int] = set()
        for forecast in forecast_rows.scalars().all():
            if forecast.horizon_minutes in seen_horizons:
                continue
            seen_horizons.add(forecast.horizon_minutes)
            mapped_forecasts.append(
                {
                    "horizon_minutes": forecast.horizon_minutes,
                    "predicted_level_cm": forecast.predicted_level_cm,
                    "lower_bound_cm": forecast.lower_bound_cm,
                    "upper_bound_cm": forecast.upper_bound_cm,
                    "target_time": forecast.target_time.isoformat(),
                    "status": forecast.status,
                }
            )
        if mapped_level is not None:
            mithi_risk = assess_current_risk(
                water_level_cm=mapped_level,
                rainfall_mm_hr=live_reading.rainfall_mm_hr,
                rise_rate_cm_per_interval=0.0,
            )
            mapped_risk = {
                "risk_level": normalize_risk(mithi_risk.risk_level),
                "risk_score": mithi_risk.risk_score,
            }
    return {
        "nodes": node_data,
        "simulation_input": {
            "status": "live" if input_is_live else "waiting",
            "label": "LIVE IoT INPUT",
            "location_label": "Physical IoT Sensor",
            "simulation_label": "MITHI RIVER DIGITAL TWIN - Mumbai Simulation",
            "driver_label": "Driven by Live IoT Input",
            "node_code": input_node.node_code if input_node else None,
            "water_level_cm": live_reading.water_level_cm if live_reading else None,
            "rainfall_mm_hr": live_reading.rainfall_mm_hr if live_reading else None,
            "mithi_water_level_cm": mapped_level,
            "risk": mapped_risk,
            "forecasts": mapped_forecasts,
            "forecast_status": "ok" if mapped_forecasts else (
                "insufficient_live_iot_data" if input_is_live else "waiting_for_iot"
            ),
            "timestamp": live_reading.recorded_at.isoformat() if live_reading else None,
            "mapping": {
                "label": "CONFIGURED CALIBRATION / SIMULATION MAPPING",
                "physical_min_cm": settings.demo_input_physical_min_cm,
                "physical_max_cm": settings.demo_input_physical_max_cm,
                "mithi_min_cm": settings.demo_input_mithi_min_cm,
                "mithi_max_cm": settings.demo_input_mithi_max_cm,
                "rainfall_factor": settings.demo_input_mithi_rainfall_factor,
                "configured_node_code": configured_input_code or None,
                "note": "Configurable calibration for mapping physical sensor input into the Mithi simulation; not a physical hydraulic transfer.",
            },
        },
    }


@router.get("/nodes/{node_code}")
async def get_flood_node(
    node_code: str,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information for a specific flood node."""
    row = (
        await db.execute(
            select(
                SensorNode,
                func.ST_Y(SensorNode.location).label("lat"),
                func.ST_X(SensorNode.location).label("lng"),
            )
            .where(SensorNode.node_code == node_code.upper())
            .limit(1)
        )
    ).first()
    if row is None:
        node = None
        lat = lng = None
    else:
        node, lat, lng = row

    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")

    latest = await get_latest_reading(
        db, node.id, sources=[SourceType.IOT.value, SourceType.SIMULATED.value]
    )
    has_live_reading = is_recent_physical_reading(node, latest)
    if not has_live_reading:
        latest = None

    # Get risk assessments for all horizons
    risk_result = await db.execute(
        select(RiskAssessment)
        .where(RiskAssessment.node_id == node.id)
        .order_by(RiskAssessment.horizon_minutes, desc(RiskAssessment.assessed_at))
    )
    risks = risk_result.scalars().all() if has_live_reading else []

    # Get forecasts
    forecast_result = await db.execute(
        select(Forecast)
        .where(
            Forecast.node_id == node.id,
            Forecast.scenario_id.is_(None),
        )
        .order_by(Forecast.horizon_minutes, desc(Forecast.made_at))
    )
    forecasts = forecast_result.scalars().all() if has_live_reading else []

    # Group by horizon
    risk_by_horizon = {}
    for r in risks:
        if r.horizon_minutes not in risk_by_horizon:
            risk_by_horizon[r.horizon_minutes] = {
                "risk_level": normalize_risk(r.risk_level),
                "risk_score": r.risk_score,
                "primary_driver": r.primary_driver,
                "predicted_level_cm": r.predicted_level_cm,
                "components": {
                    "level": r.level_component,
                    "rise_rate": r.rise_rate_component,
                    "rainfall": r.rainfall_component,
                    "soil": r.soil_component,
                },
                "assessed_at": r.assessed_at.isoformat(),
            }

    forecast_by_horizon = {}
    for f in forecasts:
        if f.horizon_minutes not in forecast_by_horizon:
            forecast_by_horizon[f.horizon_minutes] = {
                "predicted_level_cm": f.predicted_level_cm,
                "lower_bound_cm": f.lower_bound_cm,
                "upper_bound_cm": f.upper_bound_cm,
                "target_time": f.target_time.isoformat(),
                "status": f.status,
                "model_version": f.model_id,
            }

    return {
        "node_code": node.node_code,
        "name": node.name,
        "node_type": node.node_type,
        "role": node.role,
        "location": {
            "lat": float(lat) if lat is not None else None,
            "lng": float(lng) if lng is not None else None,
        },
        "reference_elevation_m": node.reference_elevation_m,
        "calibration_offset_cm": node.calibration_offset_cm,
        "baseline_level_cm": node.baseline_level_cm,
        "warning_threshold_cm": node.warning_threshold_cm,
        "latest_reading": {
            "water_level_cm": latest.water_level_cm,
            "rainfall_mm_hr": latest.rainfall_mm_hr,
            "flow_rate": latest.flow_rate,
            "soil_moisture": latest.soil_moisture,
            "battery_voltage": latest.battery_voltage,
            "timestamp": latest.recorded_at.isoformat(),
            "received_at": latest.received_at.isoformat() if latest.received_at else None,
            "source": latest.source,
        } if latest else None,
        "risk_by_horizon": risk_by_horizon,
        # Horizon 0 IS the present-tense assessment - the engine writes a
        # horizon-0 RiskAssessment alongside every reading. Exposing it under
        # its own key means a consumer does not have to know that "current"
        # means "the row keyed 0", which is what the console was assuming and
        # failing on (it read `current_risk`, which did not exist, so the
        # prediction page's risk panels never rendered).
        "current_risk": risk_by_horizon.get(0),
        "forecasts": forecast_by_horizon,
    }


@router.get("/current-summary")
async def current_flood_summary(
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current flood summary for dashboard."""
    result = await db.execute(
        select(SensorNode).where(SensorNode.is_active == True)
    )
    nodes = result.scalars().all()

    active_count = len(nodes)
    offline_count = 0
    highest_risk = "low"
    highest_risk_node = None
    total_rainfall = 0.0

    for node in nodes:
        # Check latest reading age
        latest = await get_latest_reading(
            db, node.id, sources=[SourceType.IOT.value, SourceType.SIMULATED.value]
        )

        if latest:
            recorded_at = latest.recorded_at
            if recorded_at.tzinfo is None:
                recorded_at = recorded_at.replace(tzinfo=timezone.utc)
            else:
                recorded_at = recorded_at.astimezone(timezone.utc)
            age_minutes = (datetime.now(timezone.utc) - recorded_at).total_seconds() / 60
            if age_minutes > DEVICE_STALE_AFTER_MINUTES * 3:
                offline_count += 1
            total_rainfall += latest.rainfall_mm_hr or 0

        # Check current risk
        risk_result = await db.execute(
            select(RiskAssessment)
            .where(
                RiskAssessment.node_id == node.id,
                RiskAssessment.horizon_minutes == 0,
            )
            .order_by(desc(RiskAssessment.assessed_at))
            .limit(1)
        )
        current_risk = risk_result.scalar_one_or_none()
        
        if current_risk:
            level = normalize_risk(current_risk.risk_level)
            if RISK_ORDER.get(level, 0) > RISK_ORDER.get(highest_risk, 0):
                highest_risk = level
                highest_risk_node = node.node_code

    return {
        "active_sensors": active_count - offline_count,
        "total_sensors": active_count,
        "offline_stale_sensors": offline_count,
        "highest_risk": highest_risk,
        "highest_risk_node": highest_risk_node,
        "current_rainfall_mm_hr": round(total_rainfall, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/forecasts/{node_code}")
async def get_node_forecasts(
    node_code: str,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get forecasts for a specific node."""
    node = (
        await db.execute(
            select(SensorNode).where(SensorNode.node_code == node_code.upper()).limit(1)
        )
    ).scalar_one_or_none()

    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")

    # Keep only the most recent forecast per horizon. Every prediction cycle
    # inserts new rows, so returning all of them surfaced stale duplicates.
    result = await db.execute(
        select(Forecast)
        .where(
            Forecast.node_id == node.id,
            Forecast.scenario_id.is_(None),
        )
        .order_by(Forecast.horizon_minutes, desc(Forecast.made_at))
    )
    latest: dict[int, Forecast] = {}
    for f in result.scalars().all():
        latest.setdefault(f.horizon_minutes, f)
    if node.node_type == "physical":
        current = await get_latest_reading(db, node.id, sources=[SourceType.IOT.value])
        if not is_recent_physical_reading(node, current):
            latest = {}
    forecasts = [latest[h] for h in sorted(latest)]

    return {
        "node_code": node_code,
        "forecasts": [
            {
                "horizon_minutes": f.horizon_minutes,
                "predicted_level_cm": f.predicted_level_cm,
                "lower_bound_cm": f.lower_bound_cm,
                "upper_bound_cm": f.upper_bound_cm,
                "target_time": f.target_time.isoformat(),
                "made_at": f.made_at.isoformat(),
                "status": f.status,
            }
            for f in forecasts
        ],
    }


@router.get("/layers/{time_step_minutes}")
async def get_flood_layer(
    time_step_minutes: int,
    layer_type: str = Query("depth", pattern="^(depth|risk|boundary)$"),
    scenario_id: Optional[int] = None,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get flood layer for a specific time step."""
    query = select(FloodLayer).where(
        FloodLayer.time_step_minutes == time_step_minutes,
        FloodLayer.layer_type == layer_type,
    )
    if scenario_id is not None:
        query = query.where(FloodLayer.scenario_id == scenario_id)
    else:
        query = query.where(FloodLayer.scenario_id.is_(None))
    
    query = query.order_by(desc(FloodLayer.generated_at)).limit(1)
    
    result = await db.execute(query)
    layer = result.scalar_one_or_none()

    if layer is None:
        raise HTTPException(status_code=404, detail="Flood layer not found")

    return {
        "time_step_minutes": layer.time_step_minutes,
        "layer_type": layer.layer_type,
        "geojson": layer.geojson,
        "max_depth_m": layer.max_depth_m,
        "flooded_area_m2": layer.flooded_area_m2,
        "input_source": layer.input_source,
        "grid_resolution_m": layer.grid_resolution_m,
        "generated_at": layer.generated_at.isoformat(),
        "scenario_id": layer.scenario_id,
        "impact": await _impact_summary_for_layer(db, layer),
        "note": "PREDICTED FLOOD EXTENT — model estimate, not an official flood zone.",
    }


@router.get("/layers")
async def list_flood_layers(
    scenario_id: Optional[int] = None,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List available flood layers."""
    query = select(FloodLayer)
    if scenario_id is not None:
        query = query.where(FloodLayer.scenario_id == scenario_id)
    else:
        query = query.where(FloodLayer.scenario_id.is_(None))
    
    query = query.order_by(FloodLayer.time_step_minutes, FloodLayer.layer_type)
    result = await db.execute(query)
    layers = result.scalars().all()

    return {
        "layers": [
            {
                "id": layer.id,
                "time_step_minutes": layer.time_step_minutes,
                "layer_type": layer.layer_type,
                "max_depth_m": layer.max_depth_m,
                "flooded_area_m2": layer.flooded_area_m2,
                "input_source": layer.input_source,
                "grid_resolution_m": layer.grid_resolution_m,
                "generated_at": layer.generated_at.isoformat(),
                "scenario_id": layer.scenario_id,
            }
            for layer in layers
        ]
    }


@router.get("/map-layers")
async def get_map_layers(
    layer: str = Query("roads", pattern="^(roads|buildings|waterways|sensors)$"),
    bbox: Optional[str] = None,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Vector layers for the 2D map and the 3D twin — one source for both.

    `bbox` is "south,west,north,east". Geometries are simplified so a browser
    can render thousands of features without shipping raw OSM coordinates.
    """
    from shapely.geometry import box as shapely_box

    window = None
    if bbox:
        try:
            s, w, n, e = [float(v) for v in bbox.split(",")]
            window = shapely_box(w, s, e, n)
        except ValueError:
            raise HTTPException(status_code=400, detail="bbox must be south,west,north,east")

    if layer == "waterways":
        return await _waterways_layer()

    if layer == "sensors":
        rows = await db.execute(
            select(SensorNode).where(
                SensorNode.is_active == True,
                SensorNode.node_type == "physical",
            )
        )
        features = []
        for node in rows.scalars().all():
            if not node.location:
                continue
            geo = (await db.execute(select(func.ST_AsGeoJSON(node.location)))).scalar_one()
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "node_code": node.node_code,
                        "name": node.name,
                        "node_type": node.node_type,
                        "role": node.role,
                        "reference_elevation_m": node.reference_elevation_m,
                        "warning_threshold_cm": node.warning_threshold_cm,
                    },
                    "geometry": json.loads(geo),
                }
            )
        return {"type": "FeatureCollection", "features": features, "count": len(features), "layer": "sensors"}

    model, geom = (Building, Building.footprint) if layer == "buildings" else (Road, Road.geometry)
    simplify_m = 0.00004 if layer == "buildings" else 0.00006

    if layer == "buildings":
        query = select(
            Building.id,
            Building.name,
            Building.height_m,
            Building.height_is_estimated,
            Building.building_type,
            func.ST_AsGeoJSON(func.ST_Simplify(Building.footprint, simplify_m)).label("g"),
        )
        if window is not None:
            query = query.where(
                func.ST_Intersects(Building.footprint, WKTElement(window.wkt, srid=4326))
            )
        query = query.order_by(Building.id)
    else:
        query = select(
            Road.id,
            Road.name,
            literal(None, Float).label("height_m"),
            literal(True).label("height_is_estimated"),
            Road.road_type.label("building_type"),
            func.ST_AsGeoJSON(func.ST_Simplify(Road.geometry, simplify_m)).label("g"),
        )
        if window is not None:
            query = query.where(func.ST_Intersects(Road.geometry, WKTElement(window.wkt, srid=4326)))
        query = query.order_by(Road.id)

    rows = await db.execute(query)
    features = []
    for row in rows.all():
        if not row.g:
            continue
        features.append(
            {
                "type": "Feature",
                "id": row.id,
                "properties": {
                    "id": row.id,
                    "name": row.name,
                    "height_m": row.height_m,
                    "height_is_estimated": row.height_is_estimated,
                    "type": row.building_type,
                },
                "geometry": json.loads(row.g),
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "layer": layer,
        "note": "OpenStreetMap contributors (ODbL). Heights may be estimated.",
    }


async def _waterways_layer() -> dict:
    """Serve the preprocessed waterways extract (small, offline file)."""
    import json as _json

    from app.core.config import get_settings

    path = get_settings().resolve_data_path(get_settings().osm_waterways_geojson)
    if not path.exists():
        return {"type": "FeatureCollection", "features": [], "count": 0}
    payload = _json.loads(path.read_text(encoding="utf-8"))
    features = []
    for f in payload.get("features", []):
        props = f.get("properties") or {}
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_id": props.get("osm_id"),
                    "name": props.get("name"),
                    "waterway": props.get("waterway"),
                    "is_river": bool(props.get("is_river")),
                },
                "geometry": f.get("geometry"),
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "layer": "waterways",
        "note": "OpenStreetMap contributors (ODbL).",
    }


@router.post("/simulate")
async def trigger_simulation(
    time_step_minutes: int = Query(0, ge=0),
    scenario_id: Optional[int] = None,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger flood simulation for current or scenario conditions."""
    # Get node levels
    if scenario_id:
        result = await db.execute(
            select(Forecast)
            .where(
                Forecast.scenario_id == scenario_id,
                Forecast.horizon_minutes == time_step_minutes,
                Forecast.status == "ok",
            )
        )
    else:
        result = await db.execute(
            select(Forecast)
            .where(
                Forecast.scenario_id.is_(None),
                Forecast.horizon_minutes == time_step_minutes,
                Forecast.status == "ok",
            )
        )
    forecasts = result.scalars().all()

    node_levels = {}
    for f in forecasts:
        node = await db.get(SensorNode, f.node_id)
        if node:
            node_levels[node.id] = f.predicted_level_cm

    if not node_levels:
        # Fallback to latest telemetry readings (for time_step_minutes == 0 / NOW)
        nodes_result = await db.execute(select(SensorNode).where(SensorNode.is_active == True))
        for node in nodes_result.scalars().all():
            r_res = await db.execute(
                select(TelemetryReading)
                .where(TelemetryReading.node_id == node.id)
                .order_by(desc(TelemetryReading.recorded_at))
                .limit(1)
            )
            reading = r_res.scalar_one_or_none()
            if reading and reading.water_level_cm is not None:
                node_levels[node.id] = reading.water_level_cm
            elif node.baseline_level_cm is not None:
                node_levels[node.id] = node.baseline_level_cm

    if not node_levels:
        raise HTTPException(status_code=400, detail="No forecast or telemetry data available for simulation")

    # Run simulation
    result = await flood_simulation_engine.run_simulation(
        node_levels=node_levels,
        time_step_minutes=time_step_minutes,
        scenario_id=scenario_id,
        input_source="scenario" if scenario_id else "live",
    )

    # Persist the layers + impacts; otherwise results vanish with the response.
    await flood_simulation_engine._store_flood_layers(
        result, time_step_minutes, scenario_id, "scenario" if scenario_id else "live"
    )

    return {
        "time_step_minutes": time_step_minutes,
        "max_depth_m": result["max_depth_m"],
        "flooded_area_m2": result["flooded_area_m2"],
        "layers": result["layers"],
        "impacts": result["impacts"],
    }


@router.get("/risk-colors")
async def get_risk_colors():
    """Get risk level color mapping for frontend."""
    return {
        "LOW": get_risk_color("LOW"),
        "MODERATE": get_risk_color("MODERATE"),
        "HIGH": get_risk_color("HIGH"),
        "SEVERE": get_risk_color("SEVERE"),
    }