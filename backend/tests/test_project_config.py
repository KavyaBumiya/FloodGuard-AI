from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import project_config  # noqa: E402
from app.core.project_config import CONFIG_DIR, _resolve_config_dir, get_risk_config  # noqa: E402
from app.services.ingest import is_out_of_order  # noqa: E402


def test_source_checkout_resolves_root_config_directory(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "backend" / "config").mkdir(parents=True)
    config_dir = repo_root / "config"
    (config_dir / "risk_thresholds.json").parent.mkdir(parents=True)
    (config_dir / "risk_thresholds.json").touch()

    assert _resolve_config_dir(repo_root / "backend") == config_dir


def test_container_layout_resolves_app_config_directory(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    config_dir = app_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "risk_thresholds.json").touch()

    assert _resolve_config_dir(app_root) == config_dir


def test_runtime_loads_physical_probe_thresholds() -> None:
    assert CONFIG_DIR.joinpath("risk_thresholds.json").is_file()
    thresholds = get_risk_config()["thresholds"]["water_level_cm"]

    assert {
        key: thresholds[key] for key in ("low", "moderate", "high", "severe")
    } == {"low": 0, "moderate": 20, "high": 22, "severe": 25}


def test_missing_required_risk_config_fails_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_config, "CONFIG_DIR", tmp_path)
    project_config.reload_config()

    with pytest.raises(FileNotFoundError, match="Required risk configuration"):
        project_config.get_risk_config()

    project_config.reload_config()


def test_out_of_order_timestamp_is_rejected_across_timezone_shapes() -> None:
    latest = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)

    assert is_out_of_order(datetime(2025, 1, 1, 11, 59), latest)
    assert not is_out_of_order(datetime(2025, 1, 1, 12), latest)
    assert not is_out_of_order(datetime(2025, 1, 1, 12, 1, tzinfo=timezone.utc), latest)
