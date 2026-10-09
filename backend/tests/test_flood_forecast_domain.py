from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.flood_monitor import _forecast_for_node_domain  # noqa: E402
from app.services.flood_risk import map_physical_to_mithi  # noqa: E402


def _forecast(value: float) -> SimpleNamespace:
    return SimpleNamespace(
        horizon_minutes=15,
        predicted_level_cm=map_physical_to_mithi(value),
        lower_bound_cm=map_physical_to_mithi(value - 2),
        upper_bound_cm=map_physical_to_mithi(value + 2),
        target_time=datetime.now(timezone.utc),
        made_at=datetime.now(timezone.utc),
        status="ok",
    )


@pytest.mark.parametrize("node_type", ["physical", "virtual"])
def test_forecast_values_are_returned_in_node_domain(node_type: str):
    node = SimpleNamespace(node_type=node_type)
    payload = _forecast_for_node_domain(node, _forecast(20))

    if node_type == "physical":
        assert payload["predicted_level_cm"] == pytest.approx(20)
        assert payload["lower_bound_cm"] == pytest.approx(18)
        assert payload["upper_bound_cm"] == pytest.approx(22)
    else:
        assert payload["predicted_level_cm"] == pytest.approx(map_physical_to_mithi(20))
        assert payload["lower_bound_cm"] == pytest.approx(map_physical_to_mithi(18))
        assert payload["upper_bound_cm"] == pytest.approx(map_physical_to_mithi(22))
