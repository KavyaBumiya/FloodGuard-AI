from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.flood_monitor import _current_risk_from_reading  # noqa: E402
from app.models.floodguard import RiskAssessment, SensorNode, TelemetryReading  # noqa: E402
from app.services.alert_engine import AlertEngine  # noqa: E402
from app.services.telemetry import is_sensor_online  # noqa: E402


def test_current_risk_uses_the_displayed_telemetry_reading() -> None:
    timestamp = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
    reading = TelemetryReading(
        recorded_at=timestamp,
        risk_level="SEVERE",
        risk_score=0.9,
    )
    matching_assessment = RiskAssessment(
        assessed_at=timestamp,
        primary_driver="water_level",
    )
    stale_assessment = RiskAssessment(
        assessed_at=datetime(2025, 1, 1, 11, 59, tzinfo=timezone.utc),
        primary_driver="rainfall",
    )

    assert _current_risk_from_reading(reading, matching_assessment) == {
        "risk_level": "severe",
        "risk_score": 0.9,
        "primary_driver": "water_level",
    }
    assert _current_risk_from_reading(reading, stale_assessment) == {
        "risk_level": "severe",
        "risk_score": 0.9,
        "primary_driver": None,
    }


def test_alert_evaluation_requests_coalesce_per_node() -> None:
    engine = AlertEngine()

    engine.request_node_evaluation(7)
    engine.request_node_evaluation(7)
    engine.request_node_evaluation(9)

    assert engine._pending_node_ids == {7, 9}
    assert engine._wake_event.is_set()


def test_online_counts_require_a_fresh_reading() -> None:
    now = datetime.now(timezone.utc)
    physical = SensorNode(node_type="physical")
    virtual = SensorNode(node_type="virtual")
    fresh_iot = TelemetryReading(recorded_at=now, source="iot")
    old_iot = TelemetryReading(
        recorded_at=now - timedelta(minutes=6),
        source="iot",
    )
    old_virtual = TelemetryReading(
        recorded_at=now - timedelta(minutes=16),
        source="simulated",
    )

    assert is_sensor_online(physical, fresh_iot)
    assert not is_sensor_online(physical, old_iot)
    assert not is_sensor_online(physical, None)
    assert not is_sensor_online(virtual, old_virtual)
