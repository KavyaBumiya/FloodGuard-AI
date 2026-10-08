"""Real Vadodara demo input -> simulated Mithi processing pipeline.

The pipeline is CPU-bound: `process_real_demo_input` runs four full flood
simulations, and each one walks the whole DEM grid in Python (see
`flood_simulation._apply_connectivity_mask` and `_vectorize_flood_layers`).
Running it inline on the telemetry request blocked the single event loop
(`--workers 1`), which starved the DB pool and made the dashboard's WebSocket
drop. So it runs on a background worker instead, coalescing bursts of readings
into a single run.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime, timezone

import structlog
from sqlalchemy import desc, select

from app.core.database import AsyncSessionLocal
from app.models.floodguard import SensorNode, TelemetryReading, NodeType, SourceType
from app.services.flood_simulation import flood_simulation_engine
from app.services.forecast_service import forecast_service
from app.services.websocket import publish_realtime_event

logger = structlog.get_logger()

# Shortest gap between two pipeline runs. Readings arriving faster than this
# do not each get their own four simulations - they fold into one follow-up.
MIN_RUN_INTERVAL_S = 5.0

_requested = asyncio.Event()
_last_finished_at = 0.0


def request_run() -> None:
    """Ask the background worker to refresh the Mithi twin.

    Returns immediately and never raises: the caller is the telemetry request,
    and a stale twin is far better than a blocked event loop. Bursts of
    readings coalesce into a single run (see `_worker`).
    """
    _requested.set()


async def _worker() -> None:
    global _last_finished_at
    while True:
        await _requested.wait()
        # Enforce the minimum spacing BEFORE the work. Doing it afterwards let
        # a request that landed outside the run window start another run
        # immediately, so the coalescing never happened.
        wait = MIN_RUN_INTERVAL_S - (time.monotonic() - _last_finished_at)
        if wait > 0:
            await asyncio.sleep(wait)
        # Clear last, so every request that piled up during the wait is
        # absorbed into the run we are about to do.
        _requested.clear()
        try:
            await process_real_demo_input()
        except Exception as exc:  # noqa: BLE001
            logger.error("mithi_demo_pipeline_failed", error=str(exc))
        _last_finished_at = time.monotonic()


def start() -> asyncio.Task:
    """Start the worker. Returns the task so the caller can cancel it."""
    return asyncio.create_task(_worker())


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def reset() -> None:
    """Clear pending state. Tests only."""
    _requested.clear()


async def process_real_demo_input() -> None:
    """Propagate the newest real input through the Mithi digital twin.

    The physical reading remains attached to the physical Vadodara node.
    Only virtual Mithi nodes are used as inputs to the Mithi flood model.
    """
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(SensorNode).where(
                SensorNode.node_type == NodeType.PHYSICAL.value,
                SensorNode.is_active == True,
            )
        )
        physical_nodes = list(rows.scalars().all())
        configured = forecast_service.settings.demo_input_node_code.strip().upper()
        if configured:
            physical_nodes = [n for n in physical_nodes if n.node_code == configured]
        latest: tuple[SensorNode, TelemetryReading] | None = None
        for node in physical_nodes:
            reading = (
                await db.execute(
                    select(TelemetryReading)
                    .where(TelemetryReading.node_id == node.id)
                    .where(TelemetryReading.source == SourceType.IOT.value)
                    .order_by(desc(TelemetryReading.recorded_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if reading is not None and (latest is None or reading.recorded_at > latest[1].recorded_at):
                latest = (node, reading)

    if latest is None:
        logger.warning("mithi_demo_pipeline_no_iot_reading")
        return

    physical_node, physical_reading = latest
    mapped_level = forecast_service.map_demo_level(physical_reading.water_level_cm)
    forecasts = await forecast_service.generate_mithi_forecasts(
        physical_node.id,
        mapped_level,
        physical_reading.rainfall_mm_hr,
        physical_reading.recorded_at,
        source="iot-demo",
    )
    steps = {}
    for time_step in (0, 15, 30, 60):
        levels = {}
        if time_step == 0:
            levels[physical_node.id] = mapped_level
        else:
            forecast = next(
                (item for item in forecasts if item.horizon_minutes == time_step),
                None,
            )
            if forecast is not None:
                levels[physical_node.id] = forecast.predicted_level_cm
        if not levels:
            continue
        result = await flood_simulation_engine.run_simulation(
            node_levels=levels,
            time_step_minutes=time_step,
            input_source="iot-demo",
        )
        await flood_simulation_engine._store_flood_layers(
            result, time_step, None, "iot-demo"
        )
        steps[time_step] = {
            "flooded_area_m2": result["flooded_area_m2"],
            "max_depth_m": result["max_depth_m"],
        }

    try:
        await publish_realtime_event(
            {
                "event": "flood.mithi.updated",
                "payload": {
                    "input_source": "iot",
                    "simulation_source": "iot-demo",
                    "label": "MITHI RIVER DIGITAL TWIN · DRIVEN BY LIVE IoT INPUT",
                    "steps": steps,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "iot",
            }
        )
    except Exception as exc:
        logger.warning("mithi_demo_pipeline_realtime_publish_failed", error=str(exc))
