"""
FLOODGUARD AI — configuration loader.

Reads the JSON files in <repo>/config/ so that study area, sensors, risk
weights and simulation parameters have ONE source of truth shared by the
backend services. Values are cached after the first read; call
`reload_config()` if a file is edited while the process is running.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


def _resolve_config_dir(app_root: Path) -> Path:
    """Find config/ in either the source checkout or the container image."""
    for root in (app_root, app_root.parent):
        candidate = root / "config"
        if (candidate / "risk_thresholds.json").is_file():
            return candidate
    return app_root.parent / "config"


_APP_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = _resolve_config_dir(_APP_ROOT)
REPO_ROOT = CONFIG_DIR.parent

_lock = threading.Lock()
_cache: dict[str, Any] = {}


def config_path(name: str) -> Path:
    if not name.endswith(".json"):
        name = f"{name}.json"
    return CONFIG_DIR / name


def load_config(name: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load and cache a config file. Missing file -> `default` (never raises)."""
    with _lock:
        if name in _cache:
            return _cache[name]
        path = config_path(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = default if default is not None else {}
        except (json.JSONDecodeError, OSError) as exc:
            import structlog

            structlog.get_logger().warning(
                "config_load_failed", file=str(path), error=str(exc)
            )
            data = default if default is not None else {}
        _cache[name] = data
        return data


def reload_config() -> None:
    with _lock:
        _cache.clear()


def get_risk_config() -> dict[str, Any]:
    path = config_path("risk_thresholds")
    if not path.is_file():
        raise FileNotFoundError(f"Required risk configuration not found: {path}")

    config = load_config("risk_thresholds")
    water_levels = config.get("thresholds", {}).get("water_level_cm")
    if not isinstance(water_levels, dict) or not all(
        key in water_levels for key in ("low", "moderate", "high", "severe")
    ):
        raise ValueError(f"Risk configuration has invalid water-level thresholds: {path}")
    return config


def get_virtual_sensor_config() -> dict[str, Any]:
    return load_config("virtual_sensors", {"sensors": [], "hops": []})
