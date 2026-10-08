"""
FLOODGUARD AI — IoT Telemetry API
=================================
The single telemetry ingress for the whole system. Hardware posts here over
HTTPS; there is no broker and no second wire protocol.

Normalised ingestion, latest-reading, history, and device management. Every
accepted reading continues into the same pipeline regardless of whether it came
from the physical station or from Replay mode, so the console's Live and Sim
modes share one code path.

Authentication: devices present `Authorization: Bearer <token>` obtained from
POST /api/iot/devices/register. That endpoint is gated by the
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

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
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
)
from app.services import rate_limit
from app.services.ingest import TelemetryRejected, ingest_payload
from app.services.telemetry import (
    get_latest_reading,
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
    # sensor_nodes.name is NOT NULL. Defaulting to the node code keeps the
    # documented curl (which omits `name`) working instead of raising a 500 on
    # a NOT NULL violation.
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
    name: str
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
            select(SensorNode)
            .filter_by(node_code=body.node_code.upper())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="node_code already registered")

    token = generate_device_token()
    # `name` is NOT NULL in the schema but optional in the request, so fall
    # back to the node code rather than handing NULL to Postgres.
    display_name = (body.name or "").strip() or body.node_code.upper()
    node = SensorNode(
        node_code=body.node_code.upper(),
        name=display_name,
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
    rows = (
        await db.execute(
            select(
                SensorNode,
                func.ST_Y(SensorNode.location).label("lat"),
                func.ST_X(SensorNode.location).label("lng"),
            ).order_by(SensorNode.created_at.desc())
        )
    ).all()
    return [
        DeviceResponse(
            node_code=d.node_code,
            name=d.name,
            node_type=d.node_type,
            role=d.role,
            status="active" if d.is_active else "inactive",
            lat=float(lat) if lat is not None else None,
            lng=float(lng) if lng is not None else None,
            reference_elevation_m=d.reference_elevation_m,
            calibration_offset_cm=d.calibration_offset_cm,
            baseline_level_cm=d.baseline_level_cm,
            warning_threshold_cm=d.warning_threshold_cm,
            battery_pct=d.battery_pct,
            last_seen_at=d.updated_at.isoformat() if hasattr(d, 'updated_at') and d.updated_at else None,
            created_at=d.created_at.isoformat() if d.created_at else "",
        )
        for d, lat, lng in rows
    ]


@router.get("/devices/{node_code}", response_model=DeviceResponse)
async def get_device(
    node_code: str,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get details for a specific sensor node."""
    row = (
        await db.execute(
            select(
                SensorNode,
                func.ST_Y(SensorNode.location).label("lat"),
                func.ST_X(SensorNode.location).label("lng"),
            ).where(SensorNode.node_code == node_code.upper()).limit(1)
        )
    ).first()

    if row is None:
        raise HTTPException(status_code=404, detail="Device not found")

    node, lat, lng = row
    return DeviceResponse(
        node_code=node.node_code,
        name=node.name,
        node_type=node.node_type,
        role=node.role,
        status="active" if node.is_active else "inactive",
        lat=float(lat) if lat is not None else None,
        lng=float(lng) if lng is not None else None,
        reference_elevation_m=node.reference_elevation_m,
        calibration_offset_cm=node.calibration_offset_cm,
        baseline_level_cm=node.baseline_level_cm,
        warning_threshold_cm=node.warning_threshold_cm,
        battery_pct=node.battery_pct,
        last_seen_at=node.updated_at.isoformat() if hasattr(node, 'updated_at') and node.updated_at else None,
        created_at=node.created_at.isoformat() if node.created_at else "",
    )


class DeviceUpdateRequest(BaseModel):
    name: str | None = Field(None, max_length=120)
    role: str | None = Field(None, pattern="^(upstream|midstream|downstream)$")
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    warning_threshold_cm: float | None = None
    baseline_level_cm: float | None = None
    calibration_offset_cm: float | None = None
    is_active: bool | None = None


@router.patch("/devices/{node_code}", response_model=DeviceResponse)
async def update_device(
    node_code: str,
    body: DeviceUpdateRequest,
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing node (operator only).

    Lets ops fix a node registered without coords (NULL location = invisible
    on the 2D map, which filters to the Mithi corridor) without re-registering.
    Device bearer tokens cannot call this on purpose.
    """
    node = (
        await db.execute(
            select(SensorNode).where(SensorNode.node_code == node_code.upper()).limit(1)
        )
    ).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Device not found")

    if (body.lat is None) != (body.lng is None):
        raise HTTPException(status_code=422, detail="lat and lng must be sent together")

    if body.name is not None:
        node.name = body.name.strip() or node.name
    if body.role is not None:
        node.role = body.role
    if body.lat is not None and body.lng is not None:
        node.location = f"POINT({body.lng} {body.lat})"
    if body.warning_threshold_cm is not None:
        node.warning_threshold_cm = body.warning_threshold_cm
    if body.baseline_level_cm is not None:
        node.baseline_level_cm = body.baseline_level_cm
    if body.calibration_offset_cm is not None:
        node.calibration_offset_cm = body.calibration_offset_cm
    if body.is_active is not None:
        node.is_active = body.is_active

    await db.commit()
    await db.refresh(node)

    coordinates = (
        await db.execute(
            select(
                func.ST_Y(SensorNode.location).label("lat"),
                func.ST_X(SensorNode.location).label("lng"),
            ).where(SensorNode.node_code == node.node_code)
        )
    ).one()
    lat, lng = coordinates
    return DeviceResponse(
        node_code=node.node_code,
        name=node.name,
        node_type=node.node_type,
        role=node.role,
        status="active" if node.is_active else "inactive",
        lat=float(lat) if lat is not None else None,
        lng=float(lng) if lng is not None else None,
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
    """Accept one telemetry reading over HTTPS. The only wire protocol.

    Idempotent on `event_id`: a device that retries after a timeout or a cold
    start gets the original record back rather than a duplicate row.

    Rejections are written to `rejected_readings` before the 400 is returned, so
    a misbehaving device is auditable and eventually trips the sensor-fault
    alert (3 rejections within an hour).

    Throttled per device at `IOT_TELEMETRY_RATE_LIMIT_PER_MINUTE` (default 12,
    i.e. a 5 s cadence). Exceeding it returns 429 with `Retry-After`. The check
    runs before validation so a flood of malformed bodies is also bounded.
    """
    rate_limit.enforce(
        device.node_code, get_settings().iot_telemetry_rate_limit_per_minute
    )

    # The path is authenticated by token; the body's node_code must match.
    if body.node_code.upper() != device.node_code:
        raise HTTPException(
            status_code=403,
            detail="node_code in body does not match the authenticated device",
        )

    try:
        # `source` is accepted on the wire for schema compatibility with the
        # replay payloads, but a device presenting a valid bearer token *is*
        # live hardware. Pinning it here stops a device from claiming
        # "simulated"/"replay", which would flip data_mode and pollute the
        # demo and replay bookkeeping. Replay calls ingest_payload() directly
        # and is unaffected.
        payload = {**body.model_dump(), "source": "iot"}
        outcome = await ingest_payload(db, payload, node=device)
    except TelemetryRejected as exc:
        logger.warning("telemetry_rejected_http", node_code=device.node_code, reason=exc.reason)
        raise HTTPException(status_code=400, detail=exc.reason)

    return TelemetryResponse(
        id=outcome.reading.id,
        node_code=device.node_code,
        status=outcome.status,
        risk_level=_norm_risk(outcome.reading.risk_level),
        risk_score=outcome.reading.risk_score,
        received_at=(
            outcome.reading.received_at.isoformat()
            if outcome.reading.received_at
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

    if row is None or not is_recent_physical_reading(node, row):
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
