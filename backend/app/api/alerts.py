"""
FLOODGUARD AI — Alerts API
===========================
Alert management: list, acknowledge, history, network health.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.database import get_db
from app.models.floodguard import Alert, SensorNode, TelemetryReading, SourceType
from app.services.alert_engine import alert_engine
from app.services.flood_risk import DEVICE_STALE_AFTER_MINUTES

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(
    scenario_id: Optional[int] = None,
    node_code: Optional[str] = None,
    severity: Optional[str] = Query(None, pattern="^(information|warning|high|critical)$"),
    alert_type: Optional[str] = None,
    active_only: bool = True,
    hours: int = 24,
    limit: int = Query(100, ge=1, le=500),
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List alerts with filters."""
    query = select(Alert)

    if active_only:
        query = query.where(Alert.is_active == True)
    
    if scenario_id is not None:
        query = query.where(Alert.scenario_id == scenario_id)
    else:
        query = query.where(Alert.scenario_id.is_(None))

    if node_code:
        # Join with SensorNode
        sensor_node_table = SensorNode.__table__
        query = query.join(
            sensor_node_table,
            Alert.node_id == sensor_node_table.c.id,
        ).where(
            sensor_node_table.c.node_code == node_code.upper()
        )

    if severity:
        query = query.where(Alert.severity == severity)

    if alert_type:
        query = query.where(Alert.alert_type == alert_type)

    query = query.where(
        Alert.created_at >= datetime.now(timezone.utc) - timedelta(hours=hours)
    ).order_by(desc(Alert.created_at)).limit(limit)

    result = await db.execute(query)
    alerts = result.scalars().all()

    # Enrich with node codes
    node_ids = set(a.node_id for a in alerts if a.node_id)
    nodes = {}
    if node_ids:
        node_result = await db.execute(
            select(SensorNode).where(SensorNode.id.in_(node_ids))
        )
        nodes = {n.id: n.node_code for n in node_result.scalars().all()}

    return {
        "alerts": [
            {
                "id": a.id,
                "node_code": nodes.get(a.node_id) if a.node_id is not None else None,
                "scenario_id": a.scenario_id,
                "alert_type": a.alert_type,
                "severity": a.severity,
                "message": a.message,
                "trigger_data": a.trigger_data,
                "is_scenario_alert": a.is_scenario_alert,
                "is_active": a.is_active,
                "acknowledged": a.acknowledged,
                "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
                "acknowledged_by": a.acknowledged_by,
                "created_at": a.created_at.isoformat(),
                "cleared_at": a.cleared_at.isoformat() if a.cleared_at else None,
            }
            for a in alerts
        ]
    }


@router.get("/active")
async def get_active_alerts(
    scenario_id: Optional[int] = None,
    _: dict = Depends(get_current_user),
):
    """Get all currently active alerts."""
    alerts = await alert_engine.get_active_alerts(scenario_id)
    
    # Enrich with node codes
    from app.models.floodguard import SensorNode
    from app.core.database import AsyncSessionLocal
    
    async with AsyncSessionLocal() as db:
        node_ids = set(a.node_id for a in alerts if a.node_id)
        nodes = {}
        if node_ids:
            node_result = await db.execute(
                select(SensorNode).where(SensorNode.id.in_(node_ids))
            )
            nodes = {n.id: n.node_code for n in node_result.scalars().all()}

    return {
        "alerts": [
            {
                "id": a.id,
                "node_code": nodes.get(a.node_id) if a.node_id is not None else None,
                "scenario_id": a.scenario_id,
                "alert_type": a.alert_type,
                "severity": a.severity,
                "message": a.message,
                "trigger_data": a.trigger_data,
                "is_scenario_alert": a.is_scenario_alert,
                # The query already filters on is_active, so this is always
                # true - but /history and the collection endpoint both return
                # it, and AlertStrip.tsx filters on it. Omitting it here made
                # that filter evaluate undefined for every alert, so the strip
                # never rendered at all.
                "is_active": a.is_active,
                "acknowledged": a.acknowledged,
                "created_at": a.created_at.isoformat(),
            }
            for a in alerts
        ],
        "counts": {
            "critical": len([a for a in alerts if a.severity == "critical"]),
            "high": len([a for a in alerts if a.severity == "high"]),
            "warning": len([a for a in alerts if a.severity == "warning"]),
            "information": len([a for a in alerts if a.severity == "information"]),
        }
    }


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: int,
    acknowledged_by: str = "operator",
    _: dict = Depends(get_current_user),
):
    """Acknowledge an alert."""
    alert = await alert_engine.acknowledge_alert(alert_id, acknowledged_by)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"message": "Alert acknowledged", "alert_id": alert_id}


@router.post("/acknowledge-all")
async def acknowledge_all_alerts(
    acknowledged_by: str = "operator",
    scenario_id: Optional[int] = None,
    _: dict = Depends(get_current_user),
):
    """Acknowledge all active alerts."""
    alerts = await alert_engine.get_active_alerts(scenario_id)
    count = 0
    for alert in alerts:
        await alert_engine.acknowledge_alert(alert.id, acknowledged_by)
        count += 1
    return {"message": f"Acknowledged {count} alerts"}


@router.get("/history")
async def alert_history(
    node_code: Optional[str] = None,
    scenario_id: Optional[int] = None,
    hours: int = 24,
    limit: int = Query(100, ge=1, le=500),
    _: dict = Depends(get_current_user),
):
    """Get alert history."""
    alerts = await alert_engine.get_alert_history(
        node_id=None,  # Would need to resolve node_code to ID
        scenario_id=scenario_id,
        hours=hours,
        limit=limit,
    )

    # Enrich with node codes
    from app.models.floodguard import SensorNode
    from app.core.database import AsyncSessionLocal
    
    async with AsyncSessionLocal() as db:
        node_ids = set(a.node_id for a in alerts if a.node_id)
        nodes = {}
        if node_ids:
            node_result = await db.execute(
                select(SensorNode).where(SensorNode.id.in_(node_ids))
            )
            nodes = {n.id: n.node_code for n in node_result.scalars().all()}

    return {
        "alerts": [
            {
                "id": a.id,
                "node_code": nodes.get(a.node_id) if a.node_id is not None else None,
                "scenario_id": a.scenario_id,
                "alert_type": a.alert_type,
                "severity": a.severity,
                "message": a.message,
                "trigger_data": a.trigger_data,
                "is_scenario_alert": a.is_scenario_alert,
                "is_active": a.is_active,
                "acknowledged": a.acknowledged,
                "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
                "acknowledged_by": a.acknowledged_by,
                "created_at": a.created_at.isoformat(),
                "cleared_at": a.cleared_at.isoformat() if a.cleared_at else None,
            }
            for a in alerts
        ]
    }


@router.get("/network-health")
async def network_health(
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get sensor network health overview."""
    result = await db.execute(
        select(SensorNode).where(SensorNode.is_active == True)
    )
    nodes = result.scalars().all()

    health = []
    for node in nodes:
        latest_result = await db.execute(
            select(TelemetryReading)
            .where(
                TelemetryReading.node_id == node.id,
                TelemetryReading.source.in_(
                    [SourceType.IOT.value, SourceType.SIMULATED.value]
                ),
            )
            .order_by(desc(TelemetryReading.recorded_at))
            .limit(1)
        )
        latest = latest_result.scalar_one_or_none()

        status = "online"
        last_reading = None
        minutes_since = None

        if latest:
            last_reading = latest.recorded_at.isoformat()
            recorded_at = latest.recorded_at
            if recorded_at.tzinfo is None:
                recorded_at = recorded_at.replace(tzinfo=timezone.utc)
            else:
                recorded_at = recorded_at.astimezone(timezone.utc)
            minutes_since = (datetime.now(timezone.utc) - recorded_at).total_seconds() / 60
            stale_cutoff = DEVICE_STALE_AFTER_MINUTES
            if minutes_since > stale_cutoff * 3:
                status = "offline"
            elif minutes_since > stale_cutoff:
                status = "stale"
        else:
            status = "no_data"

        # Get battery
        battery = node.battery_pct

        health.append({
            "node_code": node.node_code,
            "name": node.name,
            "node_type": node.node_type,
            "role": node.role,
            "status": status,
            "last_reading": last_reading,
            "minutes_since_last_reading": round(minutes_since, 1) if minutes_since else None,
            "battery_pct": battery,
        })

    return {
        "nodes": health,
        "summary": {
            "total": len(health),
            "online": len([h for h in health if h["status"] == "online"]),
            "stale": len([h for h in health if h["status"] == "stale"]),
            "offline": len([h for h in health if h["status"] == "offline"]),
            "no_data": len([h for h in health if h["status"] == "no_data"]),
        }
    }