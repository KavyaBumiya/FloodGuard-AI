# 13 — Testing, Quality, Dead Code and Defects

Everything in this file was verified by reading the code and, where noted, by running the tests.

---

## 1. Test inventory

| Suite | Command | Files | Test count |
|---|---|---|---|
| Backend | `pytest` from `backend/` | `tests/test_geojson_contract.py` (132 lines), `tests/test_virtual_sensor_lag_query.py` (96 lines) | **28** (17 + 11, after parametrisation) |
| Frontend | `npm test` → `node --test lib/*.test.ts` | `lib/constants.test.ts` (117 lines), `lib/format.test.ts` (137 lines) | **23** |

**There is no integration test suite, no API test suite, no CI configuration, no coverage config, no
pre-commit hook, and no linter configured for Python** (no `ruff`/`flake8`/`black` config, no
`mypy.ini` or `pyproject.toml` anywhere in the repo).

`.pytest_cache/v/cache/nodeids` records 26 previously-collected node ids, including two that **no
longer exist**: `test_ordering_compiles_for_postgres` and `test_settings_module_imports` (the file
now has `test_ordering_executes_against_postgres` and `test_config_module_imports`). So the test
surface grew and the cache is stale.

---

## 2. Live test results

### 2.1 Frontend — 22 pass, 1 fail

```
ℹ tests 23   ℹ pass 22   ℹ fail 1
```

**The failure** — `lib/constants.test.ts:71-78`, `simulated data is never labelled live`:

```
AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:
  + actual   'REAL IoT INPUT'
  - expected 'LIVE HARDWARE'
  at TestContext.<anonymous> (lib/constants.test.ts:72:10)
```

**Root cause:** the label in `lib/constants.ts:117` is
`iot: { label: 'REAL IoT INPUT', short: 'IoT', … }`, but the assertion at line 72 still expects the
older `'LIVE HARDWARE'`. The label was renamed to match the "Vadodara real demo input" framing used
across every page (`page.tsx:137`, `map/page.tsx:76`, `digital-twin/page.tsx:106`); the test was not
updated.

**Fix (one line):** either change `constants.ts:117` back to `'LIVE HARDWARE'`, or change the test's
first assertion to `'REAL IoT INPUT'`. The second is correct — the rest of the suite's intent
(`simulated !== iot`) still passes:

```ts
test('simulated data is never labelled live', () => {
  assert.equal(SOURCE_META.iot.label, 'LIVE HARDWARE')          // ← this line
  assert.equal(SOURCE_META.simulated.label, 'SIMULATED')
  assert.notEqual(SOURCE_META.simulated.label, SOURCE_META.iot.label)
  assert.notEqual(SOURCE_META.simulated.color, SOURCE_META.iot.color)
  assert.notEqual(SOURCE_META.forecast.label, SOURCE_META.iot.label)
})
```

Also note `/api/about/assumptions` still returns `"LIVE HARDWARE": "ESP32 physical sensor"` in its
`source_labels` block (`about.py:125`), so the API and the UI use two different names for the same
class.

### 2.2 Backend — not executed

The backend suite was **not run** here, for two reasons:

1. `test_virtual_sensor_lag_query.py::test_ordering_executes_against_postgres` needs a live
   PostgreSQL; it self-skips only on unreachable-DB errors.
2. `test_geojson_contract.py` imports `app.services.flood_simulation`, which imports
   `app.core.config.get_settings()` → requires `pydantic-settings` and a resolvable `.env`.

The pytest cache's `lastfailed` file is **2 bytes** (an empty JSON object `{}`), i.e. the last recorded
run had **no failures**. That is consistent with both suites passing at the time they were last run —
before the `SOURCE_META.iot.label` rename.

---

## 3. What the tests actually protect

### 3.1 `test_geojson_contract.py` — the Leaflet `type: "Feature"` contract

The docstring names the exact bug: `_vectorize_flood_layers` emitted `{"geometry":…, "properties":…}`
with no `"type"` discriminator. GeoJSON requires it, and Leaflet's `geometryToLayer` reads
`geojson.type`, finds `undefined`, falls through to its `throw`, and rejects the **whole layer** with
`Invalid GeoJSON object.` — so every flood layer failed to render.

It exercises the **real generator** (`FloodSimulationEngine._vectorize_flood_layers`), not a copy,
by bypassing `__init__` with `object.__new__`.

| Test | Parametrised over | Assertion |
|---|---|---|
| `test_collection_has_the_featurecollection_discriminator` | depth, risk, boundary | `fc["type"] == "FeatureCollection"`, `features` is a list |
| `test_every_feature_declares_type_feature` | ×3 | `feature["type"] == "Feature"` — **the exact regression** |
| `test_every_feature_has_geometry_and_properties` | ×3 | both keys present, `properties` is a dict |
| `test_every_geometry_type_is_known_to_leaflet` | ×3 | geometry type ∈ the 7 RFC 7946 types |
| `test_features_survive_a_json_round_trip` | ×3 | survives `json.dumps`/`loads` (the actual path to the browser) |
| `test_generator_actually_produced_features` | — | `len(depth.features) > 0 and len(risk.features) > 0` — guards against the rest passing vacuously |
| `test_boundary_layer_only_contains_high_and_severe` | — | boundary FC has only `high`/`severe` |

### 3.2 `test_virtual_sensor_lag_query.py` — the `abs(interval)` bug

The bug: `func.abs(TelemetryReading.recorded_at - target)` — subtracting two timestamps yields an
INTERVAL, and **PostgreSQL has no `abs(interval)`**. Every virtual-sensor cycle raised
`UndefinedFunctionError`, so no virtual node ever ingested a reading, every risk assessment froze on
stale data, and the console showed an identical score for every node.

| Test | Assertion |
|---|---|
| `test_ordering_extracts_epoch_from_the_interval` | the SQL contains `extract(epoch from` inside `abs(` |
| `test_ordering_does_not_wrap_a_bare_interval` | the SQL does **not** contain `abs(telemetry_readings.recorded_at -` |
| `test_filters_by_node_and_limits_to_one_row` | `WHERE telemetry_readings.node_id`, `ORDER BY`, `LIMIT 1` |
| `test_ordering_executes_against_postgres` | **live DB execution.** Skips only on `ConnectionError`/`OSError`/`TimeoutError`/`asyncio.TimeoutError` or a class name containing "Connect"; a SQL error re-raises (comment: "skipping it would hide exactly the `abs(interval)` class of bug this file exists to catch") |
| `test_config_module_imports` | `get_settings()` is not None — a cheap import guard |

Both suites import from the **production module** (`from app.services.flood_simulation import
FloodSimulationEngine`, `from app.services.virtual_sensors import nearest_reading_to`) with a
`sys.path.insert` of the backend root, so a regression in the real code is caught.

### 3.3 Frontend test coverage by intent

`lib/format.test.ts` (12 tests) — the honesty rule:

| Test | Intent |
|---|---|
| `isNum rejects null, undefined, NaN and non-numbers` | the gatekeeper |
| `a missing measurement never renders as zero` | **the exact regression**: `(x ?? 0).toFixed(1)` → `"0.0 mm/hr"` |
| `a real zero IS rendered as zero, not hidden` | guards the opposite failure (hiding real data) |
| `formatters apply units and precision consistently` | `156.4 → "156 cm"`, `351981 → "0.352 km²"` |
| `timeAgo returns null (not "no data") when there is no timestamp` | callers must branch on `null` |
| `timeAgo buckets elapsed time` | `2s/30s/5m/3h/2d` |
| `timeAgo never returns a negative age for clock skew` | future timestamp → `just now` |
| `fmtIsoHm falls back to a placeholder, never "Invalid Date"` | — |
| `trend reports unknown rather than guessing when an endpoint is missing` | `trend(null,100) → {dir:'unknown', delta:null}` |
| `trend glyphs never imply direction when unknown` | `unknown → '•'` |
| `latestPerHorizon keeps the newest issuance, not the first row` | the stale-forecast regression |
| `latestPerHorizon tolerates rows with no made_at` | — |
| `latestPerHorizon returns an empty map for no forecasts` | — |

`lib/constants.test.ts` (11 tests) — the semantics:

`riskLevel` normalises aliases (`critical→severe`, `warning→moderate`, `SEVERE` uppercase) and
defaults to `unknown` · `an unassessed node is NOT styled as offline` (regression: `unknown` used to
map to the offline class) · `every risk class has a distinct colour and tint` ·
`riskRank orders worst-first and sinks unknown to the bottom` · `riskColor is driven by the same
table as the badge` · **`simulated data is never labelled live` ← FAILING** ·
`every SourceType has a badge definition` · `nodeSource maps physical to hardware and everything else
to simulated` · `degraded is distinct from offline` · `alert severities sort most urgent first`.

### 3.4 What is **not** tested

| Untested area | Why it matters |
|---|---|
| **The RF inference path** | `generate_mithi_forecasts` builds 10 features for a model with `n_features_in_ = 11`. **No test asserts the feature vector length matches the model.** This is the single biggest untested risk (see §5.1). |
| `map_demo_level` | The core demo calibration. Not one unit test. |
| `assess_current_risk` / `score_to_level` / `_normalize_linear` | The whole risk engine. Not one unit test. |
| `calculate_rise_rate` | Not tested, including the `max(0, …)` clamp. |
| `flood_simulation._interpolate_water_surface` | The nearest-river-point interpolation. Not tested. |
| `flood_simulation._apply_connectivity_mask` | The BFS. Not tested (only the vectoriser is). |
| `_sample_depth_at` | Nearest-cell-centre lookup. Not tested. |
| `estimate_building_height` (preprocess) | The measured/estimated height provenance. Not tested. |
| `preprocess_imd` / `_extract_cell_series` | The IMD sentinel handling and cell selection. Not tested. |
| `build_subdaily_rainfall` | The "daily total is preserved exactly" invariant. Not asserted anywhere. |
| `simulate_storage` / `storage_to_levels` | The synthetic hydrology. Not tested. |
| Every API endpoint | No `TestClient`, no httpx-mocked tests. |
| `DeviceRegisterRequest` validators | Not tested. |
| `escapeHtml` (FloodMap) | XSS defence — not tested, though it is manually applied to every interpolated string. |
| `DigitalTwin` shader / scene | Not testable without a headless WebGL harness. |
| Scenario math | Not tested. |
| Alert hysteresis/dedup | Not tested. |

---

## 4. Dead code inventory (verified by repo-wide search)

### 4.1 Declared and never used

| Item | Declared at | Note |
|---|---|---|
| `Settings.risk_threshold_moderate_m` (1.5) | `config.py:124` | never read |
| `Settings.risk_threshold_high_m` (3.0) | `config.py:125` | never read |
| `Settings.iot_telemetry_rate_limit_per_minute` (12) | `config.py:56` | declared as "Per-device telemetry rate limit (requests/minute)" — **never enforced** |
| `Settings.demo_mode_enabled` | `config.py:128` | never read |
| `Settings.demo_replay_speed` | `config.py:129` | never read |
| `Settings.media_dir` | `config.py:138` | never read |
| `Settings.opentopography_configured` | `config.py:198` | never read |
| `Settings.cesium_ion_token` | `config.py:69` | never read (the twin is three.js) |
| `Settings.overpass_api_url` / `nominatim_*` / `imd_data_dir` | `config.py:63,64,65,108` | never read at runtime |
| `CheckConstraint` | `models/floodguard.py:15` | imported, never used — **no CHECK constraints exist in the DDL** |
| `UUID`/`uuid` | `models/floodguard.py:7` | imported, never used |
| `json` / `Path` / `os` / `Optional` | `forecast_service.py:11,12,13,20` | unused imports |
| `ndimage` (scipy) | `flood_simulation.py:22` | imported, never used |
| `SourceType`, `Scenario`, `Forecast` | `flood_simulation.py:32,37,33` | imported, unused |
| `logging`, `fileConfig` | `alert_engine.py:18` | unused imports |
| `forecast` param | `ingest.py` | accepted for call-site compatibility, unused |
| `RISK_ASSUMPTIONS` config key `defaults.forecast_weights` | `risk_thresholds.json:43-47` | keys are `persistence`/`linear_regression`/`random_forest`; the code wants `forecast_rainfall`/`current_risk` → both fall back to hard-coded defaults |
| `thresholds.forecast_rainfall_mm_hr` | absent from the config | `flood_risk.py:73-76` falls back to 5/15/35/60 |
| `defaults.soil_saturation` / `soil_available` | absent from the config | fall back to 0.4 / False |
| `roads.min_elevation_m` | `models/floodguard.py:382` | column exists, never written |
| `study_area.river_line` | `models/floodguard.py:102` | column exists, never written |
| `Building.height_method` | present in the GeoJSON | never carried into the `buildings` table |
| `FloodSimulationEngine.river_centerline_source` | `flood_simulation.py:338` | property never read |
| `node_relationships` table | `models/floodguard.py:169` | never written (`seed_virtual_network` never runs) |
| `replay_recordings` table | `models/floodguard.py:516` | never seeded |
| `virtual_sensor_engine` (whole class) | `virtual_sensors.py:500` | `start()` never called |
| `virtual_sensor_engine.generate_now()` | `virtual_sensors.py:147` | never called (its docstring says the live orchestration uses it — but the orchestration was replaced by `mithi_demo_pipeline`) |
| `virtual_sensor_engine.load_scenario/clear_scenario/set_interval` | `virtual_sensors.py:382-396` | never called |
| `forecast_service.generate_forecasts_for_nodes` | `forecast_service.py:197` | never called |
| `forecast_service.get_cached_forecast` | `forecast_service.py:504` | never called (the writer `_cache_forecast` is only on the never-called Path B) |
| `flood_risk.assess_forecast_risk` | `flood_risk.py:372` | imported by `forecast_service.py:31`, never called |
| `cache.cache_get` / `cache_set` / `make_key` | `cache.py:45,58,38` | never called — the 4 documented key conventions never materialise |
| `OpenMeteoConnector.get_forecast` | `open_meteo.py:209` | only `get_current_weather` runs, and only from `probe()` in `/health` |
| `Websocket` client `subscribe` / `unsubscribe` handlers | `websocket.py:77,86` | the frontend never emits them |
| `frontend_origin_regex` env | `backend/.env:64` | wired and working, just empty |
| 5 `Frontend/public/placeholder*` files | — | unreferenced template leftovers |
| `Frontend/next-env.d.ts`, `tsconfig.tsbuildinfo` | — | Next.js generated, gitignored |
| `data/ml/synthetic_water_levels.csv` | — | orphaned dataset, 4.9 MB |
| `data/terrain/sensor_elevations.json` | — | orphaned; its values reach the DB via `virtual_sensors.json` (also unused) and manual registration |
| `data/terrain/terrain_metadata.json` | — | documentation only |
| `DEMO_RESET_ON_STARTUP` | `backend/.env:56` | not even declared in `Settings` |

### 4.2 Promised but never implemented

| Promise | Source | Status |
|---|---|---|
| `flood.layer.updated` event | `websocket.py:9`, `lib/socket.ts:17`, subscribed by the frontend | no emitter |
| `information` alert severity | enum + API counts | never assigned |
| `ConnectionState = 'reconnecting'` | `types/index.ts:294`, `CONNECTION_META` | never dispatched |
| Camera preset `ortho` | `DigitalTwin.tsx:564` | not in the UI list |
| `demo_controller._run_replay` → per-node `node_<code>` rooms | bridge emits them | frontend never subscribes |
| Scenario comparison: additional area / roads / buildings / highest risk | `System-Architecture.md:198`, PDF mockup page 6 | accumulators declared then never filled (`scenario_service.py:317-319`) |
| `ConnectionState.reconnecting` indicator | `CommandBar` | never shown |
| `settings.docs`/… | — | — |

---

## 5. Defects found, ranked by impact

### 5.1 🔴 HIGH — the live forecast passes 10 features to an 11-feature model

`train_forecast_models.py:185` writes 11 features per row, including
`float(upstream[i]) if upstream is not None else 0.0` → `upstream_level_cm`.
The bundles report `n_features_in_ = 11`.

`forecast_service.generate_mithi_forecasts()` (`forecast_service.py:250-268`) builds a **10-key**
dict (lines 250-261) and calls `model.predict([list(features.values())])` with no upstream key
appended.

scikit-learn's `RandomForestRegressor.predict` raises
`ValueError: X has 10 features, but RandomForestRegressor is expecting 11 features as input.`

The exception is caught at lines 269-271:
```python
except Exception as exc:
    logger.error("mithi_forecast_prediction_failed", horizon=horizon, error=str(exc))
    predicted = current_level_cm
```

**Net effect: every live forecast silently equals the mapped current level — persistence — while the
UI labels it `FORECAST` and the About page reports Random-Forest MAE.**

**Why it is high-impact:** it is invisible. The pipeline "works", the numbers are plausible, the
±10 cm bounds still render, and the `/api/about/model-info` response still shows the real RF metrics.
The only trace is a `forecast_model`-adjacent ERROR line in the log.

**How to confirm in 5 seconds:**
```bash
curl -s localhost:8000/api/flood/nodes | jq '.simulation_input.mithi_water_level_cm,
   (.simulation_input.forecasts[] | select(.horizon_minutes==15) | .predicted_level_cm)'
curl -s localhost:8000/api/flood/nodes | jq '.simulation_input.forecasts[].status'
# if predicted == mithi for every horizon AND status == "ok", the catch branch is firing
grep mithi_forecast_prediction_failed <your-log>
```

**Two candidate fixes:**
1. Append `upstream_level_cm` to the features dict in `generate_mithi_forecasts` (mirroring
   `_build_features` + `generate_forecasts_for_node`, which already do it at lines 313-317) — this
   makes the RF actually run.
2. Retrain with 10 features if the upstream feature is genuinely unneeded (it carries ~0.0–0.0007
   importance, so dropping it would barely change predictions).

Option 1 is right: it also makes the model match the `System-Architecture.md:278` feature list.

### 5.2 🔴 HIGH — the 2D map renders zero sensor markers

`flood_monitor.py:146-149` filters `/api/flood/nodes` to `node_type == "physical"`.
`FloodMap.tsx:232-233` then does `if (isPhysical) return`.

The two conditions cancel exactly. No markers, no popups, no click-to-select on the map.

**Fix (one line):** either drop the `node_type == "physical"` filter from the API, or remove the
early return. The design intent — physical node in the provenance panels, virtual Mithi nodes as
markers — is visible in the popup template (`"REAL IoT DEMO INPUT"` vs `"SIMULATED MITHI LOCATION"`)
and in `types/index.ts:132-134` (`nodeSource`).

The separate endpoint `/api/flood/map-layers?layer=sensors` returns all active physical nodes and is
**not used by the frontend at all**.

### 5.3 🟠 MEDIUM — the rapid-rise alert can never clear

`config/risk_thresholds.json`: `alerts.rapid_rise_cm_per_interval = 5`.
`alert_engine.py:73-78`: `HYSTERESIS["rapid_rise_cm_per_5min"] = 5.0` (hard-coded).

Clear condition (lines 510-513):
```python
if rise_rate < (threshold - HYSTERESIS["rapid_rise_cm_per_5min"]):    # rise_rate < 0.0
    self._clear_alert(...)
```
`calculate_rise_rate` clamps at `max(0.0, …)`, so `rise_rate >= 0` always. The condition is
unsatisfiable.

**Fix:** hard-code the hysteresis to a fraction of the threshold, e.g. `threshold * 0.6`.

### 5.4 🟠 MEDIUM — predicted-risk alerts never clear

`_check_predicted_risk_alert` has no `else: _clear_alert(...)` branch — a forecast that drops back
below 150 leaves the alert active forever (lines 566-568 `continue`).

**Fix:** track which horizons were evaluated and clear `predicted_risk` alerts for the horizons
that no longer trigger.

### 5.5 🟠 MEDIUM — threshold alerts have no hysteresis despite being configured

`HYSTERESIS["water_level_cm"] = 10` is read from the config and **never used**. The clear branch
(line 331-338) is a plain `level < warning_thresh`. An ESP32 reading oscillating by ±1 cm around
150 cm will not flap (the dedup updates the existing row instead of creating a new one), but a
level hovering at 149/151 cm produces continuous updates that overwrite `created_at` — so the alert
reads "just now" forever.

**Fix:** clear at `level < warning_thresh - HYSTERESIS["water_level_cm"]`.

### 5.6 ✅ FIXED — `sensor_nodes.name` is NOT NULL but the API allowed NULL

DDL: `sa.Column('name', sa.String(length=120), nullable=False)`.
API: `DeviceRegisterRequest.name: str | None = Field(None, max_length=120)`.
`register_device` passed `name=body.name` straight through.

Registering without `name` → `IntegrityError` → **500**, not a clean 422. This was a *deployment
blocker*: on a fresh Render database, following the documented curl (which omits `name`) made
registering the ESP32 impossible.

**Fixed** by defaulting the name to the uppercased `node_code`, matching the docstring's suggestion
over making the column nullable — the console wants a human-readable label, and the node code is a
better one than blank. `RegisterResponse.name` was tightened from `str | None` to `str` to match.

Verified in the container: `POST /api/iot/devices/register` with no `name` → **201**
`{"node_code":"SMK-6903","name":"SMK-6903","token":"…","status":"active"}`.

### 5.7 🟠 MEDIUM — `.env` DB password disagrees with `docker-compose.yml`

`.env` → `postgresql+asyncpg://floodguard:floodguard@localhost:5432/floodguard`
`docker-compose.yml:7` → `POSTGRES_PASSWORD: floodguard_secret` (since changed)

The app failed to authenticate against a freshly-created compose database: `.env`, `alembic.ini:4`
and `config.py:37` used `floodguard`, while `docker-compose.yml` and `.env.example` used
`floodguard_secret`.

**Fixed** by aligning `docker-compose.yml` and `.env.example` on `floodguard` — which is what the
`pgdata` volume actually holds and what `alembic.ini` already used. A fresh clone now connects
with no edits. Residual caveat: a pre-existing volume keeps its original password until
`docker compose down -v`.

### 5.8 🟡 LOW — `GET /api/iot/devices` uses the broken geometry descriptor

`iot.py:249,285` use `d.location.y` / `d.location.x`. Geometry written as `f"POINT({lng} {lat})"`
via WKT has no SRID metadata, so the GeoAlchemy2 lazy descriptor raises `AttributeError`.
`flood_monitor.py:137-139` documents this exact trap and uses `ST_X`/`ST_Y` instead.

**Fix:** use `func.ST_Y(...)` / `func.ST_X(...)`.

### 5.9 🟡 LOW — the alert strip never shows a measured value

Backend `trigger_data` uses `level_cm` / `rise_rate_cm_per_5min` / `predicted_level_cm` /
`minutes_since_last_reading`. The frontend looks for `current_value` / `value` and
`threshold` / `threshold_cm` (`AlertStrip.tsx:34-44`, `alerts/page.tsx:285-286`).

Only `threshold_cm` matches. Result: every alert shows its threshold but never its current value.

### 5.10 🟡 LOW — scenario comparison tiles are always blank

`scenarios/page.tsx:302-340` reads `baseline_peak_level_cm`, `baseline_mean_level_cm`,
`scenario_peak_level_cm`, `scenario_mean_level_cm`, `scenario_flooded_area_m2`; the backend returns
none of those keys (see `04-DATA-PIPELINES.md` §5).

### 5.11 🟡 LOW — the map legend and the map polygons use different risk colours

`FloodMap.tsx:374-378` hard-codes `#0284c7 / #22c55e / #eab308 / #f97316 / #ef4444`.
`app/map/page.tsx:150-160` renders legend swatches from `RISK_META` (`#4fd39a / #f5b55c / #f97316 /
#ef6b62`). Only `high` matches.

### 5.12 🟡 LOW — the 3D water shader displaces the wrong axis

`DigitalTwin.tsx:237-239`:
```glsl
vec3 displaced = position;
displaced.z += elevation;     // ← should be .y
```
After `waterGeo.rotateX(-Math.PI/2)`, local Y is up. Displacing Z moves vertices horizontally, so
the wave is invisible and the water reads as a flat tinted sheet.

### 5.13 🟡 LOW — `about.py` reports the configured grid resolution, not the real one

`/api/about/assumptions` returns `grid_resolution_m: 10.0` from `settings.flood_grid_resolution_m`,
but the simulation actually uses the DEM's **30.92 m**. The `limitations` list correctly says
"~30 m SRTM", so the endpoint contradicts itself.

### 5.14 🟡 LOW — the Docstring/behaviour mismatches

| Place | Claim | Reality |
|---|---|---|
| `virtual_sensors.generate_now:150` | "The live IoT orchestration uses this after accepting an ESP32 observation" | `mithi_demo_pipeline` replaced it; never called |
| `iot.py:54-56` | `_norm_risk` "Risk is stored uppercase" ✅ | correct |
| `alert_engine.py:266` | `scenario_id` filter in `_get_latest_reading` | `Forecast.scenario_id == scenario_id` — with `scenario_id=None` this matches **live** forecasts, so the non-scenario branch below it is dead |
| `about.py:97-102` | depth thresholds | re-declared literals, will drift from `config.py` |
| `types/index.ts:233-238` | `Alert.is_active` docstring | correct and well-explained |

### 5.15 🟡 LOW — `event_bridge` has no reconnect

`EventBridge._run()` wraps `pubsub.listen()` in a single `async for`. If the Redis connection drops,
the task exits and logs `event_bridge_failed` once — **it never restarts**, so realtime goes silent
while the 3-second REST poll keeps the UI alive. The frontend would report `degraded` forever.

### 5.16 🟡 LOW — database growth is unbounded

| Table | Growth |
|---|---|
| `forecasts` | 3 rows per physical node per **minute** |
| `risk_assessments` | 1 row per accepted reading |
| `flood_layers` | **12 rows per `iot` reading** (4 steps × 3 types) |
| `flood_impacts` | 1 row per affected feature per `depth` layer |

A device posting every 5 seconds ⇒ 144 `flood_layers` rows/minute ⇒ ~207 k rows/day.
`POST /api/demo/reset` is the only cleanup. `System-Architecture.md:798` acknowledges this:
"Raw telemetry is kept for the hackathon duration."

### 5.17 🟡 LOW — `DEMO_INPUT_*` missing from `backend/.env`

`.env.example` documents the block; `.env` omits it, so the code defaults apply. Same values, but a
user editing `.env` would not discover the knobs.

### 5.18 🟡 LOW — `flood.mithi.updated` missing from `RealtimeEventName`

Emitted by `mithi_demo_pipeline.py:92`, subscribed in `useFloodguard.ts:16`, but absent from the
union in `lib/socket.ts:14-21`.

### 5.19 ✅ FIXED — the telemetry rate limit was declared but never enforced

`iot_telemetry_rate_limit_per_minute` existed in `Settings` and was set to `12` in `render.yaml`,
but a repo-wide search found **no reader**. It advertised a protection that did not exist, on the
one endpoint that is publicly reachable and expensive to serve (four flood simulations per
accepted reading).

**Fixed** by adding `app/services/rate_limit.py`: a per-device sliding window over
`time.monotonic()`, returning `429` with a `Retry-After` header, plus a sweep so retired node codes
cannot leak deques. `reset()` exists for tests.

Verified in the container: 16 rapid POSTs produced `201` ×12 then `429` ×4.

Two properties to keep in mind:

- Counters are **per-process**. Exact at the documented single worker; the allowance multiplies if
  `--workers` is raised. Move to Redis before scaling out.
- It runs *inside* the endpoint, after the auth dependency, so `401`s are not counted. That is
  deliberate — a rejected token does no database or simulation work, so there is nothing to bound.

### 5.20 ✅ FIXED — a device could claim any `source`, poisoning replay bookkeeping

`TelemetryRequest.source` was accepted from the wire with no constraint, and `ingest_payload()`
trusted it. A device holding a valid bearer token could POST `"source": "replay"` or
`"simulated"`, which flips `data_mode`, suppresses the live Mithi demo pipeline, and lets the
reading reach the replay/demo accounting as if it were not live hardware. `docs/12` §A.7 already
claimed the server pinned this; the code did not.

**Fixed** by pinning `source` to `"iot"` in `register`/`ingest` on the HTTP path. Replay calls
`ingest_payload()` directly and is unaffected, so `simulated`/`scenario`/`replay` still work where
they belong.

Verified in the container: a POST with `"source":"replay"` returned `201 accepted`, and the stored
row's `source` column is `iot`.

---

## 6. Performance observations

| Hot spot | Cost | Note |
|---|---|---|
| `flood_simulation._interpolate_water_surface` | O(rows × cols) Python loop with `shapely Point` construction + `river_line.project()` per cell = **9,720 iterations** per simulation | dominant cost; vectorisable with `scipy.spatial.cKDTree` |
| `_apply_connectivity_mask` | another O(rows × cols) `Point` + `line.distance()` loop = **9,720** shapely calls | dominant cost |
| `_vectorize_flood_layers` | O(rows × cols) BFS + `unary_union` per component | components < 4 cells are dropped, which bounds the work |
| `_query_features` | `ST_Intersects` + a Python-side `intersection()` per matched feature | GiST index makes the SQL cheap |
| `forecast_service.generate_all_forecasts` | opens a **new `AsyncSessionLocal()` per node** inside a loop (`lines 170-182`) | N+1 sessions |
| `/api/flood/nodes` | N+1 queries: latest reading + risk + forecasts per node | fine for 1 node, degrades linearly |
| `useFloodguard` poll | 5 requests / 3 s | acceptable locally |
| `FloodMap` polls | 5 independent 3 s pollers (study area, waterways, buildings, roads, layer) | 15 requests/minute |
| `DigitalTwin` | scene rebuild avoided via `nodeLayoutKey`; `setPixelRatio(min(dpr,2))`; `PCFShadowMap` at 2048² | good |

---

## 7. What is genuinely well-built

Stated plainly, because it matters for a review:

1. **The GeoJSON contract test.** A 22-test suite that exercises the real vectoriser and catches a
   whole-layer render failure is exactly right, and it is committed.
2. **The SQL regression test with a real database.** Refusing to skip on a SQL error (rather than on
   any error) is the correct call.
3. **The honesty-by-construction formatting.** `EM_DASH` + `isNum` + "a real zero IS rendered as
   zero" is a genuinely well-designed contract, with a test per direction.
4. **`degraded` vs `offline`.** Distinguishing "REST healthy, socket down" from "backend gone" is a
   real operational distinction that most dashboards get wrong.
5. **Freshness gating at four layers.** `is_recent_physical_reading` (backend), `hasRecentLiveIoTReading`
   (frontend, mirroring the 5-minute window), the `latest=null` response shape, and the explicit
   `WAITING FOR REAL IoT INPUT` copy. Consistent end to end.
6. **`resolve_data_path`.** One function that fixed a whole class of CWD bugs, with the reason
   written down.
7. **`include_object` in `alembic/env.py`.** A subtle PostGIS/Tiger trap, solved and documented.
8. **`spatial_index=False` + explicit GiST indexes.** Avoids duplicate index creation, with a comment.
9. **The synthetic-terrain opt-in.** Refusing to substitute fabricated elevation silently, and refusing
   to let it occupy the real cache key.
10. **`fileConfig(..., disable_existing_loggers=False)`** with the explanation that the default would
    silence `uvicorn.error` for the process lifetime.
11. **Every comment states *why*, not *what*.** The single biggest quality marker in this codebase.

---

## 8. Suggested fix order

| Priority | Fix | Effort |
|---|---|---|
| 1 | Append `upstream_level_cm` in `generate_mithi_forecasts` (§5.1) — the Random Forest currently never runs | 3 lines |
| 2 | Fix the map marker filter (§5.2) | 1 line |
| 3 | Align the DB password (§5.7) | 1 line |
| 4 | Make `sensor_nodes.name` nullable or default it (§5.6) | 1 line + migration |
| 5 | Fix the rapid-rise hysteresis (§5.3) | 1 line |
| 6 | Add a clear branch to `predicted_risk` (§5.4) | ~8 lines |
| 7 | Update `constants.test.ts:72` to `'REAL IoT INPUT'` | 1 line |
| 8 | Use `ST_X`/`ST_Y` in `iot.py` (§5.8) | 4 lines |
| 9 | Rename the trigger_data key or update the frontend (§5.9) | 1 line |
| 10 | Return the 4 comparison keys the UI reads (§5.10) | ~15 lines |
| 11 | Apply threshold hysteresis (§5.5) | 2 lines |
| 12 | Fix `displaced.z` → `displaced.y` (§5.12) | 1 line |
| 13 | Restart the event bridge on Redis failure (§5.15) | ~10 lines |
| 14 | Unify the two risk palettes (§5.11) | 6 lines |
| 15 | Delete `data/ml/synthetic_water_levels.csv` and `data/terrain/sensor_elevations.json` | 2 files |
| 16 | Add unit tests for `map_demo_level`, `assess_current_risk`, `calculate_rise_rate` | ~150 lines |