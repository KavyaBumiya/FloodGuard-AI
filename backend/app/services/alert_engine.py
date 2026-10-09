"""
FLOODGUARD AI — Alert Engine
=============================
Threshold-based alert system with hysteresis to prevent flapping.

Supports:
- threshold
- predicted_risk
- rapid_rise
- communication_failure
- sensor_fault
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from logging.config import fileConfig
from typing import Optional, cast

import structlog
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.project_config import get_risk_config
from app.models.floodguard import (
    SensorNode,
    TelemetryReading,
    Forecast,
    Alert,
    AlertType,
    AlertSeverity,
    RejectedReading,
    SourceType,
)
from app.services.flood_risk import calculate_rise_rate, map_physical_to_mithi


logger = structlog.get_logger()


# ============================================================================
# CONFIGURATION
# ============================================================================

# Alert thresholds are loaded from config/risk_thresholds.json when available.
_RISK_CFG = get_risk_config()

_DEFAULTS = _RISK_CFG.get("defaults", {})
_ALERT_CFG = _DEFAULTS.get("alerts", {})
_WL = _RISK_CFG.get("thresholds", {}).get("water_level_cm", {})

THRESHOLDS = {
    # Water-level thresholds are ASCENDING depth above the sensor datum
    # (config/risk_thresholds.json: 0 = dry, 20 cm = warning, 25 cm = critical),
    # so alerts fire when the reading rises ABOVE them.
    "water_level_warning_cm": float(
        _WL.get("moderate", 20.0)
    ),
    "water_level_critical_cm": float(
        _WL.get("severe", 25.0)
    ),
    "rapid_rise_cm_per_5min": float(
        _ALERT_CFG.get("rapid_rise_cm_per_interval", 15.0)
    ),
    "stale_minutes": float(
        _DEFAULTS.get("device_stale_after_minutes", 5.0)
    ),
    "offline_minutes": 15.0,
    "sensor_fault_count": 3,  # consecutive invalid/rejected readings
}

# Hysteresis prevents alerts from rapidly toggling around a threshold.
# Only rapid rise uses it: threshold alerts clear as soon as the reading is
# back inside its band. A water-level deadband calibrated on the old ascending
# 0-300 cm scale would be far too sticky on the ascending 0-30 cm probe range,
# so `threshold_hysteresis_cm` is deliberately not read here.
HYSTERESIS = {
    "rapid_rise_cm_per_5min": 5.0,
}

# Forecast horizons that may create predicted-risk alerts.
PREDICTED_RISK_HORIZONS = [
    int(h)
    for h in _ALERT_CFG.get(
        "predicted_high_horizons_minutes",
        [15, 30, 60],
    )
]


class AlertEngine:
    """
    Evaluates alert rules and manages the alert lifecycle.

    Features:
    - Threshold alerts with hysteresis
    - Predicted risk alerts from forecasts
    - Rapid rise detection
    - Communication failure detection
    - Sensor fault detection
    - Deduplication: one active alert per node/type
    - Auto-clear when conditions resolve
    - Scenario alerts kept separate from live alerts
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._eval_interval_seconds = 30
        self._pending_node_ids: set[int] = set()
        self._wake_event = asyncio.Event()

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def start(self) -> None:
        """Start the alert evaluation loop."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run())

        logger.info("alert_engine_started")

    async def stop(self) -> None:
        """Stop the alert evaluation loop."""
        self._running = False

        if self._task:
            self._task.cancel()

            try:
                await self._task
            except asyncio.CancelledError:
                pass

            self._task = None

        logger.info("alert_engine_stopped")

    async def _run(self) -> None:
        """Main alert evaluation loop."""
        try:
            await self.evaluate_all()
        except Exception as exc:
            logger.error("alert_evaluation_error", error=str(exc))

        while self._running:
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._eval_interval_seconds,
                )
            except asyncio.TimeoutError:
                try:
                    await self.evaluate_all()
                except Exception as exc:
                    logger.error("alert_evaluation_error", error=str(exc))
                continue
            except Exception as exc:
                logger.error(
                    "alert_evaluation_error",
                    error=str(exc),
                )
                continue

            self._wake_event.clear()
            pending_node_ids = set(self._pending_node_ids)
            self._pending_node_ids.clear()
            if pending_node_ids:
                try:
                    await self.evaluate_nodes(pending_node_ids)
                except Exception as exc:
                    logger.error("alert_evaluation_error", error=str(exc))

    # =========================================================================
    # EVALUATION
    # =========================================================================

    async def evaluate_all(
        self,
        scenario_id: Optional[int] = None,
    ) -> None:
        """
        Evaluate all alert rules for all active sensor nodes.
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SensorNode).where(
                    # Access the table column directly to avoid the ORM
                    # descriptor's recursive type inference in Pyright.
                    SensorNode.__table__.c.is_active.is_(True)
                )
            )

            nodes = result.scalars().all()

            for node in nodes:
                await self._evaluate_node(
                    db,
                    node,
                    scenario_id,
                )

            await db.commit()

    def request_node_evaluation(self, node_id: int) -> None:
        """Wake the evaluator for a changed live node, coalescing rapid updates."""
        self._pending_node_ids.add(node_id)
        self._wake_event.set()

    async def evaluate_nodes(self, node_ids: set[int]) -> None:
        """Evaluate only the nodes whose telemetry has just changed."""
        if not node_ids:
            return

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SensorNode).where(
                    SensorNode.__table__.c.is_active.is_(True),
                    SensorNode.id.in_(node_ids),
                )
            )
            for node in result.scalars().all():
                await self._evaluate_node(db, node, None)
            await db.commit()

    async def _evaluate_node(
        self,
        db: AsyncSession,
        node: SensorNode,
        scenario_id: Optional[int],
    ) -> None:
        """Evaluate all alert rules for a single node."""

        reading = await self._get_latest_reading(
            db,
            node.id,
            scenario_id,
        )

        if reading:
            # Current threshold alerts.
            await self._check_threshold_alert(
                db,
                node,
                reading,
                scenario_id,
            )

            # Rapid rise alerts.
            await self._check_rapid_rise_alert(
                db,
                node,
                reading,
                scenario_id,
            )

        # Forecast-based risk alerts.
        await self._check_predicted_risk_alert(
            db,
            node,
            scenario_id,
        )

        # Communication failure alerts.
        await self._check_communication_alert(
            db,
            node,
            scenario_id,
        )

        # Sensor-fault alerts.
        await self._check_sensor_fault_alert(
            db,
            node,
            scenario_id,
        )

    # =========================================================================
    # LATEST READING
    # =========================================================================

    async def _get_latest_reading(
        self,
        db: AsyncSession,
        node_id: int,
        scenario_id: Optional[int],
    ) -> Optional[TelemetryReading]:
        """
        Get the most recent telemetry reading for a node.

        For scenarios, create a small pseudo-reading based on the latest
        scenario forecast at horizon 0.
        """

        if scenario_id is not None:
            result = await db.execute(
                select(Forecast)
                .where(
                    Forecast.node_id == node_id,
                    Forecast.scenario_id == scenario_id,
                    Forecast.horizon_minutes == 0,
                )
                .order_by(desc(Forecast.made_at))
                .limit(1)
            )

            forecast = result.scalar_one_or_none()

            if forecast:
                class PseudoReading:
                    water_level_cm = forecast.predicted_level_cm
                    rainfall_mm_hr = 0.0
                    recorded_at = forecast.made_at
                    source = "scenario"

                return cast(
                    TelemetryReading,
                    PseudoReading(),
                )

            return None

        result = await db.execute(
            select(TelemetryReading)
            .where(
                TelemetryReading.node_id == node_id,
                TelemetryReading.source.in_(
                    [
                        SourceType.IOT.value,
                        SourceType.SIMULATED.value,
                    ]
                ),
            )
            .order_by(desc(TelemetryReading.recorded_at))
            .limit(1)
        )

        return result.scalar_one_or_none()

    # =========================================================================
    # THRESHOLD ALERT
    # =========================================================================

    async def _check_threshold_alert(
        self,
        db: AsyncSession,
        node: SensorNode,
        reading: TelemetryReading,
        scenario_id: Optional[int],
    ) -> None:
        """Check water-level threshold alerts."""

        level = reading.water_level_cm

        # Every domain is ascending depth (0 = dry), so both branches compare
        # with `>=` and differ only in where the thresholds come from.
        probe_domain = reading.source not in (
            SourceType.SIMULATED.value,
            SourceType.SCENARIO.value,
        )

        if probe_domain:
            warning_thresh = float(
                node.warning_threshold_cm
                or THRESHOLDS["water_level_warning_cm"]
            )
            critical_thresh = THRESHOLDS["water_level_critical_cm"]
            if critical_thresh <= warning_thresh:
                # A stale per-device override would swallow the CRITICAL band;
                # the config wins.
                critical_thresh = max(
                    THRESHOLDS["water_level_critical_cm"],
                    warning_thresh + 1.0,
                )
        else:
            warning_thresh = (
                float(node.warning_threshold_cm)
                if node.node_type == "virtual" and node.warning_threshold_cm
                else map_physical_to_mithi(
                    THRESHOLDS["water_level_warning_cm"]
                )
            )
            critical_thresh = max(
                map_physical_to_mithi(
                    THRESHOLDS["water_level_critical_cm"]
                ),
                warning_thresh + 1.0,
            )
        breaches_critical = level >= critical_thresh
        breaches_warning = level >= warning_thresh
        direction = "exceeds"

        if breaches_critical:
            severity = AlertSeverity.CRITICAL
            threshold_type = "critical"
            threshold_value = critical_thresh

        elif breaches_warning:
            severity = AlertSeverity.HIGH
            threshold_type = "warning"
            threshold_value = warning_thresh

        else:
            await self._clear_alert(
                db,
                node.id,
                AlertType.THRESHOLD,
                scenario_id,
            )
            return

        existing = await self._get_active_alert(
            db,
            node.id,
            AlertType.THRESHOLD,
            scenario_id,
        )

        message = (
            f"{node.node_code} water level "
            f"{level:.0f} cm "
            f"{direction} "
            f"{threshold_type} threshold "
            f"({threshold_value:.0f} cm)"
        )

        if existing:
            if existing.severity != severity.value:
                existing.severity = severity.value
                existing.message = message

                existing.trigger_data = {
                    "level_cm": level,
                    "threshold_cm": threshold_value,
                }

                existing.created_at = datetime.now(
                    timezone.utc
                )

                await self._publish_alert_event(
                    existing,
                    "updated",
                )

        else:
            alert = Alert(
                node_id=node.id,
                scenario_id=scenario_id,
                alert_type=AlertType.THRESHOLD.value,
                severity=severity.value,
                message=message,
                trigger_data={
                    "level_cm": level,
                    "threshold_cm": threshold_value,
                },
                is_scenario_alert=scenario_id is not None,
            )

            db.add(alert)

            await db.flush()

            await self._publish_alert_event(
                alert,
                "created",
            )

    # =========================================================================
    # RAPID RISE ALERT
    # =========================================================================

    async def _check_rapid_rise_alert(
        self,
        db: AsyncSession,
        node: SensorNode,
        reading: TelemetryReading,
        scenario_id: Optional[int],
    ) -> None:
        """Check for rapid water-level rise."""

        if scenario_id is not None:
            return

        result = await db.execute(
            select(TelemetryReading)
            .where(
                TelemetryReading.node_id == node.id,
                TelemetryReading.recorded_at
                < reading.recorded_at,
                TelemetryReading.source.in_(
                    [
                        SourceType.IOT.value,
                        SourceType.SIMULATED.value,
                    ]
                ),
            )
            .order_by(desc(TelemetryReading.recorded_at))
            .limit(1)
        )

        previous = result.scalar_one_or_none()

        if not previous:
            return

        reading_recorded_at = cast(datetime, reading.recorded_at)
        previous_recorded_at = cast(datetime, previous.recorded_at)
        interval_seconds = (
            reading_recorded_at
            - previous_recorded_at
        ).total_seconds()

        if interval_seconds <= 0:
            return

        rise_rate = calculate_rise_rate(
            reading.water_level_cm,
            previous.water_level_cm,
            interval_seconds,
        )

        threshold = THRESHOLDS[
            "rapid_rise_cm_per_5min"
        ]

        if rise_rate >= threshold:
            severity = (
                AlertSeverity.HIGH
                if rise_rate < threshold * 2
                else AlertSeverity.CRITICAL
            )

            existing = await self._get_active_alert(
                db,
                node.id,
                AlertType.RAPID_RISE,
                scenario_id,
            )

            message = (
                f"{node.node_code} rapid rise: "
                f"{rise_rate:.1f} cm/5min "
                f"(threshold: {threshold:.0f} cm/5min)"
            )

            if existing:
                if existing.severity != severity.value:
                    existing.severity = severity.value
                    existing.message = message
                    existing.trigger_data = {
                        "rise_rate_cm_per_5min": rise_rate,
                    }

                    await self._publish_alert_event(
                        existing,
                        "updated",
                    )

            else:
                alert = Alert(
                    node_id=node.id,
                    scenario_id=scenario_id,
                    alert_type=AlertType.RAPID_RISE.value,
                    severity=severity.value,
                    message=message,
                    trigger_data={
                        "rise_rate_cm_per_5min": rise_rate,
                    },
                    is_scenario_alert=False,
                )

                db.add(alert)

                await db.flush()

                await self._publish_alert_event(
                    alert,
                    "created",
                )

        else:
            if rise_rate < (
                threshold
                - HYSTERESIS["rapid_rise_cm_per_5min"]
            ):
                await self._clear_alert(
                    db,
                    node.id,
                    AlertType.RAPID_RISE,
                    scenario_id,
                )

    # =========================================================================
    # PREDICTED RISK ALERT
    # =========================================================================

    async def _check_predicted_risk_alert(
        self,
        db: AsyncSession,
        node: SensorNode,
        scenario_id: Optional[int],
    ) -> None:
        """Check forecast-based predicted risk alerts."""

        result = await db.execute(
            select(Forecast)
            .where(
                Forecast.node_id == node.id,
                Forecast.scenario_id == scenario_id,
                Forecast.horizon_minutes.in_(
                    PREDICTED_RISK_HORIZONS
                ),
                Forecast.status == "ok",
            )
            .order_by(
                Forecast.horizon_minutes,
                desc(Forecast.made_at),
            )
        )

        # Every prediction cycle appends new rows. Evaluating all of them kept
        # alerts alive from forecasts that were superseded many cycles ago, and
        # which row won a horizon depended on query order. Keep only the newest
        # issuance per horizon.
        newest: dict[int, Forecast] = {}
        for row in result.scalars().all():
            newest.setdefault(row.horizon_minutes, row)
        forecasts = [newest[h] for h in sorted(newest)]

# Forecasts are stored on the ascending Mithi stage scale, so the
# physical thresholds are mapped onto that scale.
        warning_mithi = map_physical_to_mithi(
            THRESHOLDS["water_level_warning_cm"]
        )
        critical_mithi = map_physical_to_mithi(
            THRESHOLDS["water_level_critical_cm"]
        )

        breaches: dict[int, AlertSeverity] = {}
        for forecast in forecasts:
            if forecast.predicted_level_cm >= critical_mithi:
                breaches[forecast.horizon_minutes] = AlertSeverity.CRITICAL
            elif forecast.predicted_level_cm >= warning_mithi:
                breaches[forecast.horizon_minutes] = AlertSeverity.HIGH

        for forecast in forecasts:
            severity = breaches.get(forecast.horizon_minutes)
            if severity is None:
                # Forecast does not currently trigger an alert.
                continue

            existing = await self._get_active_alert(
                db,
                node.id,
                AlertType.PREDICTED_RISK,
                scenario_id,
                horizon_minutes=forecast.horizon_minutes,
            )

            if existing:
                if existing.severity != severity.value:
                    existing.severity = severity.value
                    await self._publish_alert_event(
                        existing,
                        "updated",
                    )
                continue

            alert = Alert(
                node_id=node.id,
                scenario_id=scenario_id,
                alert_type=AlertType.PREDICTED_RISK.value,
                severity=severity.value,
                message=(
                    f"{node.node_code} forecast "
                    f"+{forecast.horizon_minutes}min: "
                    f"{forecast.predicted_level_cm:.0f} cm "
                    f"({severity.value.upper()})"
                ),
                trigger_data={
                    "horizon_minutes": forecast.horizon_minutes,
                    "predicted_level_cm": (
                        forecast.predicted_level_cm
                    ),
                },
                is_scenario_alert=scenario_id is not None,
            )

            db.add(alert)

            await db.flush()

            await self._publish_alert_event(
                alert,
                "created",
            )

        # Predicted alerts used to be created once and never cleared, so a
        # stale forecast breach stayed on the board forever. Clear any active
        # predicted alert whose horizon no longer breaches.
        active_result = await db.execute(
            select(Alert).where(
                Alert.node_id == node.id,
                Alert.alert_type == AlertType.PREDICTED_RISK.value,
                Alert.is_active.is_(True),
                Alert.scenario_id == scenario_id,
            )
        )
        for alert in active_result.scalars().all():
            horizon = (alert.trigger_data or {}).get("horizon_minutes")
            if horizon in breaches:
                continue
            alert.is_active = False
            alert.cleared_at = datetime.now(timezone.utc)
            await self._publish_alert_event(alert, "cleared")

    # =========================================================================
    # COMMUNICATION ALERT
    # =========================================================================

    async def _check_communication_alert(
        self,
        db: AsyncSession,
        node: SensorNode,
        scenario_id: Optional[int],
    ) -> None:
        """Check for stale/offline communication."""

        if scenario_id is not None:
            return

        result = await db.execute(
            select(TelemetryReading.recorded_at)
            .where(
                TelemetryReading.node_id == node.id,
                TelemetryReading.source.in_(
                    [
                        SourceType.IOT.value,
                        SourceType.SIMULATED.value,
                    ]
                ),
            )
            .order_by(desc(TelemetryReading.recorded_at))
            .limit(1)
        )

        latest_time = result.scalar_one_or_none()

        if not latest_time:
            # Node has never reported.
            return

        if latest_time.tzinfo is None:
            latest_time = latest_time.replace(
                tzinfo=timezone.utc
            )
        else:
            latest_time = latest_time.astimezone(
                timezone.utc
            )

        now = datetime.now(timezone.utc)

        minutes_since = (
            now - latest_time
        ).total_seconds() / 60.0

        if (
            minutes_since
            >= THRESHOLDS["offline_minutes"]
        ):
            severity = AlertSeverity.CRITICAL
            communication_state = "offline"

        elif (
            minutes_since
            >= THRESHOLDS["stale_minutes"]
        ):
            severity = AlertSeverity.WARNING
            communication_state = "stale"

        else:
            await self._clear_alert(
                db,
                node.id,
                AlertType.COMMUNICATION_FAILURE,
                scenario_id,
            )
            return

        existing = await self._get_active_alert(
            db,
            node.id,
            AlertType.COMMUNICATION_FAILURE,
            scenario_id,
        )

        message = (
            f"{node.node_code} communication "
            f"{communication_state}: "
            f"no data for {minutes_since:.0f} minutes"
        )

        trigger_data = {
            "minutes_since_last_reading": minutes_since
        }

        if existing:
            # Update severity/message if a stale node becomes offline.
            if (
                existing.severity != severity.value
                or existing.message != message
            ):
                existing.severity = severity.value
                existing.message = message
                existing.trigger_data = trigger_data

                await self._publish_alert_event(
                    existing,
                    "updated",
                )

        else:
            alert = Alert(
                node_id=node.id,
                scenario_id=scenario_id,
                alert_type=AlertType.COMMUNICATION_FAILURE.value,
                severity=severity.value,
                message=message,
                trigger_data=trigger_data,
                is_scenario_alert=False,
            )

            db.add(alert)

            await db.flush()

            await self._publish_alert_event(
                alert,
                "created",
            )

    # =========================================================================
    # SENSOR FAULT ALERT
    # =========================================================================

    async def _check_sensor_fault_alert(
        self,
        db: AsyncSession,
        node: SensorNode,
        scenario_id: Optional[int],
    ) -> None:
        """Check for repeated invalid/rejected readings."""

        if scenario_id is not None:
            return

        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(hours=1)
        )

        result = await db.execute(
            select(RejectedReading)
            .where(
                RejectedReading.node_code
                == node.node_code,
                RejectedReading.received_at
                >= cutoff,
            )
            .order_by(desc(RejectedReading.received_at))
            .limit(
                THRESHOLDS["sensor_fault_count"]
            )
        )

        recent_rejected = result.scalars().all()

        if (
            len(recent_rejected)
            >= THRESHOLDS["sensor_fault_count"]
        ):
            existing = await self._get_active_alert(
                db,
                node.id,
                AlertType.SENSOR_FAULT,
                scenario_id,
            )

            if existing:
                return

            alert = Alert(
                node_id=node.id,
                scenario_id=scenario_id,
                alert_type=AlertType.SENSOR_FAULT.value,
                severity=AlertSeverity.HIGH.value,
                message=(
                    f"{node.node_code} sensor fault: "
                    f"{len(recent_rejected)} invalid readings "
                    f"in last hour"
                ),
                trigger_data={
                    "rejected_count": len(recent_rejected)
                },
                is_scenario_alert=False,
            )

            db.add(alert)

            await db.flush()

            await self._publish_alert_event(
                alert,
                "created",
            )

    # =========================================================================
    # ACTIVE ALERT LOOKUP
    # =========================================================================

    async def _get_active_alert(
        self,
        db: AsyncSession,
        node_id: int,
        alert_type: AlertType,
        scenario_id: Optional[int],
        extra_filter=None,
        horizon_minutes: Optional[int] = None,
    ) -> Optional[Alert]:
        """
        Get the active alert for a node/type.

        `extra_filter` must be a predicate on the Alert table.

        Horizon matching is done against trigger_data to avoid unnecessary
        joins against Forecast.

        If duplicates exist, keep the most severe/recent alert and mark the
        others inactive while preserving the audit trail.
        """

        query = select(Alert).where(
            Alert.node_id == node_id,
            Alert.alert_type == alert_type.value,
            Alert.is_active.is_(True),
            Alert.scenario_id == scenario_id,
        )

        if extra_filter is not None:
            query = query.where(extra_filter)

        query = query.order_by(
            Alert.created_at.desc(),
            Alert.id.desc(),
        )

        result = await db.execute(query)

        # Explicit list conversion prevents the SQLAlchemy/Pylance
        # Sequence[Alert] vs list[Alert] type mismatch.
        rows: list[Alert] = list(
            result.scalars().all()
        )

        if horizon_minutes is not None:
            rows = [
                row
                for row in rows
                if (row.trigger_data or {}).get(
                    "horizon_minutes"
                )
                == horizon_minutes
            ]

        if not rows:
            return None

        severity_order = {
            AlertSeverity.CRITICAL.value: 4,
            AlertSeverity.HIGH.value: 3,
            AlertSeverity.WARNING.value: 2,
            AlertSeverity.INFORMATION.value: 1,
        }

        rows.sort(
            key=lambda row: (
                severity_order.get(
                    row.severity,
                    0,
                ),
                row.created_at,
            ),
            reverse=True,
        )

        kept = rows[0]

        for duplicate in rows[1:]:
            duplicate.is_active = False
            duplicate.cleared_at = (
                datetime.now(timezone.utc)
            )

            if duplicate.trigger_data is None:
                duplicate.trigger_data = {}

            duplicate.trigger_data[
                "superseded_by"
            ] = kept.id

            duplicate.trigger_data[
                "superseded_reason"
            ] = (
                "deduplication: kept more severe/recent alert"
            )

            logger.info(
                "alert_deduplicated",
                superseded_id=duplicate.id,
                kept_id=kept.id,
                superseded_severity=(
                    duplicate.severity
                ),
                kept_severity=kept.severity,
            )

        return kept

    # =========================================================================
    # CLEAR ALERT
    # =========================================================================

    async def _clear_alert(
        self,
        db: AsyncSession,
        node_id: int,
        alert_type: AlertType,
        scenario_id: Optional[int],
    ) -> None:
        """Clear every active alert of a given type."""

        result = await db.execute(
            select(Alert).where(
                Alert.node_id == node_id,
                Alert.alert_type == alert_type.value,
                Alert.is_active.is_(True),
                Alert.scenario_id == scenario_id,
            )
        )

        alerts = result.scalars().all()

        for alert in alerts:
            alert.is_active = False
            alert.cleared_at = (
                datetime.now(timezone.utc)
            )

            await self._publish_alert_event(
                alert,
                "cleared",
            )

    # =========================================================================
    # ACKNOWLEDGE
    # =========================================================================

    async def acknowledge_alert(
        self,
        alert_id: int,
        acknowledged_by: str,
    ) -> Optional[Alert]:
        """Acknowledge an alert."""

        async with AsyncSessionLocal() as db:
            alert = await db.get(
                Alert,
                alert_id,
            )

            if alert:
                alert.acknowledged = True
                alert.acknowledged_at = (
                    datetime.now(timezone.utc)
                )
                alert.acknowledged_by = acknowledged_by

                await db.commit()

                await self._publish_alert_event(
                    alert,
                    "acknowledged",
                )

                return alert

        return None

    # =========================================================================
    # REAL-TIME EVENT
    # =========================================================================

    async def _publish_alert_event(
        self,
        alert: Alert,
        action: str,
    ) -> None:
        """Publish alert event for real-time updates."""

        try:
            event = {
                "event": "flood.alert.updated",
                "payload": {
                    "alert_id": alert.id,
                    "node_id": alert.node_id,
                    "scenario_id": alert.scenario_id,
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "message": alert.message,
                    "is_active": alert.is_active,
                    "acknowledged": alert.acknowledged,
                    "action": action,
                },
                "timestamp": (
                    datetime.now(timezone.utc)
                    .isoformat()
                ),
                "source": "alert_engine",
            }

            from app.services.websocket import (
                publish_realtime_event
            )

            await publish_realtime_event(event)

        except Exception as exc:
            logger.warning(
                "alert_publish_failed",
                error=str(exc),
            )

    # =========================================================================
    # QUERY ACTIVE ALERTS
    # =========================================================================

    async def get_active_alerts(
        self,
        scenario_id: Optional[int] = None,
    ) -> list[Alert]:
        """Get all active alerts."""

        async with AsyncSessionLocal() as db:
            query = select(Alert).where(
                Alert.is_active.is_(True)
            )

            if scenario_id is not None:
                query = query.where(
                    Alert.scenario_id == scenario_id
                )
            else:
                query = query.where(
                    Alert.scenario_id.is_(None)
                )

            query = query.order_by(
                Alert.severity.desc(),
                Alert.created_at.desc(),
            )

            result = await db.execute(query)

            # FIX: SQLAlchemy returns Sequence[Alert] here.
            return list(result.scalars().all())

    # =========================================================================
    # QUERY ALERT HISTORY
    # =========================================================================

    async def get_alert_history(
        self,
        node_id: Optional[int] = None,
        scenario_id: Optional[int] = None,
        hours: int = 24,
        limit: int = 100,
    ) -> list[Alert]:
        """Get alert history."""

        async with AsyncSessionLocal() as db:
            query = select(Alert).where(
                Alert.created_at
                >= datetime.now(timezone.utc)
                - timedelta(hours=hours)
            )

            if node_id is not None:
                query = query.where(
                    Alert.node_id == node_id
                )

            if scenario_id is not None:
                query = query.where(
                    Alert.scenario_id == scenario_id
                )
            else:
                query = query.where(
                    Alert.scenario_id.is_(None)
                )

            query = (
                query
                .order_by(desc(Alert.created_at))
                .limit(limit)
            )

            result = await db.execute(query)

            # FIX: SQLAlchemy returns Sequence[Alert] here.
            return list(result.scalars().all())


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

alert_engine = AlertEngine()