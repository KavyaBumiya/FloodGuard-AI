# 10 — API Reference

Base URL: `http://127.0.0.1:8000` (local) or `http://<host>:8000`. Interactive docs at `/docs`
(FastAPI auto-generated from the docstrings).

**Authentication:** none on operator endpoints. `Depends(get_current_user)` resolves to
`{"sub":"dashboard","role":"operator"}` for anonymous browsers. Only two endpoints are gated:

| Endpoint | Gate |
|---|---|
| `POST /api/iot/devices/register` | `Authorization: Bearer <IOT_REGISTRATION_TOKEN>` |
| `POST /api/iot/telemetry` | `Authorization: Bearer <per-device token>` |

**Response conventions**

- Timestamps: ISO 8601 with timezone, stored UTC.
- Risk levels: **uppercase in the DB, lowercase in every API response** (`normalize_risk`).
- Layer responses always carry a `note` disclaimer.

---

## System

### `GET /health` and `GET /api/health`

Never raises. Always 200.

```json
{
  "status": "ok",                  // or "degraded"
  "service": "floodguard-ai",
  "version": "1.0.0",
  "database": "ok",                // or "error: <ExceptionTypeName>"
  "redis": "ok",                   // or "error: <ExceptionTypeName>"
  "ingest": "http",               // HTTPS is stateless - nothing to probe
  "open_meteo": "ok"               // or "unreachable" / "error: <Type>"
}
```

### `GET /api`

```json
{ "service":"FLOODGUARD AI", "version":"1.0.0", "docs":"/docs",
  "endpoints": { "iot":"/api/iot", "monitoring":"/api/flood", "scenarios":"/api/scenarios",
                 "alerts":"/api/alerts", "demo":"/api/demo", "about":"/api/about" } }
```

### `GET /socket.io` — Socket.IO (not REST)

See [`12-REALTIME-AND-ALERTING.md`](12-REALTIME-AND-ALERTING.md).

---

## IoT — `/api/iot`

### `POST /api/iot/devices/register` → 201

**Header:** `Authorization: Bearer <IOT_REGISTRATION_TOKEN>`

```jsonc
// request
{ "node_code": "VAD-ESP32-01",        // 1–32, [A-Za-z0-9_-], auto-uppercased
"name": "Vadodara Demo Sensor",   // optional; DEFAULTS TO node_code. sensor_nodes.name
                                       // is NOT NULL, so omitting it used to raise a
                                       // 500 — fixed, but send it if you want a
                                       // human label in the console.
  "node_type": "physical",            // ^(physical|virtual)$
  "role": null,                       // ^(upstream|midstream|downstream)$
  "lat": 22.3072, "lng": 73.1812,     // lat −90..90, lng −180..180
  "reference_elevation_m": 1.2,
  "calibration_offset_cm": 0.0,
  "baseline_level_cm": 0,
  "warning_threshold_cm": 25,
  "battery_pct": 100,                 // 0..100
  "metadata": null }

// response — the token is shown EXACTLY ONCE
{ "node_code": "VAD-ESP32-01", "token": "<urlsafe-32-bytes>", "name": "Vadodara Demo Sensor", "status": "active" }
```

Errors: **401** `Invalid registration token` · **409** `node_code already registered` · **422**
schema validation.

### `GET /api/iot/devices`

```json
[ { "node_code":"VAD-ESP32-01", "name":"…", "node_type":"physical", "role":null,
    "status":"active", "lat":22.3072, "lng":73.1812,
    "reference_elevation_m":1.2, "calibration_offset_cm":0.0,
    "baseline_level_cm":0.0, "warning_threshold_cm":25.0, "battery_pct":100.0,
    "last_seen_at":"2026-10-06T03:45:02+00:00", "created_at":"…" } ]
```

> ⚠️ This endpoint reads `d.location.y` / `d.location.x` (the GeoAlchemy2 lazy descriptor). Geometry
> written via `ST_GeomFromEWKT` has no SRID metadata, so this can raise `AttributeError` on
> registered nodes. The flood API avoids this by using `ST_X`/`ST_Y`.

### `GET /api/iot/devices/{node_code}`

Single node in the same shape. **404** `Device not found`.

### `POST /api/iot/telemetry` → 201

**Header:** `Authorization: Bearer <device token>`

```jsonc
// request
{ "node_code": "VAD-ESP32-01",        // must match the authenticated device
  "water_level_cm": 14.5,             // 0..500, REQUIRED
  "rainfall_mm_hr": 6.2,              // 0..500, REQUIRED
  "timestamp": "2026-10-06T03:45:00Z",     // ISO 8601, REQUIRED. Send unambiguous UTC.
  "flow_rate": null,                  // ≥0, optional  (m³/s)
  "soil_moisture": null,              // 0..1, optional
  "battery_voltage": 4.05,            // ≥0, optional  (V)
  "event_id": "evt-0001",             // ≤80, idempotency key
  "lat": null, "lng": null,           // optional location update
  "source": "iot",                    // accepted but IGNORED — pinned to "iot" (see below)
  "valid": true }

// response
{ "id": 123, "node_code": "VAD-ESP32-01", "status": "accepted",
  "risk_level": "low", "risk_score": 0.05, "received_at": "2026-10-06T03:45:02.113+00:00" }
```

| Status | Meaning |
|---|---|
| 201 `status="accepted"` | stored, risk computed, realtime events published |
| 201 `status="duplicate"` | `event_id` already stored for this node; original row returned |
| 400 | ingest-level rejection (`valid == false`, stale/unparseable timestamp) → a `rejected_readings` row was written first |
| 401 | missing / unknown device token (raised before rate limiting) |
| 403 | node deactivated, or body `node_code` ≠ authenticated device |
| 422 | Pydantic schema validation: missing field, wrong type, or `ge`/`le` range breach. Not audited |
| 429 | per-device rate limit exceeded; honour the `Retry-After` header |

`source` is present in the request schema for compatibility with replay payloads but is
**overwritten with `"iot"`** on this endpoint: a device holding a valid bearer token *is* live
hardware, and letting it claim `simulated`/`replay` would flip `data_mode` and corrupt the demo and
replay bookkeeping. Replay calls `ingest_payload()` directly and is unaffected.

Rate limiting is `IOT_TELEMETRY_RATE_LIMIT_PER_MINUTE` (default `12`, i.e. a 5 s cadence),
counted per device in-process. Verified: 16 rapid POSTs produced `201` ×12 then `429` ×4.

Side effects: writes `telemetry_readings` + a horizon-0 `risk_assessments` row; updates
`sensor_nodes.last_seen_at` and `battery_pct`; publishes `flood.sensor.updated` and
`flood.risk.updated`; **and, when `source == "iot"`, runs the whole Mithi demo pipeline** (forecast →
flood simulation at 4 steps → layer persistence → `flood.mithi.updated`).

### `GET /api/iot/devices/{node_code}/latest`

```json
{ "node_code":"VAD-ESP32-01", "water_level_cm":14.5, "rainfall_mm_hr":6.2,
  "flow_rate":null, "soil_moisture":null, "battery_voltage":4.05,
  "timestamp":"2026-10-06T09:15:00+05:30",   // device clock
  "received_at":"2026-10-06T03:45:02+00:00", // API clock
  "age_seconds": 3.1, "risk_level":"low", "risk_score":0.05,
  "source":"iot", "node_status":"active", "node_type":"physical" }
```

**404** `"No recent live IoT telemetry for this device"` when the 5-minute freshness gate fails.

### `GET /api/iot/devices/{node_code}/history`

Query: `limit` (1–1000, default 100) · `offset` (≥0) · `from` (ISO) · `to` (ISO).
Ordered **ascending** by device timestamp.

```json
[ { "water_level_cm":14.5, "rainfall_mm_hr":6.2, "flow_rate":null, "soil_moisture":null,
    "timestamp":"2026-10-06T09:15:00+05:30", "risk_level":"low", "risk_score":0.05,
    "source":"iot" } ]
```

**422** if `from`/`to` are not ISO 8601.

### `GET /api/iot/devices/{node_code}/risk-history`

Query: `limit` (1–1000, default 100). Newest first.

```json
[ { "horizon_minutes": 0, "risk_level":"low", "risk_score":0.05,
    "primary_driver":"Water Level", "predicted_level_cm":14.5,
    "components": { "level":0.0, "rise_rate":0.0, "rainfall":0.33, "soil":0.33 },
    "assessed_at":"2026-10-06T03:45:02+00:00" } ]
```

> `components` here are the **normalised** 0–1 values, not the weighted contributions.

---

## Flood monitoring — `/api/flood`

### `GET /api/flood/impact-summary`

Query: `scenario_id` (omit for the live baseline).

```json
{ "scenario_id": null,
  "steps": [ { "time_step_minutes": 0,
               "flooded_area_m2": 128400.0, "max_depth_m": 0.94,
               "affected_building_count": 37, "affected_road_count": 9,
               "depth_range_m": [0.10, 0.94],
               "buildings_by_risk": { "low": 20, "moderate": 12, "high": 4, "severe": 1 },
               "roads_by_risk":     { "low": 5, "moderate": 3, "high": 1 },
               "input_source": "iot-demo", "scenario_id": null,
               "generated_at": "2026-10-06T03:45:03+00:00",
               "note": "MODEL ESTIMATE — not an official flood zone." } ],
  "source": "iot-demo",
  "generated_at": "2026-10-06T03:45:10+00:00" }
```

Returns only steps that have a stored `depth` layer. This drives the shared NOW/+15/+30/+60 readout
on the dashboard, map and twin.

### `GET /api/flood/nodes`

**Returns only `node_type == "physical"` and `is_active == True` nodes.** Each node's
`latest_reading`, `current_risk` and `forecasts` are `null`/`[]` when the 5-minute IoT freshness gate
fails.

```jsonc
{ "nodes": [ { "node_code":"VAD-ESP32-01", "name":"Vadodara Demo Sensor",
               "node_type":"physical", "role":null,
               "location": { "lat": 22.3072, "lng": 73.1812 },
               "reference_elevation_m": 1.2, "warning_threshold_cm": 25.0,
               "latest_reading": { "water_level_cm":14.5, "rainfall_mm_hr":6.2,
                                   "timestamp":"…", "source":"iot" },
               "current_risk": { "risk_level":"low", "risk_score":0.05,
                                 "primary_driver":"Water Level" },
               "forecasts": [ { "horizon_minutes":15, "predicted_level_cm":169.0,
                                "lower_bound_cm":159.0, "upper_bound_cm":179.0,
                                "target_time":"…", "status":"ok" } ] } ],

  "simulation_input": {
    "status": "live",                      // or "waiting"
    "label": "LIVE IoT INPUT",
    "location_label": "Physical IoT Sensor",
    "simulation_label": "MITHI RIVER DIGITAL TWIN - Mumbai Simulation",
    "driver_label": "Driven by Live IoT Input",
    "node_code": "VAD-ESP32-01",
    "water_level_cm": 14.5,                // physical
    "rainfall_mm_hr": 6.2,                 // physical
    "mithi_water_level_cm": 167.3,         // MAPPED
    "risk": { "risk_level":"moderate", "risk_score":0.334 },
    "forecasts": [ … ],                    // input_source = "iot-demo"
    "forecast_status": "ok",               // or insufficient_live_iot_data | waiting_for_iot
    "timestamp": "…",
    "mapping": { "label":"CONFIGURED CALIBRATION / SIMULATION MAPPING",
                 "physical_min_cm":0.0, "physical_max_cm":30.0,
                 "mithi_min_cm":118.0, "mithi_max_cm":220.0, "rainfall_factor":0.8,
                 "configured_node_code": null,
                 "note":"Configurable calibration for mapping physical sensor input into the Mithi simulation; not a physical hydraulic transfer." } } }
```

### `GET /api/flood/nodes/{node_code}`

Adds `calibration_offset_cm`, `baseline_level_cm`, `flow_rate`, `soil_moisture`, `battery_voltage`,
`received_at`, and **all** horizons:

```jsonc
{ "node_code":"…", "latest_reading":{ … },
  "risk_by_horizon": { "0": { "risk_level":"low", "risk_score":0.05,
                              "primary_driver":"Water Level", "predicted_level_cm":14.5,
                              "components":{ "level":0.0, "rise_rate":0.0, "rainfall":0.33, "soil":0.33 },
                              "assessed_at":"…" } },
  "forecasts": { "15": { "predicted_level_cm":169.0, "lower_bound_cm":159.0,
                         "upper_bound_cm":179.0, "target_time":"…",
                         "status":"ok", "model_version": 3 } } }
```

### `GET /api/flood/current-summary`

```json
{ "active_sensors": 1, "total_sensors": 1, "offline_stale_sensors": 0,
  "highest_risk": "moderate", "highest_risk_node": "VAD-ESP32-01",
  "current_rainfall_mm_hr": 6.2, "timestamp": "…" }
```

`offline_stale_sensors` counts nodes whose latest reading is older than `5 × 3 = 15` minutes.

### `GET /api/flood/forecasts/{node_code}`

Newest row per horizon, `scenario_id IS NULL`.

```json
{ "node_code":"VAD-ESP32-01",
  "forecasts":[ { "horizon_minutes":15, "predicted_level_cm":169.0,
                  "lower_bound_cm":159.0, "upper_bound_cm":179.0,
                  "target_time":"…", "made_at":"…", "status":"ok" } ] }
```

Returns `[]` for a physical node with no recent IoT reading.

### `GET /api/flood/layers/{time_step_minutes}`

Query: `layer_type` = `depth` (default) | `risk` | `boundary` (regex-validated) · `scenario_id`.

```json
{ "time_step_minutes":30, "layer_type":"depth",
  "geojson": { "type":"FeatureCollection",
               "features":[ { "type":"Feature",
                              "geometry":{ "type":"MultiPolygon", "coordinates":"[[…]]" },
                              "properties":{ "risk_level":"moderate",
                                              "max_depth_m":0.42, "cell_count":18,
                                              "type":"depth" } } ] },
  "max_depth_m":0.94, "flooded_area_m2":128400.0,
  "input_source":"iot-demo", "grid_resolution_m":30.92,
  "generated_at":"…", "scenario_id":null,
  "impact": { …same shape as one impact-summary step… },
  "note":"PREDICTED FLOOD EXTENT — model estimate, not an official flood zone." }
```

**404** `Flood layer not found`.

> **GeoJSON contract:** every entry in `features` carries `"type": "Feature"`. This is enforced by
> `flood_simulation.py:566` and guarded by 22 regression tests — omitting it makes Leaflet reject the
> whole layer with `Invalid GeoJSON object.`

### `GET /api/flood/layers`

Query: `scenario_id`. Lists available layers (metadata only, no GeoJSON), ordered by
`(time_step_minutes, layer_type)`.

```json
{ "layers":[ { "id":42, "time_step_minutes":0, "layer_type":"depth",
               "max_depth_m":0.31, "flooded_area_m2":42000.0,
               "input_source":"iot-demo", "grid_resolution_m":30.92,
               "generated_at":"…", "scenario_id":null } ] }
```

### `GET /api/flood/map-layers`

Query: `layer` = `roads` | `buildings` | `waterways` | `sensors` (regex) · `bbox` = `"south,west,north,east"`.

| `layer` | Source | Simplification | Notes |
|---|---|---|---|
| `roads` | PostGIS `roads` | `ST_Simplify(0.00006)` | `height_m: null`, `height_is_estimated: true`, `type: road_type` |
| `buildings` | PostGIS `buildings` | `ST_Simplify(0.00004)` | `height_m`, `height_is_estimated`, `type: building_type` |
| `waterways` | `data/osm/waterways.geojson` | none | `is_river` flag preserved |
| `sensors` | `sensor_nodes` (physical + active) | none | full point geometries via `ST_AsGeoJSON` |

```json
{ "type":"FeatureCollection",
  "features":[ { "type":"Feature", "id":1024,
                 "properties":{ "id":1024, "name":"kalina university",
                                "height_m":12.0, "height_is_estimated":true,
                                "type":"university" },
                 "geometry":{ "type":"Polygon", "coordinates":"[[…]]" } } ],
  "count":4521, "layer":"buildings",
  "note":"OpenStreetMap contributors (ODbL). Heights may be estimated." }
```

**400** `bbox must be south,west,north,east` on a malformed value.

> `ST_Simplify` tolerance is in **degrees**, so 0.00006° ≈ 6.6 m E–W at 19 °N and 0.00004° ≈ 4.4 m.

### `POST /api/flood/simulate`

Query: `time_step_minutes` (≥0) · `scenario_id`.

Manually runs the simulation and **persists** the layers. Input levels come from the forecasts at
that step, falling back to the latest telemetry per node, then to `baseline_level_cm`.

```json
{ "time_step_minutes":30, "max_depth_m":0.94, "flooded_area_m2":128400.0,
  "layers": { "depth":{…}, "risk":{…}, "boundary":{…} },
  "impacts": { "buildings":[ { "id":1024, "osm_id":49014347, "name":"kalina university",
                               "feature_type":"building", "depth_m":0.31, "risk_level":"moderate",
                               "building_type":"university" } ],
               "roads":[ { "id":77, "osm_id":123, "name":"…", "feature_type":"road",
                           "depth_m":0.12, "risk_level":"low", "road_type":"residential" } ],
               "affected_building_count":37, "affected_road_count":9,
               "depth_range_m":[0.10,0.94] } }
```

**400** `No forecast or telemetry data available for simulation`.

### `GET /api/flood/risk-colors`

```json
{ "LOW":"risk-low", "MODERATE":"risk-moderate", "HIGH":"risk-high", "SEVERE":"risk-severe" }
```

These are CSS class names, not colours.

---

## Scenarios — `/api/scenarios`

### `GET /api/scenarios/presets`

```json
{ "presets": { "normal_monsoon": { "name":"Normal Monsoon Day","rainfall_mm_hr":25.0,
                                   "initial_level_cm":100.0,"drainage_capacity_pct":90.0,
                                   "duration_minutes":60 },
               "heavy_rain":    { "name":"Heavy Rain","rainfall_mm_hr":50.0,
                                   "initial_level_cm":150.0,"drainage_capacity_pct":70.0,
                                   "duration_minutes":60 },
               "extreme_rain":  { "name":"Extreme Rainfall with Poor Drainage",
                                   "rainfall_mm_hr":85.0,"initial_level_cm":165.0,
                                   "drainage_capacity_pct":35.0,"duration_minutes":60 } },
  "assumptions": { "runoff_coefficient":0.7, "level_rise_per_mm_excess_rain_cm":0.84,
                   "max_level_cm":300.0, "description":"Simplified what-if assumptions (not a hydraulic model):\n1. …" } }
```

### `POST /api/scenarios/run`

All parameters are **query parameters**, not a JSON body.

| Param | Type | Default |
|---|---|---|
| `name` | str? | — |
| `preset` | str? | — (overrides all of the below when it matches) |
| `rainfall_mm_hr` | float | 50.0 |
| `initial_level_cm` | float | 150.0 |
| `drainage_capacity_pct` | float | 70.0 |
| `duration_minutes` | int | 60 |

```json
{ "scenario_id": 7, "status": "running",
  "message": "Scenario started. Poll for completion or subscribe to WebSocket." }
```

**400** `Unknown preset: <x>`. Execution is a background task; poll `GET /{id}` or listen for
`flood.scenario.updated`.

### `GET /api/scenarios/{id}`

```json
{ "id":7, "name":"Heavy rain", "preset":"heavy_rain",
  "rainfall_mm_hr":50.0, "initial_level_cm":150.0,
  "drainage_capacity_pct":70.0, "duration_minutes":60,
  "assumptions": { "runoff_coefficient":0.7, … },
  "status":"completed",                 // running | completed | failed
  "error_message": null, "created_at":"…", "completed_at":"…",
  "forecasts":[ { "node_id":1, "horizon_minutes":60, "predicted_level_cm":158.8,
                  "lower_bound_cm":148.8, "upper_bound_cm":168.8,
                  "target_time":"…", "status":"ok" } ],
  "flood_layers":[ { "time_step_minutes":60, "layer_type":"depth",
                     "max_depth_m":1.12, "flooded_area_m2":265000.0,
                     "geojson":{…} } ] }
```

**404** `Scenario not found`.

### `GET /api/scenarios/{id}/compare`

```json
{ "node_comparisons": [ { "node_id":1, "peak_level_baseline_cm":169.0,
                          "peak_level_scenario_cm":158.8,
                          "level_difference_cm": -10.2 } ],
  "peak_level_difference_cm": -10.2,
  "average_level_difference_cm": -10.2,
  "summary": "Under the scenario, peak water levels rise by up to -10 cm (average -10 cm across nodes).",
  "assumptions": { … } }
```

> ⚠️ **Does not return** `baseline_peak_level_cm`, `baseline_mean_level_cm`,
> `scenario_peak_level_cm`, `scenario_mean_level_cm`, `scenario_flooded_area_m2`, additional roads,
> additional buildings, or highest-risk-reached — although `Frontend/app/scenarios/page.tsx:302-347`
> renders tiles for several of those keys.

### `GET /api/scenarios`

Query: `limit` (1–100, default 20).

```json
{ "scenarios":[ { "id":7, "name":"Heavy rain", "preset":"heavy_rain",
                  "rainfall_mm_hr":50.0, "initial_level_cm":150.0,
                  "drainage_capacity_pct":70.0, "duration_minutes":60,
                  "status":"completed", "created_at":"…", "completed_at":"…" } ] }
```

### `DELETE /api/scenarios/{id}`

**200** `{ "message":"Scenario deleted" }`. Deletes only the `scenarios` row; its `forecasts`,
`risk_assessments` and `flood_layers` are **orphaned** (no `ondelete` on those FKs). **404** if absent.

---

## Alerts — `/api/alerts`

### `GET /api/alerts`

| Param | Type | Default |
|---|---|---|
| `scenario_id` | int? | omit → `scenario_id IS NULL` |
| `node_code` | str? | case-insensitive, joins `SensorNode` |
| `severity` | enum regex | `information|warning|high|critical` |
| `alert_type` | str? | free |
| `active_only` | bool | `true` |
| `hours` | int | `24` |
| `limit` | 1–500 | `100` |

```json
{ "alerts":[ { "id":91, "node_code":"VAD-ESP32-01", "scenario_id":null,
               "alert_type":"threshold", "severity":"high",
               "message":"VAD-ESP32-01 water level 152 cm exceeds warning threshold (150 cm)",
               "trigger_data": { "level_cm":152.0, "threshold_cm":150.0 },
               "is_scenario_alert":false, "is_active":true, "acknowledged":false,
               "acknowledged_at":null, "acknowledged_by":null,
               "created_at":"…", "cleared_at":null } ] }
```

> ⚠️ `trigger_data` uses the keys **`level_cm`** and **`threshold_cm`**, but `AlertStrip.tsx:34-44`
> and `alerts/page.tsx:285-286` look for `current_value`/`value` and `threshold`/`threshold_cm`.
> `threshold_cm` matches; `level_cm` does not, so the strip never shows the measured value.

### `GET /api/alerts/active`

```json
{ "alerts":[ { "id":91, "node_code":"VAD-ESP32-01", "scenario_id":null,
               "alert_type":"threshold", "severity":"high", "message":"…",
               "trigger_data":{…}, "is_scenario_alert":false,
               "acknowledged":false, "created_at":"…" } ],
  "counts": { "critical":0, "high":1, "warning":0, "information":0 } }
```

**The response deliberately omits `is_active`.** Both the Dashboard and the Alerts page document
that filtering on it would empty the list.

### `POST /api/alerts/{alert_id}/acknowledge`

Query: `acknowledged_by` (default `"operator"`).

**200** `{ "message":"Alert acknowledged", "alert_id":91 }` · **404** `Alert not found`.
Sets `acknowledged`, `acknowledged_at`, `acknowledged_by` and publishes
`flood.alert.updated` with `action="acknowledged"`.

### `POST /api/alerts/acknowledge-all`

Query: `acknowledged_by`, `scenario_id`. **200** `{ "message":"Acknowledged N alerts" }`.

### `GET /api/alerts/history`

Query: `hours` (24) · `limit` (1–500, 100) · `scenario_id`. `node_code` is accepted but **ignored**.

Returns full alert objects **including `is_active`** (unlike `/active`).

### `GET /api/alerts/network-health`

```json
{ "nodes":[ { "node_code":"VAD-ESP32-01", "name":"Vadodara Demo Sensor",
              "node_type":"physical", "role":null, "status":"online",
              "last_reading":"2026-10-06T09:15:00+05:30",
              "minutes_since_last_reading":3.1, "battery_pct":83.3 } ],
  "summary": { "total":1, "online":1, "stale":0, "offline":0, "no_data":0 } }
```

`status` = `offline` (>15 min) · `stale` (>5 min) · `online` · `no_data` (never reported).

---

## Demo / replay — `/api/demo`

| Endpoint | Params | Response |
|---|---|---|
| `GET /status` | — | `{ "mode":"live", "replay_speed":1.0, "replay_recording":null, "replay_active":false }` |
| `POST /mode` | `mode` regex `^(live\|replay\|scenario)$` | `{ "mode":"live", "message":"Mode set to live" }` |
| `POST /reset` | — | `{ "message":"Demo reset completed" }` — deletes `flood_impacts`, `flood_layers`, `alerts`, `risk_assessments`, `forecasts`, `telemetry_readings`, `scenarios`; flushes `floodguard:*`, `forecast:*`, `weather:*` from Redis; keeps nodes, study area, roads, buildings, model registry |
| `POST /seed` | — | `{ "message":"Demo data seeded" }` — **no-op**, only logs `demo_seed_skipped` |
| `GET /recordings` | — | `{ "recordings":[ { "id":1, "name":"…", "description":"…", "origin":"physical_recording", "duration_seconds":300, "created_at":"…" } ] }` |
| `POST /recordings` | `name` (query, required) · `duration_seconds` (300) | `{ "id":1, "name":"…", "duration_seconds":300, "message":"Recording created" }` |
| `POST /replay/{id}/start` | `speed` 0.1–10.0 (1.0) | `{ "message":"Replay started at 1.0x speed" }` · **404** if absent |
| `POST /replay/stop` | — | `{ "message":"Replay stopped" }` |

---

## About / transparency — `/api/about`

### `GET /api/about/data-sources`

```json
{ "data_sources":[ { "id":1, "name":"SRTM / Copernicus GLO-30 Terrain", "category":"terrain",
                     "provider":"OpenTopography (SRTMGL3) / Copernicus GLO-30 via AWS Open Data",
                     "resolution":"30 m",
                     "licence_note":"SRTM is NASA/USGS public domain; Copernicus GLO-30 is free for all uses.",
                     "limitation":"Surface elevation at 30 m. Cannot resolve bunds, kerbs or channel roughness; inundation depths inherit this error.",
                     "last_updated":null } ] }
```

5 seeded rows: `terrain`, `historical_reference`, `terrain_osm`, `forecast`, `live_hardware`.

### `GET /api/about/model-info`

```json
{ "models": {
    "15min": { "model_name":"rf_15_30_60_15min", "version":"rf-v1-202610051958",
               "algorithm":"random_forest",
               "mae":0.23921207869798636, "rmse":0.6438925451490184,
               "baseline_mae":0.34672900315200633, "baseline_rmse":1.0122373318056461,
               "trained_at":"2026-10-06T03:58:37+00:00",
               "training_data_description":"Rainfall forcing: real IMD gridded daily observations. Training levels: SIMULATED single-reservoir storage-routing series at 5-minute steps. Chronological 70/15/15 split, no shuffling. Prototype grade." },
    "30min": { … }, "60min": { … } },
  "persistence_baseline": { "description":"Future water level equals current level (no change)",
                            "note":"All ML models are evaluated against this baseline" } }
```

Falls back to the `.joblib` bundle's own metadata with `registered_in_db: false` if no
`model_registry` row exists, or to `{"model_name":"persistence","version":"baseline",
"registered_in_db":false}` if there is no artefact either.

### `GET /api/about/assumptions`

```jsonc
{ "risk_scoring": { "weights": { "water_level":0.35, "rise_rate":0.30,
                                 "rainfall":0.20, "soil_saturation":0.15 },
                    "thresholds_cm": { "low":50.0, "moderate":100.0, "high":150.0, "severe":200.0 },
                    "note":"Weights and thresholds are prototype assumptions requiring local calibration. Not scientifically validated." },
  "flood_simulation": { "method":"elevation_based_inundation",
    "description":"Simplified raster inundation model. Not a hydraulic model. Does not solve momentum or mass conservation. No flow velocity calculation.",
    "grid_resolution_m":10.0, "connectivity_buffer_m":50, "max_depth_m":5.0,
    "depth_risk_thresholds_m": { "low":0.1, "moderate":0.3, "high":0.6, "severe":1.0 },
    "limitations": [ "Accuracy limited by elevation data resolution (~30m SRTM)",
                     "Cannot represent individual streets, drains, or building thresholds",
                     "Does not model drainage network hydraulics",
                     "Assumes instantaneous water surface equilibration" ] },
  "forecast": { "horizons_minutes":[15,30,60], "sampling_interval_minutes":5,
                "feature_window_readings":12, "lags_used":[1,2,3,6],
                "model":"Random Forest Regressor (primary)",
                "note":"A few minutes of prototype data cannot establish real-world accuracy. Use for preparedness and exploration, not life-safety decisions." },
  "data_priority": { "current_rainfall":["ESP32 (live)","Open-Meteo (forecast)","IMD (historical)"],
                     "current_water_level":["ESP32 (live)"],
                     "terrain":["Local SRTM GeoTIFF","Cesium Ion (online fallback)"],
                     "buildings":["OpenStreetMap (Overpass)"] },
  "source_labels": { "LIVE HARDWARE":"ESP32 physical sensor",
                     "SIMULATED":"Virtual sensor network with documented propagation rules",
                     "HISTORICAL / REFERENCE":"IMD gridded rainfall, SRTM terrain",
                     "FORECAST":"Open-Meteo weather forecast",
                     "TERRAIN / OSM":"SRTM elevation, OpenStreetMap features",
                     "PREDICTED":"FLOODGUARD model output" } }
```

> ⚠️ Two staleness issues: `grid_resolution_m` reports the **configured** `10.0`, but the real DEM
> resolution is **30.92 m** and that is what the simulation actually uses. And `source_labels` still
> says `LIVE HARDWARE` while the frontend badge says `REAL IoT INPUT`, plus it claims a "Virtual sensor
> network" which is not running. And `data_priority.terrain` cites "Cesium Ion" which is unused.

### `GET /api/about/study-area`

```json
{ "name":"Mithi River Corridor - BKC to Kurla",
  "bounds": { "south":19.05, "west":72.85, "north":19.09, "east":72.9 },
  "representative_point": { "lat":19.0713, "lng":72.883 },
  "grid_resolution_m":10.0,
  "description":"1-2 km section of Mithi River around BKC and Kurla region, Mumbai" }
```

> The description says 1–2 km; the bounding box is ~4.4 × 5.2 km.

### `GET /api/about/honesty-note`

```json
{ "note": "Prototype readings and scenario outputs are clearly labeled. A few minutes of data cannot establish real-world model accuracy. Use this interface for preparedness and exploration, not as a life-safety system.",
  "disclaimers": [ "Flood simulation is a simplified elevation-based inundation estimate, not a hydraulic model.",
                   "Elevation data resolution (~30m) limits street-level accuracy.",
                   "Risk weights and thresholds are starting assumptions requiring local calibration.",
                   "Satellite rainfall data (IMD, GPM) is too coarse (~10km) for street-level rainfall; local rain gauge is primary live source.",
                   "No public live Mithi River water-level API is assumed to exist.",
                   "Building heights from OSM are often missing; estimated heights are clearly labeled.",
                   "Generated flood extents are PREDICTED, not OFFICIAL FLOOD ZONES." ] }
```

---

## Quick-start smoke test

```bash
# 1. is everything up?
curl -s http://127.0.0.1:8000/health

# 2. what data do we have?
curl -s http://127.0.0.1:8000/api/about/data-sources
curl -s http://127.0.0.1:8000/api/about/study-area

# 3. register the physical device
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/iot/devices/register \
  -H "Authorization: Bearer $IOT_REGISTRATION_TOKEN" -H "Content-Type: application/json" \
  -d '{"node_code":"VAD-ESP32-01","name":"Vadodara Demo Sensor","node_type":"physical",
       "lat":22.3072,"lng":73.1812,"reference_elevation_m":1.2,
       "baseline_level_cm":0,"warning_threshold_cm":25}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

# 4. send 8 readings at 5-minute spacing so forecasts unlock
for i in $(seq 0 7); do
  LVL=$(python -c "print(round(5 + $i*1.8, 1))")
  TS=$(python -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)-timedelta(minutes=(7-$i)*5)).isoformat())")
  curl -s -X POST http://127.0.0.1:8000/api/iot/telemetry \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "{\"node_code\":\"VAD-ESP32-01\",\"water_level_cm\":$LVL,\"rainfall_mm_hr\":5.5,
         \"timestamp\":\"$TS\",\"event_id\":\"boot-$i\"}" > /dev/null
done

# 5. read the derived state
curl -s http://127.0.0.1:8000/api/flood/nodes | python -m json.tool
curl -s http://127.0.0.1:8000/api/flood/impact-summary | python -m json.tool
curl -s http://127.0.0.1:8000/api/flood/layers/0 | python -m json.tool
curl -s http://127.0.0.1:8000/api/flood/map-layers?layer=roads | python -c "import sys,json;print(json.load(sys.stdin)['count'])"
```

Step 4's spacing matters: `MIN_READINGS_FOR_FORECAST = 8` and the feature window is 12 samples at an
assumed 5-minute cadence, so 8 readings sent back-to-back would produce a 0-second spacing that the
feature design does not model.