# 14 — Honesty and Limitations: everything the product says about itself

This file collects **every** disclaimer, caveat and self-limitation statement in the repository,
grouped, with the exact location of each.

---

## 1. The five product principles (`PRD.md:686-692`)

1. **Physical-to-digital** — a real change in the channel must visibly change the virtual world.
2. **Prediction-to-impact** — a forecast must always lead to a map of consequences, not stop at a number.
3. **Preparation** — a user must be able to change conditions and see a different future.
4. **Honesty** — every number and layer shows its source, and every simplification is stated.
5. **Clarity** — one glance should tell the user what is happening now, what is coming and where.

`PRD.md:19` states the governing rule for a hackathon build:
> "Honesty matters. Where something is simplified, simulated, or illustrative, the product labels it
> as such. A smaller thing that is truthful and works beats a bigger thing that is faked."

---

## 2. The API honesty note — `GET /api/about/honesty-note`

**Note:**
> "Prototype readings and scenario outputs are clearly labeled. A few minutes of data cannot establish
> real-world model accuracy. Use this interface for preparedness and exploration, not as a life-safety
> system."

**7 disclaimers:**

1. "Flood simulation is a simplified elevation-based inundation estimate, not a hydraulic model."
2. "Elevation data resolution (~30m) limits street-level accuracy."
3. "Risk weights and thresholds are starting assumptions requiring local calibration."
4. "Satellite rainfall data (IMD, GPM) is too coarse (~10km) for street-level rainfall; local rain
   gauge is primary live source."
5. "No public live Mithi River water-level API is assumed to exist."
6. "Building heights from OSM are often missing; estimated heights are clearly labeled."
7. "Generated flood extents are PREDICTED, not OFFICIAL FLOOD ZONES."

> Disclaimer 4 says "~10 km" for IMD; the actual grid is **0.25° ≈ 25 km**. The seeded
> `DataSource.resolution` says "0.25 deg (~25 km)" and the IMD JSON says "~25 km". The endpoint's
> "~10km" appears to be leftover GPM-IMERG language. Minor but it is an inaccuracy in a disclaimer.

---

## 3. Per-dataset limitations — the `data_sources` table (`seed.py:25-81`)

### SRTM / Copernicus GLO-30 Terrain · `terrain` · 30 m
- Licence: "SRTM is NASA/USGS public domain; Copernicus GLO-30 is free for all uses."
- **Limitation:** "Surface elevation at 30 m. Cannot resolve bunds, kerbs or channel roughness;
  inundation depths inherit this error."

### IMD Gridded Rainfall · `historical_reference` · 0.25° (~25 km) daily
- Licence: "IMD data policy applies."
- **Limitation:** "~25 km grid. Cannot represent street-level rainfall. Historical context and model
  training only, never a live sensor reading."

### OpenStreetMap Features · `terrain_osm` · Vector
- Licence: "(c) OpenStreetMap contributors, ODbL 1.0."
- **Limitation:** "Building heights are largely untagged and are ESTIMATED from building type and
  level counts; treat as indicative only."

### Open-Meteo Weather Forecast · `forecast` · hourly ~9 km
- Licence: "Free for non-commercial use under CC BY 4.0."
- **Limitation:** "FORECAST only. Never a substitute for an in-channel ESP32 reading; a forecast that
  disagrees with hardware does not override it."

### ESP32 Field Sensors · `live_hardware` · per-node ~5–30 s
- Licence: "Own deployment."
- **Limitation:** "Ultrasonic sensors can foam-fail in fast turbid flow; readings are range-checked
  and rejected outliers are logged, not dropped."

---

## 4. Data-file embedded limitations

### `data/rainfall/imd/processed/mumbai_rainfall.json`
> "0.25 deg (~25 km) grid. One cell covers the entire study area, so this cannot represent
> street-level or catchement-scale rainfall. Use for historical context and model training only,
> never as a live rainfall reading."

### `data/terrain/terrain_metadata.json`
> "DEM is coarse (~30m). Not fine enough for individual drain/street/building-threshold accuracy."

### `data/osm/buildings.geojson → height_estimate_method`
> "Explicit OSM `height` when present, else building:levels x 3 m, else a type-based storey estimate.
> Values flagged height_is_estimated=true are visualisation assumptions, not authoritative
> measurements."

### `config/risk_thresholds.json`
- `_readme`: "Risk engine configuration. Weights and thresholds are prototype assumptions, not
  scientifically validated coefficients."
- `note`: "These are prototype assumptions for hackathon demonstration. Not calibrated to real Mithi
  River hydraulics."
- `defaults.assumptions[3]`: **"Synthetic data used for training - not operational gauges"**

### `config/virtual_sensors.json`
> "Every value here is SIMULATED and must be labelled as such in the UI (source = simulated).
> Propagation uses SIMPLIFIED SENSOR PROPAGATION: downstream(t+delay) = upstream(t) \* attenuation +
> baseline_component. This is NOT a hydraulic model."

---

## 5. ML model self-report

### `models/flood/model_metadata.json`

`rainfall_source`: "IMD gridded rainfall (real observations)"
`level_source`: **"SIMULATED single-reservoir storage-routing model driven by IMD rainfall. Not
measured gauge data."**

**4 limitations:**
1. "Training levels are simulated, so reported skill is optimistic relative to real gauges."
2. "Sub-daily rainfall shape is a modelling assumption (gamma + diurnal), not measured."
3. "Single reservoir: no channel routing, backwater or tide."
4. "Prototype only; not validated for operational flood forecasting."

### `backend/scripts/train_forecast_models.py:3-11`
> "\* **REAL:** the rainfall forcing series comes from the IMD gridded files … \* **SIMULATED:** the
> 5-minute water-level series is generated by a documented single-reservoir (storage-routing) model
> driven by that rainfall. The project has no year of 5-minute gauge records, so training levels must
> be synthesised. Every artefact and every metric written by this script is labelled accordingly and
> the UI must present results as prototype-grade, not operational."

And line 15-16:
> "Feature order is fixed by `forecast_service._build_features`; the first feature MUST be
> `current_level` because the persistence baseline indexes x[0]. Time-series discipline: the split is
> chronological. Nothing is shuffled."

### The `ModelRegistry.training_data_description` (served by `/api/about/model-info`, printed as the caveat on `/prediction`)
> "Rainfall forcing: real IMD gridded daily observations. Training levels: SIMULATED single-reservoir
> storage-routing series at 5-minute steps. Chronological 70/15/15 split, no shuffling. Prototype
> grade."

### `flood_risk.py:6-8` (the risk engine header)
> "Explainable, rule-based flood risk scoring … This is a prototype decision model — not a
> scientifically validated hydraulic model. Weights and thresholds are starting assumptions
> requiring local calibration. The interface must state this clearly."

---

## 6. Flood simulation self-report

### `flood_simulation.py:7-9`
> "This is a simplified inundation estimate, not a full hydraulic model. Does not solve momentum or
> mass conservation. No flow velocity calculation. Accuracy limited by elevation data resolution
> (~30m SRTM)."

### `flood_monitor.py` inline notes
- Layer response: **"PREDICTED FLOOD EXTENT — model estimate, not an official flood zone."**
- Impact summary: **"MODEL ESTIMATE — not an official flood zone."**
- Roads/buildings layer: "OpenStreetMap contributors (ODbL). Heights may be estimated."
- Waterways layer: "OpenStreetMap contributors (ODbL)."

### `/api/about/assumptions → flood_simulation`
- `method`: `elevation_based_inundation`
- `description`: "Simplified raster inundation model. Not a hydraulic model. Does not solve momentum
  or mass conservation. No flow velocity calculation."
- `limitations`:
  1. "Accuracy limited by elevation data resolution (~30m SRTM)"
  2. "Cannot represent individual streets, drains, or building thresholds"
  3. "Does not model drainage network hydraulics"
  4. "Assumes instantaneous water surface equilibration"

### Code-comment honesty about specific choices
- River clamp (`flood_simulation.py:415`): *"Clamping to the surveyed span: extrapolating past the
  outermost sensors invents a water surface that no observation supports."*
- Area counting (line 654-657): *"The reported area must match the polygons the map actually draws."*
- No-grid guard (line 602-603): *"Never run against a missing grid: a silent synthetic substitute
  would be reported as real inundation."*
- Synthetic terrain (line 122-130): *"synthetic elevation — outputs are SIMULATED, not real
  inundation"*; *"Never let a synthetic grid occupy the real-terrain cache key, or it would masquerade
  as real elevation for the next 24 hours."*
- Impact depth sampling (line 709-711): *"a building that only clips the flood edge must not be
  assigned the depth of its dry far corner (nor dropped for it)."*

### `config.py:131-133` on `allow_synthetic_terrain`
> "When the real terrain raster is missing, flood simulation must fail loudly rather than quietly
  substituting synthetic elevation: synthetic results are not real inundation and must never be
> labelled as such. Opt in explicitly."

---

## 7. Scenario assumptions — printed verbatim on `/scenarios`

```json
"description":
  "Simplified what-if assumptions (not a hydraulic model):\n
   1. Runoff coefficient: 0.7 (70% of rainfall becomes runoff)\n
   2. Excess rainfall = rainfall × (1 - drainage_capacity/100) × runoff_coefficient\n
   3. Water level rise = excess_rainfall × 0.84 cm/mm\n
   4. Level capped at 300 cm\n
   5. Flood extent from elevation-based inundation, not hydraulic routing"
```

`flood_risk.assess_scenario_risk` also embeds (lines 443-451):
```
SCENARIO MODE — Simplified what-if assumptions:
  Rainfall intensity: N mm/hr
  Initial water level: N cm
  Drainage capacity: N%
  Runoff coefficient: 0.7 (assumed)
  Level rise per mm excess rain: 0.84 cm (assumed)
  Not a hydraulic model — elevation-based inundation only
```

### `PRD.md:544-546` on the scenario engine
> "The simulator must not simply multiply rainfall by a percentage and claim it represents hydraulic
> flood propagation. It uses stated, simplified assumptions, and an Assumptions panel lists them in
> plain words."

---

## 8. UI honesty copy — every disclaimer string on screen

### Global / shell
| Location | String |
|---|---|
| `Shell.tsx:72` | "Predicted extent — model estimate, not an official flood zone." |
| `Shell.tsx:100` | "Recorded data being replayed — not live conditions." |
| `Shell.tsx:105` | "User-defined scenario — not live conditions." |
| `Shell.tsx:89` | "Backend unreachable — showing last loaded state." |
| `constants.ts:59-60` | "`unknown` … is deliberately NOT styled as 'offline' any more: an unassessed node is not a failed node." |

### Dashboard
| Location | String |
|---|---|
| `page.tsx:157` | "DEMO CALIBRATION · Physical IoT Input → Mithi Simulation. This is not a Vadodara-to-Mumbai hydraulic relationship." |
| `page.tsx:152` | `WAITING FOR REAL IoT INPUT` |
| `page.tsx:227,291,335` | `WAITING FOR SENSOR` · `NO LIVE IOT DATA` |
| `page.tsx:371` | "Model estimate, not an official flood zone." |
| `page.tsx:440-442` | "Corridor data last refreshed … Flood extent is a model estimate — not an official flood zone, and not an emergency warning system." |
| `page.tsx:277-278` | "ESP32 telemetry" vs "virtual sensor — simulated" |

### 2D map
| Location | String |
|---|---|
| `map/page.tsx:67` | "SIMULATED FLOOD EXTENT · Mithi River Mumbai · driven by real IoT demo input" |
| `map/page.tsx:107` | `WAITING FOR REAL IoT INPUT` |
| `map/page.tsx:246-248` | "SIMULATED MITHI CONDITION · driven by REAL IoT INPUT" / "WAITING FOR REAL IoT INPUT · no virtual sensor fallback is active" |
| `map/page.tsx:266` | "Model estimate, not an official flood zone." |
| `FloodMap.tsx:399` | "Elevation-threshold simulation estimate" |
| `FloodMap.tsx:438` | "OSM Vector Geometry" |
| `FloodMap.tsx:437` | `Estimated ~12m` when no height is available |

### 3D twin
| Location | String |
|---|---|
| `digital-twin/page.tsx:205-207` | "Buildings are **procedurally placed placeholders**, not surveyed OSM footprints, and the terrain is a schematic corridor rather than the Copernicus DEM. The water surface is elevation-threshold inundation computed by the backend." |
| `digital-twin/page.tsx:209-213` | "Per-step surface heights follow the backend forecast where one exists and fall back to an illustrative curve otherwise — such frames are labelled SIMULATED in the viewer." |
| `digital-twin/page.tsx:110` | `WAITING FOR REAL IoT INPUT` |

> ⚠️ The second paragraph says "fall back to an illustrative curve". The code **does not** do that —
> `backendWaterHeight` returns `null` → target 0 → the mesh drops to its base offset. The copy
> describes a behaviour that was removed. (Honest, but stale.)

### Prediction
| Location | String |
|---|---|
| `prediction/page.tsx:282-284` | "Prototype trained on synthetic water levels — not operationally validated." (fallback text) |
| `prediction/page.tsx:301-302` | "This is a rule-based prototype assessment, not a validated hydraulic result." |
| `prediction/page.tsx:289` | Panel header: **"deterministic, not an LLM"** |
| `prediction/page.tsx:261` | "vs persistence baseline" |

### Alerts
| Location | String |
|---|---|
| `alerts/page.tsx:116` | "Active thresholds, sensor faults and event history" |
| (severity → risk mapping) | `critical → severe` in the badge palette |

### About this data — `data/page.tsx:21-30` (8 hard-coded limitations)

1. **DEM resolution** — "Surface elevation at ~30 m cannot resolve bunds, kerbs or drain thresholds."
2. **Rainfall spatial resolution** — "IMD 0.25° is a ~25 km grid — never street-level, never a live reading."
3. **Building heights estimated** — "Where OSM has no height, levels × floor height is an estimate, flagged as such."
4. **Live sensor network** — "**No virtual sensor network is active.** The live application waits for the registered physical IoT input."
5. **Risk weights are prototype** — "The 35/30/20/15 weighting is an assumption set, not a calibrated model."
6. **Flood simulation is simplified** — "Elevation-threshold inundation with connectivity masking — not a hydraulic solver."
7. **ML is prototype-grade** — "Trained on synthetic water levels; performance is not operationally validated."
8. **Not an emergency warning system** — "Built for a hackathon demonstration. Do not use for life-safety decisions."

Plus `data/page.tsx:117-120`:
> "Simulated data is never shown as live. Forecast is never shown as observed. Model output is always
> labelled a predicted flood extent, never an official flood zone."

---

## 9. The architecture doc's honesty section — `System-Architecture.md:871-878`

Verbatim:
- "Every reading and layer carries a source field: live hardware, simulated, historical or scenario."
- "The flood simulation is a simplified elevation-based inundation estimate, not a hydraulic model,
  and does not compute flow velocity."
- "Elevation and satellite rainfall resolutions are coarse and limit accuracy; this is stated in the
  interface."
- "Risk weights and thresholds are starting assumptions requiring local calibration."
- "Prototype data cannot establish real-world forecast accuracy."
- "The outputs are a preliminary scenario visualization, not a street-level prediction."

`System-Architecture.md:434-437` on the simulation engine:
- "This is a simplified inundation estimate, not a full hydraulic model. It does not solve momentum
  or mass conservation and does not calculate flow velocity."
- "It is a preliminary scenario visualization."
- "Accuracy is limited by the elevation data resolution."

`PRD.md:112-113` on the study area:
- "A coarse elevation model (for example 30 m or 90 m resolution) is good for regional context but
  cannot represent individual streets, drains or building thresholds. The product must state this
  limitation in the assumptions panel."
- "We do not claim street-level accuracy. We claim a preliminary scenario visualization."

---

## 10. Honest limitations a reviewer should raise

Things the product **does** disclose, restated for clarity:

| # | Limitation | Where disclosed |
|---|---|---|
| 1 | Flood simulation is elevation-threshold, not hydraulic | everywhere |
| 2 | DEM is ~30 m | everywhere |
| 3 | No flow velocity, no momentum, no mass conservation | everywhere |
| 4 | Risk weights are hand-set, uncalibrated | everywhere |
| 5 | ML training levels are synthetic | everywhere |
| 6 | 96.5 % of building heights are estimates | `data_sources`, `/data` |
| 7 | IMD rainfall is ~25 km, training-only | everywhere |
| 8 | No public live Mithi API is assumed | `/honesty-note` |
| 9 | The Vadodara → Mithi link is a linear calibration, not hydraulics | many places |
| 10 | Predicted extent ≠ official flood zone | everywhere |
| 11 | Not a life-safety system | `/honesty-note`, dashboard footer, `/data` |
| 12 | The 3D scene is schematic, not surveyed | `/digital-twin` |
| 13 | No virtual sensor network is running | `/data` |

Things **not** disclosed in the UI, which a sharp reviewer may notice:

| # | Issue |
|---|---|
| A | The forecast uncertainty band is a **hard-coded ±10 cm**, not a model-derived interval. The prediction page prints the bounds as `120–180 cm bounds` with no caveat. `System-Architecture.md:269` promised a tree-spread percentile. |
| B | The live forecasts almost certainly **equal the current level** because the RF receives the wrong feature count (§5.1 of `13-TESTING-AND-QUALITY.md`). The UI shows `FORECAST` badges and RF metrics. |
| C | The MAE figures (0.24 / 0.33 / 0.48 cm) are **impressively small because the target is synthetic**. The `/data` and `/prediction` copy does say "trained on synthetic water levels", but a reader may still take 0.24 cm as impressive. |
| D | 94–99.7 % of each RF's predictive power is the current level. Nothing in the UI shows feature importances, so "Random Forest" implies far more model than exists. |
| E | `RISK_MODEL_VERSION` = `"rf_15min"` for a **rule-based** model, surfaced on `/prediction` as `rule_based_v1 vrf_15min`. |
| F | `RISK_MODEL_NAME = rule_based_v1` but `/api/about/assumptions → forecast.model` says **"Random Forest Regressor (primary)"**, conflating the ML model with the risk model. |
| G | `assess_scenario_risk` passes an **mm/hr** value as the rainfall component, saturating scenario risk. No UI caveat. |
| H | The flood-sim `grid_resolution_m` reported to the UI is 10.0 while the real one is 30.92 — the endpoint contradicts its own limitation text. |
| I | The scenario comparison tiles render `—` for peak/mean levels because the API omits those keys; nothing tells the user the feature is incomplete. |
| J | The 3D building flood colouring is driven by the timeline slider, not by `flood_impacts` — so the 2D and 3D flood depictions can disagree. The "TWIN SPECIFICATION" panel says the water surface comes from the backend but says nothing about the colouring. |
| K | The PDF mockups show a CesiumJS twin, 5 nodes, and a `RF v1.0.3` model version that exist nowhere in the build. The mockup page footers do say "All values are sample data for illustration", but a judge comparing the PDF to the demo may expect a discrepancy that isn't disclosed. |
| L | `/api/about/assumptions → source_labels` still calls the hardware class `LIVE HARDWARE` (the UI says `REAL IoT INPUT`) and claims a "Virtual sensor network with documented propagation rules" that is **not running**. |

---

## 11. Verbatim: what the project does **not** claim (`PRD.md:675-682`)

- Simulating all of Mumbai.
- A production-grade, calibrated hydraulic model.
- Claims of validated real-world prediction accuracy.
- Reliance on a public live Mithi River water-level API (we do not assume one exists).
- Street-level rainfall from satellite data (the satellite rainfall grid is about 10 km and too coarse
  for this; the local rain gauge is the primary live rainfall input).
- Mobile apps, user accounts and public notification delivery (SMS or push) beyond dashboard alerts.

`System-Architecture.md:447`:
> "No channel routing, backwater or tide." — stated for the synthetic training reservoir.

---

## 12. The single most important sentence in the codebase

`backend/app/core/config.py:93-95`:

```python
# ── Real demo input -> simulated Mithi calibration ───────────────────
# These are configurable demo calibration points, not a geographic or
# hydraulic relationship between Vadodara and Mumbai.
```

Everything else follows from taking that seriously: the mapping is disclosed in the API payload, in
the dashboard footer, on the map subtitle, on the twin overlay, and in the PRD's "Live hackathon input
contract". A judge who pours water into the channel sees the level change, the mapped Mithi level
change, the forecast change, the flood polygons change and the 3D water rise — and every surface
says exactly what is real and what is calibrated.