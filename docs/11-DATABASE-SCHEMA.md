# 11 — Database Schema

**PostgreSQL 16 + PostGIS 3.4** (`postgis/postgis:16-3.4`).
All timestamps are `timestamptz` (UTC). All geometry is **SRID 4326**.
Schema owner: Alembic. Migration head: **`0933a21ed37c`**.

`main._verify_schema()` compares `Base.metadata.tables` against the live table list at startup and only
**warns** — it never calls `create_all`, because Alembic is the single source of truth.

---

## 0. The extension

```sql
CREATE EXTENSION IF NOT EXISTS postgis;     -- main.py:39, on every startup
```

The `postgis/postgis` image also ships Tiger/TopoDB tables (`spatial_ref_sys`, `tiger_faces`,
`place_lookup`, …). `alembic/env.py:21-35` `include_object()` filters autogenerate to only tables in
`Base.metadata`, and drops reflected indexes — without that, autogenerate would emit `DROP TABLE` for
extension-owned tables and break the database.

---

## 1. Table inventory (16 tables, 4 groups)

| # | Table | Rows at rest | Purpose |
|---|---|---|---|
| 1 | `study_area` | **1** | the corridor definition |
| 2 | `sensor_nodes` | 1 physical (after registration) | node registry + device auth |
| 3 | `node_relationships` | 0 | upstream/downstream propagation rules |
| 4 | `telemetry_readings` | grows per reading | the time series |
| 5 | `rejected_readings` | grows per rejection | audit log, feeds the sensor-fault alert |
| 6 | `model_registry` | 3 (after training) | model versions + metrics |
| 7 | `forecasts` | 3 per node per cycle | every prediction ever made |
| 8 | `risk_assessments` | 1 per reading + 4 per scenario node | risk history |
| 9 | `roads` | ~4,376 | OSM roads |
| 10 | `buildings` | ~4,521 | OSM footprints |
| 11 | `scenarios` | 0 | what-if runs |
| 12 | `flood_layers` | 12 per simulation | depth/risk/boundary GeoJSON |
| 13 | `flood_impacts` | varies | affected features |
| 14 | `alerts` | varies | alert lifecycle |
| 15 | `replay_recordings` | 0 | replay payloads |
| 16 | `data_sources` | **5** (seeded) | provenance registry |

---

## 2. Reference & configuration

### 2.1 `study_area`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `INTEGER` PK | no | not autoincrement; one row |
| `name` | `VARCHAR(100)` | no | seeded `"Mithi River — BKC to Kurla, Mumbai"` |
| `min_lat`, `min_lon`, `max_lat`, `max_lon` | `FLOAT8` | no | from `settings.study_area_*` |
| `boundary` | `geometry(POLYGON, 4326)` | no | GiST `ix_study_area_boundary` |
| `river_line` | `geometry(LINESTRING, 4326)` | yes | **always NULL** — never populated |
| `grid_cell_size_m` | `FLOAT8` | no | seeded from `flood_grid_resolution_m` = **10.0** (the DEM is actually 30.92 m) |
| `created_at` | `TIMESTAMPTZ` | no | `DEFAULT now()` |

Seeded by `seed.py:93-107` with a WKT ring built from the four bounds. Idempotent (checks for any
existing row).

### 2.2 `sensor_nodes`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | `INTEGER` PK | no | autoincrement | |
| `node_code` | `VARCHAR(32)` | no | — | **UNIQUE**, indexed `ix_sensor_nodes_node_code` |
| `name` | `VARCHAR(120)` | no | — | **NOT NULL in DDL**, but `register_device` passes `body.name` which is nullable → an insert with `name: null` fails |
| `node_type` | `VARCHAR(20)` | no | `"physical"` | `physical` \| `virtual` (no CHECK constraint) |
| `role` | `VARCHAR(20)` | yes | — | `upstream` \| `midstream` \| `downstream` |
| `location` | `geometry(POINT, 4326)` | no | — | GiST `ix_sensor_nodes_location` |
| `reference_elevation_m` | `FLOAT8` | yes | — | fallback `grid["min_elevation"]` if NULL |
| `calibration_offset_cm` | `FLOAT8` | no | `0.0` | |
| `baseline_level_cm` | `FLOAT8` | yes | — | |
| `warning_threshold_cm` | `FLOAT8` | yes | — | per-node threshold alert override |
| `is_active` | `BOOLEAN` | no | `True` | |
| `token_hash` | `VARCHAR(64)` | yes | — | **UNIQUE** — SHA-256 hex of the device bearer token |
| `battery_pct` | `FLOAT8` | yes | — | derived: `(V − 3.3) / 0.9 × 100`, clamped 0–100 |
| `last_seen_at` | `TIMESTAMPTZ` | yes | — | set on every accepted reading |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | no | `now()` | `updated_at` has `onupdate=now()` |

Indexes: `ix_sensor_nodes_active (is_active)`, `ix_sensor_nodes_location (gist)`, unique
`ix_sensor_nodes_node_code`.

> **DDL vs model mismatch:** the migration declares `name TEXT`/`VARCHAR` **NOT NULL**, and
> `DeviceRegisterRequest.name` is `str | None = None`. Registering without a `name` will raise an
> IntegrityError, not a clean 422.

### 2.3 `node_relationships`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `INTEGER` PK | no | autoincrement |
| `upstream_node_id` | `INTEGER` FK → `sensor_nodes.id` | no | |
| `downstream_node_id` | `INTEGER` FK → `sensor_nodes.id` | no | |
| `delay_seconds` | `INTEGER` | no | the lag |
| `attenuation` | `FLOAT8` | no | 0–1 factor |
| `distance_m` | `FLOAT8` | yes | |
| `notes` | `TEXT` | yes | |

`UNIQUE (upstream_node_id, downstream_node_id)` named `uq_node_relationship`. **Empty** — only
`virtual_sensors.seed_virtual_network()` writes it, and that is never called.

---

## 3. Time-series & quality

### 3.1 `telemetry_readings`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | `BIGINT` PK | no | autoincrement | |
| `node_id` | `INTEGER` FK → `sensor_nodes.id` | no | | |
| `recorded_at` | `TIMESTAMPTZ` | no | | **device** timestamp (the "when") |
| `received_at` | `TIMESTAMPTZ` | no | `now()` | **API** timestamp (the "received") |
| `water_level_cm` | `FLOAT8` | no | | cm above the sensor reference |
| `rainfall_mm_hr` | `FLOAT8` | no | `0.0` | mm/hr |
| `flow_rate` | `FLOAT8` | yes | | m³/s |
| `soil_moisture` | `FLOAT8` | yes | | 0–1 |
| `battery_voltage` | `FLOAT8` | yes | | volts |
| `source` | `VARCHAR(20)` | no | `"iot"` | `iot` \| `simulated` \| `replay` \| `scenario` |
| `data_mode` | `VARCHAR(20)` | no | `"live"` | `live` \| `replay` \| `scenario` |
| `event_id` | `VARCHAR(80)` | yes | | device idempotency key; auto-filled with `uuid4().hex` if absent |
| `risk_level` | `VARCHAR(20)` | yes | | denormalised risk snapshot, **UPPERCASE** |
| `risk_score` | `FLOAT8` | yes | | |

Indexes (4):

| Index | Columns | Type |
|---|---|---|
| `ix_telemetry_node_time` | `(node_id, recorded_at)` | btree |
| `ix_telemetry_source` | `(source)` | btree |
| `ix_telemetry_data_mode` | `(data_mode)` | btree |
| `ix_telemetry_event_id` | `(event_id)` | btree |

> **No uniqueness on `event_id`** — idempotency is enforced in application code by a
> `SELECT … WHERE node_id = ? AND event_id = ? LIMIT 1` before insert. Two genuinely concurrent
> retries of the same `event_id` could both insert.

### 3.2 `rejected_readings`

| Column | Type | Null |
|---|---|---|
| `id` | `BIGINT` PK | no |
| `node_code` | `VARCHAR(32)` | yes |
| `received_at` | `TIMESTAMPTZ` not null, `DEFAULT now()` | no |
| `raw_payload` | `JSONB` | no |
| `reject_reason` | `TEXT` | no |

Indexes: `ix_rejected_node_time (node_code, received_at)` and the auto `ix_rejected_readings_node_code`.

Every `reject_reason` value, all now produced by `app/services/ingest.py` (the single
transport-neutral validator shared by the HTTP endpoint and Replay mode):

| Reason | Source |
|---|---|
| `"Sensor reported invalid reading"` | device sent `valid: false` |
| `"Missing timestamp"` | no `timestamp` field |
| `"Invalid timestamp format"` | not parseable as ISO 8601 |
| `"Timestamp outside ±24h of server time: …"` | device clock implausible |
| `"Invalid numeric field: …"` | `water_level_cm` missing or non-numeric |
| `"Invalid source: <x>"` | `source` not in `{iot, simulated, replay, scenario}` |
| `"Soil moisture out of range: <x>"` | outside 0–1 |
| `"Water level out of range: <x>"` | outside 0–500 cm, or non-finite |
| `"Rainfall out of range: <x>"` | outside 0–500 mm/hr, or non-finite |

Three rejections are **not** audited, because they are not payload defects — they describe the
target rather than the body, and the caller maps them to HTTP codes:

| Condition | HTTP |
|---|---|
| `"Missing node_code"` | 400 |
| `"Unregistered node: <code>"` | 400 (live hardware must be registered first) |
| `"Node <code> is deactivated."` | 403 (set by `get_current_device` before this point) |

The alert engine's sensor-fault rule counts rows in the **last hour** and fires at 3
(`alert_engine.py:751-775`).

---

## 4. AI / ML

### 4.1 `model_registry`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `INTEGER` PK | no | autoincrement |
| `model_name` | `VARCHAR(64)` | no | `rf_15_30_60_{15,30,60}min` or `persistence_{…}min` |
| `version` | `VARCHAR(32)` | no | `rf-v1-<YYYYMMDDHHMM>` |
| `horizon_minutes` | `INTEGER` | no | 15 / 30 / 60 |
| `algorithm` | `VARCHAR(64)` | no | `random_forest` or `persistence` |
| `artifact_path` | `VARCHAR(512)` | no | absolute resolved path |
| `training_data_description` | `TEXT` | no | the honesty string |
| `mae`, `rmse` | `FLOAT8` | yes | |
| `baseline_mae`, `baseline_rmse` | `FLOAT8` | yes | persistence reference |
| `trained_at` | `TIMESTAMPTZ` | no | |
| `is_active` | `BOOLEAN` | no | `False` default; the trainer upserts with `True` |

`UNIQUE (model_name, version, horizon_minutes)` named `uq_model_registry`.

### 4.2 `forecasts`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `BIGINT` PK | no | autoincrement |
| `node_id` | `INTEGER` FK → `sensor_nodes.id` | no | |
| `model_id` | `INTEGER` FK → `model_registry.id` | yes | **NULL when the artefact bundle path is used** (`_model_registry` lookup misses → `None`) |
| `scenario_id` | `INTEGER` FK → `scenarios.id` | yes | **NULL = live/replay baseline** |
| `made_at` | `TIMESTAMPTZ` | no | when the prediction was made |
| `target_time` | `TIMESTAMPTZ` | no | `made_at + horizon` |
| `horizon_minutes` | `INTEGER` | no | 15 / 30 / 60 (and 0 for scenario horizons) |
| `predicted_level_cm` | `FLOAT8` | no | rounded to 1 dp, clamped ≥ 0 |
| `lower_bound_cm`, `upper_bound_cm` | `FLOAT8` | yes | **hard-coded ±10 cm** |
| `input_source` | `VARCHAR(20)` | no | `live` \| `simulated` \| `replay` \| `scenario` \| **`iot-demo`** |
| `status` | `VARCHAR(20)` | no | `ok` \| `insufficient_data` |

Indexes: `ix_forecasts_node_time (node_id, made_at)`, `ix_forecasts_scenario (scenario_id)`,
`ix_forecasts_node_target (node_id, target_time)` — the last added by migration `0933a21ed37c`.

> **This table grows unboundedly.** The 60-second auto-forecast writes 3 rows per physical node per
> minute. With one node that is 4,320 rows/day. `POST /api/demo/reset` is the only cleanup path.

> The `input_source` value `"iot-demo"` is **not in the enum** documented in
> `System-Architecture.md:607`. Since there is no CHECK constraint, nothing rejects it — but any
> consumer filtering on the documented enum will miss every live forecast.

### 4.3 `risk_assessments`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | `BIGINT` PK | no | | autoincrement |
| `node_id` | `INTEGER` FK | no | | |
| `forecast_id` | `BIGINT` FK → `forecasts.id` | yes | | NULL for horizon-0 ingest rows and for some scenario rows |
| `scenario_id` | `INTEGER` FK → `scenarios.id` | yes | | NULL = live |
| `assessed_at` | `TIMESTAMPTZ` | no | `now()` | |
| `horizon_minutes` | `INTEGER` | no | | **0 = current**, 15/30/60 |
| `risk_score` | `FLOAT8` | no | | 0–1 |
| `risk_level` | `VARCHAR(20)` | no | | **UPPERCASE** |
| `predicted_level_cm` | `FLOAT8` | yes | | |
| `level_component` | `FLOAT8` | no | `0.0` | **normalised** 0–1, not weighted |
| `rise_rate_component` | `FLOAT8` | no | `0.0` | |
| `rainfall_component` | `FLOAT8` | no | `0.0` | |
| `soil_component` | `FLOAT8` | no | `0.0` | |
| `primary_driver` | `VARCHAR(64)` | yes | | `Water Level` \| `Rise Rate` \| `Rainfall` \| `Soil Saturation` |

Indexes: `ix_risk_node_time (node_id, assessed_at)`, `ix_risk_scenario (scenario_id)`.

---

## 5. Geospatial base data

### 5.1 `roads`

| Column | Type | Null |
|---|---|---|
| `id` | `BIGINT` PK | no |
| `osm_id` | `BIGINT` | yes |
| `name` | `VARCHAR(255)` | yes |
| `road_type` | `VARCHAR(64)` | yes |
| `geometry` | `geometry(LINESTRING, 4326)` | no |
| `min_elevation_m` | `FLOAT8` | yes — **never written** |

GiST `ix_roads_geom`. Loaded by `load_osm_layers.load_roads()` from `data/osm/roads.geojson`.

### 5.2 `buildings`

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | `BIGINT` PK | no | |
| `osm_id` | `BIGINT` | yes | |
| `name` | `VARCHAR(255)` | yes | |
| `building_type` | `VARCHAR(64)` | yes | |
| `height_m` | `FLOAT8` | yes | |
| `height_is_estimated` | `BOOLEAN` | no | `True` |
| `ground_elevation_m` | `FLOAT8` | yes | sampled from the DEM at the centroid; NULL outside the raster |
| `footprint` | `geometry(POLYGON, 4326)` | no | |

GiST `ix_buildings_geom`.

> `height_method` (the provenance string: `osm_height_tag` / `building_levels_x3m` /
> `estimated_<type>_levels`) exists in the GeoJSON file but is **not carried into the table**. So
> once loaded, the database cannot tell you which of the 4,521 buildings had a real measured height.
> Only the boolean survives.

---

## 6. Simulation & scenarios

### 6.1 `scenarios`

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | `INTEGER` PK | no | autoincrement |
| `name` | `VARCHAR(120)` | yes | |
| `preset` | `VARCHAR(64)` | yes | |
| `rainfall_mm_hr` | `FLOAT8` | no | |
| `initial_level_cm` | `FLOAT8` | no | |
| `drainage_capacity_pct` | `FLOAT8` | no | |
| `duration_minutes` | `INTEGER` | no | |
| `assumptions` | `JSONB` | no | `SCENARIO_ASSUMPTIONS` + the four inputs + the preset key |
| `status` | `VARCHAR(20)` | no | `"running"` → `completed` / `failed` |
| `error_message` | `TEXT` | yes | |
| `created_at` | `TIMESTAMPTZ` | no | `now()` |
| `completed_at` | `TIMESTAMPTZ` | yes | |

No indexes. Note `drainage_capacity_pct` has **no CHECK** despite `System-Architecture.md:682`
specifying `BETWEEN 0 AND 100`.

### 6.2 `flood_layers`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `BIGINT` PK | no | autoincrement |
| `scenario_id` | `INTEGER` FK → `scenarios.id` | yes | NULL = live baseline |
| `generated_at` | `TIMESTAMPTZ` | no | `now()` |
| `time_step_minutes` | `INTEGER` | no | 0 / 15 / 30 / 60 |
| `layer_type` | `VARCHAR(20)` | no | `depth` \| `risk` \| `boundary` |
| `geojson` | `JSONB` | no | a whole `FeatureCollection` |
| `max_depth_m` | `FLOAT8` | yes | capped at 5.0 by the engine |
| `flooded_area_m2` | `FLOAT8` | yes | `Σ cell_count × resolution²` |
| `input_source` | `VARCHAR(20)` | no | `live` \| `simulated` \| `replay` \| `scenario` \| `iot-demo` |
| `grid_resolution_m` | `FLOAT8` | no | **30.92** in practice (the DEM's real resolution) |

Index: `ix_flood_layers_lookup (scenario_id, time_step_minutes, layer_type)`.

**Row growth:** each simulation writes 3 rows, 4 steps → **12 rows per cycle**. The Mithi demo
pipeline runs one simulation per `iot` reading, so a device posting every 5 seconds generates
**144 rows/minute**. Read paths always take `ORDER BY generated_at DESC LIMIT 1` per
`(scenario_id, time_step, layer_type)`, so correctness is fine but storage grows without bound.

### 6.3 `flood_impacts`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `BIGINT` PK | no | autoincrement |
| `flood_layer_id` | `BIGINT` FK → `flood_layers.id` **ON DELETE CASCADE** | no | only the `depth` layer carries impacts |
| `feature_type` | `VARCHAR(20)` | no | `road` \| `building` |
| `feature_id` | `BIGINT` | no | `roads.id` or `buildings.id` — **not** `osm_id` |
| `estimated_depth_m` | `FLOAT8` | no | sampled at the intersection's representative point |
| `risk_level` | `VARCHAR(20)` | no | **lowercase** (from `_classify_depth_risk`) |

Index: `ix_impacts_layer (flood_layer_id, feature_type)`.

> `feature_id` has **no FK** to `roads`/`buildings` — it is a loose reference. Combined with
> `ON DELETE CASCADE` on the layer, reloading OSM layers (`--force` deletes all rows and reinserts
> with new autoincrement ids) silently invalidates every historical `feature_id`.

---

## 7. Alerts

### 7.1 `alerts`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | `BIGINT` PK | no | | autoincrement |
| `node_id` | `INTEGER` FK → `sensor_nodes.id` | yes | | |
| `scenario_id` | `INTEGER` FK → `scenarios.id` | yes | | NULL = live |
| `alert_type` | `VARCHAR(40)` | no | | `threshold` \| `predicted_risk` \| `rapid_rise` \| `communication_failure` \| `sensor_fault` |
| `severity` | `VARCHAR(20)` | no | | `information` \| `warning` \| `high` \| `critical` |
| `message` | `TEXT` | no | | plain language |
| `trigger_data` | `JSONB` | yes | | rule-specific |
| `is_scenario_alert` | `BOOLEAN` | no | `False` | |
| `is_active` | `BOOLEAN` | no | `True` | |
| `created_at` | `TIMESTAMPTZ` | no | `now()` | **overwritten** on a threshold severity change |
| `cleared_at` | `TIMESTAMPTZ` | yes | | set when the condition resolves |
| `acknowledged` | `BOOLEAN` | no | `False` | |
| `acknowledged_at` | `TIMESTAMPTZ` | yes | | |
| `acknowledged_by` | `VARCHAR(64)` | yes | | default `"operator"` |

Indexes: `ix_alerts_active (is_active, severity, created_at)`,
`ix_alerts_node_type (node_id, alert_type, is_active)`.

### 7.2 `trigger_data` keys per rule

| Rule | Keys |
|---|---|
| `threshold` | `{"level_cm", "threshold_cm"}` |
| `rapid_rise` | `{"rise_rate_cm_per_5min"}` |
| `predicted_risk` | `{"horizon_minutes", "predicted_level_cm"}` |
| `communication_failure` | `{"minutes_since_last_reading"}` |
| `sensor_fault` | `{"rejected_count"}` |
| any superseded duplicate | `+ {"superseded_by", "superseded_reason"}` |

> The frontend's `AlertStrip` and `alerts/page.tsx` look for `current_value`/`value` and
> `threshold`/`threshold_cm`. Only the threshold rule uses the second convention, so **measured
> values never render on the alert strip** for any rule.

---

## 8. Demo support & metadata

### 8.1 `replay_recordings`

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | `INTEGER` PK | no | autoincrement |
| `name` | `VARCHAR(120)` | no | |
| `description` | `TEXT` | yes | `"Recorded N readings over Ns"` |
| `origin` | `VARCHAR(40)` | no | `physical_recording` (only value written) |
| `duration_seconds` | `INTEGER` | no | |
| `payload` | `JSONB` | no | `{"readings":[{node_code, water_level_cm, rainfall_mm_hr, timestamp, source}]}` |
| `created_at` | `TIMESTAMPTZ` | no | `now()` |

No indexes. Empty by default.

### 8.2 `data_sources`

| Column | Type | Null |
|---|---|---|
| `id` | `INTEGER` PK | no |
| `name` | `VARCHAR(120)` | no |
| `category` | `VARCHAR(40)` | no |
| `provider` | `VARCHAR(120)` | yes |
| `resolution` | `VARCHAR(64)` | yes |
| `licence_note` | `TEXT` | yes |
| `limitation` | `TEXT` | **no** |
| `last_updated` | `TIMESTAMPTZ` | yes — **never written** |

No indexes. Seeded with 5 rows (see `02-DATA-INVENTORY.md` §10b). `limitation` being NOT NULL is a
deliberate design choice: every registered dataset **must** state its own limitation.

---

## 9. Index summary (21 total)

| Table | Index | Columns | Type |
|---|---|---|---|
| `study_area` | `ix_study_area_boundary` | boundary | GiST |
| `study_area` | `ix_study_area_river_line` | river_line | GiST |
| `sensor_nodes` | `ix_sensor_nodes_active` | is_active | btree |
| `sensor_nodes` | `ix_sensor_nodes_location` | location | GiST |
| `sensor_nodes` | `ix_sensor_nodes_node_code` (unique) | node_code | btree |
| `telemetry_readings` | `ix_telemetry_node_time` | node_id, recorded_at | btree |
| `telemetry_readings` | `ix_telemetry_source` | source | btree |
| `telemetry_readings` | `ix_telemetry_data_mode` | data_mode | btree |
| `telemetry_readings` | `ix_telemetry_event_id` | event_id | btree |
| `rejected_readings` | `ix_rejected_node_time` | node_code, received_at | btree |
| `rejected_readings` | `ix_rejected_readings_node_code` | node_code | btree |
| `forecasts` | `ix_forecasts_node_time` | node_id, made_at | btree |
| `forecasts` | `ix_forecasts_scenario` | scenario_id | btree |
| `forecasts` | `ix_forecasts_node_target` | node_id, target_time | btree |
| `risk_assessments` | `ix_risk_node_time` | node_id, assessed_at | btree |
| `risk_assessments` | `ix_risk_scenario` | scenario_id | btree |
| `roads` | `ix_roads_geom` | geometry | GiST |
| `buildings` | `ix_buildings_geom` | footprint | GiST |
| `alerts` | `ix_alerts_active` | is_active, severity, created_at | btree |
| `alerts` | `ix_alerts_node_type` | node_id, alert_type, is_active | btree |
| `flood_layers` | `ix_flood_layers_lookup` | scenario_id, time_step_minutes, layer_type | btree |
| `flood_impacts` | `ix_impacts_layer` | flood_layer_id, feature_type | btree |

Plus two implicit unique indexes: `uq_model_registry (model_name, version, horizon_minutes)` and
`uq_node_relationship (upstream_node_id, downstream_node_id)`, and the unique constraint on
`sensor_nodes.token_hash`.

---

## 10. Foreign-key graph

```
sensor_nodes ──┬─< telemetry_readings      (node_id)
               ├─< forecasts                (node_id)
               ├─< risk_assessments         (node_id)
               ├─< alerts                   (node_id)
               └─< node_relationships       (upstream_node_id, downstream_node_id)

model_registry ──< forecasts                (model_id, nullable)

scenarios ──┬─< forecasts                  (scenario_id, nullable)
            ├─< risk_assessments           (scenario_id, nullable)
            ├─< flood_layers               (scenario_id, nullable)
            └─< alerts                     (scenario_id, nullable)

flood_layers ──< flood_impacts              (flood_layer_id, ON DELETE CASCADE)
```

`roads` and `buildings` have **no** FK references, yet `flood_impacts.feature_id` points at them
loosely.

---

## 11. Migrations

### `046c73cac147` — `initial floodguard schema` (2026-10-05 01:28)

`down_revision = None`. Creates all 16 tables and 20 indexes in dependency order
(`buildings`, `data_sources`, `model_registry`, `rejected_readings`, `replay_recordings`, `roads`,
`scenarios`, `sensor_nodes`, `study_area`, then `alerts`, `flood_layers`, `forecasts`,
`node_relationships`, `telemetry_readings`, `flood_impacts`, `risk_assessments`).

Geometry columns render as
`geoalchemy2.types.Geometry(geometry_type=…, srid=4326, dimension=2, spatial_index=False,
from_text='ST_GeomFromEWKT', name='geometry')`. The header comment explains why `geoalchemy2` is
imported in the template: "PostGIS geometry columns render as geoalchemy2.types.Geometry;
autogenerate does not add this import itself, so the template supplies it."

`downgrade()` drops everything in reverse order.

### `0933a21ed37c` — `add composite index on forecasts node_id target_time` (2026-10-05 14:16)

`down_revision = 046c73cac147`. Adds `ix_forecasts_node_target` on `(node_id, target_time)`.

### Applying them

```bash
cd backend
alembic upgrade head        # DB URL is read via get_settings(), NOT alembic.ini
alembic downgrade base
alembic revision --autogenerate -m "…"
```

`alembic/env.py:61-85` rewrites the URL: `postgres://` → `postgresql+asyncpg://`,
`postgresql://` → `postgresql+asyncpg://`, and appends `ssl=require` for any host not in
`{localhost, 127.0.0.1, db, postgres}`. The comment at lines 64-70 explains why `os.getenv` is not
used: "A bare `os.getenv` skipped all of that and fell through to alembic.ini's Docker-Compose-only
`db:5432`, which fails DNS off-Docker — and, worse, would silently migrate a different database than
the one serving traffic."

---

## 12. Redis keys

| Key | TTL | Written by | Read by |
|---|---|---|---|
| `floodguard:realtime` | — (pub/sub) | `publish_realtime_event()` | `EventBridge._run()` |
| `floodguard:terrain_grid:real` | 86,400 s | `flood_simulation.load_terrain` (**only if not synthetic**) | same |
| `forecast:{node_id}:{horizon}` | 300 s | `forecast_service._cache_forecast` (Path B, never runs) | `get_cached_forecast` (never called) |

`demo_controller.reset_demo()` deletes `floodguard:*`, `forecast:*`, `weather:*`.

The documented `cache.py` conventions — `weather:{lat}:{lng}` (300 s), `forecast:{lat}:{lng}`
(1800 s), `imd:rainfall:{region}` (86400 s), `osm:{bbox}` (86400 s) — are described in
`integrations/cache.py:4-8` but **`cache_get`/`cache_set`/`make_key` are never called anywhere**, so
none of those keys exist.

---

## 13. Reset semantics

`POST /api/demo/reset` deletes, in FK-safe order:

```
flood_impacts → flood_layers → alerts → risk_assessments → forecasts → telemetry_readings → scenarios
```

Then flushes Redis. **Preserved:** `sensor_nodes`, `sensor_nodes.token_hash`, `study_area`, `roads`,
`buildings`, `model_registry`, `data_sources`, `replay_recordings`, `node_relationships`.

So reset does **not** de-register devices, does not restore `battery_pct` / `last_seen_at`, and does
not clear the replay library.