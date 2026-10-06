"""
FLOODGUARD AI — Central configuration.

Every setting is loaded from environment variables. Secrets are read
server-side only and are never serialised into an HTTP response.

Search order: CWD .env -> repo-root .env -> backend/.env
(local uvicorn usually runs with CWD=backend/; Docker injects env vars).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # .../backend
_PROJECT_ROOT = _BACKEND_ROOT.parent  # repo root
_REPO_ROOT = _PROJECT_ROOT
_ENV_FILES = (
    Path(".env"),
    _PROJECT_ROOT / ".env",
    _BACKEND_ROOT / ".env",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=tuple(str(p) for p in _ENV_FILES),
        extra="ignore",
        env_prefix="",
    )

    # ── Database ─────────────────────────────────────────────────────────
    # Default target is the docker-compose `db` service (PostGIS).
    database_url: str = "postgresql+asyncpg://floodguard:floodguard@db:5432/floodguard"

    # ── Redis (cache + Socket.IO event bus) ──────────────────────────────
    redis_url: str = "redis://redis:6379/0"

    # ── MQTT broker (ESP32 telemetry ingress) ────────────────────────────
    mqtt_broker_host: str = "mqtt"
    mqtt_broker_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_topic_prefix: str = "floodguard"
    # Set false to disable the MQTT subscriber entirely (HTTP ingest only).
    mqtt_keepalive_seconds: int = 60
    mqtt_enabled: bool = True

    # ── Device auth ──────────────────────────────────────────────────────
    # Bootstrap secret gating POST /api/iot/devices (registration only).
    iot_registration_token: str = ""
    # Per-device telemetry rate limit (requests/minute).
    iot_telemetry_rate_limit_per_minute: int = 12

    # ── External data sources ────────────────────────────────────────────
    open_meteo_api_base: str = "https://api.open-meteo.com/v1"
    open_meteo_archive_base: str = "https://archive-api.open-meteo.com/v1/era5"
    opentopography_api_key: str = ""
    opentopography_base_url: str = "https://portal.opentopography.org/API/globaldem"
    overpass_api_url: str = "https://overpass-api.de/api/interpreter"
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "FLOODGUARD-AI/1.0"

    # Optional: only needed for Cesium World Terrain / OSM Buildings.
    # Blank => the app renders bundled offline SRTM + OSM extrusion instead.
    cesium_ion_token: str = ""

    # ── Study area: Mithi River, BKC -> Kurla, Mumbai ────────────────────
    study_area_min_lat: float = 19.0500
    study_area_max_lat: float = 19.0900
    study_area_min_lon: float = 72.8500
    study_area_max_lon: float = 72.9000
    # Representative point used for Open-Meteo point queries.
    study_area_lat: float = 19.0713
    study_area_lon: float = 72.8830
    # DEM download bbox (tighter than the study area).
    terrain_min_lat: float = 19.0600
    terrain_max_lat: float = 19.0850
    terrain_min_lon: float = 72.8600
    terrain_max_lon: float = 72.8900
    # Flood simulation grid cell size in metres.
    flood_grid_resolution_m: float = 10.0
    flood_connectivity_buffer_m: float = 50.0
    flood_max_depth_m: float = 5.0
    flood_depth_risk_low_m: float = 0.1
    flood_depth_risk_moderate_m: float = 0.3
    flood_depth_risk_high_m: float = 0.6
    flood_depth_risk_severe_m: float = 1.0

    # ── Real demo input -> simulated Mithi calibration ───────────────────
    # These are configurable demo calibration points, not a geographic or
    # hydraulic relationship between Vadodara and Mumbai.
    demo_input_physical_min_cm: float = 0.0
    demo_input_node_code: str = ""
    demo_input_physical_max_cm: float = 30.0
    demo_input_mithi_min_cm: float = 118.0
    demo_input_mithi_max_cm: float = 220.0
    demo_input_mithi_rainfall_factor: float = 0.8

    # ── Local dataset paths ──────────────────────────────────────────────
    # Terrain raster (Copernicus GLO-30 GeoTIFF) clipped to the study area by
    # scripts/preprocess_data.py. Real elevation data, not synthetic.
    terrain_raster_path: str = "data/terrain/dem_mithi.tif"
    # IMD gridded rainfall NetCDF directory (raw, offline input only).
    imd_data_dir: str = "data/IMD"
    # IMD rainfall after offline preprocessing -> compact Mumbai JSON.
    imd_processed_dir: str = "data/rainfall/imd/processed"
    # OSM layers split offline by scripts/preprocess_data.py. The raw
    # overpass turbo.geojson is never served to a browser.
    osm_buildings_geojson: str = "data/osm/buildings.geojson"
    osm_roads_geojson: str = "data/osm/roads.geojson"
    osm_waterways_geojson: str = "data/osm/waterways.geojson"

    # ── ML model paths ───────────────────────────────────────────────────
    flood_model_dir: str = "models/flood"
    flood_model_15min: str = "rf_15min.joblib"
    flood_model_30min: str = "rf_30min.joblib"
    flood_model_60min: str = "rf_60min.joblib"

    # ── Risk thresholds (metres above sensor datum) ──────────────────────
    risk_threshold_moderate_m: float = 1.5
    risk_threshold_high_m: float = 3.0

    # ── Demo / replay ────────────────────────────────────────────────────
    demo_mode_enabled: bool = True
    demo_replay_speed: float = 1.0

    # When the real terrain raster is missing, flood simulation must fail loudly
    # rather than quietly substituting synthetic elevation: synthetic results are
    # not real inundation and must never be labelled as such. Opt in explicitly.
    allow_synthetic_terrain: bool = False

    # ── App ──────────────────────────────────────────────────────────────
    app_env: str = "development"
    media_dir: str = "media/audio"
    # Comma-separated browser origins for CORS. Empty = allow all (local dev).
    frontend_origin: str = ""
    frontend_origin_regex: str = ""

    # ── Derived properties ───────────────────────────────────────────────
    @property
    def study_area_bbox(self) -> dict[str, float]:
        return {
            "south": self.study_area_min_lat,
            "west": self.study_area_min_lon,
            "north": self.study_area_max_lat,
            "east": self.study_area_max_lon,
        }

    @property
    def is_development(self) -> bool:
        return (self.app_env or "").strip().lower() in {"dev", "development", "local"}

    @property
    def cors_origins(self) -> list[str]:
        raw = (self.frontend_origin or "").strip()
        if not raw:
            return ["*"]
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        return origins or ["*"]

    @property
    def cors_origin_regex(self) -> str | None:
        patterns: list[str] = []
        custom = (self.frontend_origin_regex or "").strip()
        if custom:
            patterns.append(custom)
        if self.is_development:
            # Dev servers pick a random free port; allow loopback in dev only.
            patterns.append(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")
        if not patterns:
            return None
        return "|".join(f"(?:{p})" for p in patterns)

    def is_origin_allowed(self, origin: str | None) -> bool:
        """Single origin check shared by REST CORS and the Socket.IO handshake."""
        if not origin:
            # Non-browser clients (curl, health checks) send no Origin.
            return True
        if "*" in self.cors_origins:
            return True
        if origin in self.cors_origins:
            return True
        pattern = self.cors_origin_regex
        if pattern and re.fullmatch(pattern, origin):
            return True
        return False

    @property
    def cors_allow_credentials(self) -> bool:
        # Wildcard origin + credentials is invalid per the CORS spec.
        return "*" not in self.cors_origins

    @property
    def opentopography_configured(self) -> bool:
        return bool(self.opentopography_api_key)

    def resolve_data_path(self, relative_path: str) -> Path:
        """Resolve a configured dataset path against the repository root.

        Settings hold repo-relative paths ("data/terrain/dem_mithi.tif") but the
        process CWD varies (uvicorn from backend/, alembic, scripts, tests), so
        resolving against CWD silently produced a missing file and a fallback to
        synthetic data. Resolve against the repo root instead.
        """
        candidate = Path(relative_path)
        if candidate.is_absolute():
            return candidate
        return _REPO_ROOT / candidate


@lru_cache
def get_settings() -> Settings:
    return Settings()