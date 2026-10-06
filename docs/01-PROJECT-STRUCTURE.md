# 01 — Project Structure: every file, what it does

All paths relative to `D:\Projects\Flood-Monitoring-System`.

---

## Root

| File | Size | Purpose |
|---|---|---|
| `PRD.md` | 41,377 B / 692 lines | Product Requirements Document. Defines 20 numbered features, study area, hardware plan, honesty principles. **Written before the code — several proposed technologies were not built.** |
| `System-Architecture.md` | 48,280 B / 878 lines | Proposed layer-by-layer design: frontend, backend, AI/ML, IoT, data sources, 16-table schema, end-to-end demo flow. **Design intent, not as-built.** |
| `FLOODGUARD_AI_UI_Mockups.pdf` | 115,270 B / 7 pages | ReportLab-generated design mockups for all 7 screens. Contains **sample/illustrative numbers only** (each page footer states "All values are sample data for illustration"). Full text is transcribed in [`07-MAP-AND-3D-DETAILS.md`](07-MAP-AND-3D-DETAILS.md). |
| `docker-compose.yml` | 115 lines | The whole server: PostGIS + Redis + a one-shot `migrate` + the `backend` API. See `15-HOW-TO-RUN.md` §1. |
| `.gitignore` | 44 lines | Ignores `.env` (except `.env.example`), Python caches, `node_modules`, `.next`, `*.tsbuildinfo`, `data/terrain/*.vrt`, `data/processed/`, and `backend/app/ml/artifacts/*.joblib` (a path that does not exist — models actually live in `models/flood/`). |
| `.vscode/settings.json` | 3 lines | `python-envs.defaultEnvManager: ms-python.python:system` |

---

## `config/` — shared JSON config (single source of truth for tuning)

| File | Purpose |
|---|---|
| `risk_thresholds.json` | All risk weights, all thresholds, score bands, alert tuning, stale cutoff, model name/version, assumptions list. Loaded by `app/core/project_config.py` → `app/services/flood_risk.py` and `app/services/alert_engine.py`. |
| `virtual_sensors.json` | Definition of the 3 simulated Mithi nodes (code, name, role, lat/lon, ground elevation, baseline level, warning threshold) and the 2 propagation hops (delay_seconds, attenuation, distance_m). **Currently unused at runtime** — see `13-TESTING-AND-QUALITY.md`. |

---

## `backend/`

### `backend/.env` (1,829 B) and `backend/.env.example` (2,083 B)
Environment for the backend. Full table in [`09-CONFIGURATION-AND-ENV.md`](09-CONFIGURATION-AND-ENV.md).

> **Note:** `.env.example` contains a `DEMO_INPUT_*` block that `.env` does **not**. Since
> `pydantic-settings` supplies the defaults from `config.py`, the running values are the code
> defaults (`0→30 cm` physical, `118→220 cm` Mithi).

### `backend/requirements.txt` (43 lines)
Runtime dependencies. See `00-OVERVIEW.md` §3.

### `backend/alembic.ini` (38 lines)
`script_location = alembic`, `prepend_sys_path = .`, and a `sqlalchemy.url` that is
**overridden at runtime** by `alembic/env.py:71` (`get_settings().database_url`) — the ini value
`postgresql+asyncpg://floodguard:floodguard@localhost:5432/floodguard` is effectively dead.

### `backend/.dockerignore` (24 lines)
Excludes `.env`, caches, VCS dirs, `media/`, `*.log` from any Docker build context.

---

### `backend/alembic/` — schema migrations

| File | Lines | Purpose |
|---|---|---|
| `env.py` | 91 | Async Alembic runner. **Key behaviours:** (a) `include_object()` at line 21 filters autogenerate to only tables in `Base.metadata`, because the `postgis/postgis` image ships Tiger/TopoDB tables that autogenerate would otherwise try to DROP; (b) `fileConfig(..., disable_existing_loggers=False)` at line 16 with a comment explaining that the default `True` silences `uvicorn.error` for the whole process; (c) DB URL is read through `get_settings()`, not `os.getenv`; (d) appends `ssl=require` for non-local hosts. |
| `script.py.mako` | 775 B | Migration template. |
| `versions/046c73cac147_initial_floodguard_schema.py` | 315 | Creates all 16 tables + 21 indexes. Revision `046c73cac147`, `down_revision = None`. Created 2026-10-05 01:28. |
| `versions/0933a21ed37c_add_composite_index_on_forecasts_node_.py` | 35 | Adds `ix_forecasts_node_target` on `forecasts(node_id, target_time)`. Revision `0933a21ed37c`, `down_revision = 046c73cac147`. Created 2026-10-05 14:16. |

---

### `backend/app/__init__.py` (16 lines)
**Windows-only event-loop policy fix.** Lines 13-16 force
`asyncio.WindowsSelectorEventLoopPolicy()` on `win32`. This was originally for paho-mqtt's socket
thread and is now **kept deliberately**: `sqlalchemy[asyncio]` and `aiosqlite` drive their greenlet
work through the loop's self-pipe, which only the selector loop implements on Windows.
This runs at package import, before uvicorn calls `asyncio.run()`.

---

### `backend/app/core/`

| File | Lines | Purpose |
|---|---|---|
| `config.py` | 217 | `Settings(BaseSettings)`. Env file search order: `CWD/.env` → repo-root `/.env` → `backend/.env` (lines 21-25). ~70 settings. Derived properties: `study_area_bbox`, `is_development`, `cors_origins`, `cors_origin_regex`, `cors_allow_credentials`, `opentopography_configured`. Two methods that matter: `is_origin_allowed()` (line 178, shared by CORS middleware and the Socket.IO handshake) and `resolve_data_path()` (line 201, resolves repo-relative paths against the repo root, **not** the CWD — the comment explains that CWD-relative resolution silently produced a missing file and a fallback to synthetic data). |
| `database.py` | 54 | Async SQLAlchemy engine (`pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`), `AsyncSessionLocal` with `expire_on_commit=False, autoflush=False`, `Base(DeclarativeBase)`, and the `get_db()` FastAPI dependency that commits on success / rolls back on exception. |
| `project_config.py` | 61 | Loads `<repo>/config/*.json` with a thread lock + in-memory cache. `load_config(name, default)` never raises (returns default on `FileNotFoundError` / JSON error, logging a warning). `reload_config()` clears the cache. Exposes `get_risk_config()` and `get_virtual_sensor_config()`. |

---

### `backend/app/models/`

| File | Lines | Purpose |
|---|---|---|
| `floodguard.py` | 548 | 16 ORM classes + 7 enums. Full table-by-table detail in [`11-DATABASE-SCHEMA.md`](11-DATABASE-SCHEMA.md). Enums: `SourceType`(iot/simulated/replay/scenario), `NodeType`(physical/virtual), `NodeRole`(upstream/midstream/downstream), `RiskLevel`(low/moderate/high/severe), `AlertType`(threshold/predicted_risk/rapid_rise/communication_failure/sensor_fault), `AlertSeverity`(information/warning/high/critical), `DataMode`(live/replay/scenario). |
| `__init__.py` | 51 | Re-exports all 22 model/enum names with `__all__`. Imported by `alembic/env.py` to register metadata. |

**Notable:** `CheckConstraint` is imported at `floodguard.py:15` but **never used**. All the
`CHECK` constraints written in `System-Architecture.md` §6 exist only in the prose, not in the actual
DDL produced by the migration. Columns are plain `String(n)` / `Float`.

---

### `backend/app/api/` — HTTP layer

| File | Lines | Router prefix | Endpoints |
|---|---|---|---|
| `main.py` (in `app/`, not `api/`) | 227 | — | app factory, lifespan, CORS, `/health`, `/api`, Socket.IO mount |
| `iot.py` | 558 | `/api/iot` | device register, device list/detail, POST telemetry, latest, history, risk-history |
| `flood_monitor.py` | 854 | `/api/flood` | impact-summary, nodes, node detail, current-summary, forecasts, layers, layer detail, map-layers, simulate, risk-colors |
| `scenarios.py` | 173 | `/api/scenarios` | presets, run, detail, compare, list, delete |
| `alerts.py` | 292 | `/api/alerts` | list, active, acknowledge, acknowledge-all, history, network-health |
| `demo.py` | 114 | `/api/demo` | status, mode, reset, seed, recordings (list/create), replay start/stop |
| `about.py` | 182 | `/api/about` | data-sources, model-info, assumptions, study-area, honesty-note |

---

### `backend/app/auth/`

| File | Lines | Purpose |
|---|---|---|
| `device.py` | 88 | Physical-device auth. `generate_device_token()` = `secrets.token_urlsafe(32)`. `hash_device_token()` = SHA-256 hex (justified in the docstring: high-entropy random, not user-chosen). `get_current_device()` extracts the bearer header, looks up `token_hash`, does a constant-time `hmac.compare_digest` against a dummy on miss (so a missing node and a wrong token are timing-indistinguishable), and returns **403** for a deactivated node. Sets `request.state.operator = {"sub": node_code, "role": "device", ...}`. |
| `dependencies.py` | 38 | **There is no user/login system.** `get_current_operator` returns `{"sub": "dashboard", "role": "operator", "device": None}` for anonymous browsers. `get_current_user` is an alias kept so route signatures are stable if a real auth scheme is added later. Every read/write endpoint in the API depends on it, but it grants nothing — it only records who is acting. |

---

### `backend/app/services/` — business logic

| File | Lines | Purpose |
|---|---|---|
| `telemetry.py` | 214 | The shared ingestion pipeline. `is_recent_physical_reading()` (line 27) is the gate used everywhere: a `physical` node only counts if its latest reading has `source == "iot"` and age ≤ `DEVICE_STALE_AFTER_MINUTES` (5 min). `battery_pct_from_voltage()` maps 3.3–4.2 V → 0–100 %. `ingest_telemetry_reading()` computes rise rate from the previous reading, calls `assess_current_risk`, writes a `TelemetryReading` **and** a horizon-0 `RiskAssessment`, updates `node.last_seen_at` / `battery_pct`, commits, then emits **two** realtime envelopes (`flood.sensor.updated`, `flood.risk.updated`). If `source == "iot"` it also fires `process_real_demo_input()`. |
| `flood_risk.py` | 502 | The rule-based risk engine. See `05-ALGORITHMS-AND-AI-ML.md` §2. |
| `forecast_service.py` | 560 | Model loading, `map_demo_level()`, `generate_mithi_forecasts()`, `generate_all_forecasts()`, feature building, Redis forecast cache, model metadata. |
| `flood_simulation.py` | 897 | The elevation-threshold inundation engine: terrain load, river centreline, water-surface interpolation, depth, connectivity mask, risk classification, vectorisation, PostGIS impact intersection, layer persistence. |
| `alert_engine.py` | 1121 | 5 alert rules + dedup + hysteresis + auto-clear + acknowledge + realtime. |
| `mithi_demo_pipeline.py` | 105 | Called after every `iot` reading. Finds the newest physical reading, maps it to the Mithi level, generates Mithi forecasts, runs the flood simulation at 0/15/30/60, persists all layers, publishes `flood.mithi.updated`. |
| `ingest.py` | ~150 | Transport-neutral `ingest_payload()`: payload validation, node resolution, `RejectedReading` audit logging and `event_id` idempotency. Shared by `api/iot.py` (HTTPS) and demo Replay mode. |
| `rate_limit.py` | ~75 | Per-device sliding-window limit for `POST /api/iot/telemetry`, driven by `iot_telemetry_rate_limit_per_minute` (default 12/min). Raises `429` with `Retry-After`. In-process, so exact only at one uvicorn worker; `reset()` exists for tests. |
| `virtual_sensors.py` | 500 | Simulated-node generator with delay+attenuation propagation. **Instantiated but never started** — see `13-TESTING-AND-QUALITY.md`. |
| `scenario_service.py` | 407 | What-if scenario creation, background execution, baseline comparison, presets, assumptions. |
| `demo_controller.py` | 290 | Mode switching (live/replay/scenario), replay playback, full demo reset, recording capture. |
| `websocket.py` | 88 | `sio` (python-socketio `AsyncServer`, `async_mode="asgi"`, `cors_allowed_origins=settings.is_origin_allowed`), `GLOBAL_ROOM = "global_feed"`, `REALTIME_CHANNEL = "floodguard:realtime"`, `publish_realtime_event()`, and the `connect` / `disconnect` / `subscribe` / `unsubscribe` handlers. |
| `event_bridge.py` | 85 | Redis pub/sub → Socket.IO. Subscribes to `floodguard:realtime` on a **dedicated** Redis connection (line 68 comment: a client in subscribe mode cannot issue other commands, so it must not be the shared cache client) and re-emits every message into `global_feed` plus `node_<node_code>` when a code is present. |
| `seed.py` | 116 | Idempotent seeding of **one** `study_area` row and **five** `data_sources` rows. Every seeded `DataSource` carries an explicit `limitation` string. |

---

### `backend/app/integrations/`

| File | Lines | Purpose |
|---|---|---|
| `__init__.py` | 13 | Documents the "flood only" policy: no seismic/fire/storm/traffic/social/satellite connector exists or should be added without a data source. |
| `cache.py` | 64 | Redis helpers: `get_redis()` (module-level singleton, `decode_responses=True`), `make_key()` (prefix + md5 of joined parts), `cache_get()`, `cache_set()`. Every failure is swallowed — cache is an optimisation, never a dependency. |
| `weather/__init__.py` | 15 | Re-exports the 5 Open-Meteo symbols. |
| `weather/open_meteo.py` | 281 | `OpenMeteoConnector` — thin `httpx` client for `api.open-meteo.com`. Returns dataclasses (`WeatherObservation`, `ForecastSlice`, `ForecastResult`) not raw provider JSON. Full WMO code → (group, description) table (26 codes). `probe()` is called from `/health`. **Only `get_current_weather` is ever called, and only by `probe()`.** |

---

### `backend/scripts/`

| File | Lines | Purpose |
|---|---|---|
| `preprocess_data.py` | 431 | Offline prep. `validate_dem()` reports the real raster properties. `preprocess_imd()` discovers IMD variable/dimension names **case-insensitively at runtime** (IMD publishes `LATITUDE/LONGITUDE/TIME`, netCDF4 returns lowercase dim keys), finds the rainfall variable by name substring then falls back to the largest 3-D non-coordinate field, and extracts the grid cell nearest (19.0713, 72.8830). `preprocess_osm()` splits the 8 MB extract into 3 layers with honest building-height metadata. |
| `train_forecast_models.py` | 405 | Trains the three Random Forests. |
| `load_osm_layers.py` | 300 | Loads the GeoJSON extracts into PostGIS with geometry repair (`buffer(0)`), study-bbox clipping, multi-part explosion, and DEM sampling for `buildings.ground_elevation_m`. Idempotent (skips existing `osm_id`s) or `--force` to wipe. |

### `backend/tests/`

| File | Lines | Purpose |
|---|---|---|
| `test_geojson_contract.py` | 132 | 22 tests (with parametrisation) guarding the GeoJSON `type` discriminator. See `13-TESTING-AND-QUALITY.md`. |
| `test_virtual_sensor_lag_query.py` | 96 | 6 tests guarding the `abs(extract(epoch from interval))` PostgreSQL fix. |

---

## `Frontend/`

### Config

| File | Lines | Purpose |
|---|---|---|
| `package.json` | 40 | Scripts: `dev` (`next dev`), `build`, `start`, `typecheck` (`tsc --noEmit`), `lint` (`eslint .`), `test` (`node --test lib/*.test.ts`). |
| `tsconfig.json` | 35 | Strict mode, `noUnusedLocals: true`, `moduleResolution: "bundler"`, path alias `@/* → ./*`, `target: ES2022`. |
| `next.config.mjs` | 11 | `typescript.ignoreBuildErrors: false` (build fails on type errors), `images.unoptimized: true`. |
| `eslint.config.mjs` | 22 | `eslint-config-next` core-web-vitals + typescript; downgrades `no-explicit-any`, `no-unused-vars`, `react-hooks/exhaustive-deps` to **warn**. |
| `postcss.config.mjs` | 8 | Tailwind 4 via `@tailwindcss/postcss`. |
| `.env.example` | 6 | `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000` |
| `.gitignore` | 15 | v0 sandbox leftovers, `.env*.local`, `node_modules`, `.next`. |
| `next-env.d.ts`, `tsconfig.tsbuildinfo` | 295 B / 240 KB | Generated by Next.js. |

### `Frontend/app/` — App Router pages

| File | Lines | Route |
|---|---|---|
| `layout.tsx` | 40 | Root layout. Metadata (title "FLOODGUARD AI · Command Centre"), viewport, icons. Wraps children in `Providers` → `ErrorBoundary` → `Shell`. |
| `providers.tsx` | 8 | Single `react-redux` `Provider` with the imported store. |
| `globals.css` | 3,473 | The entire design system. 46 commented sections (`Design Tokens`, `App Shell`, `Sidebar`, `Topbar`, `Panels`, `Charts`, `Badges`, `Risk Levels`, `Timeline`, `Map`, `Map Overrides`, `Responsive`, …). CSS custom properties mirror `lib/constants.ts` 1:1, plus legacy aliases (`--cyan`, `--amber`, `--red`, `--green`, `--violet`) that the Leaflet/three.js code still references. |
| `page.tsx` | 446 | `/` Command Centre Dashboard. |
| `prediction/page.tsx` | 320 | `/prediction` AI Prediction. |
| `map/page.tsx` | 303 | `/map` 2D Flood Map (Leaflet, `ssr: false`). |
| `digital-twin/page.tsx` | 220 | `/digital-twin` 3D Twin (three.js, `ssr: false`). |
| `scenarios/page.tsx` | 429 | `/scenarios` What-If Simulator. |
| `alerts/page.tsx` | 333 | `/alerts` Alert Centre + Network Health + History. |
| `data/page.tsx` | 197 | `/data` About This Data. |

### `Frontend/components/`

| File | Lines | Purpose |
|---|---|---|
| `Shell.tsx` | 120 | The single app shell. Owns **one** realtime pipeline (`useFloodguardData`), **one** 1-second IST clock, **one** timeline autoplay driver, and renders Sidebar + CommandBar + AlertStrip + TimelineControl + page children. The clock is seeded as `''` (not `fmtClock()`) to avoid a React #418 hydration mismatch (comment at lines 23-25). |
| `Sidebar.tsx` | 104 | Nav rail. Lucide icons per route, active state from `usePathname()`, unacknowledged-alert badge on `/alerts`, status block injected via `children`. |
| `CommandBar.tsx` | 67 | Breadcrumb + `ModePill` (only when ≠ live) + `ConnectionPill` + "LAST UPDATE" clock. |
| `AlertStrip.tsx` | 89 | One-line worst-open-alert strip: severity chip, message, node, value/threshold, age, link to `/prediction`, `+N more`. |
| `TimelineControl.tsx` | 89 | The shared NOW/+15/+30/+60 radiogroup + PLAY/PAUSE button, plus `useTimelineAutoplay(intervalMs = 3500)`. Mounted exactly once, in `Shell`. |
| `FloodMap.tsx` | 675 | Leaflet map. 9 `useEffect` blocks: study area → map init → waterways → buildings/roads → inundation layer → sensor markers → waterways render → inundation render → buildings render → roads render. Contains `escapeHtml()` (line 19) used on every interpolated string. |
| `DigitalTwin.tsx` | 641 | three.js scene. See `07-MAP-AND-3D-DETAILS.md`. |
| `WaterLevelChart.tsx` | 363 | `WaterLevelChart` (Recharts `ComposedChart`: observed solid line, forecast dashed line, stacked uncertainty band, NOW rule, WARNING/CRITICAL reference lines, `HISTORY_WINDOW = 26`) and `ModelMetricsChart` (MAE vs persistence grouped `BarChart`). |
| `ErrorBoundary.tsx` | 87 | Class component React error boundary with a "Try Again" reset. |
| `ui/Primitives.tsx` | 247 | `DataSourceBadge`, `RiskBadge`, `PanelTitle`, `PageHeader`, `KpiCard`, `MetricValue`, `EmptyState`, `ErrorState`, `SkeletonKpi/Rows/Block`. |
| `ui/StatusPill.tsx` | 52 | `ConnectionPill` and `ModePill`. |

### `Frontend/hooks/`

| File | Lines | Purpose |
|---|---|---|
| `useFloodguard.ts` | 155 | `useFloodguardData()` — the one realtime pipeline: initial `Promise.all` REST load (nodes, summary, active alerts, impact summary, demo status), Socket.IO handlers for 8 events, a 3 s REST poll safety net, and the live / degraded / reconnecting / offline decision logic. Deliberately does **not** disconnect the socket on unmount (module singleton; teardown aborts in-flight handshakes). |
| `useNodeSeries.ts` | 91 | `useNodeSeries(nodeCode, pollMs = 3000)` — one poller for `/history?limit=60` + `/forecasts/{code}`, shared by the Dashboard and the Prediction page (they used to run two independent 3 s timers). Keeps the previous series on screen when a poll fails. |

### `Frontend/lib/`

| File | Lines | Purpose |
|---|---|---|
| `api.ts` | 176 | The only HTTP client. `API_BASE = process.env.NEXT_PUBLIC_API_BASE \|\| 'http://127.0.0.1:8000'`. `ApiError` carries status. `fetchJson` converts network failure to `ApiError(0, ...)` and non-2xx to `ApiError(status, body.detail)`. The exported `api` object has 24 methods. |
| `constants.ts` | 198 | Design system + semantics: `COLORS`, `SPACE`, `RADIUS`, `TYPE`, `RISK_META`, `riskLevel/riskColor/riskClass/riskRank`, `SOURCE_META` (8 provenance classes), `nodeSource`, `hasRecentLiveIoTReading`, `CONNECTION_META`, `MODE_META`, `TIME_STEPS`, `NAV_ITEMS`, `ALERT_SEVERITY_ORDER`. |
| `format.ts` | 141 | Formatters. `EM_DASH = '—'` is the universal "no value" glyph. `fmtNum/fmtInt/fmtArea/fmtPct/splitValue`, `timeAgo`, `fmtClock/fmtHm/fmtIsoHm` (all `Asia/Kolkata`), `trend/trendGlyph/trendWord`, `latestPerHorizon`, `cx`. |
| `socket.ts` | 45 | `getSocket()` — a module-level Socket.IO singleton with `path: '/socket.io'`, `transports: ['websocket','polling']`, infinite reconnection, 500 ms → 5 s backoff, 8 s timeout. Exports `RealtimeEventName` and `RealtimeEvent`. |
| `constants.test.ts` | 117 | 11 tests on risk/provenance/connection semantics. |
| `format.test.ts` | 137 | 12 tests on formatter honesty. |

### `Frontend/store/index.ts` (131 lines)
Redux Toolkit slice `floodguard`. 15 reducers. State: `selectedNode`, `timelineStep`, `timelinePlaying`, `mode`, `connection`, `nodes`, `simulationInput`, `summary`, `risk`, `impacts`, `alerts`, `activeScenarioId`, `scenarioRunning`, `lastUpdatedAt`, `error`, `loading`. `serializableCheck` disabled.

### `Frontend/types/index.ts` (294 lines)
Every TypeScript interface mirroring a backend response: `SourceType`, `RiskLevel`, `Mode`, `TimeStep`, `SensorReading`, `LatestReading`, `RiskComponent`, `RiskResult`, `ForecastPoint`, `FloodNode`, `SimulationInput`, `CurrentSummary`, GeoJSON types, `ImpactSummary`, `FloodLayerResponse`, `ImpactSummaryResponse`, `ScenarioPreset`, `Scenario`, `ScenarioComparison`, `Alert`, `NetworkHealth`, `DataSource`, `ModelInfo`, `DemoStatus`, `HealthStatus`, `ConnectionState`.

### `Frontend/public/`
`icon.svg`, `icon-light-32x32.png`, `icon-dark-32x32.png`, `apple-icon.png`, plus 4 unused v0/Next placeholders (`placeholder.svg`, `placeholder.jpg`, `placeholder-logo.svg`, `placeholder-logo.png`, `placeholder-user.jpg`).

---

## `data/`

| Path | Size | See |
|---|---|---|
| `overpass turbo.geojson` | 8,280,012 B (8 MB) | [`02-DATA-INVENTORY.md`](02-DATA-INVENTORY.md) §1 |
| `IMD/gridded rainfall 19.nc` | 25,431,832 B | §2 |
| `IMD/gridded rainfall 23.nc` | 25,431,832 B | §2 |
| `IMD/gridded rainfall 24.nc` | 25,501,532 B | §2 |
| `rainfall/imd/processed/mumbai_rainfall.json` | 30,473 B | §3 |
| `osm/buildings.geojson` | 2,104,250 B | §4 |
| `osm/roads.geojson` | 1,268,117 B | §4 |
| `osm/waterways.geojson` | 15,755 B | §4 |
| `terrain/dem_mithi.tif` | 39,553 B | §5 |
| `terrain/_copernicus_N19E072.tif` | 13,336,459 B | §5 |
| `terrain/terrain_metadata.json` | 550 B | §5 |
| `terrain/sensor_elevations.json` | 1,045 B | §6 |
| `ml/synthetic_water_levels.csv` | 4,894,863 B | §7 |

---

## `models/flood/`

| Path | Size | See |
|---|---|---|
| `rf_15min.joblib` | 34,256,261 B | §8 |
| `rf_30min.joblib` | 32,763,845 B | §8 |
| `rf_60min.joblib` | 31,575,989 B | §8 |
| `model_metadata.json` | 2,586 B | §8 |