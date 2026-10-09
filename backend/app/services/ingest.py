"""FLOODGUARD AI — transport-neutral telemetry ingestion.

HTTP is the only wire protocol (POST /api/iot/telemetry). This module owns
everything that happens *after* a payload crosses that boundary, so that the
endpoint and Replay mode share one definition of "valid" and one audit trail.

Split of responsibilities:

    api/iot.py            HTTP contract: bearer auth, Pydantic shape, 4xx codes
    ingest.py  (this)     validation, node resolution, rejected_readings audit
    telemetry.py          the shared pipeline: rise rate -> risk -> store ->
                          realtime events -> Mithi demo pipeline

Rejected readings are written to the `rejected_readings` table rather than only
logged, so bad data is auditable and feeds the sensor-fault alert rule
(3 rejections in an hour -> HIGH).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.floodguard import (
    RejectedReading,
    SensorNode,
    SourceType,
    TelemetryReading,
)
from app.services.flood_risk import FloodRiskAssessment
from app.services.telemetry import ingest_telemetry_reading

logger = structlog.get_logger()

# Plausibility bounds. Deliberately wider than any plausible real reading so
# that a misconfigured sensor is caught, while a genuine flash flood is not
# silently dropped.
WATER_LEVEL_MIN_CM, WATER_LEVEL_MAX_CM = 0.0, 500.0
RAINFALL_MIN_MM_HR, RAINFALL_MAX_MM_HR = 0.0, 500.0
SOIL_MIN, SOIL_MAX = 0.0, 1.0

VALID_SOURCES = frozenset(
    {
        SourceType.IOT.value,
        SourceType.SIMULATED.value,
        SourceType.REPLAY.value,
        SourceType.SCENARIO.value,
    }
)

# Where a self-registering node lands if the payload carries no coordinates:
# the centre of the Mithi study area.
DEFAULT_LAT = 19.0713
DEFAULT_LON = 72.8830


@dataclass
class ValidationResult:
    """Outcome of validating one inbound telemetry payload."""

    valid: bool
    reason: str = ""
    water_level_cm: float = 0.0
    rainfall_mm_hr: float = 0.0
    flow_rate: float | None = None
    soil_moisture: float | None = None
    battery_voltage: float | None = None
    timestamp: datetime | None = None
    source: str = SourceType.IOT.value
    data_mode: str = "live"


@dataclass
class IngestResult:
    """Stored (or previously stored) reading plus its risk snapshot."""

    reading: TelemetryReading
    risk: FloodRiskAssessment | None
    status: str  # "accepted" | "duplicate"
    node: SensorNode | None = None


class TelemetryRejected(Exception):
    """A payload failed validation and has already been audited.

    `reason` is safe to surface to the caller: it never echoes payload content.
    """

    def __init__(self, reason: str, *, node_code: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.node_code = node_code


def is_out_of_order(timestamp: datetime, latest_timestamp: datetime) -> bool:
    """Compare device timestamps safely across timezone-aware and naive DB rows."""
    timestamp_utc = (
        timestamp.replace(tzinfo=timezone.utc)
        if timestamp.tzinfo is None
        else timestamp.astimezone(timezone.utc)
    )
    latest_utc = (
        latest_timestamp.replace(tzinfo=timezone.utc)
        if latest_timestamp.tzinfo is None
        else latest_timestamp.astimezone(timezone.utc)
    )
    return timestamp_utc < latest_utc


def validate_payload(payload: dict[str, Any]) -> ValidationResult:
    """Validate a telemetry payload dict.

    Order matters: cheap structural checks first so the caller gets the most
    actionable message, and `valid=false` last so an explicit device fault is
    reported as such rather than as a missing field.
    """
    result = ValidationResult(valid=False)

    # ── numeric core ────────────────────────────────────────────────────
    try:
        result.water_level_cm = float(payload["water_level_cm"])
        result.rainfall_mm_hr = float(payload.get("rainfall_mm_hr", 0.0))
    except (KeyError, TypeError, ValueError) as exc:
        result.reason = f"Invalid numeric field: {exc}"
        return result

    # ── optional passthroughs ───────────────────────────────────────────
    for attr, key in (
        ("flow_rate", "flow_rate"),
        ("soil_moisture", "soil_moisture"),
        ("battery_voltage", "battery_voltage"),
    ):
        value = payload.get(key)
        setattr(result, attr, float(value) if value is not None else None)

    if result.soil_moisture is not None and not (SOIL_MIN <= result.soil_moisture <= SOIL_MAX):
        result.reason = f"Soil moisture out of range: {result.soil_moisture}"
        return result

    # ── timestamp ───────────────────────────────────────────────────────
    ts_raw = payload.get("timestamp")
    if not ts_raw:
        result.reason = "Missing timestamp"
        return result
    try:
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        result.timestamp = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
    except ValueError:
        result.reason = "Invalid timestamp format"
        return result

    # A device clock years off would corrupt every downstream window.
    now = datetime.now(timezone.utc)
    if abs((now - result.timestamp).total_seconds()) > 86_400:
        result.reason = f"Timestamp outside ±24h of server time: {result.timestamp.isoformat()}"
        return result

    # ── provenance ──────────────────────────────────────────────────────
    source = str(payload.get("source", SourceType.IOT.value))
    if source not in VALID_SOURCES:
        result.reason = f"Invalid source: {source}"
        return result
    result.source = source
    result.data_mode = "live" if source == SourceType.IOT.value else source

    # ── plausibility ────────────────────────────────────────────────────
    if not math.isfinite(result.water_level_cm) or not (
        WATER_LEVEL_MIN_CM <= result.water_level_cm <= WATER_LEVEL_MAX_CM
    ):
        result.reason = f"Water level out of range: {result.water_level_cm}"
        return result
    if not math.isfinite(result.rainfall_mm_hr) or not (
        RAINFALL_MIN_MM_HR <= result.rainfall_mm_hr <= RAINFALL_MAX_MM_HR
    ):
        result.reason = f"Rainfall out of range: {result.rainfall_mm_hr}"
        return result

    # ── device's own verdict ────────────────────────────────────────────
    # A sensor that reports itself as faulty is trusted: the whole point of the
    # flag is that the device knows something its host does not (a foam-failed
    # ultrasonic reading, a detached probe).
    if payload.get("valid") is False:
        result.reason = "Sensor reported invalid reading"
        return result

    result.valid = True
    return result


async def _audit_rejection(
    db: AsyncSession, node_code: str | None, payload: dict[str, Any], reason: str
) -> None:
    db.add(
        RejectedReading(
            node_code=node_code,
            raw_payload=_jsonable(payload),
            reject_reason=reason,
        )
    )
    await db.commit()
    logger.warning("telemetry_rejected", node_code=node_code, reason=reason)


def _jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    """JSONB needs plain types; a device can send anything."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        else:
            out[key] = str(value)
    return out


async def resolve_node(
    db: AsyncSession,
    node_code: str,
    payload: dict[str, Any],
    *,
    allow_self_register: bool,
) -> Optional[SensorNode]:
    """Find the target node, optionally registering it for non-hardware sources.

    `allow_self_register` is True only for Replay, where the recording itself is
    the authority. Live hardware must be registered explicitly through
    POST /api/iot/devices/register, because this endpoint is reachable by
    anyone holding a device token and a node code must never be self-asserted.
    """
    result = await db.execute(select(SensorNode).where(SensorNode.node_code == node_code))
    node = result.scalar_one_or_none()
    if node is not None:
        return node

    if not allow_self_register:
        logger.warning(
            "unregistered_node_rejected",
            node_code=node_code,
            source=str(payload.get("source", SourceType.IOT.value)),
            hint="register the device via POST /api/iot/devices/register first",
        )
        return None

    source = str(payload.get("source", SourceType.IOT.value))
    node = SensorNode(
        node_code=node_code,
        name=str(payload.get("name") or node_code),
        node_type="virtual",
        role=payload.get("role"),
        location=(
            f"POINT({float(payload.get('lon', DEFAULT_LON))} "
            f"{float(payload.get('lat', DEFAULT_LAT))})"
        ),
        reference_elevation_m=payload.get("reference_elevation_m"),
        calibration_offset_cm=float(payload.get("calibration_offset_cm", 0.0)),
        baseline_level_cm=payload.get("baseline_level_cm"),
        warning_threshold_cm=payload.get("warning_threshold_cm"),
        is_active=True,
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    logger.info("node_auto_created", node_code=node_code, source=source)
    return node


async def ingest_payload(
    db: AsyncSession,
    payload: dict[str, Any],
    *,
    node: SensorNode | None = None,
    node_code: str | None = None,
    allow_self_register: bool = False,
) -> IngestResult:
    """Validate and store one telemetry payload.

    `node` short-circuits node resolution for the HTTP path, which has already
    authenticated the bearer token against it. `node_code` is required when
    `node` is None.

    Raises `TelemetryRejected` after writing the audit row; the caller decides
    whether that becomes a 400 or a log line.
    """
    if node is None:
        resolved_code = node_code or str(payload.get("node_code") or "").strip()
        if not resolved_code:
            raise TelemetryRejected("Missing node_code", node_code=None)
        node = await resolve_node(
            db, resolved_code.upper(), payload, allow_self_register=allow_self_register
        )
        if node is None:
            # Unresolvable node: not a payload defect, so nothing is audited.
            raise TelemetryRejected(
                f"Unregistered node: {resolved_code.upper()}", node_code=resolved_code.upper()
            )

    if not node.is_active:
        raise TelemetryRejected(
            f"Node {node.node_code} is deactivated.", node_code=node.node_code
        )

    validation = validate_payload(payload)
    if not validation.valid:
        await _audit_rejection(db, node.node_code, payload, validation.reason)
        raise TelemetryRejected(validation.reason, node_code=node.node_code)

    assert validation.timestamp is not None  # guaranteed by validate_payload

    # Idempotency: a device that retries after a timeout replays the same
    # event_id and gets the original row back rather than a duplicate reading.
    event_id = payload.get("event_id")
    if event_id:
        existing = (
            await db.execute(
                select(TelemetryReading).where(
                    TelemetryReading.node_id == node.id,
                    TelemetryReading.event_id == str(event_id),
                ).order_by(TelemetryReading.id).limit(1)
            )
        ).scalars().first()
        if existing is not None:
            logger.info(
                "telemetry_duplicate",
                node_code=node.node_code,
                event_id=str(event_id),
                reading_id=existing.id,
            )
            return IngestResult(
                reading=existing,
                risk=None,
                status="duplicate",
                node=node,
            )

    latest_result = await db.execute(
        select(TelemetryReading)
        .where(
            TelemetryReading.node_id == node.id,
            TelemetryReading.source == validation.source,
        )
        .order_by(TelemetryReading.recorded_at.desc(), TelemetryReading.id.desc())
        .limit(1)
    )
    latest_reading = latest_result.scalar_one_or_none()
    if latest_reading and is_out_of_order(validation.timestamp, latest_reading.recorded_at):
        reason = "Timestamp is older than the latest accepted reading for this source"
        await _audit_rejection(db, node.node_code, payload, reason)
        raise TelemetryRejected(reason, node_code=node.node_code)

    # Optional live position update — keeps the node marker where the device
    # actually is, which matters for a demo unit that moves.
    lat, lng = payload.get("lat"), payload.get("lng")
    if lat is not None and lng is not None:
        node.location = f"POINT({float(lng)} {float(lat)})"
        await db.commit()

    reading, risk = await ingest_telemetry_reading(
        db,
        node,
        timestamp=validation.timestamp,
        water_level_cm=validation.water_level_cm,
        rainfall_mm_hr=validation.rainfall_mm_hr,
        flow_rate=validation.flow_rate,
        soil_moisture=validation.soil_moisture,
        battery_voltage=validation.battery_voltage,
        source=validation.source,
        event_id=str(event_id) if event_id else None,
    )

    logger.info(
        "telemetry_stored",
        node_code=node.node_code,
        water_level_cm=validation.water_level_cm,
        rainfall_mm_hr=validation.rainfall_mm_hr,
        source=validation.source,
        risk_level=risk.risk_level,
        risk_score=risk.risk_score,
    )
    return IngestResult(reading=reading, risk=risk, status="accepted", node=node)