"""
FLOODGUARD AI — MQTT Ingestion Service
=======================================
Subscribes to ESP32 telemetry topics, validates, stores in PostgreSQL,
and fans out via Redis pub/sub for real-time processing.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import paho.mqtt.client as paho
from paho.mqtt.enums import CallbackAPIVersion
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.floodguard import (
    SensorNode,
    RejectedReading,
    SourceType,
    DataMode,
)
from app.services.telemetry import ingest_telemetry_reading

logger = structlog.get_logger()


@dataclass
class ValidationResult:
    """Result of validating an incoming telemetry payload."""

    valid: bool
    reason: str = ""
    water_level_cm: float = 0.0
    rainfall_mm_hr: float = 0.0
    flow_rate: float | None = None
    soil_moisture: float | None = None
    battery_voltage: float | None = None
    timestamp: datetime | None = None
    source: str = SourceType.IOT.value
    data_mode: str = DataMode.LIVE.value

# Topic structure:
# floodguard/nodes/<node_code>/telemetry
# floodguard/nodes/<node_code>/status
# floodguard/alerts
# floodguard/system/status


class MQTTIngestionService:
    """
    MQTT subscriber for ESP32 and virtual sensor telemetry.
    Runs as a background task in the FastAPI lifespan.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: Optional[paho.Client] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Connect to MQTT broker and start listening."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("mqtt_ingestion_started", broker=self.settings.mqtt_broker_host)

    async def stop(self) -> None:
        """Stop the MQTT listener."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # The paho client owns its own network thread, shut down in _run's
        # finally block; nothing to await here.
        self._client = None
        logger.info("mqtt_ingestion_stopped")

    async def _run(self) -> None:
        """Main MQTT loop with auto-reconnect.

paho runs its own network thread (loop_start), so the socket is never driven
through loop.add_writer -- that call is unavailable on Windows' event loop and
silently breaks the aiomqtt transport. Inbound messages are handed to the
running asyncio loop with call_soon_threadsafe.
"""
        while self._running:
            client = paho.Client(
                CallbackAPIVersion.VERSION2,
                client_id=f"floodguard-backend-{uuid4().hex[:8]}",
            )
            if self.settings.mqtt_username:
                client.username_pw_set(
                    self.settings.mqtt_username, self.settings.mqtt_password or None
                )

            connected = asyncio.Event()

            def on_connect(_client, _userdata, _flags, reason_code, _props=None):
                code = getattr(reason_code, "value", reason_code)
                if code == 0:
                    topic = f"{self.settings.mqtt_topic_prefix}/nodes/+/telemetry"
                    _client.subscribe(topic)
                    logger.info("mqtt_subscribed", topic=topic)
                    loop.call_soon_threadsafe(connected.set)
                else:
                    logger.error("mqtt_connect_refused", code=str(code))

            def on_message(_client, _userdata, message):
                loop.call_soon_threadsafe(
                    lambda t=message.topic, p=message.payload:
                    asyncio.ensure_future(self._handle_message(t, p))
                )

            client.on_connect = on_connect
            client.on_message = on_message

            try:
                loop = asyncio.get_running_loop()
                self._client = client
                client.connect_async(
                    self.settings.mqtt_broker_host,
                    self.settings.mqtt_broker_port,
                    keepalive=self.settings.mqtt_keepalive_seconds,
                )
                client.loop_start()

                try:
                    await asyncio.wait_for(connected.wait(), timeout=15)
                except asyncio.TimeoutError:
                    logger.error(
                        "mqtt_connection_timeout",
                        host=self.settings.mqtt_broker_host,
                        port=self.settings.mqtt_broker_port,
                    )
                    raise

                # Hold the connection open until stopped.
                while self._running and client.is_connected():
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("mqtt_connection_error", error=str(exc))
                if self._running:
                    await asyncio.sleep(5)  # Backoff before reconnect
            finally:
                try:
                    client.loop_stop()
                    client.disconnect()
                except Exception:  # noqa: BLE001 - shutdown must not raise
                    pass
                self._client = None

    async def _handle_message(self, topic: str, raw_payload: bytes) -> None:
        """Process incoming telemetry message."""
        try:
            payload = json.loads(raw_payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("mqtt_invalid_json", topic=str(topic), error=str(exc))
            return

        # Topic is <prefix>/nodes/<node_code>/telemetry, e.g.
        # floodguard/nodes/ESP32-MG-01/telemetry -> parts[2] is the node code.
        topic_parts = [p for p in str(topic).split("/") if p]
        if len(topic_parts) < 4 or topic_parts[-1] != "telemetry":
            logger.warning("mqtt_invalid_topic", topic=str(topic))
            return
        node_code = topic_parts[-2]

        await self._process_telemetry(node_code, payload)

    async def _process_telemetry(self, node_code: str, payload: dict) -> None:
        """Validate and store telemetry reading."""
        async with AsyncSessionLocal() as db:
            # Find or create sensor node
            node = await self._get_or_create_node(db, node_code, payload)
            if not node:
                return

            # Validate reading
            validation = self._validate_reading(payload)
            if not validation.valid:
                await self._store_rejected_reading(db, node_code, payload, validation.reason)
                return

            timestamp = validation.timestamp
            if timestamp is None:
                await self._store_rejected_reading(
                    db, node_code, payload, "Missing timestamp"
                )
                return

            reading, risk = await ingest_telemetry_reading(
                db,
                node,
                timestamp=timestamp,
                water_level_cm=validation.water_level_cm,
                rainfall_mm_hr=validation.rainfall_mm_hr,
                flow_rate=validation.flow_rate,
                soil_moisture=validation.soil_moisture,
                battery_voltage=validation.battery_voltage,
                source=validation.source,
                event_id=payload.get("event_id"),
            )

            logger.info(
                "telemetry_stored",
                node_code=node_code,
                water_level_cm=validation.water_level_cm,
                rainfall_mm_hr=validation.rainfall_mm_hr,
                risk_level=risk.risk_level,
                risk_score=risk.risk_score,
            )

    def _validate_reading(self, payload: dict) -> "ValidationResult":
        """Validate telemetry payload."""
        result = ValidationResult(valid=False)

        # Required fields
        try:
            result.water_level_cm = float(payload["water_level_cm"])
            result.rainfall_mm_hr = float(payload.get("rainfall_mm_hr", 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            result.reason = f"Invalid numeric field: {exc}"
            return result

        # Optional fields
        result.flow_rate = payload.get("flow_rate")
        result.soil_moisture = payload.get("soil_moisture")
        result.battery_voltage = payload.get("battery_voltage")

        # Timestamp
        ts_str = payload.get("timestamp")
        if not ts_str:
            result.reason = "Missing timestamp"
            return result
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            result.timestamp = ts
        except ValueError:
            result.reason = "Invalid timestamp format"
            return result

        # Source validation
        source = payload.get("source", "iot")
        if source not in ("iot", "simulated", "replay", "scenario"):
            result.reason = f"Invalid source: {source}"
            return result
        result.source = source
        result.data_mode = "live" if source == "iot" else source

        # Range checks
        if not (0 <= result.water_level_cm <= 500):
            result.reason = f"Water level out of range: {result.water_level_cm}"
            return result
        if not (0 <= result.rainfall_mm_hr <= 500):
            result.reason = f"Rainfall out of range: {result.rainfall_mm_hr}"
            return result

        # Valid flag
        if payload.get("valid") is False:
            result.reason = "Sensor reported invalid reading"
            return result

        result.valid = True
        return result

    async def _get_or_create_node(
        self, db: AsyncSession, node_code: str, payload: dict
    ) -> Optional[SensorNode]:
        """Get existing node, or auto-create it for simulated sources only."""
        result = await db.execute(
            select(SensorNode).where(SensorNode.node_code == node_code)
        )
        node = result.scalar_one_or_none()

        if node:
            return node

        # MQTT ingest is unauthenticated, so a code prefix must never be treated
        # as identity: any publisher could otherwise self-register a node (and
        # even choose its node_type) simply by naming itself "MR-V...".
        # Physical nodes must be pre-registered via POST /api/iot/devices/register.
        source = payload.get("source", SourceType.IOT.value)
        if source not in ("simulated", "scenario", "replay"):
            logger.warning(
                "unregistered_node_rejected",
                node_code=node_code,
                source=source,
                hint="register the device via POST /api/iot/devices/register first",
            )
            return None

        # Simulated / replay nodes are generated internally and self-register.
        node = SensorNode(
            node_code=node_code,
            name=payload.get("name", node_code),
            node_type="virtual",
            role=payload.get("role"),
            location=f"POINT({payload.get('lon', 72.8830)} {payload.get('lat', 19.0713)})",
            reference_elevation_m=payload.get("reference_elevation_m"),
            calibration_offset_cm=payload.get("calibration_offset_cm", 0.0),
            baseline_level_cm=payload.get("baseline_level_cm"),
            warning_threshold_cm=payload.get("warning_threshold_cm"),
            is_active=True,
        )
        db.add(node)
        await db.commit()
        await db.refresh(node)
        logger.info("node_auto_created", node_code=node_code)
        return node

    async def _store_rejected_reading(
        self, db: AsyncSession, node_code: str, payload: dict, reason: str
    ) -> None:
        """Store rejected reading for audit trail."""
        rejected = RejectedReading(
            node_code=node_code,
            raw_payload=payload,
            reject_reason=reason,
        )
        db.add(rejected)
        await db.commit()
        logger.warning("telemetry_rejected", node_code=node_code, reason=reason)

# Global instance
mqtt_ingestion = MQTTIngestionService()
