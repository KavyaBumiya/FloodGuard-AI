"""
FLOODGUARD AI — Replay & Demo Controller
=========================================
Manages demo mode, replay of recorded data, and system reset for hackathon demos.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from importlib import import_module
from typing import Any, Optional

import structlog
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.floodguard import (
    SensorNode,
    TelemetryReading,
    Forecast,
    RiskAssessment,
    Alert,
    FloodLayer,
    FloodImpact,
    Scenario,
    ReplayRecording,
    SourceType,
    DataMode,
)
_mqtt_ingestion_module = import_module("app.services.mqtt_ingestion")
mqtt_ingestion: Any = next(
    (
        value
        for name, value in vars(_mqtt_ingestion_module).items()
        if not name.startswith("_")
        and not isinstance(value, type)
        and callable(getattr(value, "_process_telemetry", None))
    ),
    _mqtt_ingestion_module,
)
from app.services.forecast_service import forecast_service
from app.services.flood_simulation import flood_simulation_engine
from app.services.alert_engine import alert_engine
from app.integrations import cache

logger = structlog.get_logger()


class DemoController:
    """
    Controls demo mode, replay, and system reset.
    
    Modes:
    - LIVE: Real ESP32 input driving the Mithi simulation
    - REPLAY: Playback recorded data through same pipeline
    - SCENARIO: What-if simulation active
    
    Provides single reset action to return to clean demo state.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._mode = "live"
        self._replay_task: Optional[asyncio.Task] = None
        self._replay_speed = 1.0
        self._replay_recording: Optional[ReplayRecording] = None

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_replay(self) -> bool:
        return self._mode == "replay"

    @property
    def is_scenario(self) -> bool:
        return self._mode == "scenario"

    async def set_mode(self, mode: str) -> None:
        """Set system mode: live, replay, scenario."""
        if mode not in ("live", "replay", "scenario"):
            raise ValueError(f"Invalid mode: {mode}")
        
        # Stop current mode
        if self._mode == "replay":
            await self.stop_replay()
        elif self._mode == "scenario":
            await self.clear_scenario()

        self._mode = mode
        logger.info("demo_mode_changed", mode=mode)
        await self._publish_mode_event()

    async def start_replay(self, recording_id: int, speed: float = 1.0) -> None:
        """Start replay of a recorded session."""
        async with AsyncSessionLocal() as db:
            recording = await db.get(ReplayRecording, recording_id)
            if not recording:
                raise ValueError(f"Recording {recording_id} not found")

        self._replay_recording = recording
        self._replay_speed = max(0.1, min(10.0, speed))
        await self.set_mode("replay")
        
        self._replay_task = asyncio.create_task(self._run_replay(recording))
        logger.info("replay_started", recording=recording.name, speed=speed)

    async def stop_replay(self) -> None:
        """Stop current replay."""
        if self._replay_task:
            self._replay_task.cancel()
            try:
                await self._replay_task
            except asyncio.CancelledError:
                pass
            self._replay_task = None
        self._replay_recording = None
        logger.info("replay_stopped")

    async def _run_replay(self, recording: ReplayRecording) -> None:
        """Run replay by feeding recorded data through ingestion pipeline."""
        payload = recording.payload
        readings = payload.get("readings", [])
        
        if not readings:
            logger.warning("replay_empty", recording_id=recording.id)
            return

        # Calculate timing
        start_time = datetime.fromisoformat(readings[0]["timestamp"].replace("Z", "+00:00"))
        last_time = start_time

        for i, reading_data in enumerate(readings):
            if self._mode != "replay":
                break

            # Calculate delay to next reading
            current_time = datetime.fromisoformat(reading_data["timestamp"].replace("Z", "+00:00"))
            if i > 0:
                delay = (current_time - last_time).total_seconds() / self._replay_speed
                await asyncio.sleep(max(0, delay))

            last_time = current_time

            # Mark as replay
            reading_data["source"] = SourceType.REPLAY.value
            reading_data["data_mode"] = DataMode.REPLAY.value

            # Feed through MQTT ingestion (which stores and publishes)
            node_code = reading_data.get("node_code", f"REPLAY-{i}")
            await mqtt_ingestion._process_telemetry(node_code, reading_data)

        logger.info("replay_completed", recording_id=recording.id)
        await self.set_mode("live")

    async def clear_scenario(self) -> None:
        """Clear active scenario, return to live/replay mode."""
        await self.set_mode("live" if not self.is_replay else "replay")
        logger.info("scenario_cleared")

    async def reset_demo(self) -> None:
        """
        Complete demo reset: clears all telemetry, forecasts, alerts, 
        scenarios, and flood layers. Returns to clean starting state.
        Preserves static data: sensor nodes, study area, roads, buildings, models.
        """
        logger.info("demo_reset_started")
        
        # Stop any running replay
        await self.stop_replay()
        
        # Clear scenario
        await self.clear_scenario()

        async with AsyncSessionLocal() as db:
            # Delete dynamic data in order (respecting FKs)
            await db.execute(delete(FloodImpact))
            await db.execute(delete(FloodLayer))
            await db.execute(delete(Alert))
            await db.execute(delete(RiskAssessment))
            await db.execute(delete(Forecast))
            await db.execute(delete(TelemetryReading))
            await db.execute(delete(Scenario))
            await db.commit()

        # Clear Redis caches
        try:
            redis = await cache.get_redis()
            keys = await redis.keys("floodguard:*")
            if keys:
                await redis.delete(*keys)
            keys = await redis.keys("forecast:*")
            if keys:
                await redis.delete(*keys)
            keys = await redis.keys("weather:*")
            if keys:
                await redis.delete(*keys)
        except Exception as exc:
            logger.warning("demo_redis_clear_failed", error=str(exc))

        logger.info("demo_reset_completed")
        await self._publish_mode_event()

    async def seed_demo_data(self) -> None:
        """Keep demo reset/replay available without creating sensor telemetry."""
        logger.info("demo_seed_skipped", reason="live_input_requires_registered_esp32")

    async def create_demo_recording(self, name: str, duration_seconds: int = 300) -> ReplayRecording:
        """Create a recording from current live data for later replay."""
        async with AsyncSessionLocal() as db:
            # Get recent telemetry
            result = await db.execute(
                select(TelemetryReading)
                .where(TelemetryReading.recorded_at >= datetime.now(timezone.utc) - timedelta(seconds=duration_seconds))
                .order_by(TelemetryReading.recorded_at)
            )
            readings = result.scalars().all()

            payload_readings = []
            for r in readings:
                node = await db.get(SensorNode, r.node_id)
                if node is None:
                    continue
                payload_readings.append(
                    {
                        "node_code": node.node_code,
                        "water_level_cm": r.water_level_cm,
                        "rainfall_mm_hr": r.rainfall_mm_hr,
                        "timestamp": r.recorded_at.isoformat(),
                        "source": r.source,
                    }
                )

            payload = {"readings": payload_readings}

            recording = ReplayRecording(
                name=name,
                description=f"Recorded {len(readings)} readings over {duration_seconds}s",
                origin="physical_recording",
                duration_seconds=duration_seconds,
                payload=payload,
            )
            db.add(recording)
            await db.commit()
            await db.refresh(recording)

            return recording

    async def list_recordings(self) -> list[ReplayRecording]:
        """List available replay recordings."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ReplayRecording).order_by(ReplayRecording.created_at.desc())
            )
            return list(result.scalars().all())

    async def _publish_mode_event(self) -> None:
        """Publish mode change event."""
        try:
            event = {
                "event": "flood.mode.changed",
                "payload": {
                    "mode": self._mode,
                    "replay_speed": self._replay_speed,
                    "recording": self._replay_recording.name if self._replay_recording else None,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "demo_controller",
            }
            from app.services.websocket import publish_realtime_event

            await publish_realtime_event(event)
        except Exception as exc:
            logger.warning("mode_publish_failed", error=str(exc))

    async def get_status(self) -> dict:
        """Get current demo status."""
        return {
            "mode": self._mode,
            "replay_speed": self._replay_speed,
            "replay_recording": self._replay_recording.name if self._replay_recording else None,
            "replay_active": self._replay_task is not None and not self._replay_task.done(),
        }


# Global instance
demo_controller = DemoController()