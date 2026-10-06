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

_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # .../backend
REPO_ROOT = _BACKEND_ROOT.parent
CONFIG_DIR = REPO_ROOT / "config"

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
    return load_config("risk_thresholds")


def get_virtual_sensor_config() -> dict[str, Any]:
    return load_config("virtual_sensors", {"sensors": [], "hops": []})
