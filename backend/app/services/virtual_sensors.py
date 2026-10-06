"""
FLOODGUARD AI — Virtual Sensor Engine
======================================
Generates simulated sensor readings for virtual nodes (upstream, midstream, downstream).
Propagates physical sensor changes downstream with configurable delay and attenuation.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

import structlog
from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.project_config import get_virtual_sensor_config
from app.core.database import AsyncSessionLocal
from app.models.floodguard import (
    SensorNode,
    TelemetryReading,
    NodeRelationship,
    SourceType,
    DataMode,
    NodeType,
    NodeRole,
)
from app.services.telemetry import ingest_telemetry_reading
from app.services.flood_risk import DEVICE_STALE_AFTER_MINUTES

logger = structlog.get_logger()


def nearest_reading_to(node_id: int, target: datetime):
    """Select the reading for ``node_id`` whose timestamp is closest to ``target``.

    Returned as a statement rather than executed here so the ordering can be
    unit-tested without a live database (see
    ``tests/test_virtual_sensor_lag_query.py``).

    The interval-to-seconds conversion is load-bearing. Subtracting two
    timestamps yields an INTERVAL, and PostgreSQL has no ``abs(interval)``.
    An earlier version ordered by ``func.abs(recorded_at - target)`` directly,
    which raised ``UndefinedFunctionError`` on every cycle: no virtual node
    ever ingested a reading, so every node's risk assessment silently froze on
    stale data and appeared identical.
    """
    return (
        select(TelemetryReading)
        .where(TelemetryReading.node_id == node_id)
        .order_by(
            func.abs(
                extract(
                    "epoch",
                    TelemetryReading.recorded_at - target,
                )
            )
        )
        .limit(1)
    )


class VirtualSensorEngine:
    """
    Manages virtual sensor nodes and generates simulated readings.
    
    Virtual nodes react to physical node changes with:
    - Configurable delay (seconds)
    - Configurable attenuation (0-1)
    - Small bounded noise for realism
    
    Also supports scripted scenarios for repeatable demos.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._interval_seconds = 10  # Default 10 second cadence
        self._scenario_mode = False
        self._scenario_data: dict = {}
        # Documented simplification: the physical rise is damped once when it
        # enters the simulated chain, then again at each documented hop.
        # Attenuation values loaded from virtual_sensors.json config
        vs_config = get_virtual_sensor_config()
        self._head_attenuation = vs_config.get("head_attenuation", 0.9)
        self._rain_attenuation = vs_config.get("rain_attenuation", 0.8)

    async def start(self, interval_seconds: int = 10) -> None:
        """Start the virtual sensor generator."""
        if self._running:
            return

        self._interval_seconds = interval_seconds
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("virtual_sensor_engine_started", interval_seconds=interval_seconds)

    async def stop(self) -> None:
        """Stop the virtual sensor generator."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("virtual_sensor_engine_stopped")

    async def _run(self) -> None:
        """Main loop generating readings for all virtual nodes."""
        while self._running:
            try:
                await self._generate_cycle()
            except Exception as exc:
                logger.error("virtual_sensor_cycle_error", error=str(exc))
            
            await asyncio.sleep(self._interval_seconds)

    async def _generate_cycle(self) -> None:
        """Generate one reading for each active virtual node."""
        async with AsyncSessionLocal() as db:
            # Get all active virtual nodes
            result = await db.execute(
                select(SensorNode).where(
                    SensorNode.node_type == NodeType.VIRTUAL.value,
                    SensorNode.is_active == True,
                )
            )
            virtual_nodes = result.scalars().all()

            if not virtual_nodes:
                return

            # Get latest physical node reading for propagation
            physical_reading = await self._get_latest_physical_reading(db)

            for node in virtual_nodes:
                reading_data = await self._generate_reading(db, node, physical_reading)
                if reading_data:
                    await self._store_reading(db, node, reading_data)

            await db.commit()

    async def generate_now(self) -> None:
        """Run one virtual Mithi propagation cycle immediately.

        The live IoT orchestration uses this after accepting an ESP32
        observation so the demo does not wait for the periodic timer.
        """
        await self._generate_cycle()

    async def _get_latest_physical_reading(self, db: AsyncSession) -> Optional[TelemetryReading]:
        """Get most recent reading from any physical node."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=DEVICE_STALE_AFTER_MINUTES
        )
        query = (
            select(TelemetryReading)
            .join(SensorNode, TelemetryReading.node_id == SensorNode.id)
            .where(
                SensorNode.node_type == NodeType.PHYSICAL.value,
                TelemetryReading.source == SourceType.IOT.value,
                TelemetryReading.recorded_at >= cutoff,
            )
        )
        configured_code = self.settings.demo_input_node_code.strip().upper()
        if configured_code:
            query = query.where(SensorNode.node_code == configured_code)
        result = await db.execute(
            query.order_by(TelemetryReading.recorded_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def _generate_reading(
        self,
        db: AsyncSession,
        node: SensorNode,
        physical_reading: Optional[TelemetryReading],
    ) -> Optional[dict]:
        """Generate a reading for a virtual node."""
        now = datetime.now(timezone.utc)

        # Check for upstream relationship
        upstream_level = None
        upstream_time = None
        upstream_baseline = None
        upstream_rainfall = None
        
        result = await db.execute(
            select(NodeRelationship).where(
                NodeRelationship.downstream_node_id == node.id
            )
        )
        relationship = result.scalar_one_or_none()

        if relationship and physical_reading:
            # A hop's delay_seconds is a LAG, not a gate: follow the upstream
            # sample that was current delay_seconds ago. Gating on the age of
            # the newest reading can never fire, because that reading is only
            # one cycle old while the delay is longer.
            lag_until = now - timedelta(seconds=relationship.delay_seconds)
            # Find reading closest to lag_until (target time), not just latest
            # before it. Absolute time difference handles sparse readings.
            upstream_result = await db.execute(
                nearest_reading_to(
                    relationship.upstream_node_id, lag_until
                )
            )
            upstream_reading = upstream_result.scalar_one_or_none()
            
            if upstream_reading:
                upstream_level = upstream_reading.water_level_cm
                upstream_rainfall = upstream_reading.rainfall_mm_hr
                upstream_time = upstream_reading.recorded_at

                # The upstream node's own baseline is the reference the
                # deviation must be measured against; NodeRelationship does
                # not carry it, so read it from the upstream node.
                upstream_node_result = await db.execute(
                    select(SensorNode).where(
                        SensorNode.id == relationship.upstream_node_id
                    )
                )
                upstream_node = upstream_node_result.scalar_one_or_none()
                upstream_baseline = (
                    (upstream_node.baseline_level_cm if upstream_node else None) or 100.0
                )

        # Generate reading based on mode
        if self._scenario_mode and node.node_code in self._scenario_data:
            return self._generate_scenario_reading(node, now)
        elif upstream_level is not None and relationship:
            return self._generate_propagated_reading(
                node, relationship, upstream_level, upstream_time or now,
                upstream_baseline or 100.0, upstream_rainfall or 0.0, now,
            )
        elif physical_reading is not None:
            # Head of the simulated chain: follow the real sensor so a physical
            # rise actually reaches the virtual network.
            return await self._generate_from_physical(db, node, physical_reading, now)
        else:
            return self._generate_baseline_reading(node, now)

    async def _generate_from_physical(
        self,
        db: AsyncSession,
        node: SensorNode,
        physical_reading: TelemetryReading,
        now: datetime,
    ) -> dict:
        """Head-of-chain node: carry the physical sensor's deviation downstream.

        This is a simplified propagation rule, not a hydraulic model: the
        physical rise above its own baseline is attenuated once and applied to
        this node's baseline.
        """
        settings = self.settings
        physical_min = settings.demo_input_physical_min_cm
        physical_max = settings.demo_input_physical_max_cm
        mithi_min = settings.demo_input_mithi_min_cm
        mithi_max = settings.demo_input_mithi_max_cm
        if physical_max <= physical_min:
            raise ValueError("DEMO_INPUT_PHYSICAL_MAX_CM must exceed MIN_CM")
        normalized = (physical_reading.water_level_cm - physical_min) / (
            physical_max - physical_min
        )
        normalized = max(0.0, min(1.0, normalized))
        water_level = mithi_min + normalized * (mithi_max - mithi_min)
        # Rain is also an observed input. The factor is configurable and is
        # only a demo calibration, not a Mumbai rainfall measurement.
        rainfall = max(
            0.0,
            (physical_reading.rainfall_mm_hr or 0.0)
            * settings.demo_input_mithi_rainfall_factor,
        )

        return {
            "water_level_cm": round(water_level, 1),
            "rainfall_mm_hr": round(rainfall, 1),
            "timestamp": now.isoformat(),
            "source": SourceType.SIMULATED.value,
            "data_mode": DataMode.LIVE.value,
        }

    def _generate_propagated_reading(
        self,
        node: SensorNode,
        relationship: NodeRelationship,
        upstream_level: float,
        upstream_time: datetime,
        upstream_baseline: float,
        upstream_rainfall: float,
        now: datetime,
    ) -> dict:
        """Generate reading propagated from upstream with delay and attenuation."""
        baseline = node.baseline_level_cm or 100.0

        if upstream_level is None:
            # Warm-up: the lag window has not been filled with upstream history
            # yet, so hold the baseline rather than inventing a value.
            water_level = baseline
        else:
            # Propagate with attenuation: carry the upstream rise above the
            # upstream baseline downstream, damped by the relationship factor.
            deviation = upstream_level - (upstream_baseline or 100.0)
            water_level = baseline + (deviation * relationship.attenuation)
            # Add small noise

        # Ensure reasonable bounds
        water_level = max(0, min(300, water_level))

        # Downstream virtual rainfall follows the propagated simulated input.
        rainfall = max(0.0, upstream_rainfall * self._rain_attenuation)

        return {
            "water_level_cm": round(water_level, 1),
            "rainfall_mm_hr": round(rainfall, 1),
            "timestamp": now.isoformat(),
            "source": SourceType.SIMULATED.value,
            "data_mode": DataMode.LIVE.value,
        }

    def _generate_baseline_reading(self, node: SensorNode, now: datetime) -> dict:
        """Generate baseline reading for independent virtual node."""
        baseline = node.baseline_level_cm or 100.0
        
        # Small random walk around baseline
        water_level = baseline + random.uniform(-5, 5)
        water_level = max(0, min(300, water_level))
        
        rainfall = max(0, random.gauss(1.5, 1.0))

        return {
            "water_level_cm": round(water_level, 1),
            "rainfall_mm_hr": round(rainfall, 1),
            "timestamp": now.isoformat(),
            "source": SourceType.SIMULATED.value,
            "data_mode": DataMode.LIVE.value,
        }

    def _generate_scenario_reading(self, node: SensorNode, now: datetime) -> dict:
        """Generate reading from scripted scenario data."""
        scenario = self._scenario_data.get(node.node_code, {})
        curve = scenario.get("level_curve", [])
        
        if not curve:
            return self._generate_baseline_reading(node, now)

        # Find closest time point in curve
        elapsed = (now - scenario.get("start_time", now)).total_seconds()
        index = min(int(elapsed / self._interval_seconds), len(curve) - 1)
        water_level = curve[index]
        
        rainfall = scenario.get("rainfall_curve", [2.0])[min(index, len(scenario.get("rainfall_curve", [2.0])) - 1)]

        return {
            "water_level_cm": round(water_level, 1),
            "rainfall_mm_hr": round(rainfall, 1),
            "timestamp": now.isoformat(),
            "source": SourceType.SIMULATED.value,
            "data_mode": DataMode.LIVE.value,
        }

    async def _store_reading(self, db: AsyncSession, node: SensorNode, reading_data: dict) -> None:
        """Store generated reading and assess risk."""
        timestamp = datetime.fromisoformat(reading_data["timestamp"].replace("Z", "+00:00"))

        reading, risk = await ingest_telemetry_reading(
            db,
            node,
            timestamp=timestamp,
            water_level_cm=reading_data["water_level_cm"],
            rainfall_mm_hr=reading_data["rainfall_mm_hr"],
            source=reading_data["source"],
        )


    # Scenario control methods
    def load_scenario(self, scenario_data: dict) -> None:
        """Load a scripted scenario for virtual nodes."""
        self._scenario_mode = True
        self._scenario_data = scenario_data
        logger.info("virtual_scenario_loaded", nodes=list(scenario_data.keys()))

    def clear_scenario(self) -> None:
        """Clear scenario mode, return to baseline propagation."""
        self._scenario_mode = False
        self._scenario_data = {}
        logger.info("virtual_scenario_cleared")

    def set_interval(self, interval_seconds: int) -> None:
        """Update generation interval."""
        self._interval_seconds = max(1, interval_seconds)

    async def seed_virtual_network(self) -> int:
        """Create the simulated node chain and its propagation rules.

        Idempotent: safe to call on every startup. The chain is a documented
        simplified propagation model (delay + attenuation), NOT a hydraulic
        simulation, and every node it creates is labelled SIMULATED.

        Reads sensor specs and hop rules from virtual_sensors.json config.
        """
        config = get_virtual_sensor_config()
        sensors = config.get("sensors", [])
        hops = config.get("hops", [])

        if not sensors:
            logger.warning("virtual_network_seeded_no_sensors", config_keys=list(config.keys()))
            return 0

        created = 0
        async with AsyncSessionLocal() as db:
            by_code: dict[str, SensorNode] = {}
            for sensor in sensors:
                code = sensor.get("node_code")
                if not code:
                    continue
                name = sensor.get("name", code)
                role = sensor.get("role", "midstream")
                lat = sensor.get("latitude")
                lon = sensor.get("longitude")
                baseline = sensor.get("baseline_water_level_cm", 120.0)
                threshold = sensor.get("warning_threshold_cm", 180.0)
                ref_elev = sensor.get("ground_elevation_m", 1.0)
                active = sensor.get("active", True)

                if lat is None or lon is None:
                    logger.warning("virtual_sensor_missing_coords", node_code=code)
                    continue

                node = (
                    await db.execute(
                        select(SensorNode).where(SensorNode.node_code == code)
                    )
                ).scalar_one_or_none()
                if node is None:
                    node = SensorNode(
                        node_code=code,
                        name=name,
                        node_type=NodeType.VIRTUAL.value,
                        role=role,
                        location=f"POINT({lon} {lat})",
                        baseline_level_cm=baseline,
                        warning_threshold_cm=threshold,
                        reference_elevation_m=ref_elev,
                        is_active=active,
                    )
                    db.add(node)
                    await db.flush()
                    created += 1
                by_code[code] = node

            # Documented hop rules: downstream lags and damps the rise.
            for hop in hops:
                up_code = hop.get("upstream")
                down_code = hop.get("downstream")
                if not up_code or not down_code:
                    continue
                if up_code not in by_code or down_code not in by_code:
                    logger.warning("virtual_hop_missing_node", up_code=up_code, down_code=down_code)
                    continue
                delay = hop.get("delay_seconds", 60)
                atten = hop.get("attenuation", self._head_attenuation)
                distance = hop.get("distance_m", 1000.0)
                existing = (
                    await db.execute(
                        select(NodeRelationship).where(
                            NodeRelationship.upstream_node_id == by_code[up_code].id,
                            NodeRelationship.downstream_node_id == by_code[down_code].id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    db.add(
                        NodeRelationship(
                            upstream_node_id=by_code[up_code].id,
                            downstream_node_id=by_code[down_code].id,
                            delay_seconds=delay,
                            attenuation=atten,
                            distance_m=distance,
                            notes=(
                                "Simplified sensor propagation model for prototype "
                                "demonstration. Not a hydraulic simulation."
                            ),
                        )
                    )
                    created += 1

            await db.commit()

        logger.info("virtual_network_seeded", created=created)
        return created


# Global instance
virtual_sensor_engine = VirtualSensorEngine()
