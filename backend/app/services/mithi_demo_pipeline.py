"""Real Vadodara demo input -> simulated Mithi processing pipeline."""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import desc, select

from app.core.database import AsyncSessionLocal
from app.models.floodguard import SensorNode, TelemetryReading, NodeType, SourceType
from app.services.flood_simulation import flood_simulation_engine
from app.services.forecast_service import forecast_service
from app.services.websocket import publish_realtime_event

logger = structlog.get_logger()


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
                    "label": "MITHI RIVER DIGITAL TWIN · DRIVEN BY REAL IoT DEMO INPUT",
                    "steps": steps,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "iot",
            }
        )
    except Exception as exc:
        logger.warning("mithi_demo_pipeline_realtime_publish_failed", error=str(exc))
