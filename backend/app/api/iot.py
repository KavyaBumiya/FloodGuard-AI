"""
FLOODGUARD AI — IoT Telemetry API
==================================
Normalized ingestion, latest-reading, history, and device management for
flood-monitoring devices. Accepts both real device telemetry (source="iot")
and simulated data (source="simulated") so the frontend's Sim mode and Live
mode share one pipeline.

Authentication: devices present `Authorization: Bearer <token>` obtained from
POST /api/iot/v1/devices/register. That endpoint is gated by the
IOT_REGISTRATION_TOKEN bootstrap secret.

Units:
  water_level_cm     — centimetres
  rainfall_mm_hr     — millimetres per hour
  flow_rate          — m³/s (optional)
  soil_moisture      — 0-1 (optional)
  battery_voltage    — volts (optional)
  lat / lng          — WGS84 degrees
  timestamps         — ISO 8601 with timezone
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.device import (
    generate_device_token,
    get_current_device,
    hash_device_token,
)
from app.core.config import get_settings
from app.core.database import get_db
from app.models.floodguard import (
    SensorNode,
    TelemetryReading,
    RejectedReading,
    SourceType,
    NodeType,
)
from app.services.telemetry import (
    get_latest_reading,
    ingest_telemetry_reading,
    is_recent_physical_reading,
)

def _norm_risk(value: str | None) -> str:
    """Risk is stored uppercase; the API contract is lowercase."""
    return str(value).strip().lower() if value else "unknown"


router = APIRouter(prefix="/api/iot", tags=["iot"])
logger = structlog.get_logger()


# ── Request / response schemas ──────────────────────────────────────────────


class DeviceRegisterRequest(BaseModel):
    node_code: str = Field(..., min_length=1, max_length=32)
    name: str | None = Field(None, max_length=120)
    node_type: str = Field("physical", pattern="^(physical|virtual)$")
    role: str | None = Field(None, pattern="^(upstream|midstream|downstream)$")
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    reference_elevation_m: float | None = None
    calibration_offset_cm: float = 0.0
    baseline_level_cm: float | None = None
    warning_threshold_cm: float | None = None
    battery_pct: float | None = Field(None, ge=0, le=100)
    metadata: dict | None = None

    @field_validator("node_code")
    @classmethod
    def _node_code_alnum(cls, v: str) -> str:
        if not all(c.isalnum() or c in "-_" for c in v):
            raise ValueError("node_code may only contain letters, digits, '-' and '_'")
        return v.upper()


class TelemetryRequest(BaseModel):
    node_code: str = Field(..., min_length=1, max_length=32)
    water_level_cm: float = Field(..., ge=0, le=500)
    rainfall_mm_hr: float = Field(..., ge=0, le=500)
    timestamp: str = Field(..., min_length=8, max_length=40)
    flow_rate: float | None = Field(None, ge=0)
    soil_moisture: float | None = Field(None, ge=0, le=1)
    battery_voltage: float | None = Field(None, ge=0)
    event_id: str | None = Field(None, max_length=80)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    source: str = Field("iot", pattern="^(iot|simulated|replay|scenario)$")
    valid: bool = True

    @field_validator("timestamp")
    @classmethod
    def _valid_timestamp(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("timestamp must be a valid ISO 8601 string")
        return v


class TelemetryResponse(BaseModel):
    id: int
    node_code: str
    status: str
    risk_level: str | None = None
    risk_score: float | None = None
    received_at: str


class LatestReading(BaseModel):
    node_code: str
    water_level_cm: float
    rainfall_mm_hr: float
    flow_rate: float | None = None
    soil_moisture: float | None = None
    battery_voltage: float | None = None
    timestamp: str
    received_at: str | None = None
    age_seconds: float | None = None
    risk_level: str | None = None
    risk_score: float | None = None
    source: str
    node_status: str | None = None
    node_type: str | None = None


class HistoryPoint(BaseModel):
    water_level_cm: float
    rainfall_mm_hr: float
    flow_rate: float | None = None
    soil_moisture: float | None = None
    timestamp: str
    risk_level: str | None = None
    risk_score: float | None = None
    source: str


class DeviceResponse(BaseModel):
    node_code: str
    name: str | None
    node_type: str
    role: str | None
    status: str
    lat: float | None
    lng: float | None
    reference_elevation_m: float | None
    calibration_offset_cm: float
    baseline_level_cm: float | None
    warning_threshold_cm: float | None
    battery_pct: float | None
    last_seen_at: str | None
    created_at: str


class RegisterResponse(BaseModel):
    node_code: str
    token: str
    name: str | None
    status: str


# ── Device registration ────────────────────────────────────────────────────


@router.post("/devices/register", response_model=RegisterResponse, status_code=201)
async def register_device(
    body: DeviceRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Register a new flood sensor node and return its bearer token.

    Gated by the IOT_REGISTRATION_TOKEN bootstrap secret. The token is shown
    exactly once — only its hash is stored.
    """
    settings = get_settings()
    presented = request.headers.get("Authorization") or request.headers.get("authorization")
    expected = f"Bearer {settings.iot_registration_token}"
    if not settings.iot_registration_token or presented != expected:
        raise HTTPException(status_code=401, detail="Invalid registration token")

    existing = (
        await db.execute(
            select(SensorNode).where(SensorNode.node_code == body.node_code.upper()).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="node_code already registered")

    token = generate_device_token()
    node = SensorNode(
        node_code=body.node_code.upper(),
        name=body.name,
        node_type=body.node_type,
        role=body.role,
        reference_elevation_m=body.reference_elevation_m,
        calibration_offset_cm=body.calibration_offset_cm,
        baseline_level_cm=body.baseline_level_cm,
        warning_threshold_cm=body.warning_threshold_cm,
        token_hash=hash_device_token(token),
        battery_pct=body.battery_pct,
        is_active=True,
    )
    if body.lat is not None and body.lng is not None:
        node.location = f"POINT({body.lng} {body.lat})"

    db.add(node)
    await db.commit()

    return RegisterResponse(
        node_code=node.node_code,
        token=token,
        name=node.name,
        status="active",
    )


@router.get("/devices", response_model=list[DeviceResponse])
async def list_devices(
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all registered sensor nodes.

    Operator read, not a device operation: the console needs it to populate a
    device picker, so it uses the same public passthrough as every other
    console read in this API. A device token is NOT accepted here on purpose --
    the browser must never hold one (see /telemetry for the device-gated path).
    """
    rows = (await db.execute(select(SensorNode).order_by(SensorNode.created_at.desc()))).scalars().all()
    return [
        DeviceResponse(
            node_code=d.node_code,
            name=d.name,
            node_type=d.node_type,
            role=d.role,
            status="active" if d.is_active else "inactive",
lat=float(d.location.y) if d.location else None,
        lng=float(d.location.x) if d.location else None,
            reference_elevation_m=d.reference_elevation_m,
            calibration_offset_cm=d.calibration_offset_cm,
            baseline_level_cm=d.baseline_level_cm,
            warning_threshold_cm=d.warning_threshold_cm,
            battery_pct=d.battery_pct,
            last_seen_at=d.updated_at.isoformat() if hasattr(d, 'updated_at') and d.updated_at else None,
            created_at=d.created_at.isoformat() if d.created_at else "",
        )
        for d in rows
    ]


@router.get("/devices/{node_code}", response_model=DeviceResponse)
async def get_device(
    node_code: str,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get details for a specific sensor node."""
    node = (
        await db.execute(
            select(SensorNode).where(SensorNode.node_code == node_code.upper()).limit(1)
        )
    ).scalar_one_or_none()

    if node is None:
        raise HTTPException(status_code=404, detail="Device not found")

    return DeviceResponse(
        node_code=node.node_code,
        name=node.name,
        node_type=node.node_type,
        role=node.role,
        status="active" if node.is_active else "inactive",
        lat=float(node.location.y) if node.location else None,
        lng=float(node.location.x) if node.location else None,
        reference_elevation_m=node.reference_elevation_m,
        calibration_offset_cm=node.calibration_offset_cm,
        baseline_level_cm=node.baseline_level_cm,
        warning_threshold_cm=node.warning_threshold_cm,
        battery_pct=node.battery_pct,
        last_seen_at=node.updated_at.isoformat() if hasattr(node, 'updated_at') and node.updated_at else None,
        created_at=node.created_at.isoformat() if node.created_at else "",
    )


# ── Telemetry ingestion ─────────────────────────────────────────────────────


@router.post("/telemetry", response_model=TelemetryResponse, status_code=201)
async def ingest_telemetry(
    body: TelemetryRequest,
    device: SensorNode = Depends(get_current_device),
    db: AsyncSession = Depends(get_db),
):
    """Accept one normalized flood reading from an authenticated device.

    Idempotent on `event_id`: a device that retries after a timeout gets 200
    with the original record rather than a duplicate row.
    """
    # The path is authenticated by token; the body's node_code must match.
    if body.node_code.upper() != device.node_code:
        raise HTTPException(
            status_code=403,
            detail="node_code in body does not match the authenticated device",
        )

    # Validate sensor reported valid
    if not body.valid:
        # Store as rejected
        rejected = RejectedReading(
            node_code=device.node_code,
            raw_payload=body.model_dump(),
            reject_reason="Sensor reported invalid reading (valid=false)",
        )
        db.add(rejected)
        await db.commit()
        raise HTTPException(status_code=400, detail="Sensor reported invalid reading")

    # Idempotency: if this event_id was already stored, return it.
    if body.event_id:
        existing = (
            await db.execute(
                select(TelemetryReading)
                .where(
                    TelemetryReading.node_id == device.id,
                    TelemetryReading.event_id == body.event_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return TelemetryResponse(
                id=existing.id,
                node_code=device.node_code,
                status="duplicate",
                risk_level=existing.risk_level,
                risk_score=existing.risk_score,
                received_at=existing.received_at.isoformat() if existing.received_at else "",
            )

    ts = datetime.fromisoformat(body.timestamp.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    # Shared pipeline: rise rate, risk snapshot, TelemetryReading +
    # RiskAssessment, node freshness, one realtime envelope.
    if body.lat is not None and body.lng is not None:
        device.location = f"POINT({body.lng} {body.lat})"
        await db.commit()

    telemetry, risk = await ingest_telemetry_reading(
        db,
        device,
        timestamp=ts,
        water_level_cm=body.water_level_cm,
        rainfall_mm_hr=body.rainfall_mm_hr,
        flow_rate=body.flow_rate,
        soil_moisture=body.soil_moisture,
        battery_voltage=body.battery_voltage,
        source=body.source,
        event_id=body.event_id,
    )


    return TelemetryResponse(
        id=telemetry.id,
        node_code=device.node_code,
        status="accepted",
        risk_level=getattr(risk, "risk_level", None),
        risk_score=getattr(risk, "risk_score", None),
        received_at=(
            telemetry.received_at.isoformat()
            if telemetry.received_at
            else datetime.now(timezone.utc).isoformat()
        ),
    )


# ── Latest reading ─────────────────────────────────────────────────────────


@router.get("/devices/{node_code}/latest", response_model=LatestReading)
async def latest_reading(
    node_code: str,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Most recent telemetry for a device.

    Operator read used by the console's Live mode, so it follows the same
    public passthrough as the rest of the API rather than device auth -- a
    device token is a device secret and must never be shipped to a browser.

    Returns the device timestamp (what the sensor reported) separately from
    received_at (when the API accepted it) so the frontend can show the right
    one. Includes device health fields.
    """
    node = (
        await db.execute(
            select(SensorNode).where(SensorNode.node_code == node_code.upper()).limit(1)
        )
    ).scalar_one_or_none()

    if node is None:
        raise HTTPException(status_code=404, detail="Device not found")

    row = await get_latest_reading(db, node.id)

    if not is_recent_physical_reading(node, row):
        raise HTTPException(status_code=404, detail="No recent live IoT telemetry for this device")

    age = None
    if row.received_at is not None:
        age = round(
            max(0.0, (datetime.now(timezone.utc) - row.received_at).total_seconds()), 1
        )

    return LatestReading(
        node_code=node.node_code,
        water_level_cm=row.water_level_cm,
        rainfall_mm_hr=row.rainfall_mm_hr,
        flow_rate=row.flow_rate,
        soil_moisture=row.soil_moisture,
        battery_voltage=row.battery_voltage,
        timestamp=row.recorded_at.isoformat(),
        received_at=row.received_at.isoformat() if row.received_at else None,
        age_seconds=age,
        risk_level=_norm_risk(row.risk_level),
        risk_score=row.risk_score,
        source=row.source,
        node_status="active" if node.is_active else "inactive",
        node_type=node.node_type,
    )


# ── History ────────────────────────────────────────────────────────────────


@router.get("/devices/{node_code}/history", response_model=list[HistoryPoint])
async def device_history(
    node_code: str,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
):
    """Chronological telemetry history for a device (console read, not device-gated).

    `from` and `to` are ISO 8601 timestamps. Results are ascending by device
    timestamp so a chart can render them in order.
    """
    node = (
        await db.execute(
            select(SensorNode).where(SensorNode.node_code == node_code.upper()).limit(1)
        )
    ).scalar_one_or_none()

    if node is None:
        raise HTTPException(status_code=404, detail="Device not found")

    stmt = (
        select(TelemetryReading)
        .where(TelemetryReading.node_id == node.id)
        .order_by(TelemetryReading.recorded_at.asc())
    )
    if from_ts:
        try:
            stmt = stmt.where(
                TelemetryReading.recorded_at
                >= datetime.fromisoformat(from_ts.replace("Z", "+00:00"))
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="from must be ISO 8601")
    if to_ts:
        try:
            stmt = stmt.where(
                TelemetryReading.recorded_at
                <= datetime.fromisoformat(to_ts.replace("Z", "+00:00"))
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="to must be ISO 8601")

    rows = (await db.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return [
        HistoryPoint(
            water_level_cm=r.water_level_cm,
            rainfall_mm_hr=r.rainfall_mm_hr,
            flow_rate=r.flow_rate,
            soil_moisture=r.soil_moisture,
            timestamp=r.recorded_at.isoformat(),
            risk_level=_norm_risk(r.risk_level),
            risk_score=r.risk_score,
            source=r.source,
        )
        for r in rows
    ]


# ── Risk history ───────────────────────────────────────────────────────────


@router.get("/devices/{node_code}/risk-history")
async def device_risk_history(
    node_code: str,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
):
    """Risk assessment history for a device."""
    node = (
        await db.execute(
            select(SensorNode).where(SensorNode.node_code == node_code.upper()).limit(1)
        )
    ).scalar_one_or_none()

    if node is None:
        raise HTTPException(status_code=404, detail="Device not found")

    from app.models.floodguard import RiskAssessment
    rows = (
        await db.execute(
            select(RiskAssessment)
            .where(RiskAssessment.node_id == node.id)
            .order_by(RiskAssessment.assessed_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    return [
        {
            "horizon_minutes": r.horizon_minutes,
            "risk_level": _norm_risk(r.risk_level),
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
        for r in rows
    ]
