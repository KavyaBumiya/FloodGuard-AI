# 03 — Backend Analysis, module by module

---

## 1. `backend/app/main.py` (227 lines) — application entrypoint

### Structure

```
1-10     module docstring: "Everything degrades rather than crashes"
11-31    imports, logger, settings = get_settings()
36-39    _ensure_postgis()      -> CREATE EXTENSION IF NOT EXISTS postgis
42-61    _verify_schema()       -> compares Base.metadata.tables with the live table list
64-85    _start_engines()       -> starts forecast + alert engines, each in try/except
88-103   _stop_engines()        -> reverse, each in try/except
106-130  lifespan()              -> the startup/shutdown contract
133-142  app = FastAPI(...)
144-151  CORSMiddleware
154-184  GET /health and GET /api/health
187-201  GET /api
204-209  router registration
213-227  Socket.IO ASGI mount
```

### Design decisions worth knowing

**`_verify_schema()` deliberately does NOT call `Base.metadata.create_all()`** (lines 44-47):

> "Alembic is the single source of truth for schema, and a silent create_all would make the database and the migration history disagree."

If tables are missing it only logs `schema_incomplete_missing_tables` with the hint
`run \`alembic upgrade head\` in backend/`.

**`_start_engines()` isolates failures** (lines 78-85) so one broken engine cannot stop telemetry:

```python
for name, start in starters:
    try:
        result = start()
        if asyncio.iscoroutine(result):
            await result
        logger.info("engine_started", engine=name)
    except Exception as exc:
        logger.error("engine_start_failed", engine=name, error=str(exc))
```

**Lifespan order** (lines 107-130):

1. `_ensure_postgis()` → `_verify_schema()` → `seed_reference_data()` — all inside one try/except so
   "A cold database must not stop the API from booting and explaining itself via /health."
2. `bridge.start()` — the Redis→Socket.IO bridge.
3. `_start_engines()`.
4. `yield`
5. finally: `_stop_engines()`, `bridge.stop()`, `engine.dispose()`.

**CORS** (lines 144-151) uses `settings.cors_origins` + `settings.cors_origin_regex`. With
`FRONTEND_ORIGIN=http://localhost:3000` in `.env`, and `APP_ENV=development`, the regex becomes
`https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?`.

**`/health`** (lines 154-184) never raises. It checks the DB with `SELECT 1`, Redis with `PING`, and
Open-Meteo with `open_meteo.probe()`. Any failure downgrades `status` to `"degraded"` and records
`error: <ExceptionTypeName>`. `ingest` is reported as `"http"` — stateless, so unlike the old broker
field there is nothing to probe.

There is also a **`/healthz`** endpoint that touches nothing at all, for Render's platform liveness
check, so a slow third-party API cannot get a healthy instance killed (see
[`16-DEPLOYMENT.md`](16-DEPLOYMENT.md) §5).

**The Socket.IO double-export** (lines 212-227) is a real bug fix, documented in place:

```python
socket_app = socketio.ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io")
app = socket_app
```

The comment records that previously only `socket_app` carried the mount, so the intuitive
`uvicorn app.main:app` started a server that answered REST but had no `/socket.io` route, leaving the
console permanently in DEGRADED with no error to explain it. `engineio.ASGIApp` forwards the
`lifespan` scope to `other_asgi_app`, so the FastAPI lifespan still runs.

### Routers registered

```python
app.include_router(iot.router)            # /api/iot
app.include_router(flood_monitor.router)  # /api/flood
app.include_router(scenarios.router)      # /api/scenarios
app.include_router(alerts.router)         # /api/alerts
app.include_router(demo.router)           # /api/demo
app.include_router(about.router)          # /api/about
```

---

## 2. `backend/app/core/config.py` (217 lines) — every setting

See [`09-CONFIGURATION-AND-ENV.md`](09-CONFIGURATION-AND-ENV.md) for the full table. Behaviour that
matters:

- **Env file search order** (lines 21-25): `CWD/.env` → `<repo>/.env` → `<repo>/backend/.env`. Local
  uvicorn usually runs with CWD=`backend/`; Docker injects env vars directly.
- **`extra="ignore"`** so unknown env vars never crash startup.
- **`resolve_data_path()`** (lines 201-212) is the single most important utility in the backend:

  ```python
  candidate = Path(relative_path)
  if candidate.is_absolute():
      return candidate
  return _REPO_ROOT / candidate
  ```

  The docstring records the failure it fixes: "the process CWD varies (uvicorn from backend/,
  alembic, scripts, tests), so resolving against CWD silently produced a missing file and a fallback
  to synthetic data."
- **`is_origin_allowed()`** (lines 178-190) is shared by the REST CORS middleware and the Socket.IO
  handshake so both agree. A missing `Origin` header (curl, health checks) is allowed.
- **`cors_allow_credentials`** returns `False` when origins is `["*"]`, because wildcard +
  credentials is invalid per the CORS spec.
- **`allow_synthetic_terrain: bool = False`** (line 134) with the reasoning in comments 131-133:
  "When the real terrain raster is missing, flood simulation must fail loudly rather than quietly
  substituting synthetic elevation."

---

## 3. `backend/app/core/database.py` (54 lines)

```python
engine = create_async_engine(settings.database_url, echo=False,
                             pool_pre_ping=True, pool_size=10, max_overflow=20)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession,
                                       expire_on_commit=False, autoflush=False)
class Base(DeclarativeBase): pass
```

`get_db()` (lines 35-54) is the only DB access path for HTTP handlers:
- session creation failure → HTTP 503 "Database unavailable. Check DATABASE_URL and that migrations ran."
- exception in handler → `rollback()` and re-raise
- success → `commit()`
- finally → `close()`

---

## 4. `backend/app/core/project_config.py` (61 lines)

```python
BACKEND_ROOT = Path(__file__).resolve().parents[2]   # .../backend
REPO_ROOT    = BACKEND_ROOT.parent
CONFIG_DIR   = REPO_ROOT / "config"
```

`load_config(name, default=None)` acquires a `threading.Lock`, returns the cached value if present,
else reads and `json.loads` the file. On `FileNotFoundError` or `JSONDecodeError`/`OSError` it logs a
structlog warning and returns the default. **It never raises.** `reload_config()` clears the cache so
an edited file can be picked up without a restart.

---

## 5. `backend/app/models/floodguard.py` (548 lines) — the ORM

16 classes, 7 enums. Full schema in [`11-DATABASE-SCHEMA.md`](11-DATABASE-SCHEMA.md). Structural notes:

- **Geometry columns use `spatial_index=False`** (e.g. line 99-101) with the reason in a comment:
  GeoAlchemy2 would otherwise auto-create its own GiST index, duplicating the explicit
  `Index(..., postgresql_using="gist")` entries in `__table_args__`.
- **`SensorNode.token_hash`** is `String(64)`, `unique=True`, nullable. SHA-256 hex digest of the
  per-node bearer token; the plaintext is returned once at registration and never stored.
- **`TelemetryReading.event_id`** (line 232) is the device-supplied idempotency key. The comment:
  "A device that retries after a timeout replays the same event_id and gets the original row back
  (200, not 201)."
- **`TelemetryReading.risk_level` / `risk_score`** (lines 234-235) — a risk snapshot denormalised onto
  each reading so a reading is interpretable on its own.
- **`Forecast.status`** defaults to `"ok"`; `insufficient_data` is the other value.
- **No `CHECK` constraints exist** despite `System-Architecture.md` defining many and `CheckConstraint`
  being imported at line 15. Enum validation is therefore application-level only.
- **`FloodImpact.flood_layer_id`** has `ondelete="CASCADE"`.

---

## 6. `backend/app/api/iot.py` (558 lines) — the ingestion API

### Pydantic schemas

| Schema | Validation |
|---|---|
| `DeviceRegisterRequest` | `node_code` 1–32 chars, alnum/`-`/`_` only, auto-uppercased via `@field_validator`; `node_type` regex `^(physical\|virtual)$`; `role` regex `^(upstream\|midstream\|downstream)$`; lat −90..90, lng −180..180; `battery_pct` 0..100 |
| `TelemetryRequest` | `water_level_cm` 0..500; `rainfall_mm_hr` 0..500; `timestamp` 8–40 chars, validated with `datetime.fromisoformat` after `Z`→`+00:00`; `soil_moisture` 0..1; `source` regex `^(iot\|simulated\|replay\|scenario)$`; `event_id` ≤ 80; `valid: bool = True` |
| `TelemetryResponse` | id, node_code, status, risk_level, risk_score, received_at |
| `LatestReading` | + `age_seconds`, `node_status`, `node_type` |
| `HistoryPoint` | water_level, rainfall, flow, soil, timestamp, risk_level, risk_score, source |
| `DeviceResponse` / `RegisterResponse` | node fields; register returns the one-time `token` |

### `POST /api/iot/devices/register` (line 176, 201)

- Compares the presented header against `f"Bearer {settings.iot_registration_token}"` with `!=`
  (not constant-time — acceptable for a bootstrap secret compared once).
- 409 if `node_code` already exists.
- Generates a token, stores only `hash_device_token(token)`.
- Sets `node.location = f"POINT({lng} {lat})"` (WKT, SRID implied by column).
- Returns the plaintext token **once**.

### `POST /api/iot/telemetry` (line 300)

Dependency `get_current_device` → bearer token auth. Then, in order:

1. **403** if `body.node_code.upper() != device.node_code`.
2. If `valid == False`: insert a `RejectedReading` with reason
   `"Sensor reported invalid reading (valid=false)"`, commit, then **400**.
3. **Idempotency**: if `event_id` was already stored for this node, return the original row with
   `status="duplicate"` (HTTP 201 still, but no new row).
4. Parse `timestamp`; naive timestamps are forced to UTC.
5. If `lat`/`lng` supplied → update `device.location`, commit.
6. `ingest_telemetry_reading(...)` — the shared pipeline (rise rate → risk → reading + risk row →
   freshness → 2 realtime events → if `source == "iot"`, the Mithi demo pipeline).
7. Return `status="accepted"` with the risk snapshot.

### Read endpoints

| Endpoint | Notes |
|---|---|
| `GET /api/iot/devices` | list, newest first. Uses `d.location.y` / `d.location.x` — this **will** raise for geometry written via WKT with no SRID metadata (the same trap the flood API explicitly avoids with `ST_X`/`ST_Y` at `flood_monitor.py:137-139`). |
| `GET /api/iot/devices/{code}` | single node |
| `GET /api/iot/devices/{code}/latest` | **404** if `is_recent_physical_reading()` is false, with detail `"No recent live IoT telemetry for this device"`. Returns `age_seconds` and keeps `timestamp` (device clock) separate from `received_at` (API clock). |
| `GET /api/iot/devices/{code}/history` | `limit` 1..1000, `offset` ≥ 0, `from`/`to` ISO 8601. **Ascending** by device timestamp. |
| `GET /api/iot/devices/{code}/risk-history` | latest 100 `RiskAssessment` rows with the component breakdown. |

All read endpoints use `Depends(get_current_user)` — the no-op operator dependency.

---

## 7. `backend/app/api/flood_monitor.py` (854 lines) — the monitoring API

### `RISK_ORDER` / `normalize_risk()` (lines 45-52)

Risk is **persisted uppercase** (`"LOW"`) by the risk engine; the API contract and dashboard use
lowercase. Every endpoint that returns a level passes it through `normalize_risk()`, and ordering
uses `RISK_ORDER = {"low":0,"moderate":1,"high":2,"severe":3}` so comparisons never depend on casing.

### `GET /api/flood/impact-summary` (line 97)

Groups `FloodLayer` by `time_step_minutes`, keeping the **newest** per step
(`latest.setdefault(layer.time_step_minutes, layer)` after `ORDER BY generated_at DESC`), then
aggregates each layer's `FloodImpact` rows into:
`affected_building_count`, `affected_road_count`, `depth_range_m`, `buildings_by_risk`,
`roads_by_risk`, plus the fixed note `"MODEL ESTIMATE — not an official flood zone."`.

This endpoint is what drives the shared NOW/+15/+30/+60 readout on the dashboard, map and twin.

### `GET /api/flood/nodes` (line 131) — the most important endpoint

Filters **`node_type == "physical"` and `is_active == True`**. (Virtual nodes are therefore invisible
to the whole console.)

Coordinates are read with `func.ST_Y(SensorNode.location)` / `func.ST_X(...)`, with the reason spelled
out at lines 137-139:

> "Read coordinates with PostGIS ST_X/ST_Y rather than the GeoAlchemy2 `.x`/`.y` descriptor:
> geometry written via ST_GeomFromEWKT has no SRID metadata, so the lazy descriptor raises
> AttributeError on access."

Per node it then:
- fetches the latest reading from `sources = ["iot","simulated"]`
- applies `is_recent_physical_reading(node, latest)`; **if false, `latest` is set to `None`**
- only then loads the horizon-0 `RiskAssessment` and the `scenario_id IS NULL, status="ok"` forecasts

So a stale physical node reports `latest_reading: null` and `current_risk: null` — it never shows a
stale number as if it were current.

**The `simulation_input` block** (lines 222-320) is the provenance heart of the product:

1. Finds the newest `source="iot"` reading on a physical node, restricted to
   `settings.demo_input_node_code` when that setting is non-empty.
2. `input_is_live` = that reading passes `is_recent_physical_reading`.
3. `mapped_level = forecast_service.map_demo_level(live_reading.water_level_cm)`.
4. Loads the mapped forecasts: `input_source == "iot-demo"`, `scenario_id IS NULL`, newest per
   horizon.
5. Recomputes the Mithi risk with `assess_current_risk(water_level_cm=mapped_level,
   rainfall_mm_hr=live rainfall, rise_rate=0.0)`.
6. Returns a fixed set of display labels:
   - `label` = `"REAL IoT DEMO INPUT"`
   - `location_label` = `"Vadodara Physical Sensor"`
   - `simulation_label` = `"MITHI RIVER DIGITAL TWIN - Mumbai Simulation"`
   - `driver_label` = `"Driven by Real IoT Demo Input"`
   - `status` = `"live"` or `"waiting"`
   - `forecast_status` = `"ok"` | `"insufficient_live_iot_data"` | `"waiting_for_iot"`
   - `mapping.note` = `"Configurable demonstration calibration; not a Vadodara-to-Mumbai hydraulic relationship."`

### `GET /api/flood/nodes/{code}` (line 323)
Same freshness gate, but returns **all** risk horizons and all forecasts grouped by horizon (newest
per horizon wins), plus `calibration_offset_cm`, `baseline_level_cm`, and the full latest reading
(including `flow_rate`, `soil_moisture`, `battery_voltage`, `received_at`).

### `GET /api/flood/current-summary` (line 434)
Iterates active nodes, sums rainfall, counts offline when age > `DEVICE_STALE_AFTER_MINUTES * 3`
(= 15 min), and finds the highest risk via `RISK_ORDER` comparison. Returns `active_sensors`,
`total_sensors`, `offline_stale_sensors`, `highest_risk`, `highest_risk_node`,
`current_rainfall_mm_hr`, `timestamp`.

### `GET /api/flood/forecasts/{code}` (line 497)
Groups all forecasts for the node by horizon, **newest per horizon** (comment at 513-514: "Every
prediction cycle inserts new rows, so returning all of them surfaced stale duplicates"). For a
physical node with no recent IoT reading, returns an **empty** list.

### `GET /api/flood/layers/{time_step}` (line 549)
`layer_type` is regex-validated to `^(depth|risk|boundary)$`. Returns the newest layer plus an
embedded `impact` summary and the note
`"PREDICTED FLOOD EXTENT — model estimate, not an official flood zone."` 404 if none exists.

### `GET /api/flood/map-layers` (line 625)
`layer` regex `^(roads|buildings|waterways|sensors)$`, optional `bbox` = `"south,west,north,east"`.

| Layer | Source | Simplification tolerance |
|---|---|---|
| `roads` | PostGIS `roads` table | `ST_Simplify(geometry, 0.00006)` |
| `buildings` | PostGIS `buildings` table | `ST_Simplify(footprint, 0.00004)` |
| `waterways` | reads `data/osm/waterways.geojson` from disk (line 736) | none |
| `sensors` | `sensor_nodes` where `physical` + `is_active` | none |

Buildings and roads are returned as a `FeatureCollection` with
`note: "OpenStreetMap contributors (ODbL). Heights may be estimated."`. Roads get
`height_m: None`, `height_is_estimated: True`, `type: road_type` so the frontend can reuse one
feature shape for both layers.

### `POST /api/flood/simulate` (line 770)
Manual trigger. Reads forecasts at the requested step (scenario-scoped if `scenario_id`), falls back
to the latest telemetry or `baseline_level_cm` per node when no forecast exists, runs the engine, then
**persists** the layers via `_store_flood_layers` (line 833, with the comment "otherwise results vanish
with the response"). 400 when there is nothing to simulate.

### `GET /api/flood/risk-colors` (line 846)
Returns `{LOW, MODERATE, HIGH, SEVERE} → "risk-low" | "risk-moderate" | ...` from
`flood_risk.get_risk_color`. These are CSS class names, not hex.

---

## 8. `backend/app/api/scenarios.py` (173 lines)

| Endpoint | Behaviour |
|---|---|
| `GET /presets` | `{"presets": SCENARIO_PRESETS, "assumptions": SCENARIO_ASSUMPTIONS}` |
| `POST /run` | Query params (not a body): `name`, `preset`, `rainfall_mm_hr=50`, `initial_level_cm=150`, `drainage_capacity_pct=70`, `duration_minutes=60`. 400 on an unknown preset. Returns `{"scenario_id", "status", "message": "Scenario started. Poll for completion or subscribe to WebSocket."}` — execution happens in a background task. |
| `GET /{id}` | Scenario + all its forecasts + all its flood layers (GeoJSON included) |
| `GET /{id}/compare` | Baseline vs scenario comparison |
| `GET ""` | Recent scenarios, `limit` 1..100 |
| `DELETE /{id}` | Deletes the scenario row only. Its forecasts/flood_layers are **not** cascaded (no `ondelete` on those FKs) → orphaned rows remain. |

---

## 9. `backend/app/api/alerts.py` (292 lines)

| Endpoint | Behaviour |
|---|---|
| `GET ""` | Filters: `scenario_id`, `node_code` (joins `SensorNode`), `severity` (regex `^(information\|warning\|high\|critical)$`), `alert_type`, `active_only=True`, `hours=24`, `limit` 1..500. Enriches each alert with its node code via a batched `IN` lookup. |
| `GET /active` | Delegates to `alert_engine.get_active_alerts(scenario_id)`, then returns `{"alerts": [...], "counts": {critical, high, warning, information}}`. **The response deliberately omits `is_active`** — the frontend comments (`page.tsx:83-88`, `alerts/page.tsx:89-95`) explain that filtering on `is_active` would empty the list. |
| `POST /{id}/acknowledge` | `acknowledged_by` defaults to `"operator"`. 404 if the alert does not exist. |
| `POST /acknowledge-all` | Loops every active alert and acknowledges each. Returns `"Acknowledged {n} alerts"`. |
| `GET /history` | `hours=24`, `limit` 1..500. `node_code` is accepted but ignored — the code comment at line 181 says "Would need to resolve node_code to ID". |
| `GET /network-health` | Per active node: newest reading from `["iot","simulated"]`, then `status` = `offline` if age > `stale*3`, `stale` if age > `stale`, else `online`; `no_data` when there is no reading at all. Returns `battery_pct` from `sensor_nodes`. |

---

## 10. `backend/app/api/demo.py` (114 lines)

| Endpoint | Behaviour |
|---|---|
| `GET /status` | `{mode, replay_speed, replay_recording, replay_active}` |
| `POST /mode` | `mode` regex `^(live\|replay\|scenario)$`; stops replay / clears scenario as needed |
| `POST /reset` | Full wipe — see `demo_controller.reset_demo` below |
| `POST /seed` | **Currently a no-op**: `demo_controller.seed_demo_data()` only logs `demo_seed_skipped` with reason `live_input_requires_registered_esp32`. |
| `GET /recordings` | list |
| `POST /recordings` | capture the last `duration_seconds` (default 300) of telemetry into a `ReplayRecording` |
| `POST /replay/{id}/start` | `speed` 0.1..10.0; 404 if the recording does not exist |
| `POST /replay/stop` | cancel |

---

## 11. `backend/app/api/about.py` (182 lines)

| Endpoint | Returns |
|---|---|
| `GET /data-sources` | every `DataSource` row, ordered by category then name |
| `GET /model-info` | `{"models": {"15min": …, "30min": …, "60min": …}, "persistence_baseline": {"description": "Future water level equals current level (no change)", "note": "All ML models are evaluated against this baseline"}}`. Each model dict comes from `forecast_service.get_model_info(h)` and may come from the DB `model_registry` row **or** fall back to the artefact's own embedded metadata. |
| `GET /assumptions` | risk weights, water-level thresholds, the flood-simulation method + grid resolution + depth bands + 4 limitations, forecast config (horizons, `sampling_interval_minutes: 5`, `feature_window_readings: 12`, `lags_used: [1,2,3,6]`), data priority, and the 6 source-label definitions. |
| `GET /study-area` | name, bounds, representative point, `grid_resolution_m`, description |
| `GET /honesty-note` | one note + 7 disclaimers |

Note the file has `from app.services.forecast_service import forecast_service` at line 182 — a
bottom-of-module import, which works because it executes at import time.

---

## 12. `backend/app/auth/` — summarised above

The important takeaway: **there is no authentication on the operator API.** Every read and write
endpoint is open. Only `POST /api/iot/telemetry` and `POST /api/iot/devices/register` are gated, and
the second by a shared bootstrap secret.

---

## 13. `backend/app/services/seed.py` (116 lines)

Idempotent. Seeding logic:
- If `SELECT StudyArea.id LIMIT 1` returns nothing → insert the single study-area row with a WKT
  boundary built from the four configured bounds and `grid_cell_size_m = settings.flood_grid_resolution_m`.
- For each of the 5 data-source specs → `SELECT DataSource.id WHERE name = ?`; insert only if absent.

The 5 source rows are reproduced verbatim in [`02-DATA-INVENTORY.md`](02-DATA-INVENTORY.md) §10b.

---

## 14. Background engines actually running

Started in `main._start_engines()`:

| Engine | Loop period | What it does |
|---|---|---|
| `ingest` | request-driven | `ingest_payload()` called inline by `api/iot.py` - no background task, no reconnect loop |
| `forecast_service` | **60 s** | `generate_all_forecasts()` for every active physical node |
| `alert_engine` | **30 s** | `evaluate_all()` → per node: threshold, rapid rise, predicted risk, communication, sensor fault |

**Not started:** `virtual_sensor_engine`. Verified by searching the whole backend for
`virtual_sensor_engine`, `seed_virtual_network` and `generate_now` — the only hits are inside
`virtual_sensors.py` itself. Consequences are in [`13-TESTING-AND-QUALITY.md`](13-TESTING-AND-QUALITY.md).