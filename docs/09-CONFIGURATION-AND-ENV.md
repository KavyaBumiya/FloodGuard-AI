# 09 — Configuration and Environment

---

## 1. `backend/.env` (the file on disk right now)

**64 lines.** Read by `pydantic-settings` in this search order (`config.py:21-25`):
`CWD/.env` → `<repo>/.env` → `<repo>/backend/.env`. `.env.*` is gitignored; `.env.example` is not.

### Database / cache / auth

| Variable | Value on disk | Used? |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://floodguard:floodguard@localhost:5432/floodguard` | ✅ the real DB target |
| `REDIS_URL` | `redis://localhost:6379/0` | ✅ |
| `JWT_SECRET` | `your-super-secret-jwt-key-change-in-production` | ❌ unused (no JWT code exists) |
| `JWT_ALGORITHM` | `HS256` | ❌ |
| `JWT_EXPIRE_MINUTES` | `60` | ❌ |

> ⚠️ Note the password here is `floodguard`, but `docker-compose.yml:7` sets
> `POSTGRES_PASSWORD: floodguard_secret`, and `alembic.ini:4` and `config.py:37` both use
> **`floodguard`** (`floodguard:floodguard@…`). So the compose file and the app disagree: the app
> will fail to authenticate against the compose database unless the password is aligned.

### IoT

| Variable | Value on disk | Used? |
|---|---|---|
| `IOT_REGISTRATION_TOKEN` | `your-registration-token-here` | ✅ **gates `POST /api/iot/devices/register`. Change it.** |
| `MQTT_BROKER_HOST` | `localhost` | ✅ |
| `MQTT_BROKER_PORT` | `1883` | ✅ |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | empty | ✅ if set (broker is anonymous, so leave empty) |
| `MQTT_TOPIC_PREFIX` | `floodguard` | ✅ — produces `floodguard/nodes/+/telemetry` |

### External providers

| Variable | Value on disk | Used? |
|---|---|---|
| `OPENTOPOGRAPHY_API_KEY` | empty | ❌ (`opentopography_configured` is never read) |
| `OPENTOPOGRAPHY_BASE_URL` | `https://portal.opentopography.org/API/globaldem` | ❌ |
| `CESIUM_ION_TOKEN` | empty | ❌ (the twin is three.js) |
| `OPEN_METEO_API_BASE` | `https://api.open-meteo.com/v1` | ✅ — `/health` probe only |
| `OPEN_METEO_ARCHIVE_BASE` | `https://archive-api.open-meteo.com/v1/era5` | ❌ |
| `OVERPASS_API_URL` | `https://overpass-api.de/api/interpreter` | ❌ (extract downloaded out-of-band) |
| `NOMINATIM_BASE_URL` | `https://nominatim.openstreetmap.org` | ❌ |
| `NOMINATIM_USER_AGENT` | `FLOODGUARD-AI/1.0` | ❌ |

### ML model paths

| Variable | Value on disk | Resolved to |
|---|---|---|
| `FLOOD_MODEL_DIR` | `models/flood` | `<repo>/models/flood` |
| `FLOOD_MODEL_15MIN` | `rf_15min.joblib` | `<repo>/models/flood/rf_15min.joblib` |
| `FLOOD_MODEL_30MIN` | `rf_30min.joblib` | … |
| `FLOOD_MODEL_60MIN` | `rf_60min.joblib` | … |

Read by `forecast_service.load_models()` via `settings.resolve_data_path(...)`, which is why the
models load regardless of the process CWD.

### Demo settings

| Variable | Value on disk | Used? |
|---|---|---|
| `DEMO_MODE_ENABLED` | `true` | ❌ `demo_mode_enabled` is declared in `config.py:128` and never read |
| `DEMO_REPLAY_SPEED` | `1.0` | ❌ same |
| `DEMO_RESET_ON_STARTUP` | `false` | ❌ not even declared in `config.py` |

> ⚠️ `.env` has **no `DEMO_INPUT_*` block at all.** The defaults in `config.py:96-101` apply:
> `DEMO_INPUT_NODE_CODE=""`, physical `0→30 cm`, Mithi `118→220 cm`, rainfall factor `0.8`.
> `.env.example` *does* document the block (lines 58-65) with the same values.

### App / CORS

| Variable | Value on disk | Effect |
|---|---|---|
| `APP_ENV` | `development` | → `is_development = True` → the loopback CORS regex is appended |
| `MEDIA_DIR` | `media/audio` | ❌ unused |
| `FRONTEND_ORIGIN` | `http://localhost:3000` | `cors_origins = ["http://localhost:3000"]` |
| `FRONTEND_ORIGIN_REGEX` | empty | combined with the dev loopback pattern |

Resulting `cors_origin_regex`: `https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?`
(`config.py:171-176`).

---

## 2. `backend/.env.example` (73 lines) — differences from `.env`

| Key | `.env` | `.env.example` |
|---|---|---|
| `DATABASE_URL` password | `floodguard` | `floodguard_secret` ← **matches docker-compose** |
| `DEMO_INPUT_NODE_CODE` | *absent* | `` (empty) |
| `DEMO_INPUT_PHYSICAL_MIN_CM` | *absent* | `0` |
| `DEMO_INPUT_PHYSICAL_MAX_CM` | *absent* | `30` |
| `DEMO_INPUT_MITHI_MIN_CM` | *absent* | `118` |
| `DEMO_INPUT_MITHI_MAX_CM` | *absent* | `220` |
| `DEMO_INPUT_MITHI_RAINFALL_FACTOR` | *absent* | `0.8` |

The `DEMO_INPUT_*` block carries this comment (lines 58-59):
> "Vadodara real demo input → simulated Mithi calibration. This is a configurable demonstration
> mapping, not a hydraulic relationship."

---

## 3. Every `Settings` field (`backend/app/core/config.py`)

`env_prefix = ""`, `extra = "ignore"`. All are case-insensitive by pydantic convention.

### Database

| Field | Type | Default | Used |
|---|---|---|---|
| `database_url` | str | `postgresql+asyncpg://floodguard:floodguard@db:5432/floodguard` | ✅ (Docker default; `.env` overrides to localhost) |

### Redis / MQTT

| Field | Type | Default | Used |
|---|---|---|---|
| `redis_url` | str | `redis://redis:6379/0` | ✅ |
| `mqtt_broker_host` | str | `mqtt` | ✅ |
| `mqtt_broker_port` | int | `1883` | ✅ |
| `mqtt_username` | str | `""` | ✅ |
| `mqtt_password` | str | `""` | ✅ |
| `mqtt_topic_prefix` | str | `floodguard` | ✅ |
| `mqtt_keepalive_seconds` | int | `60` | ✅ |
| `mqtt_enabled` | bool | `True` | ✅ (gates the MQTT engine start) |

### Device auth

| Field | Type | Default | Used |
|---|---|---|---|
| `iot_registration_token` | str | `""` | ✅ — with an empty value, `POST /devices/register` always 401s (line 190) |
| `iot_telemetry_rate_limit_per_minute` | int | `12` | ❌ declared, never enforced |

### External sources

`open_meteo_api_base`, `open_meteo_archive_base` (❌), `opentopography_api_key` (❌),
`opentopography_base_url` (❌), `overpass_api_url` (❌), `nominatim_base_url` (❌),
`nominatim_user_agent` (❌), `cesium_ion_token` (❌).

### Study area — the geography of the whole product

| Field | Default | Meaning |
|---|---|---|
| `study_area_min_lat` | `19.0500` | south |
| `study_area_max_lat` | `19.0900` | north |
| `study_area_min_lon` | `72.8500` | west |
| `study_area_max_lon` | `72.9000` | east |
| `study_area_lat` | `19.0713` | representative point for Open-Meteo + IMD cell selection |
| `study_area_lon` | `72.8830` | ″ |
| `terrain_min_lat` | `19.0600` | DEM download bbox (tighter than the study area) |
| `terrain_max_lat` | `19.0850` | |
| `terrain_min_lon` | `72.8600` | |
| `terrain_max_lon` | `72.8900` | |

The study bbox is **~4.4 km N–S × ~5.2 km E–W** at 19 °N — consistent with the PRD's "1 to 2 km
section" claim being about the *river corridor*, not the bounding box.

### Flood simulation parameters

| Field | Default | Consumed by |
|---|---|---|
| `flood_grid_resolution_m` | `10.0` | stored on `study_area.grid_cell_size_m`; the synthetic grid's cell size. **The real DEM's own 30.92 m resolution overrides this in practice** (`run_simulation` reads `self._terrain_grid["resolution_m"]` first, line 658) |
| `flood_connectivity_buffer_m` | `50.0` | `flood_simulation.py:78` → the river-seed radius (divided by 111000 to become degrees) |
| `flood_max_depth_m` | `5.0` | the `np.minimum(depth, 5.0)` clamp |
| `flood_depth_risk_low_m` | `0.1` | depth→risk classification |
| `flood_depth_risk_moderate_m` | `0.3` | |
| `flood_depth_risk_high_m` | `0.6` | |
| `flood_depth_risk_severe_m` | `1.0` | |

These same four depth thresholds are re-declared as literals in `/api/about/assumptions`
(`about.py:97-102`) — they will drift if you change the settings.

### Demo calibration

| Field | Default |
|---|---|
| `demo_input_physical_min_cm` | `0.0` |
| `demo_input_node_code` | `""` |
| `demo_input_physical_max_cm` | `30.0` |
| `demo_input_mithi_min_cm` | `118.0` |
| `demo_input_mithi_max_cm` | `220.0` |
| `demo_input_mithi_rainfall_factor` | `0.8` |

### Local dataset paths — all repo-relative, all resolved via `resolve_data_path()`

| Field | Default | Read by |
|---|---|---|
| `terrain_raster_path` | `data/terrain/dem_mithi.tif` | `flood_simulation.load_terrain`, `load_osm_layers.DemSampler` |
| `imd_data_dir` | `data/IMD` | `preprocess_data.py` (hard-coded constant, not the setting) |
| `imd_processed_dir` | `data/rainfall/imd/processed` | `train_forecast_models.load_imd_rainfall` |
| `osm_buildings_geojson` | `data/osm/buildings.geojson` | `load_osm_layers.load_buildings` |
| `osm_roads_geojson` | `data/osm/roads.geojson` | `load_osm_layers.load_roads` |
| `osm_waterways_geojson` | `data/osm/waterways.geojson` | `flood_simulation._get_river_centerline`, `flood_monitor._waterways_layer` |
| `flood_model_dir` | `models/flood` | `forecast_service.load_models`, `train_forecast_models` |

### ML

`flood_model_dir`, `flood_model_15min`, `flood_model_30min`, `flood_model_60min`.

### Risk (declared but unused)

| Field | Default | Note |
|---|---|---|
| `risk_threshold_moderate_m` | `1.5` | ❌ never read |
| `risk_threshold_high_m` | `3.0` | ❌ never read |

The actual risk thresholds come from `config/risk_thresholds.json`.

### Demo / replay

| Field | Default | Used |
|---|---|---|
| `demo_mode_enabled` | `True` | ❌ |
| `demo_replay_speed` | `1.0` | ❌ |
| **`allow_synthetic_terrain`** | **`False`** | ✅ — `flood_simulation.py:117,122`. The comment: "When the real terrain raster is missing, flood simulation must fail loudly rather than quietly substituting synthetic elevation." Set to `true` only if you want the fabricated DEM; outputs will be labelled SIMULATED. |

### App

| Field | Default | Used |
|---|---|---|
| `app_env` | `development` | ✅ (`is_development`) |
| `media_dir` | `media/audio` | ❌ |
| `frontend_origin` | `""` | ✅ — empty ⇒ `["*"]` |
| `frontend_origin_regex` | `""` | ✅ |

### Derived properties

| Property | Returns |
|---|---|
| `study_area_bbox` | `{south, west, north, east}` floats |
| `is_development` | `app_env.strip().lower() in {"dev","development","local"}` |
| `cors_origins` | `["*"]` if empty, else the comma-split list |
| `cors_origin_regex` | joined non-capturing groups, or `None` |
| `cors_allow_credentials` | `False` when origins is `["*"]` |
| `opentopography_configured` | `bool(api_key)` — ❌ unused |
| `is_origin_allowed(origin)` | single check for CORS **and** the Socket.IO handshake |
| `resolve_data_path(rel)` | absolute passthrough, else `_REPO_ROOT / rel` |

---

## 4. `config/risk_thresholds.json` — the full tuning surface

Loaded once at import of `flood_risk.py` (module-level `_CFG = get_risk_config()`), so **editing it
requires a process restart** (or a call to `reload_config()`).

```json
"weights": { "water_level": 0.35, "rise_rate": 0.30, "rainfall": 0.20, "soil_saturation": 0.15 }
"thresholds": {
  "water_level_cm":            { "low": 50, "moderate": 100, "high": 150, "severe": 200 },
  "rise_rate_cm_per_interval": { "low":  1, "moderate":   5, "high":  15, "severe":  30 },
  "rainfall_mm_hr":            { "low":  5, "moderate":  20, "high":  50, "severe":  80 },
  "soil_saturation":           { "low": 0.3, "moderate": 0.5, "high": 0.7, "severe": 0.85 }
},
"bands":      { "low": 0.0, "moderate": 0.25, "high": 0.50, "severe": 0.75 },
"terrain_elevation_m": 1.2,
"defaults": {
  "forecast_weights": { "persistence": 0.5, "linear_regression": 0.35, "random_forest": 0.65 },
  "device_stale_after_minutes": 5,
  "model_name": "rule_based_v1",
  "model_version": "rf_15min",
  "assumptions": [
    "Risk weights: 35% water level, 30% rise rate, 20% rainfall, 15% soil saturation",
    "Thresholds are prototype assumptions - not calibrated for Mithi River",
    "Model: rule_based_v1 (not ML-validated)",
    "Synthetic data used for training - not operational gauges"
  ],
  "alerts": {
    "rapid_rise_cm_per_interval": 5,
    "threshold_hysteresis_cm": 10,
    "predicted_high_horizons_minutes": [15, 30, 60]
  }
},
"note": "These are prototype assumptions for hackathon demonstration. Not calibrated to real Mithi River hydraulics."
```

### Derived constants (with the values actually in effect)

| Python name | Source | Effective value |
|---|---|---|
| `WATER_LEVEL_LOW_CM` … `SEVERE_CM` | `thresholds.water_level_cm` | 50 / 100 / 150 / 200 |
| `RISE_RATE_LOW` … `SEVERE` | `thresholds.rise_rate_cm_per_interval` | 1 / 5 / 15 / 30 |
| `RAINFALL_LOW` … `SEVERE` | `thresholds.rainfall_mm_hr` | 5 / 20 / 50 / 80 |
| `SOIL_LOW` … `SEVERE` | `thresholds.soil_saturation` | 0.30 / 0.50 / 0.70 / 0.85 |
| `FORECAST_RAIN_LOW` … `SEVERE` | `thresholds.forecast_rainfall_mm_hr` | **missing** → code defaults 5 / 15 / 35 / 60 |
| `ELEVATION_HIGH_RISK_M` | `terrain_elevation_m + 2.0` | 3.2 |
| `ELEVATION_MOD_RISK_M` | `terrain_elevation_m + 0.5` | 1.7 |
| `DEVICE_STALE_AFTER_MINUTES` | `defaults.device_stale_after_minutes` | 5.0 |
| `DEFAULT_SOIL_SATURATION` | `defaults.soil_saturation` → missing | code default **0.4** |
| `DEFAULT_SOIL_AVAILABLE` | `defaults.soil_available` → missing | **False** |
| `BAND_SEVERE` / `HIGH` / `MODERATE` | `bands` | 0.75 / 0.50 / 0.25 |
| `WEIGHT_WATER_LEVEL` / `RISE_RATE` / `RAINFALL` / `SOIL` | `weights` | 0.35 / 0.30 / 0.20 / 0.15 |
| `WEIGHT_FORECAST_RAIN` | `defaults.forecast_weights.forecast_rainfall` → **missing** | code default **0.25** |
| `WEIGHT_CURRENT_RISK` | `defaults.forecast_weights.current_risk` → **missing** | code default **0.75** |
| `RISK_MODEL_NAME` | `defaults.model_name` | `rule_based_v1` |
| `RISK_MODEL_VERSION` | `defaults.model_version` | `rf_15min` ⚠️ misleading — this is a *rule-based* model, and `rf_15min` is the name of the ML artefact |
| `RISK_ASSUMPTIONS` | `defaults.assumptions` | the 4 strings above, surfaced on `/prediction` |

> ⚠️ `defaults.forecast_weights` = `{persistence, linear_regression, random_forest}` is a
> **classification confidence** table (which algorithm to trust) that nothing reads. The forecast-risk
> code wants `forecast_rainfall` and `current_risk`. Both keys are absent, so both fall back to
> hard-coded defaults. Since `assess_forecast_risk` is never called, this is currently inert.

### Same file also drives the alert engine (`alert_engine.py:49-87`)

| Alert constant | Source | Effective |
|---|---|---|
| `THRESHOLDS["water_level_warning_cm"]` | `thresholds.water_level_cm.high` | **150 cm** |
| `THRESHOLDS["water_level_critical_cm"]` | `thresholds.water_level_cm.severe` | **200 cm** |
| `THRESHOLDS["rapid_rise_cm_per_5min"]` | `defaults.alerts.rapid_rise_cm_per_interval` | **5 cm/5min** (the code's own fallback is 15 — the config value is what wins) |
| `THRESHOLDS["stale_minutes"]` | `defaults.device_stale_after_minutes` | **5 min** |
| `THRESHOLDS["offline_minutes"]` | hard-coded | **15 min** |
| `THRESHOLDS["sensor_fault_count"]` | hard-coded | **3** rejected readings / hour |
| `HYSTERESIS["water_level_cm"]` | `defaults.alerts.threshold_hysteresis_cm` | **10 cm** |
| `HYSTERESIS["rapid_rise_cm_per_5min"]` | hard-coded | **5 cm/5min** |
| `PREDICTED_RISK_HORIZONS` | `defaults.alerts.predicted_high_horizons_minutes` | **[15, 30, 60]** |

> ⚠️ The **threshold alert has no hysteresis in practice.** `_check_threshold_alert` clears whenever
> `level < warning_thresh`; the 10 cm `HYSTERESIS["water_level_cm"]` is never subtracted. Only the
> rapid-rise rule actually uses its hysteresis band.

---

## 5. `config/virtual_sensors.json` — ⚪ unused at runtime

Full contents in [`08-DEMO-AND-SIMULATED-DATA.md`](08-DEMO-AND-SIMULATED-DATA.md) §4. Key values:
`propagation_model: "SIMPLIFIED SENSOR PROPAGATION"`, `default_delay_seconds: 60`,
`head_attenuation: 0.9`, `rain_attenuation: 0.8`, 3 sensors, 2 hops.

Its header states the honesty rule:
> "Every value here is SIMULATED and must be labelled as such in the UI (source = simulated).
> Propagation uses SIMPLIFIED SENSOR PROPAGATION: downstream(t+delay) = upstream(t) * attenuation +
> baseline_component. This is NOT a hydraulic model."

---

## 6. `docker-compose.yml` (52 lines)

| Service | Image | Container | Port(s) | Volume | Healthcheck |
|---|---|---|---|---|---|
| `db` | `postgis/postgis:16-3.4` | `floodguard-db` | `5432:5432` | `pgdata:/var/lib/postgresql/data` | `pg_isready -U floodguard -d floodguard`, 5 s / 5 s / 20 |
| `redis` | `redis:7-alpine` | `floodguard-redis` | `6379:6379` | `redisdata:/data` | `redis-cli ping`, 5 s / 3 s / 20 |
| `mqtt` | `eclipse-mosquitto:2` | `floodguard-mqtt` | `1883:1883`, `9001:9001` | bind-mounts `backend/deploy/mosquitto/mosquitto.conf` | `mosquitto_sub -h localhost -t '$$SYS/#' -C 1 -W 3`, 10 s / 5 s / 10, `start_period: 10s` |

All three: `restart: unless-stopped`.

Environment for `db`: `POSTGRES_USER=floodguard`, `POSTGRES_PASSWORD=floodguard_secret`,
`POSTGRES_DB=floodguard`.

**The backend and the frontend are NOT in this compose file** — they run from the host.

---

## 7. `backend/deploy/mosquitto/mosquitto.conf` (10 lines)

```
listener 1883 0.0.0.0
protocol mqtt
listener 9001 0.0.0.0
protocol websockets
allow_anonymous true
persistence false
```

Anonymous access on both listeners — fine for a LAN hackathon demo, **not** for anything else. Port
9001 is exposed for browser-based MQTT debugging.

---

## 8. `Frontend/.env.example` (6 lines)

```
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
```

Only `NEXT_PUBLIC_*` reaches the browser (Next.js inlining rule). `lib/api.ts:9` and
`lib/socket.ts:34` both read it, so the same value drives REST **and** the Socket.IO connection.
Copy to `Frontend/.env.local`.

---

## 9. Frontend build configuration

### `Frontend/next.config.mjs` (11 lines)

```js
const nextConfig = { typescript: { ignoreBuildErrors: false }, images: { unoptimized: true } }
```

A type error **fails the build**. No webpack/turbopack customisation, no Cesium externals, no
transpile packages, no rewrites/proxies — CORS is handled entirely by the backend middleware.

### `Frontend/tsconfig.json` (35 lines)

`strict: true`, `noUnusedLocals: true`, `target: ES2022`, `module: esnext`,
`moduleResolution: "bundler"`, `resolveJsonModule: true`, `isolatedModules: true`,
`jsx: "react-jsx"`, `incremental: true`, `allowImportingTsExtensions: true`, `skipLibCheck: true`,
`allowJs: true`, `noEmit: true`, path alias `@/* → ./*`.

`noUnusedLocals` without a matching `noUnusedParameters` is why several hooks take `_`-prefixed
unused args.

### `Frontend/eslint.config.mjs` (22 lines)

`eslint-config-next` core-web-vitals + typescript, then three downgrades to **warn**:
`@typescript-eslint/no-explicit-any`, `@typescript-eslint/no-unused-vars` (with `^_` ignore
patterns), `react-hooks/exhaustive-deps`.

`npm run lint` therefore reports but does not fail on those three.

### `Frontend/postcss.config.mjs` (8 lines)

Tailwind 4 via `@tailwindcss/postcss`. `globals.css` starts with
`@import "tailwindcss"; @import "tw-animate-css";` and then writes ~3,400 lines of hand-authored
CSS. Tailwind's utility classes are **not** used in any component — every style is either a
hand-written class from `globals.css` or an inline `style={{}}` object.

---

## 10. Configuration-change checklist

| To change | Edit | Restart needed? |
|---|---|---|
| Risk weights / thresholds / bands | `config/risk_thresholds.json` | ✅ (module-level `_CFG`) |
| Alert thresholds / hysteresis | `config/risk_thresholds.json → defaults.alerts` | ✅ |
| Stale cutoff (also the frontend's 5-min window) | `config/risk_thresholds.json → defaults.device_stale_after_minutes` **and** `lib/constants.ts:143` | ✅ both |
| Study area bounds | `backend/.env` | ✅ (+ re-seed `study_area`) |
| Simulation grid / depth bands / connectivity buffer | `backend/.env` or `config.py` | ✅ |
| Scenario presets / runoff coefficient / cm-per-mm | `backend/app/services/scenario_service.py:35-72` | ✅ (hard-coded) |
| Demo calibration range | `DEMO_INPUT_*` env vars | ✅ |
| Node geographic resolution / river line | regenerate `data/osm/waterways.geojson` | ✅ + `Redis DEL floodguard:terrain_grid:real` if terrain changed |
| Terrain raster | `TERRAIN_RASTER_PATH` + `Redis DEL floodguard:terrain_grid:real` (24 h TTL) | ✅ + cache flush |
| Models | retrain into `models/flood/` | ✅ + restart |
| CORS origins | `FRONTEND_ORIGIN` / `FRONTEND_ORIGIN_REGEX` | ✅ |
| Backend URL in the browser | `Frontend/.env.local` → `NEXT_PUBLIC_API_BASE` | ✅ (Next.js inlines at build) |
| 3D buildings / terrain | `components/DigitalTwin.tsx` `buildingDefs` and the vertex loop | ✅ |

> 🔑 **Two configs that are duplicated in code and will drift:**
> 1. The depth-risk thresholds — `config.py:88-91` *and* literal numbers in `about.py:97-102`.
> 2. The 5-minute staleness window — `risk_thresholds.json → device_stale_after_minutes` *and*
>    `constants.ts:143` (`ageMs <= 5 * 60 * 1000`).
> 3. Also the scenario assumptions are duplicated: `scenario_service.SCENARIO_ASSUMPTIONS` and the
>    `description` string are separate literals, and the PDF mockup states a **different** runoff
>    coefficient (0.6 vs 0.7).