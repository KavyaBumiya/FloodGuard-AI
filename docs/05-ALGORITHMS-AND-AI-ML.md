# 05 — Algorithms and AI/ML: every formula, with real parameters

---

## 1. Is there any AI/ML in this project?

**Yes — exactly one model family: three scikit-learn `RandomForestRegressor` regressors**, one per
forecast horizon (15, 30, 60 minutes), plus two baselines used for comparison
(`PersistenceModel` and `LinearRegression`).

**There is no LLM anywhere.** No OpenAI/Anthropic/LangChain call, no prompt, no API key. Search the
repo: the only place a "plain-language summary" appears is a hard-coded template string
(`Frontend/app/prediction/page.tsx:292-307`).

**Everything else is deterministic arithmetic:**

| Component | Class of algorithm |
|---|---|
| Flood-risk scoring | weighted rule-based score with a step normaliser |
| Flood extent | elevation-threshold raster inundation + breadth-first connectivity mask |
| Water-surface field | 1-D linear interpolation along a river centreline |
| Scenario projection | closed-form linear accumulation |
| Virtual-node propagation | delay + attenuation rule (present in code, not running) |
| Training target series | nonlinear-reservoir (storage-routing) hydrology |
| 3D water surface | Gerstner-wave GLSL vertex shader |

---

## 2. Flood-risk scoring — `backend/app/services/flood_risk.py` (502 lines)

This is **not** machine learning. It is a transparent weighted sum, chosen deliberately so every
number can be explained to a disaster-management officer.

### 2.1 Inputs and normaliser

Four inputs, each normalised to `{0.0, 0.33, 0.67, 1.0}` by `_normalize_linear()` (lines 185-217):

```python
def _normalize_linear(value, low, moderate, high, severe, *, inverse=False):
    # forward direction
    if value >= severe:   return "SEVERE",   1.0
    if value >= high:     return "HIGH",     0.67
    if value >= moderate: return "MODERATE", 0.33
    if value >= low:      return "LOW",      0.0
    return "LOW", 0.0
```

Note the banded values are **hard-coded constants**, not linear interpolation. So each component
contributes one of exactly four discrete amounts. This is what makes the score interpretable and also
what makes it coarse.

### 2.2 All thresholds (loaded from `config/risk_thresholds.json`)

| Input | low | moderate | high | severe | weight |
|---|---|---|---|---|---|
| `water_level_cm` | 50 | 100 | 150 | 200 | **0.35** |
| `rise_rate_cm_per_interval` (per 5 min) | 1 | 5 | 15 | 30 | **0.30** |
| `rainfall_mm_hr` | 5 | 20 | 50 | 80 | **0.20** |
| `soil_saturation` (0–1) | 0.30 | 0.50 | 0.70 | 0.85 | **0.15** |
| `forecast_rainfall_mm_hr` | 5 | 15 | 35 | 60 | (0.25, via `assess_forecast_risk`) |

Score bands (`bands` in the config):

| Band | Score threshold |
|---|---|
| LOW | `< 0.25` |
| MODERATE | `≥ 0.25` |
| HIGH | `≥ 0.50` |
| SEVERE | `≥ 0.75` |

Other constants:

```python
ELEVATION_HIGH_RISK_M  = config.terrain_elevation_m + 2.0   # 1.2 + 2.0 = 3.2 m
ELEVATION_MOD_RISK_M   = config.terrain_elevation_m + 0.5   # 1.2 + 0.5 = 1.7 m
DEVICE_STALE_AFTER_MINUTES = 5.0          # also the frontend's hasRecentLiveIoTReading window
DEFAULT_SOIL_SATURATION   = 0.4           # used when no soil sensor
DEFAULT_SOIL_AVAILABLE    = False
RISK_MODEL_NAME    = "rule_based_v1"
RISK_MODEL_VERSION = "rf_15min"            # ← a copy/paste artefact; it is not an RF version
```

### 2.3 The score

```python
total_score = sum(c.contribution for c in components)   # Σ normalized_i × weight_i
total_score = min(1.0, total_score)
risk_level  = score_to_level(total_score)
```

Because weights sum to exactly 1.0 and normalised values are ≤ 1.0, the score is already ≤ 1.0; the
`min` is defensive.

**Possible scores.** With 4 components on {0, .33, .67, 1} × weights {.35, .30, .20, .15}, the
maximum is 1.00 and the practical reachable set is coarse. Working examples:

| Level | Rain | Rise | Rainfall | Soil | Components | Score | Band |
|---|---|---|---|---|---|---|---|
| 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | — | 0.00 | LOW |
| idle | 0.00 (50 cm) | 0.00 | 0.00 | 0.00 (default 0.4 → LOW band) | 0 + 0 + 0 + 0 | **0.00** | LOW |
| moderate | 0.33 (120 cm) | 0.00 | 0.00 | 0.00 | .1155 + 0 + 0 + 0 | **0.116** | LOW |
| high level | 0.67 (170 cm) | 0.33 (8 cm/5min) | 0.00 | 0.00 | .2345 + .099 | **0.334** | MODERATE |
| severe | 1.00 (205 cm) | 0.67 (18 cm/5min) | 0.67 (60 mm/hr) | 0.67 (0.75) | .35 + .201 + .134 + .1005 | **0.786** | SEVERE |
| worst case | 1.00 | 1.00 | 1.00 | 1.00 | .35 + .30 + .20 + .15 | **1.00** | SEVERE |

**Practical consequence:** because the score has only ~14 reachable values, the MODERATE band
(0.25–0.50) is entered around 0.334 and HIGH (0.50–0.75) around 0.50–0.67. The bands are usable but
visibly steppy.

### 2.4 Terrain elevation handling

`assess_current_risk` accepts `terrain_elevation_m` but **does not add to the score** — it only
appends a human-readable string to `assumptions` (lines 331-337):

```python
if terrain_elevation_m <= ELEVATION_HIGH_RISK_M:      # ≤ 3.2 m
    assumptions.append(f"Low terrain elevation ({terrain_elevation_m:.1f}m) increases risk")
elif terrain_elevation_m <= ELEVATION_MOD_RISK_M:     # ≤ 1.7 m
    assumptions.append(f"Moderate terrain elevation ({terrain_elevation_m:.1f}m)")
```

Despite the wording "increases risk", the score is unchanged. The comment at line 334 ("bump risk
score slightly") describes an intent that was not implemented.

### 2.5 Rise rate

```python
def calculate_rise_rate(current_level, previous_level, interval_seconds=300.0):
    if interval_seconds <= 0: return 0.0
    intervals = interval_seconds / 300.0
    return max(0.0, (current_level - previous_level) / intervals)
```

Normalised to **cm per 5 minutes**, then clamped to ≥ 0 so a falling level contributes nothing to
the rise-rate component. In `telemetry.py:97-99` the interval comes from the actual timestamps of the
current and previous readings, so irregular ESP32 sampling is handled correctly.

### 2.6 Soil handling

If `soil_available` and `soil_saturation is not None`, the measured value is used. Otherwise
`DEFAULT_SOIL_SATURATION = 0.4` is substituted **and** an assumption string is appended:
`"Soil saturation unavailable — using default 0.4"`.

Note: 0.4 falls between `moderate (0.5)`? No — 0.4 < 0.5, so it maps to the MODERATE band's lower
edge… precisely, `_normalize_linear(0.4, 0.3, 0.5, 0.7, 0.85)` returns `("MODERATE", 0.33)`, so the
default soil contribution is `0.33 × 0.15 = 0.0495` — a small constant floor on every score. (The
PRD proposed renormalising the remaining weights when soil is missing; the code does not renormalise.)

### 2.7 Explainability output

```python
primary = max(components, key=lambda c: c.contribution)
primary_driver = primary.name.replace("_", " ").title()   # "Water Level" | "Rise Rate" |
                                                          # "Rainfall" | "Soil Saturation"
```

Plus one extra assumption line naming the value and band, e.g.
`"Primary driver: Rapid rise at 8.0 cm/interval (HIGH)"`.

Each `RiskComponent` carries: `name`, `value`, `normalized`, `weight`, `contribution`, `band`. These
are emitted in full on the `flood.risk.updated` Socket.IO event (`telemetry.py:184-194`) and rendered
as the "RISK CONTRIBUTORS" bar list on `/prediction` (`prediction/page.tsx:222-242`).

### 2.8 Persistence details

- `telemetry_readings.risk_level` and `risk_assessments.risk_level` store **UPPERCASE**
  (`"LOW"`, `"SEVERE"`). The API lowercases via `normalize_risk()`; the frontend `riskLevel()` maps
  both cases plus `critical → severe` and `warning → moderate`.
- `risk_assessments.*_component` columns store the **normalised** value, not the weighted
  contribution (`telemetry.py:139-142`) — so a component column of `0.67` means "at the high band",
  not "contributed 0.67 to the score".
- The scenario service stores the same way via `_component()` (`scenario_service.py:75-80`).

### 2.9 Forecast risk (`assess_forecast_risk`, line 372) — implemented but never called

```python
fc_contrib     = _normalize_linear(forecast_rainfall_mm_hr, 5, 15, 35, 60)[1] × WEIGHT_FORECAST_RAIN
combined_score = min(1.0, WEIGHT_CURRENT_RISK × current_risk.risk_score + fc_contrib)
```

`WEIGHT_CURRENT_RISK` and `WEIGHT_FORECAST_RAIN` default to 0.75 / 0.25. Note the config key is
`defaults.forecast_weights`, which contains `persistence` / `linear_regression` / `random_forest` —
**neither `current_risk` nor `forecast_rainfall` is present**, so both fall back to the code defaults.
That keys-vs-code mismatch is dead configuration.

`assess_forecast_risk` is imported by `forecast_service.py:31` and never called.

### 2.10 Scenario risk (`assess_scenario_risk`, line 431)

```python
effective_rain = scenario_rainfall_mm_hr × (1 - scenario_drainage_pct/100) × 0.7
rise_rate      = effective_rain × 0.84 / (60/5)       # = effective_rain × 0.84 / 12
                 # cm per 5-minute interval
return assess_current_risk(water_level_cm=predicted_level_cm,
                           rainfall_mm_hr=effective_rain,       # ← note: 60× the rate
                           rise_rate_cm_per_interval=rise_rate)
```

Hard-coded assumption strings embedded in the returned `assumptions` list include
`"Runoff coefficient: 0.7 (assumed)"` and `"Level rise per mm excess rain: 0.84 cm (assumed)"`.

> ⚠️ `effective_rain` is an mm/**hr** quantity (≈ 15 for the heavy-rain preset), but it is passed as
> `rainfall_mm_hr` to the current-risk scorer. That places the rainfall component at SEVERE for any
> preset, so scenario risk saturates early. This is a unit bug, visible in the source.

---

## 3. Water-level forecasting — `backend/scripts/train_forecast_models.py` + `forecast_service.py`

### 3.1 Algorithm

`sklearn.ensemble.RandomForestRegressor` — an ensemble of 300 decorrelated CART regression trees
(variance reduction, bootstrap sampling `max_features='sqrt'` by default).

Hyperparameters (all three horizons identical):

```python
n_estimators       = 300
max_depth          = 14
min_samples_leaf   = 5
random_state       = 42
n_jobs             = -1
criterion          = "squared_error"   (sklearn default, not set explicitly)
bootstrap          = True              (default)
max_features       = "sqrt"            (default → √11 ≈ 3 features per split)
```

### 3.2 Feature vector — order is load-bearing

`FEATURE_ORDER` in the trainer and `_build_features()` in the service must agree exactly, because
`model.predict([list(features.values())])` relies on Python dict insertion order.

| # | Feature | Definition | Units |
|---|---|---|---|
| 0 | `current_level` | latest water level | cm |
| 1 | `prev_level_1` | level − 1 step (5 min) | cm |
| 2 | `prev_level_2` | level − 2 steps (10 min) | cm |
| 3 | `prev_level_3` | level − 3 steps (15 min) | cm |
| 4 | `prev_level_6` | level − 6 steps (30 min) | cm |
| 5 | `current_rainfall` | current rainfall | mm/hr |
| 6 | `avg_rainfall_1h` | mean of the last **12** samples = 1 hour | mm/5min |
| 7 | `rainfall_trend` | `rainfall[i] - rainfall[i-1]` | mm/5min |
| 8 | `rise_rate` | `levels[i] - levels[i-1]` | cm |
| 9 | `level_std` | `np.std` of the last 6 levels | cm |
| 10 | `upstream_level_cm` | latest level of the node's upstream neighbour, else `0.0` | cm |

**Feature 0 must be `current_level`** — `PersistenceModel.predict` returns `x[0]`, and the trainer
docstring says so explicitly (line 15): "the first feature MUST be `current_level` because the
persistence baseline indexes x[0]."

The trainer appends the upstream feature inside `build_dataset` with the comment (line 60-61):
"Appended by forecast_service._build_features' caller from the node's upstream relationship. MUST be
trained on or sklearn rejects the vector."

### 3.3 Training target construction

`targets.append(levels[i + horizon_steps])` where `horizon_steps = horizon_minutes // 5`
→ **3, 6, 12 steps** for the 15/30/60-minute horizons.

### 3.4 Split

Chronological 70 / 15 / 15 by index slicing. No shuffling anywhere. `System-Architecture.md:298` and
the trainer docstring both stress this to prevent future information leaking into training.

### 3.5 Baselines

```python
class PersistenceModel:
    def predict(self, X):
        return np.array([row[0] for row in X])       # future = current level
```

Metrics: MAE = `mean(|e|)`, RMSE = `sqrt(mean(e²))`.

### 3.6 Ship gate

```python
skill  = (base_mae - rf_mae) / base_mae * 100
use_rf = rf_mae < base_mae
bundle["model"]     = rf if use_rf else PersistenceModel()
bundle["algorithm"] = "random_forest" if use_rf else "persistence"
```

All three horizons passed (skill +31.0 %, +30.3 %, +29.8 %), so all three shipped Random Forest.

### 3.7 Inference — `forecast_service.generate_mithi_forecasts()`

```python
FEATURE_WINDOW_SIZE         = 12      # 12 × 5 min = 1 hour of history
LAGS                        = [1, 2, 3, 6]
HORIZONS                    = [15, 30, 60]
SAMPLING_INTERVAL_MINUTES   = 5
MIN_READINGS_FOR_FORECAST   = max(LAGS) + 2 = 8
```

Two distinct prediction paths exist:

**Path A — `generate_mithi_forecasts` (this is what actually runs):**
1. Load the last 12 `source="iot"` readings for the node.
2. If `< 8`, log `insufficient_mapped_iot_history` and return `[]` (no fake numbers).
3. Map **every** historical reading through `map_demo_level`, then append the current mapped level.
4. Build the 10-feature dict from that mapped history.
5. `model.predict([list(features.values())])` per horizon.
6. `predicted_level_cm = max(0.0, round(pred, 1))`
7. **`lower_bound_cm = max(0, pred - 10.0)`, `upper_bound_cm = pred + 10.0`** — a hard-coded ±10 cm.
8. Insert 3 `Forecast` rows with `input_source = "iot-demo"`, `status = "ok"`.

> **The uncertainty band is a constant, not a measurement.** `System-Architecture.md:269` proposed
> using the 10th–90th percentile spread across the forest's trees; the implementation uses a fixed
> ±10 cm instead. Measured tree-spread would have been available: with 300 trees, the per-sample
> standard deviation across tree predictions is computable for free. This is the single most
> impactful accuracy gap in the ML serving path.

> **This path previously never actually ran the Random Forest.** It built a **10**-key dict with no
> `upstream_level_cm`, so `predict()` raised against a model trained with `n_features_in_ = 11`; the
> exception was caught and the value fell back to `current_level_cm`. The cause was not a single typo
> but four layered defects — a missing feature, a wrong value scale (raw centimetres where the model
> expected mapped stage), a duplicated tail sample that zeroed `rise_rate` and `rainfall_trend`, and a
> `PersistenceModel` installed as the runtime fallback. Full detail in
> [`13-TESTING-AND-QUALITY.md`](13-TESTING-AND-QUALITY.md) §5.1.
>
> Both paths now build the vector through one ordered helper, `_feature_vector()`, driven by the
> module-level `FEATURE_ORDER` that mirrors `train_forecast_models.FEATURE_ORDER`. Levels are mapped
> through `map_demo_level()` first, and the upstream neighbour is resolved on that same scale.
> Failures set `status = "model_error"` instead of `"ok"`, and `_verify_model_features()` logs an
> `error` if any loaded model's `n_features_in_` disagrees with `FEATURE_ORDER` at startup.

**Verified** on a rising series — the three horizons now differ, where they previously collapsed to a
single number:

```
+15  187.0      +30  191.2      +60  205.8
```

**Path B — `generate_forecasts_for_node`** remains the architecturally complete version: it uses
`_get_recent_readings` (which includes `simulated`) and is reached through
`generate_forecasts_for_nodes`. It now shares the same corrected vector builder, so the two paths can
no longer disagree about feature order or scale.

### 3.8 No artefact means no forecast, not a fake one

`load_models()` no longer installs `PersistenceModel()` when a `.joblib` is missing or fails to load.
It logs `forecast_model_artifact_missing` / `forecast_model_load_failed` at `error` level, removes the
horizon from `self._models`, and the prediction loops skip it via `if model is None: continue`.

This is a deliberate reversal. `PersistenceModel.predict` returns `x[0]` — the current level — which is
indistinguishable from a trained model that happened to predict no change. Serving it produced
plausible-looking flat +15/+30/+60 numbers with `status = "ok"` and no warning anywhere, which is
exactly the failure mode that hid the feature-vector bug for as long as it did. `PersistenceModel` is
retained solely for **offline** evaluation inside `train_forecast_models.py`.

`get_model_info()` correspondingly returns `available: false` with a `note` explaining that no
forecast is produced, instead of a `persistence` entry that implied one was.

### 3.9 Redis forecast cache (write-only)

`_cache_forecast()` writes `forecast:{node_id}:{horizon}` with a 300 s TTL — but only from Path B,
which never runs. `get_cached_forecast()` exists to read it and is never called. **The forecast cache
is currently entirely dormant.**

### 3.10 Model metadata exposure

`get_model_info(horizon)` (line 516) prefers the DB `model_registry` row; if none exists it falls back
to the artefact bundle (`algorithm`, `trained_at`, `metrics`, `level_source`) and sets
`registered_in_db: False`; if neither, `{"model_name": "persistence", "version": "baseline"}`.

---

## 4. Flood simulation — `backend/app/services/flood_simulation.py` (897 lines)

### 4.1 Inputs → water-surface elevation

```python
water_surface_elev_m = (node.reference_elevation_m or grid["min_elevation"])
                      + node_levels[node.id] / 100.0
                      + node.calibration_offset_cm / 100.0
```

So a sensor-relative cm level plus the node's reference elevation becomes an absolute (m ASL) water
surface. If `reference_elevation_m` is NULL, the **grid minimum elevation** is used — a coarse
substitution that biases the whole surface downward.

### 4.2 Terrain grid

`load_terrain()` (line 91) resolution order:

1. Redis `floodguard:terrain_grid:real` (24 h TTL) → immediate hit after the first run.
2. `rasterio.open(settings.resolve_data_path("data/terrain/dem_mithi.tif"))`.
3. If missing **and** `allow_synthetic_terrain` is false → log `terrain_raster_missing` with the hint
   and return `False`. `run_simulation()` then raises:
   ```
   RuntimeError("Terrain unavailable — flood simulation cannot run.
                 Check TERRAIN_RASTER_PATH or run `python -m scripts.preprocess_data`.")
   ```
   with the reason recorded at `flood_simulation.py:602-603`: "Never run against a missing grid: a
   silent synthetic substitute would be reported as real inundation."
4. If missing **and** `allow_synthetic_terrain` is true → `_create_synthetic_grid()`, log
   `terrain_raster_missing_using_synthetic` with "outputs are SIMULATED, not real inundation", and
   **do not** write to the `real` cache key ("Never let a synthetic grid occupy the real-terrain cache
   key, or it would masquerade as real elevation for the next 24 hours").

**The synthetic grid** (line 218, opt-in only) is:

```
rows = max(50, int( lat_range_deg × 111000 / grid_resolution_m ))
cols = max(50, int( lon_range_deg × 111000 × cos(mean_lat) / grid_resolution_m ))
river_dist = |lon_grid - centre_lon| × m_per_deg_lon
elevation  = 5.0 + 3.0 × tanh(river_dist/200) - 4.0 × exp(-river_dist/100)
elevation += np.random.normal(0, 0.3, shape)
elevation  = max(elevation, 0.5)
```

`grid["resolution_m"]` is derived per-axis for a geographic CRS:

```python
res_x_m = res_x * 111320.0 * |cos(radians(mean_lat))|
res_y_m = res_y * 110540.0
resolution_m = (res_x_m + res_y_m) / 2          # ≈ 30.92 m for the study-area clip
```

### 4.3 River centreline

`_get_river_centerline()` (line 275), cached on the instance:

1. Read `data/osm/waterways.geojson`.
2. Keep features with `properties.is_river == True` → the Mithi River (`osm_id 204352191`, 53 points).
3. If none, take the single longest waterway.
4. Drop the Z ordinate and consecutive duplicate points.
5. If < 2 points, or on any exception → a 3-point straight line at the centre longitude from south to
   north, and log `river_centerline_fallback` with "interpolation axis is approximate - regenerate OSM
   waterways".
6. Record the source string, exposed as `river_centerline_source` (currently never read).

### 4.4 Water-surface interpolation

`_interpolate_water_surface()` (line 341):

```
1. water_surface = full((rows, cols), NaN)
2. project each supplied sensor onto the centreline:
      proj_dist = river_line.project(Point)
      river_pt  = river_line.interpolate(proj_dist)
   → (x, y, level) triples
3. if fewer than 2 projections:
      water_surface.fill(first level)         ← single-sensor case: constant surface
      return
4. dists   = [river_line.project(Point(p)) for p in river_points]
   order   = argsort(dists);  sort dists and values by it
5. interp = scipy.interpolate.interp1d(dists, values, kind='linear',
                                       bounds_error=False, fill_value='extrapolate')
6. for every grid cell (O(rows × cols) Python loop, 9,720 iterations here):
      skip if elevation is NaN
      proj = clip(river_line.project(cell_pt), d_min, d_max)
      water_surface[i][j] = float(interp(proj))
```

Two important properties:

- **"Nearest river point" projection, not distance decay.** Every cell takes the water level of the
  *nearest* point on the river, so the surface is a set of isolines parallel to the river, not a
  distance-falloff field. Physically that means water does not get shallower away from the bank.
- **Clamping to the surveyed span** (line 415) prevents extrapolation past the outermost sensors:
  "Clamping to the surveyed span: extrapolating past the outermost sensors invents a water surface
  that no observation supports."

### 4.5 Depth

```python
depth_grid = np.maximum(0, water_surface - elevation)
depth_grid = np.where(np.isnan(elevation), 0, depth_grid)
depth_grid = np.minimum(depth_grid, self.max_depth_m)      # 5.0 m
```

The `min(…, 5.0)` clamp means the reported `max_depth_m` can never exceed 5.0 m.

### 4.6 Connectivity mask

`_apply_connectivity_mask()` (line 420) — breadth-first flood fill seeded from the river:

```
river_cells = [ (i,j) for every cell where distance_to_centreline < 50 m / 111000 ]
              (i.e. within ~50 m of the river — the buffer is expressed in degrees)
if river_cells is empty: return depth_grid unchanged

flooded = zeros((rows, cols), bool);  queue = list(river_cells);  mark all seeds flooded
while queue:
    i, j = queue.pop(0)
    for (di,dj) in [(-1,0),(1,0),(0,-1),(0,1)]:        # 4-connected
        ni, nj = i+di, j+dj
        if in bounds and not flooded[ni,nj] and not isnan(elevation[ni,nj]):
            if water_surface[ni,nj] > elevation[ni,nj]:
                flooded[ni,nj] = True ;  queue.append((ni,nj))
return np.where(flooded, depth_grid, 0.0)
```

This is the mechanism that keeps disconnected low spots dry (PRD Feature 12, "Connectivity mask").
Cost: O(rows × cols) shapely `Point` + `line.distance()` calls — 9,720 per simulation run.

### 4.7 Depth → risk classification

`_classify_depth_risk(depth)`:

| depth (m) | risk_level |
|---|---|
| ≥ 1.0 | `severe` |
| ≥ 0.6 | `high` |
| ≥ 0.3 | `moderate` |
| ≥ 0.1 | `low` |
| < 0.1 | `none` (cell is skipped entirely — no polygon) |

So anything under 10 cm produces **no** polygon, no impact row, and no area contribution.

### 4.8 Vectorisation

`_vectorize_flood_layers()` (line 484):

```
half_y = resolution_m / 2 / 111000.0
half_x = resolution_m / 2 / (111000.0 × max(0.1, cos(radians(mean_lat))))     ← latitude correction
                                                         (comment at line 499-500)

for each unvisited cell with depth > 0 and risk != "none":
    target_risk = risk_grid[i][j]
    BFS over 4-connected neighbours that are unvisited, non-NaN, depth > 0, same risk
    if len(component) < 4: skip          ← "Minimum for a polygon"
    polys = [box around each cell centre]
    union = unary_union(polys)
    if not union.is_valid or union.is_empty: discard
    feature = {
        "type": "Feature",
        "geometry": mapping(union),
        "properties": {"risk_level", "max_depth_m": max over component,
                       "cell_count": len(component)}
    }
    depth_fc.features.append({**feature, "properties": {..., "type": "depth"}})
    risk_fc.features.append(feature)                      if risk in the 4 risk buckets
    boundary_fc.features.append(feature)                  if risk in ("high","severe")

return depth_fc, risk_fc, boundary_fc
```

The latitude correction matters: without `cos(lat)` the drawn cell width would be ~6 % too large at
19° N. The `type: "Feature"` discriminator is enforced with a long comment (line 561-565) because its
absence previously made Leaflet reject every layer with "Invalid GeoJSON object."

### 4.9 Area computation

```python
vectorized_cells = Σ feature.properties.cell_count   over depth_fc.features
flooded_area_m2   = vectorized_cells × resolution_m²
```

The comment at lines 654-657 explains the choice: "The reported area must match the polygons the map
actually draws, so it is counted from the vectorised extent rather than from every cell with
depth > 0."

For the shipped clip: `resolution_m ≈ 30.92`, so **one cell ≈ 956 m²**, and each polygon has
`cell_count × 956` m².

### 4.10 Impact computation

`_compute_impacts()` (line 757) → `_query_features()` (line 697):

```
flood_geom = unary_union([shape(f["geometry"]) for f in depth_fc.features])
if not flood_geom.is_valid: flood_geom = flood_geom.buffer(0)

flood_wkt = WKTElement(flood_geom.wkt, srid=4326)

for (model, geom_col) in [(Building, Building.footprint), (Road, Road.geometry)]:
    SELECT id, osm_id, name, ST_AsGeoJSON(geom)
    FROM model WHERE ST_Intersects(geom_col, flood_wkt)
    for each row:
        geom    = shape(json.loads(row.geojson))
        overlap = geom ∩ flood_geom
        if overlap.is_empty: continue
        point   = overlap.representative_point()
        depth   = _sample_depth_at(depth_grid, point.x, point.y)     # nearest cell centre
        if depth <= 0: continue
        entry = {id, osm_id, name, feature_type, depth_m=round(depth,3),
                 risk_level=_classify_depth_risk(depth),
                 building_type | road_type}
```

Sampling at the **intersection's** representative point rather than the whole feature's is
deliberate — comment at line 709-711: "a building that only clips the flood edge must not be assigned
the depth of its dry far corner (nor dropped for it)."

Result: `affected_building_count`, `affected_road_count`, `depth_range_m`, and per-risk breakdowns.
The whole operation is wrapped in try/except returning an empty structure, so a PostGIS failure
degrades to "0 affected" rather than breaking the run.

### 4.11 Persistence

`_store_flood_layers()` (line 860) inserts one `FloodLayer` per type (depth/risk/boundary) with the
full GeoJSON in a JSONB column, then one `FloodImpact` row per affected feature against the **depth**
layer, then commits.

### 4.12 The four time steps

`run_for_time_steps()` (line 820) runs steps 0, 15, 30, 60 and stores each. Note that forecasts are
only generated for horizons 15/30/60, so **step 0 usually produces no layers from
`run_for_time_steps`** (`node_levels` is empty for that step and the step is skipped). The live path
uses `mithi_demo_pipeline` instead, which explicitly supplies the mapped current level for step 0.

---

## 5. Virtual-sensor propagation — implemented, not running

`backend/app/services/virtual_sensors.py` (500 lines). The rule, stated verbatim in
`config/virtual_sensors.json`:

> `SIMPLIFIED SENSOR PROPAGATION` — "downstream(t+delay) = upstream(t) * attenuation + baseline_component.
> This is NOT a hydraulic model."

### 5.1 The lag query

`nearest_reading_to(node_id, target)` returns a *statement* (so it can be unit-tested without a
database) that orders by:

```sql
abs(extract(epoch from telemetry_readings.recorded_at - <target>))
```

The comment records the bug this fixed: "Subtracting two timestamps yields an INTERVAL, and
PostgreSQL has no `abs(interval)`. An earlier version ordered by `func.abs(recorded_at - target)`
directly, which raised `UndefinedFunctionError` on every cycle: no virtual node ever ingested a
reading, so every node's risk assessment silently froze on stale data and appeared identical."

And the semantic correction at lines 201-207: "A hop's `delay_seconds` is a **LAG**, not a gate …
Gating on the age of the newest reading can never fire, because that reading is only one cycle old
while the delay is longer."

### 5.2 Head-of-chain generation (`_generate_from_physical`, line 247)

```python
normalized = clamp((physical_level_cm - physical_min_cm) / (physical_max_cm - physical_min_cm), 0, 1)
water_level = mithi_min_cm + normalized × (mithi_max_cm - mithi_min_cm)
rainfall     = max(0, physical_rainfall_mm_hr × settings.demo_input_mithi_rainfall_factor)
```

This is **the same `map_demo_level` formula** the forecast service uses — the live path already
does this once, in `process_real_demo_input`, so the virtual engine would duplicate the work.

### 5.3 Hop propagation (`_generate_propagated_reading`, line 288)

```python
deviation    = upstream_level - upstream_baseline            # upstream_baseline from the upstream
                                                          # node row, defaulting to 100.0
water_level  = node.baseline_level_cm + deviation × relationship.attenuation
rainfall     = max(0.0, upstream_rainfall × self._rain_attenuation)     # 0.8 from config
clamp water_level to [0, 300]
```

If no upstream reading exists yet, the node holds its baseline — "Warm-up: the lag window has not been
filled with upstream history yet, so hold the baseline rather than inventing a value."

### 5.4 Baseline walk (`_generate_baseline_reading`, line 326)

```python
water_level = baseline + random.uniform(-5, 5)
rainfall    = max(0, random.gauss(1.5, 1.0))
```

Random walk ±5 cm and a Gaussian rainfall draw. **This is the only genuinely random number generator
in the running system.**

### 5.5 Configured network (`config/virtual_sensors.json`)

| node_code | name | role | lat | lon | ground_elev_m | baseline_cm | warning_cm |
|---|---|---|---|---|---|---|---|
| MITHI-SIM-01 | Mithi virtual BKC (upstream) | upstream | 19.0600 | 72.8810 | 1.1 | 118.0 | 175.0 |
| MITHI-SIM-02 | Mithi virtual Saki Naka (midstream) | midstream | 19.0700 | 72.8830 | 1.5 | 120.0 | 180.0 |
| MITHI-SIM-03 | Mithi virtual Kurla (downstream) | downstream | 19.0800 | 72.8855 | 1.9 | 122.0 | 185.0 |

| hop | delay_seconds | attenuation | distance_m |
|---|---|---|---|
| MITHI-SIM-01 → MITHI-SIM-02 | 60 | 0.85 | 1400.0 |
| MITHI-SIM-02 → MITHI-SIM-03 | 90 | 0.75 | 1500.0 |

Global: `default_delay_seconds: 60`, `head_attenuation: 0.9`, `rain_attenuation: 0.8`.

`seed_virtual_network()` (line 398) would create these nodes and the `NodeRelationship` rows
idempotently. **It is never called.**

### 5.6 Runtime cadence if it were started

`start(interval_seconds = 10)` → `_run()` → `_generate_cycle()` → one reading per active virtual
node per 10 s. The forecast service, meanwhile, assumes a **5-minute** sampling interval
(`SAMPLING_INTERVAL_MINUTES = 5`). A 10 s virtual cadence is 30× denser than the feature design
assumes, which would make the 5-index lag equal 50 seconds rather than 5 minutes.

---

## 6. Scenario projection — `backend/app/services/scenario_service.py`

Closed-form, as shown in `04-DATA-PIPELINES.md` §5:

```
effective_rain = rainfall_mm_hr × (1 − drainage_capacity_pct/100) × 0.70
rise_per_min   = effective_rain × 0.84 / 60
level(h)       = min(initial_level_cm + rise_per_min × h, 300)
```

Worked example for the `heavy_rain` preset (50 mm/hr, 150 cm, 70 % drainage, 60 min):

```
effective_rain = 50 × 0.30 × 0.70 = 10.5 mm/hr
rise_per_min   = 10.5 × 0.84 / 60 = 0.147 cm/min
h=0   → 150.0 cm
h=15  → 152.2 cm
h=30  → 154.4 cm
h=60  → 158.8 cm      (cap 300 never reached)
```

For `extreme_rain` (85 mm/hr, 165 cm, 35 %):

```
effective_rain = 85 × 0.65 × 0.70 = 38.675 mm/hr
rise_per_min   = 38.675 × 0.84 / 60 = 0.5415 cm/min
h=60  → 165.0 + 32.5 = 197.5 cm      (crosses the 200 cm SEVERE alert threshold? no — 197.5 < 200)
```

The comparison summary is a formatted string, not an LLM output:

```python
f"Under the scenario, peak water levels rise by up to {max_level_diff:.0f} cm "
f"(average {avg_level_diff:.0f} cm across nodes). "
```

---

## 7. The 3D twin — `Frontend/components/DigitalTwin.tsx`

No ML. Two shader/math pieces are worth describing.

### 7.1 Gerstner-wave water shader

Custom `THREE.ShaderMaterial` (lines 205-277). Vertex shader:

```glsl
float gerstnerWave(vec2 pos, float amplitude, float wavelength, vec2 direction, float speed, float time) {
    float phase = dot(pos, direction) * wavelength + speed * time;
    return amplitude * sin(phase);
}
// three superposed waves
elevation += gerstnerWave(position.xz, 0.12, 0.15, vec2( 0.7,  0.7), 1.6, uTime);
elevation += gerstnerWave(position.xz, 0.08, 0.18, vec2(-0.5,  0.8), 1.4, uTime);
elevation += gerstnerWave(position.xz, 0.04, 0.25, vec2( 0.3, -0.9), 1.1, uTime);
vec3 displaced = position;  displaced.z += elevation;
gl_Position = projectionMatrix * modelViewMatrix * vec4(displaced, 1.0);
```

Fragment shader adds the emissive tint, a Fresnel term
`pow(1.0 - dot(normalize(vec3(0,1,0)), vec3(0,0,1)), 3.0)`, and depth-tinted darkening
`vec3(0, 0.1, 0.2) × elevation × 2.0`. Final alpha is the constant `uOpacity = 0.72`.

> **Note a genuine 3D bug in the shader:** after `waterGeo.rotateX(-π/2)`, the plane's local Y axis
> is world up, but the vertex shader displaces `displaced.z`. Since `uTime` only modulates a
> ±0.24 m vertical wave that is being written into the wrong axis, the water surface does not visibly
> undulate; it reads as a flat tinted sheet. The intended line is `displaced.y += elevation`.

### 7.2 Backend-driven water height

`backendWaterHeight` (lines 62-79) prefers the backend forecast:

```
if simulationInput.status == "live":
    cm = forecast(horizon == timelineStep)?.predicted_level_cm
         ?? (timelineStep == 0 ? simulationInput.mithi_water_level_cm : null)
    return max(0, cm / 100)                 # cm → metres
else:
    max over nodes of { forecast(horizon)?.predicted_level_cm
                        ?? (horizon == 0 ? node.latest_reading?.water_level_cm : null) } / 100
```

Then `waterMeshRef.current.position.y = backendWaterHeight`. If the backend gives nothing, the target
is 0 — i.e. the water drops to its base offset rather than holding an illustrative value. That is
consistent with the honesty rule, and the page's "TWIN SPECIFICATION" panel says exactly that
(`digital-twin/page.tsx:209-213`).

### 7.3 Building flood recolouring

```javascript
const isFlooded =
     (obj.userData.type === 'low_elevation' && timelineStep >= 15) ||
     (obj.userData.type === 'transit'        && timelineStep >= 30) ||
     (timelineStep >= 60 && obj.position.x < 0)             // "Kurla side inundated"
mat.color.setHex(timelineStep === 60 ? 0xef4444 : 0xf59e0b)
mat.emissive.setHex(timelineStep === 60 ? 0x7f1d1d : 0x78350f)
mat.emissiveIntensity = 0.5
```

**This is a hand-authored demonstration rule keyed off the timeline, not a result of the flood
simulation.** It does not consult `flood_impacts`. The page discloses this: "Buildings are
*procedurally placed placeholders*, not surveyed OSM footprints, and the terrain is a schematic
corridor rather than the Copernicus DEM."

Full scene description in [`07-MAP-AND-3D-DETAILS.md`](07-MAP-AND-3D-DETAILS.md).