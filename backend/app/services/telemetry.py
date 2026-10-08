"""Shared telemetry ingestion pipeline for real IoT readings."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.floodguard import (
    RiskAssessment,
    SensorNode,
    SourceType,
    TelemetryReading,
)
from app.services.flood_risk import (
    DEVICE_STALE_AFTER_MINUTES,
    FloodRiskAssessment,
    assess_current_risk,
    calculate_rise_rate,
)

logger = structlog.get_logger()


def is_recent_physical_reading(node: SensorNode, reading: TelemetryReading | None) -> bool:
    """Return whether a physical node has a recent, real IoT observation."""
    if node.node_type != "physical":
        return reading is not None
    if reading is None or reading.source != "iot":
        return False
    recorded_at = reading.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    else:
        recorded_at = recorded_at.astimezone(timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - recorded_at).total_seconds() / 60
    return age_minutes <= DEVICE_STALE_AFTER_MINUTES


def battery_pct_from_voltage(voltage: float | None) -> float | None:
    """Map a LiPo cell voltage (3.3–4.2 V) onto a 0–100% charge estimate."""
    if voltage is None:
        return None
    return max(0.0, min(100.0, (voltage - 3.3) / (4.2 - 3.3) * 100.0))


def data_mode_for_source(source: str) -> str:
    return "live" if source == "iot" else source


async def get_latest_reading(
    db: AsyncSession, node_id: int, *, sources: list[str] | None = None
) -> Optional[TelemetryReading]:
    """Most recent reading for a node — the one query every caller shares."""
    stmt = (
        select(TelemetryReading)
        .where(TelemetryReading.node_id == node_id)
        .order_by(TelemetryReading.recorded_at.desc())
        .limit(1)
    )
    if sources:
        stmt = stmt.where(TelemetryReading.source.in_(sources))
    return (await db.execute(stmt)).scalar_one_or_none()


async def ingest_telemetry_reading(
    db: AsyncSession,
    node: SensorNode,
    *,
    timestamp: datetime,
    water_level_cm: float,
    rainfall_mm_hr: float,
    flow_rate: float | None = None,
    soil_moisture: float | None = None,
    battery_voltage: float | None = None,
    source: str,
    event_id: str | None = None,
) -> tuple[TelemetryReading, FloodRiskAssessment]:
    """Validate-free store of one reading plus risk, one realtime envelope.

    Returns (reading, risk). The caller is responsible for validation and
    node authorization/registration.
    """
    node_id = getattr(node, "id")
    # Physical probe readings (iot/replay) are inverted: 0 cm = flooded.
    # Simulated and scenario readings are ascending Mithi-stage centimetres.
    inverted = source not in (
        SourceType.SIMULATED.value,
        SourceType.SCENARIO.value,
    )
    prev_result = await db.execute(
        select(TelemetryReading)
        .where(
            TelemetryReading.node_id == node_id,
            TelemetryReading.recorded_at < timestamp,
        )
        .order_by(TelemetryReading.recorded_at.desc())
        .limit(1)
    )
    prev = prev_result.scalar_one_or_none()
    rise_rate = 0.0
    if prev:
        interval = (timestamp - prev.recorded_at).total_seconds()
        rise_rate = calculate_rise_rate(
            water_level_cm,
            prev.water_level_cm,
            interval,
            inverted=inverted,
        )

    risk = assess_current_risk(
        water_level_cm=water_level_cm,
        rainfall_mm_hr=rainfall_mm_hr,
        rise_rate_cm_per_interval=rise_rate,
        soil_saturation=soil_moisture,
        soil_available=soil_moisture is not None,
        terrain_elevation_m=node.reference_elevation_m,
        device_timestamp=timestamp.isoformat(),
        inverted=inverted,
    )

    reading = TelemetryReading(
        node_id=node.id,
        recorded_at=timestamp,
        water_level_cm=water_level_cm,
        rainfall_mm_hr=rainfall_mm_hr,
        flow_rate=flow_rate,
        soil_moisture=soil_moisture,
        battery_voltage=battery_voltage,
        source=source,
        data_mode=data_mode_for_source(source),
        event_id=event_id or uuid4().hex,
        risk_level=risk.risk_level,
        risk_score=risk.risk_score,
    )
    db.add(reading)

    # Risk history stays queryable for every pipeline, with normalized
    # component scores (0-1) — not scenario contribution units.
    comp = {c.name: c for c in (risk.components or [])}
    db.add(
        RiskAssessment(
            node_id=node.id,
            assessed_at=timestamp,
            horizon_minutes=0,
            risk_score=risk.risk_score,
            risk_level=risk.risk_level,
            predicted_level_cm=water_level_cm,
            primary_driver=risk.primary_driver,
            level_component=getattr(comp.get("water_level"), "normalized", 0.0),
            rise_rate_component=getattr(comp.get("rise_rate"), "normalized", 0.0),
            rainfall_component=getattr(comp.get("rainfall"), "normalized", 0.0),
            soil_component=getattr(comp.get("soil_saturation"), "normalized", 0.0),
        )
    )

    node.is_active = True
    node.last_seen_at = timestamp
    battery_pct = battery_pct_from_voltage(battery_voltage)
    if battery_pct is not None:
        node.battery_pct = battery_pct

    await db.commit()
    await db.refresh(reading)

    try:
        from app.services.websocket import publish_realtime_event

        await publish_realtime_event({
            "event": "flood.sensor.updated",
            "payload": {
                "node_code": node.node_code,
                "timestamp": timestamp.isoformat(),
                "water_level_cm": water_level_cm,
                "rainfall_mm_hr": rainfall_mm_hr,
                "flow_rate": flow_rate,
                "soil_moisture": soil_moisture,
                "battery_voltage": battery_voltage,
                "risk_level": risk.risk_level,
                "risk_score": risk.risk_score,
                "source": source,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
        })
        await publish_realtime_event({
            "event": "flood.risk.updated",
            "payload": {
                "node_code": node.node_code,
                "horizon_minutes": 0,
                "risk_level": risk.risk_level,
                "risk_score": risk.risk_score,
                "primary_driver": risk.primary_driver,
                "predicted_level_cm": water_level_cm,
                "components": [
                    {
                        "name": c.name,
                        "value": c.value,
                        "normalized": c.normalized,
                        "weight": c.weight,
                        "contribution": c.contribution,
                        "band": c.band,
                    }
                    for c in (risk.components or [])
                ],
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("realtime_publish_failed", error=str(exc))

    if source == "iot":
        try:
            from app.services.mithi_demo_pipeline import process_real_demo_input

            await process_real_demo_input()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "mithi_demo_pipeline_failed",
                node_code=node.node_code,
                error=str(exc),
            )

    return reading, risk
