# 04 — Data Pipelines: five, step by step

---

## Pipeline 1 — Offline data preparation

**Script:** `backend/scripts/preprocess_data.py` (431 lines)
**Invocation:** `python -m scripts.preprocess_data [--skip-dem] [--skip-imd] [--skip-osm]` (run from `backend/`)

The whole point, stated in the docstring: "Turns the heavyweight raw sources into small,
project-scoped artefacts that the API and frontend can serve directly. Run once."

### 1a. DEM validation (`validate_dem`, line 66)

Not a processing step — a **reporting** step. It picks the smallest non-underscore `.tif` in
`data/terrain/` (i.e. the clip, not the 1° source tile) and prints:

```
DEM: dem_mithi.tif
  CRS      EPSG:4326
  size     108 x 90
  bounds   BoundingBox(...)
  res      0.00027778 deg (~31 m)
  nodata   -9999.0
  elev     min / max / mean
  land     min / max / mean   (valid > -5 m)
  pixels   9708 valid / 9720 total
  note     N coastal/sea cells (<= -5 m) are nodata-flagged
```

The design intent is in the function name: **report the raster's real properties instead of assuming
them.**

### 1b. IMD rainfall (`preprocess_imd`, line 119)

```
data/IMD/*.nc
   │  _resolve(ds,"latitude") / "lat"  ← case-insensitive; IMD writes LATITUDE, netCDF4 keys lowercase
   │  _resolve(ds,"longitude") / "lon"
   │  _resolve(ds,"time") / "valid_time"
   ▼
find rainfall variable:
   first pass — first var whose lowercased name contains "rain" | "precip" | "rf"   → RAINFALL
   fallback  — largest 3-D variable that is not a coordinate
   ▼
_extract_cell_series(data, lats, lons)
   i = argmin(|lats - 19.0713|);  j = argmin(|lons - 72.8830|)
   series = data[:, i, j]
   each value → None if isnan or > 1e5 or < 0, else round(v, 2)
   also returns `cell_used` = {lat, lon, lat_index, lon_index, offset_from_study_point_deg}
   ▼
dates = num2date(times[:], times.units, only_use_cftime_datetimes=False)
   ▼
data/rainfall/imd/processed/mumbai_rainfall.json
   {source, source_label, provider, resolution, frequency, region,
    study_bbox, representative_point, limitation, years:[{file, variable, grid, cell_used, dates, daily_mm}]}
```

Why the cell is *disclosed* (comment at `preprocess_data.py:228-231`): "a 0.25 deg cell is ~25 km
across, so which one was picked materially changes the number and must not be implicit."

Why variable names are discovered at runtime (line 121-123): "the IMD files have changed
names/format between releases, so nothing is hardcoded."

Measured result: 3 files, 1,096 daily values, all from cell (19.0, 73.0).

### 1c. OSM layers (`preprocess_osm`, line 313)

```
data/overpass turbo.geojson   (9,099 features)
   │
   ├─ _clip_bbox(geom)  → keep only features whose FIRST coordinate is inside
   │                       19.05–19.09 lat / 72.85–72.90 lon
   │
   ├─ if props["building"]                → buildings.geojson
   │     estimate_building_height(props) → (height_m, height_is_estimated, method)
   │        1. props["height"]            → parse, keep if 1.0 ≤ v ≤ 400.0 → "osm_height_tag", estimated=False
   │        2. props["building:levels"]   → levels × 3.0 m, keep if 1 ≤ levels ≤ 120
   │                                       → "building_levels_x3m", estimated=False
   │        3. TYPICAL_LEVELS[building]   → levels × 3.0 m → "estimated_<type>_levels", estimated=True
   │           fallback DEFAULT_LEVELS = 3
   │
   ├─ elif props["highway"]               → roads.geojson      (osm_id, name, road_type)
   │
   └─ elif props["waterway"] or props["natural"]=="water"
                                         → waterways.geojson  (+ is_river = (waterway == "river"))
   ▼
data/osm/{buildings,roads,waterways}.geojson   each with
   type, source ("OpenStreetMap contributors (ODbL 1.0)"), source_label ("TERRAIN / OSM"),
   bbox [72.85, 19.05, 72.90, 19.09], features
buildings.geojson additionally carries height_estimate_method (the long honesty string)
```

`_clip_bbox` is a cheap first-coordinate test, not a true intersection — documented as such at line
255: "True if a geometry's first coordinate falls inside the study bbox."

Measured result: 4,521 buildings, 4,376 roads, 42 waterways (from 9,099 features).

---

## Pipeline 2 — Geo load into PostGIS

**Script:** `backend/scripts/load_osm_layers.py` (300 lines)
**Invocation:** `python -m scripts.load_osm_layers [--force]`

```
data/osm/buildings.geojson
   │  shape(geometry)
   │  _valid_geometry  → if not valid: buffer(0); if still invalid: DROP
   │  _clipped        → intersect with box(72.85, 19.05, 72.90, 19.09); None ⇒ DROP
   │  _explode(..., ("Polygon",))  → MultiPolygon → list of Polygons
   │  DemSampler.sample(centroid.x, centroid.y)  → ground_elevation_m
   ▼
PostGIS `buildings`  (BigInteger PK, osm_id, name, building_type, height_m,
                      height_is_estimated, ground_elevation_m, footprint POLYGON 4326,
                      GiST index ix_buildings_geom)

data/osm/roads.geojson
   │  same repair/clip, _explode(..., ("LineString",))
   ▼
PostGIS `roads`      (BigInteger PK, osm_id, name, road_type, geometry LINESTRING 4326,
                      min_elevation_m — always NULL, GiST index ix_roads_geom)
```

Details that matter:

- **`DemSampler`** (line 102) opens `data/terrain/dem_mithi.tif` with rasterio, reads band 1 as
  float64, and returns `round(value, 3)` — or `None` for out-of-raster, nodata, or non-finite. The
  comment: "outside the raster it stays NULL and is never invented."
- **`min_elevation_m` on `roads` is never populated** — the column exists in the schema but no code
  writes it.
- **Idempotency**: without `--force`, existing `osm_id`s are skipped and counted. With `--force`,
  `DELETE FROM buildings` / `DELETE FROM roads` first.
- `_explode` exists because clipping on the study-area boundary turns a LineString into a
  MultiLineString (or a GeometryCollection), and the DB columns are single-part (comment at 83-89).
- Prints a `[warn]` if either table ends up empty, because then "infrastructure impact will report
  zero".
- Flushes every 500 rows.

---

## Pipeline 3 — ML training

**Script:** `backend/scripts/train_forecast_models.py` (405 lines)
**Invocation:** `python -m scripts.train_forecast_models`

### Stage 1 — real rainfall loading

```
load_imd_rainfall()
   reads settings.imd_processed_dir / "mumbai_rainfall.json"
   flattens years[*].dates + years[*].daily_mm into {date: mm}
   returns ([mm sorted by date], [dates])
   HARD STOPS if the file is missing:
     "IMD artefact missing. Run: python -m scripts.preprocess_data --skip-dem --skip-osm"
   Measured: "IMD rainfall: 1096 days from 2019-01-01 to 2024-12-31"
```

### Stage 2 — sub-daily disaggregation (SIMULATED shape, EXACT daily totals)

`build_subdaily_rainfall()` (line 93):

```
hours      = arange(288) * 5 / 60                       # 288 five-minute steps per day
diurnal    = 0.35 + exp(-(((hours - 16.0) / 3.2) ** 2))  # late-afternoon convective peak
diurnal    = diurnal / diurnal.sum()                     # normalised
per day:   shape = diurnal * rng.gamma(shape=1.6, scale=1.0, size=288)
           if shape.sum() is not finite or <= 0:  shape = diurnal  (avoids 0/0 → NaN)
           out[288i : 288(i+1)] = shape / shape.sum() * daily_total
```

So the **daily total is preserved exactly**; only its intraday distribution is invented. The comment
is explicit: "The shape is a modelling assumption, not measured data."

### Stage 3 — reservoir routing (SIMULATED hydrology)

`simulate_storage()` (line 120), 5-minute steps:

```
rain_mm_hr  = rain_5min[i] * 12                      # 5-min depth → mm/hr

soil       += rain_mm_hr / 12.0
runoff_frac = min(1.0, soil / SOIL_CAPACITY_MM)     # SOIL_CAPACITY_MM = 45.0
effective   = rain_mm_hr * runoff_frac
soil        = max(0.0, soil - rain_mm_hr / 12.0 * 0.55)

inflow   = effective * 5/60                        # mm this step
outflow  = (DRAINAGE_MM_HR + STORAGE_K * max(storage,0)**STORAGE_X) * 5/60
           DRAINAGE_MM_HR = 6.0, STORAGE_K = 0.55, STORAGE_X = 0.30
storage  = max(0.0, storage + inflow - outflow)
```

This is the classical nonlinear-reservoir (Modified Puls / storage-indication) outflow law.

### Stage 4 — storage → stage (SIMULATED datum mapping)

`storage_to_levels()` (line 144):

```
p50    = percentile(storage, 50)
p99    = percentile(storage, 99)
span   = max(p99 - p50, 1e-6)
norm   = clip((storage - p50) / span, 0, 1)
swing  = DESIGN_PEAK_CM - BASE_LEVEL_CM            # 245.0 - 120.0 = 125.0
target = BASE_LEVEL_CM + swing * norm ** 0.7        # concave: channel widens with stage

routing lag:  level = 0.72 * level + 0.28 * target
sensor noise: + rng.normal(0.0, 0.18)
clamp:        0.0 … 400.0
NaN/inf  → BASE_LEVEL_CM
```

The comment at 148-151 explains why this is percentile-relative rather than a fixed coefficient: "A
fixed coefficient pins extreme monsoon bursts against the clamp, which teaches the models a saturated
series."

### Stage 5 — the upstream stand-in

```
upstream_rain    = concat([zeros(4), rain_5min[:-4]])       # same rainfall, 4 steps (20 min) earlier
upstream_levels  = storage_to_levels(simulate_storage(upstream_rain), rng)
```

Rationale (line 232-235): "A second, slightly lagged series stands in for the upstream neighbour so
the `upstream_level_cm` feature carries the same meaning it will at inference time, instead of being
a constant zero the model ignores."

### Stage 6 — supervised matrix

`build_dataset()` (line 172), exact feature order:

```
index           current_level  prev_level_1  prev_level_2  prev_level_3  prev_level_6
                current_rainfall  avg_rainfall_1h  rainfall_trend  rise_rate  level_std  upstream_level_cm

for i in range(11, len(levels) - horizon_steps):
    lvl  = levels[i-5 : i+1]        # 6 samples for std
    rain = rainfall[i-11 : i+1]     # 12 samples = exactly 1 hour
    X row = [ levels[i], levels[i-1], levels[i-2], levels[i-3], levels[i-6],
              rainfall[i], mean(rain), rainfall[i]-rainfall[i-1],
              levels[i]-levels[i-1], std(lvl), upstream[i] ]
    y     = levels[i + horizon_steps]        # horizon_steps = horizon // 5
X = nan_to_num(X);  y = nan_to_num(y, nan=BASE_LEVEL_CM)
```

Loop starts at 11 (not 0) because `rainfall[i-11:i+1]` must be a real 12-sample window — "a negative
start index silently yields an empty slice and a NaN mean."

### Stage 7 — chronological split + 3 models

```
i1, i2 = int(n*0.70), int(n*0.85)
X_tr,y_tr = X[:i1],   y[:i1]      # 70 %
X_va,y_va = X[i1:i2], y[i1:i2]    # 15 %
X_te,y_te = X[i2:],   y[i2:]      # 15 %
NO SHUFFLING — enforced by slicing, and stated at line 16: "Nothing is shuffled."

PersistenceModel.predict(X) = np.array([row[0] for row in X])     # future = current
LinearRegression().fit(X_tr, y_tr)
RandomForestRegressor(n_estimators=300, max_depth=14,
                      min_samples_leaf=5, random_state=42, n_jobs=-1).fit(X_tr, y_tr)

mae/rmse = ( mean|e|, sqrt(mean(e²)) )

skill  = (base_mae - rf_mae) / base_mae * 100
use_rf = rf_mae < base_mae
```

Random seed: `np.random.default_rng(20240115)` — "fixed seed -> reproducible artefacts". The comment at
299-301 explains the ship gate: "Ship RF only if it beats persistence on the test split, otherwise
the service falls back to the baseline and the UI says so."

### Stage 8 — artefact + report + DB registration

```
bundle = {model, algorithm, feature_order, horizon_minutes, trained_at,
          metrics{mae, rmse, baseline_mae, baseline_rmse}, level_source}
joblib.dump(bundle, models/flood/rf_{15,30,60}min.joblib)
models/flood/model_metadata.json  ← the full report (see 02-DATA-INVENTORY.md §8a)

register_in_db():
    version = strftime("%Y%m%d%H%M")
    description = "Rainfall forcing: real IMD gridded daily observations.
                   Training levels: SIMULATED single-reservoir storage-routing series at 5-minute
                   steps. Chronological 70/15/15 split, no shuffling. Prototype grade."
    upsert ModelRegistry row per horizon (horizon, model_name, version, algorithm,
    artifact_path, mae, rmse, baseline_mae, baseline_rmse, trained_at, is_active=True)
```

---

## Pipeline 4 — Live telemetry → forecast → inundation → browser

This is the demo-critical path.

```
                    ┌────────────────────────────────────────────────────────────────────┐
 ESP32              │  hardware: POST JSON to /api/iot/telemetry (HTTPS + bearer token)    │
 (or curl)  ────────┤           OR POST http://<host>:8000/api/iot/telemetry             │
                    └────────────────────────────────────────────────────────────────────┘
                                        │
        ┌───────────────────────────────┴───────────────────────────────┐
        │                                                               │
   HTTPS path
 api/iot.py (Pydantic) → services/ingest.py → telemetry.ingest_telemetry
   ├─ json.loads                                          ├─ get_current_device (bearer → SHA-256 lookup)
   ├─ topic_parts[-2] = node_code                         ├─ 403 if body.node_code != device
   ├─ _get_or_create_node                                 ├─ valid==False → RejectedReading + 400
   │    source=="iot" and unknown → REJECT                 ├─ event_id already seen → duplicate
   │    simulated/scenario/replay → auto-create virtual     ├─ optional location update
   ├─ _validate_reading                                    └─ ingest_telemetry_reading(...)
   │    level 0..500, rainfall 0..500,                            │
   │    ISO timestamp, source in enum, valid flag                 │
   └─ on failure → _store_rejected_reading                      │
                     │                                           │
                     └──────────────┬────────────────────────────┘
                                    ▼
                 telemetry.py:68  ingest_telemetry_reading(db, node, ...)
                                    │
                                    ├─ 1. SELECT previous reading (recorded_at < ts)
                                    ├─ 2. rise_rate = calculate_rise_rate(cur, prev, interval_s)
                                    ├─ 3. risk = assess_current_risk(level, rain, rise, soil,
                                    │                                   terrain=node.reference_elevation_m)
                                    ├─ 4. INSERT TelemetryReading (with risk_level/risk_score snapshot)
                                    ├─ 5. INSERT RiskAssessment (horizon_minutes=0,
                                    │        component columns = NORMALISED 0..1 values)
                                    ├─ 6. UPDATE sensor_nodes: is_active=True, last_seen_at,
                                    │        battery_pct = f(battery_voltage) if present
                                    ├─ 7. COMMIT
                                    ├─ 8. publish_realtime_event("flood.sensor.updated")
                                    ├─ 9. publish_realtime_event("flood.risk.updated", full components)
                                    └─ 10. if source == "iot": process_real_demo_input()
                                                              │
                                                              ▼
                             mithi_demo_pipeline.py:18  process_real_demo_input()
                                                              │
   ┌──────────────────────────────────────────────────────────┤
   │  a. find newest physical node with a source="iot" reading
   │     (restricted to settings.demo_input_node_code when set)
   │  b. mapped_level = forecast_service.map_demo_level(reading.water_level_cm)
   │  c. forecasts = forecast_service.generate_mithi_forecasts(
   │           node.id, mapped_level, reading.rainfall_mm_hr, reading.recorded_at,
   │           source="iot-demo")
   │        ├─ needs ≥ MIN_READINGS_FOR_FORECAST (= max(LAGS)+2 = 8) iot readings
   │        ├─ maps EVERY historical reading through map_demo_level (levels list)
   │        ├─ resolves the upstream neighbour on the mapped scale (0.0 if none)
   │        ├─ builds the ordered 11-feature vector via _feature_vector(FEATURE_ORDER)
   │        ├─ for each of 15/30/60: model.predict([vector]) → predicted_level_cm
   │        │     lower_bound = max(0, pred - 10.0);  upper_bound = pred + 10.0
   │        ├─ on failure: status="model_error" (never a silent persistence value)
   │        ├─ INSERT 3 Forecast rows (input_source="iot-demo", status="ok")
   │        ├─ COMMIT
   │        └─ publish "flood.forecast.updated" × 3
   │  d. for time_step in (0, 15, 30, 60):
   │        levels = {node.id: mapped_level if step 0 else forecast(level)}
   │        result = flood_simulation_engine.run_simulation(levels, step, input_source="iot-demo")
   │        flood_simulation_engine._store_flood_layers(result, step, None, "iot-demo")
   │        steps[step] = {flooded_area_m2, max_depth_m}
   │  e. publish "flood.mithi.updated" {input_source, simulation_source, label, steps, updated_at}
   │
   ├──────────────────────────────────────────────────────────┘
   │
   ▼  flood_simulation.py:585  run_simulation()
   │
   ├─ load_terrain()  ──► Redis "floodguard:terrain_grid:real" (24 h TTL)
   │                       └─ miss → rasterio.open(data/terrain/dem_mithi.tif)
   │                            _load_from_geotiff():
   │                              band 1 → float32;  nodata → NaN
   │                              lats/lons grids via rasterio.transform.xy(...).reshape(h, w)
   │                              resolution_m = (res_x_m + res_y_m)/2
   │                              res_x_m = res_x * 111320 * |cos(mean_lat)|   (geographic CRS)
   │                              res_y_m = res_y * 110540
   ├─ for each supplied node:  water_surface_elev_m =
   │        node.reference_elevation_m + level_cm/100 + node.calibration_offset_cm/100
   ├─ _interpolate_water_surface():
   │        project each sensor onto the OSM Mithi River centreline
   │        sort by along-river distance; scipy interp1d(kind='linear',
   │        bounds_error=False, fill_value='extrapolate')
   │        single sensor → constant fill
   │        per grid cell: proj = clip(river.project(cell), d_min, d_max)
   │                       water_surface[i][j] = float(interp(proj))
   ├─ depth = maximum(0, water_surface - elevation);  NaN elevation → 0;  clip to max_depth_m (5.0)
   ├─ _apply_connectivity_mask():
   │        river cells  = cells with distance to centreline < 50 m / 111000
   │        BFS (queue, 4-connected) from those cells,
   │        admitting a neighbour only when water_surface[n] > elevation[n]
   │        masked_depth = where(flooded, depth, 0.0)
   ├─ risk_grid = np.vectorize(_classify_depth_risk)(depth)
   │        severe ≥1.0 m · high ≥0.6 · moderate ≥0.3 · low ≥0.1 · else "none"
   ├─ _vectorize_flood_layers():
   │        BFS same-risk connected components (4-connected)
   │        components smaller than 4 cells are skipped
   │        per cell: half_y = res/2/111000
   │              half_x = res/2/(111000·cos(mean_lat))     ← latitude correction
   │        unary_union of the cell squares; drop if not valid or empty
   │        emit depth / risk / boundary FeatureCollections
   │        (every entry carries "type":"Feature" — the Leaflet contract)
   ├─ flooded_area_m2 = Σ cell_count × resolution_m²      (counts only VECTORISED cells)
   ├─ _compute_impacts():
   │        flood_geom = unary_union of the depth features (buffer(0) if invalid)
   │        PostGIS: SELECT id, osm_id, name, ST_AsGeoJSON(g)
   │                 FROM buildings/roads WHERE ST_Intersects(g, <flood WKT>)
   │        per feature: overlap = geom ∩ flood_geom
   │                      point  = overlap.representative_point()
   │                      depth  = _sample_depth_at(depth_grid, point.x, point.y)
   │                      keep only if depth > 0
   └─ return {layers{depth,risk,boundary}, max_depth_m, flooded_area_m2,
              impacts{buildings, roads, counts, depth_range_m}, grid_resolution_m}
   │
   ▼  _store_flood_layers()
   INSERT 3 FloodLayer rows (layer_type depth/risk/boundary, geojson JSONB,
                             max_depth_m, flooded_area_m2, input_source, grid_resolution_m)
   INSERT FloodImpact rows against the depth layer
   COMMIT
   │
   ▼  Redis channel "floodguard:realtime"
   event_bridge.py:32 _run() → sio.emit(event, payload, room="global_feed")
                                        → sio.emit(event, payload, room="node_<code>")
   │
   ▼  Socket.IO /socket.io  →  browser
   Frontend/lib/socket.ts getSocket()
   useFloodguard.ts listens on 8 event names → refreshAll()
   refreshAll() → Promise.all(nodes, summary, activeAlerts, impactSummary, demoStatus)
   → Redux → every page re-renders
```

### Concurrent, independent paths

| Timer | Interval | Action |
|---|---|---|
| `forecast_service._auto_forecast_loop` | 60 s | `generate_all_forecasts()` — same mapped-level forecast path as (c) above, for every active physical node |
| `alert_engine._run` | 30 s | `evaluate_all()` — 5 rules per node (see `12-REALTIME-AND-ALERTING.md`) |
| `api/iot.py` + `ingest` | request | inbound telemetry over HTTPS |
| Frontend `useFloodguard` poll | 3 s | REST safety net |
| Frontend `useNodeSeries` poll | 3 s | per-node history + forecasts |
| Frontend `FloodMap` polls | 3 s each | study area, waterways, buildings, roads, inundation layer |
| Frontend `Shell` clock | 1 s | IST clock |
| Frontend `useTimelineAutoplay` | 3.5 s | advances NOW → +15 → +30 → +60 → NOW |

---

## Pipeline 5 — What-if scenario

```
POST /api/scenarios/run?rainfall_mm_hr=…&initial_level_cm=…&drainage_capacity_pct=…&duration_minutes=…
   │
   ├─ if preset given and in SCENARIO_PRESETS → preset values OVERRIDE the query params
   ├─ assumptions = {**SCENARIO_ASSUMPTIONS, rainfall_mm_hr, initial_level_cm,
   │                  drainage_capacity_pct, duration_minutes, preset}
   ├─ INSERT Scenario(status="running"); COMMIT
   └─ asyncio.create_task(_run_scenario(id))            ← fire-and-forget background task
        │
        ├─ _generate_scenario_forecasts(scenario):
        │     drainage_factor = 1 - drainage_capacity_pct/100
        │     effective_rain  = rainfall_mm_hr × drainage_factor × 0.70      (runoff coefficient)
        │     rise_per_min    = effective_rain × 0.84 / 60                    (cm per mm of excess rain)
        │     for every active node:
        │       for horizon in (0, 15, 30, 60):
        │         predicted = initial_level_cm + rise_per_min × horizon   (horizon 0 = initial)
        │         predicted = min(predicted, 300.0)
        │         risk = assess_scenario_risk(...)
        │         INSERT Forecast(scenario_id, horizon, ±10 cm bounds, input_source="scenario")
        │         INSERT RiskAssessment(scenario_id, forecast_id, horizon, components,
        │                              risk_level, primary_driver)
        │     COMMIT
        │
        ├─ flood_simulation_engine.run_for_time_steps(forecasts_by_node, scenario_id, "scenario")
        │     for step in (0,15,30,60): run_simulation(...) → _store_flood_layers(..., scenario_id, "scenario")
        │
        ├─ Scenario.status = "completed"; completed_at = now; COMMIT
        ├─ publish "flood.scenario.updated" {status:"completed", data: sim_results}
        └─ on any exception: Scenario.status="failed", error_message=str(exc),
                            publish "flood.scenario.updated" {status:"failed", data:{error}}
```

### Presets (`scenario_service.py:35-57`)

| Key | Name | Rainfall | Initial level | Drainage | Duration |
|---|---|---|---|---|---|
| `normal_monsoon` | Normal Monsoon Day | 25.0 mm/hr | 100.0 cm | 90 % | 60 min |
| `heavy_rain` | Heavy Rain | 50.0 mm/hr | 150.0 cm | 70 % | 60 min |
| `extreme_rain` | Extreme Rainfall with Poor Drainage | 85.0 mm/hr | 165.0 cm | 35 % | 60 min |

### The assumptions block (`scenario_service.py:60-72`, verbatim)

```json
{
  "runoff_coefficient": 0.7,
  "level_rise_per_mm_excess_rain_cm": 0.84,
  "max_level_cm": 300.0,
  "description":
    "Simplified what-if assumptions (not a hydraulic model):\n
     1. Runoff coefficient: 0.7 (70% of rainfall becomes runoff)\n
     2. Excess rainfall = rainfall × (1 - drainage_capacity/100) × runoff_coefficient\n
     3. Water level rise = excess_rainfall × 0.84 cm/mm\n
     4. Level capped at 300 cm\n
     5. Flood extent from elevation-based inundation, not hydraulic routing"
}
```

These are returned verbatim by `GET /api/scenarios/presets` and printed on the
`/scenarios` page panel titled **"SCENARIO ASSUMPTIONS"** (`Frontend/app/scenarios/page.tsx:371-389`).

### Scenario comparison (`scenario_service.py:312-360`)

For every node, `peak_level_baseline_cm`, `peak_level_scenario_cm`,
`level_difference_cm`. Then `peak_level_difference_cm` (max across nodes) and
`average_level_difference_cm` (mean across nodes), plus a templated summary sentence:

```
"Under the scenario, peak water levels rise by up to {max} cm (average {avg} cm across nodes)."
```

**Not implemented, despite being in the PRD and mockups:** additional flooded area, additional roads,
additional buildings, and highest-risk-reached. `System-Architecture.md:198` and the page-6 mockup both
describe them; the code declares the accumulators at lines 317-319 and then never fills them
(`total_additional_flooded_area`, `total_additional_roads`, `total_additional_buildings` are all
dead). The frontend still renders the UI slots for them, which is why
`scenarios/page.tsx:302-340` reads `comparison.baseline_peak_level_cm`,
`comparison.baseline_mean_level_cm`, `comparison.scenario_peak_level_cm`,
`comparison.scenario_mean_level_cm` — **none of which the backend returns** — so those four metric
tiles always render `—`.

---

## Replay pipeline (built, reachable, empty by default)

```
POST /api/demo/recordings?name=…&duration_seconds=300
   → SELECT all TelemetryReading in the last N seconds, ORDER BY recorded_at
   → payload = {"readings":[{node_code, water_level_cm, rainfall_mm_hr, timestamp, source}, …]}
   → INSERT ReplayRecording(origin="physical_recording")

POST /api/demo/replay/{id}/start?speed=1.0
   → mode = "replay"
   → for each reading, in order:
        delay = (t[i] - t[i-1]).total_seconds() / speed ;  await sleep(delay)
        reading["source"]    = "replay"
        reading["data_mode"] = "replay"
        ingest.ingest_payload(reading)                           ← same pipeline as live HTTPS
   → on completion: mode = "live"
```

Because there are no seeded recordings and `POST /api/demo/seed` is a no-op, replay only works after
an operator records a real live session. The `demo_controller` imports the ingestion singleton via
`import_module` + duck-typing on `_process_telemetry` (`demo_controller.py:32-42`) — a deliberate
workaround for a circular import, documented only by the code shape.