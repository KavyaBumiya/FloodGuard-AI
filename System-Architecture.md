# FLOODGUARD AI — System Architecture

Project: IoT-Powered AI Flood Prediction and 3D Digital Twin for Mumbai
Event: IIT Bombay Hackathon (36 to 48 hour build)
Study area: Mithi River corridor, BKC to Kurla, 1 to 2 km
Document status: Hackathon build version 1.0

This document describes how FLOODGUARD AI is built, layer by layer: frontend, backend, AI/ML, IoT, data sources, database schema and finally the overall system design. It follows the PRD and keeps its principles: the physical sensor must visibly change the virtual world, every forecast must lead to a map of consequences, and every number must show whether it is live, historical or simulated.

---

## 1. Frontend Layer (UI / UX)

**Tech tags:** `HTML` · `CSS` · `React` · `JavaScript` · `TypeScript`

Supporting libraries: Vite (build tool), React Router, Zustand (state), TanStack Query (REST data), Tailwind CSS (styling), Recharts or ECharts (charts), Leaflet with react-leaflet (2D map), CesiumJS with Resium (3D twin), native WebSocket client with auto-reconnect.

### 1.1 Design principles

- Dark, high-contrast command-centre theme so it reads well on a projector.
- One shared risk colour scheme (Low, Moderate, High, Severe) used on every page. Colour is never the only signal; every level also carries a text label and an icon.
- A one-glance rule: a new user understands the corridor status within five seconds.
- Honesty in the UI: every sensor card, chart and map layer carries a source badge (Live Hardware, Simulated, Historical) and a mode indicator (Live, Replay, Scenario).
- Forecast views are always labelled "Forecast" and current views "Current", with text such as "Showing predicted conditions at +30 minutes".

### 1.2 Application shell

- Top bar: project name, global mode indicator (Live / Replay / Scenario), connection state ("Connected" or "Reconnecting"), model version, active alert badge count, IST clock.
- Critical alert banner: appears at the top of every page when a Critical alert is active.
- Scenario banner: persistent banner stating "You are viewing a simulated what-if scenario, not live conditions" whenever a scenario is active.
- Side navigation: Dashboard, AI Prediction, 2D Flood Map, 3D Digital Twin, What-If Simulator, Alerts and Monitoring, About This Data.
- Toast notifications for new alerts.

### 1.3 Pages and components

**Command Centre Dashboard (home)**
- Summary strip: active sensors, offline or stale sensors.
- Sensor card list: one card per node with water level, rainfall intensity, risk level (colour + text), source badge, last-updated time, stale/offline indicator.
- Live water-level line chart for the selected node, advancing in real time.
- Rainfall indicator.
- Highest-risk locations list, ordered by risk.
- Latest alerts list linking to the Alerts page.
- Compact forecast summary: +15, +30, +60 minute predicted levels.
- System status strip: connection, model version, data mode.
- Interaction: clicking a card selects that node and updates the chart and forecast summary.

**AI Prediction Page**
- Node selector.
- Chart of history followed by forecast with a visible break at "now", markers at +15, +30, +60, and a shaded uncertainty band.
- Risk score, risk level and a contribution breakdown (for example "driven mainly by rapid rise rate").
- Model card: model name, version, training data description, last trained time.
- Metrics block: MAE and RMSE shown beside the persistence baseline.
- Plain-language summary sentence generated from the forecast.
- Note shown when the selected node is simulated.

**2D Flood Map (Leaflet + OpenStreetMap)**
- Base map centred on the corridor, with pan and zoom limits.
- Sensor markers: different outline/icon for physical versus simulated nodes, filled by risk colour.
- Flood depth polygons or heatmap from the simulation engine, plus flood boundaries for Now, +15, +30, +60.
- Road and building overlays; affected ones are highlighted for the selected time step.
- Marker popup: node name, source badge, water level, rainfall, risk, three forecast values.
- Feature popup: estimated flood depth and time step for a clicked road or building.
- Layer control: sensors, heatmap, roads, buildings, flood boundaries, each toggling independently.
- Legend and a side summary with counts of affected roads and buildings.

**3D Digital Twin (CesiumJS)**
- Terrain, river channel, extruded OSM buildings, draped roads, sensor station markers.
- Semi-transparent water surface or flood overlay driven by the same GeoJSON layers as the 2D map.
- Affected buildings change colour when the flood reaches them.
- Camera presets: corridor overview, close-up of the physical sensor, along-the-river view.
- Click cards for sensors and buildings.
- Fallback chain: Cesium world terrain, then preprocessed local terrain, then a message pointing to the fully working 2D map.
- Kept to a small area so it renders smoothly on the demo laptop.

**Forecast Timeline Control (shared component)**
- Four positions: Now, +15, +30, +60 minutes.
- Play button that steps through the four positions as a short animation.
- Stored in global state so the 2D map, 3D twin, affected counts and forecast summary all change together.

**What-If Scenario Simulator**
- Sliders with numeric inputs and units: rainfall intensity (mm/hr), initial water level (cm), drainage capacity (%), duration (minutes), optional scenario name.
- Presets: Normal Monsoon Day, Heavy Rain, Extreme Rainfall With Poor Drainage.
- Run Scenario and Reset buttons, a loading state, and a clear error message that keeps the previous result on failure.
- Assumptions panel listing, in plain words, how rainfall and drainage change the water level and how water spreads.
- Saved scenarios list for reopening past runs.

**Scenario Comparison**
- Chart overlaying baseline and scenario water-level curves.
- Split or toggle map view with the additional flooded area highlighted.
- Summary numbers: peak level, change in flooded area, extra roads, extra buildings, highest risk reached in each case.
- One-sentence automatic summary.

**Alerts and Monitoring Page**
- Active critical warnings, predicted risks, communication failures and sensor faults.
- Acknowledge one or many alerts.
- History with filters (node, severity, type, time range), search and sort.
- Alert detail view with triggering readings and a link to the node on the map.
- Network health overview: each node's status (online, stale, offline), last reading time, battery voltage where available.

**About This Data / Assumptions Panel**
- Datasets used and known limitations, including coarse elevation and satellite rainfall resolution.
- Risk score weights and thresholds stated as starting assumptions, not validated coefficients.
- The simulation limitation statement: simplified inundation estimate, not a hydraulic model.

### 1.4 Frontend data flow

- Initial page load: REST calls for sensors, recent readings, latest forecasts, alerts and flood layers.
- Live updates: one WebSocket connection delivers readings, predictions, alerts and mode changes. The store merges them and charts advance without a refresh.
- On disconnect the client shows "Reconnecting", retries with exponential backoff, and refetches missed data on recovery.
- A node with no report within a set period is rendered as stale, then offline.
- Timestamps arrive in UTC and are converted to IST only for display.

### 1.5 Frontend folder structure (suggested)

- `src/pages` — Dashboard, Prediction, Map2D, Twin3D, Simulator, Alerts, About
- `src/components` — SensorCard, RiskBadge, SourceBadge, ModeIndicator, TimelineControl, ScenarioBanner, LegendPanel
- `src/store` — live data, selected node, selected time step, mode, active scenario
- `src/services` — REST client, WebSocket client
- `src/types` — TypeScript interfaces mirroring backend schemas (Reading, Forecast, RiskResult, FloodLayer, Scenario, Alert)

---

## 2. Backend Layer

**Tech tags:** `Python` · `PostgreSQL (with PostGIS)` · `Redis`

Supporting stack: FastAPI (REST and WebSocket), Uvicorn, Pydantic (validation), SQLAlchemy plus GeoAlchemy2, asyncpg, Alembic (migrations), paho-mqtt or aiomqtt, Eclipse Mosquitto (MQTT broker), NumPy, SciPy, rasterio, shapely, geopandas, APScheduler or asyncio background tasks.

### 2.1 Services inside the backend

The backend is one deployable Python application organised into modules. This keeps the hackathon build simple while preserving clean boundaries.

1. **Ingestion service** — subscribes to MQTT, accepts HTTP telemetry, validates, stores.
2. **Virtual sensor engine** — generates simulated readings and propagates the physical node's changes downstream.
3. **Forecast service** — loads trained models and produces 15, 30, 60 minute predictions.
4. **Risk service** — turns forecasts into explainable risk scores.
5. **Flood simulation engine** — converts water levels into flood layers and impact counts.
6. **Scenario service** — runs what-if scenarios and comparisons.
7. **Alert engine** — evaluates rules, deduplicates, clears, and records acknowledgements.
8. **Realtime gateway** — pushes updates to browsers over WebSocket.
9. **Replay and demo controller** — plays back recorded data and manages demo mode and reset.

### 2.2 Telemetry ingestion flow

1. The ESP32 publishes JSON to an MQTT topic such as `floodguard/nodes/<node_code>/telemetry`. The virtual sensor engine publishes to the same topic structure.
2. The ingestion service receives the message and validates it with Pydantic: node registered, numeric values in plausible ranges, timestamp present and sensible (not far in the future or past).
3. Invalid readings are rejected, written to the rejected readings table with a reason, and logged. The service keeps running.
4. Valid readings are stored in the telemetry table (UTC) along with received time, and the latest value is cached in Redis.
5. The service publishes an internal event on Redis pub/sub, which triggers forecasting, risk scoring, alert evaluation and WebSocket fan-out.

Other MQTT topics: `floodguard/alerts` and `floodguard/system/status`.

Supporting REST endpoints: register a node, submit a reading (HTTP fallback), list nodes, fetch historical readings for a node.

### 2.3 Backend algorithms

**Reading validation and cleaning**
- Range checks per field (water level, rainfall intensity, battery voltage).
- Timestamp sanity check.
- Optional light smoothing for display: exponential moving average, never applied to stored raw values.

**Stale/offline detection**
- Scheduled check comparing each node's last reading time to thresholds (for example stale after a few missed intervals, offline after a longer limit). Drives the communication failure alert.

**Virtual sensor propagation (simplified rule, not hydraulics)**
- Each virtual node has a baseline level and a relationship to an upstream or downstream neighbour defined in a small config file: delay in seconds and attenuation factor.
- Downstream level at time t = baseline + attenuation × (physical node deviation from its own baseline at time t minus delay), plus small bounded noise.
- Scripted scenarios (gradual rise, sudden surge, steady normal) are defined as time-indexed level curves.
- Documented honestly as a propagation rule.

**Flood-risk scoring**
- Normalise four inputs to 0–1: water-level severity, rise rate, rainfall severity, soil saturation, using configured min and max values.
- Score = 0.35 × level + 0.30 × rise rate + 0.20 × rainfall + 0.15 × soil saturation.
- If soil moisture is unavailable, use a documented default and renormalise the remaining weights.
- Map score to Low, Moderate, High, Severe using configurable cut-offs.
- Explainability: compute each input's weighted contribution and report the largest as the driver.

**Flood simulation (elevation-based inundation)**
1. Load the precomputed terrain grid (for example 100 × 100 cells at about 10 m) from cache.
2. Convert each station's forecast water level to water-surface elevation using its calibrated reference elevation.
3. Interpolate the water surface along the river centreline between stations (linear interpolation by distance along the river), then assign each grid cell the surface elevation of its nearest river point.
4. Depth per cell = max(0, water-surface elevation − ground elevation).
5. Connectivity mask: run a flood-fill (breadth-first search or SciPy connected-component labelling) starting from river cells and drainage connection cells, so only hydraulically connected low cells are flooded. Disconnected low spots remain dry.
6. Classify depth into risk levels to produce a depth layer and a risk layer.
7. Repeat for Now, +15, +30, +60.
8. Vectorise the result to GeoJSON polygons.
9. Impact estimate: spatial intersection of flooded cells with road and building geometries using PostGIS (or shapely with a spatial index), reporting counts and depth ranges per time step.
10. Cache results in Redis keyed by inputs, with a coarser grid fallback if a run would exceed a few seconds.

**What-if scenario logic (simplified, stated assumptions)**
- Inputs: rainfall intensity, initial water level, drainage capacity percent, duration.
- Effective excess rainfall = rainfall intensity × runoff coefficient × (1 − drainage capacity fraction adjustment), where the runoff coefficient and the conversion from excess rainfall to channel level rise (cm per mm) are configurable constants, labelled as assumptions.
- Scenario level curve = initial level + accumulated rise over time, capped by a configured maximum, passed through the same forecasting-to-risk-to-simulation pipeline.
- The Assumptions panel prints these rules in plain words. The simulator does not claim hydraulic propagation.
- Each run creates a scenario record, stores its layers and assumptions, and returns the scenario identifier.

**Scenario comparison**
- Run baseline and scenario through the same engine, then compute peak level difference, flooded area difference (cell count × cell area), additional roads and buildings (set difference of affected IDs), highest risk in each, and generate a one-sentence summary from a template.

**Alert rules**
- Threshold: current level above configured level.
- Predicted risk: forecast risk reaches High or Severe within a horizon.
- Rapid rise: rise rate above a limit.
- Communication failure: node stale or offline.
- Sensor fault: repeated invalid readings.
- Deduplication: one active alert per node and type; auto-clear when the condition ends, retained in history.
- Scenario alerts are flagged and kept separate from live alerts.

### 2.4 Realtime delivery

- WebSocket endpoint with topics for readings, forecasts, alerts, risk updates and mode changes.
- Redis pub/sub fans events out so multiple backend workers could be added later.
- Heartbeat messages let the client detect drops quickly.

### 2.5 Redis usage (caching and messaging)

- Latest reading per node (fast dashboard load).
- Latest forecast and risk result per node.
- Cached flood layers per time step, keyed by a hash of water levels or scenario identifier.
- Cached terrain grid metadata.
- Alert deduplication keys with expiry.
- Pub/sub channel for internal events and WebSocket broadcasting.
- Current system mode (Live, Replay, Scenario) and replay position.

### 2.6 PostgreSQL usage

- Time-series telemetry with a composite index on node and timestamp.
- PostGIS geometry columns for nodes, roads, buildings, river and flood layers, with GiST spatial indexes.
- Forecasts, risk results, scenarios, alerts and model registry (see Section 6).

### 2.7 REST API surface (grouped)

- Nodes: register, list, detail.
- Telemetry: submit, history for a node.
- Forecast: latest forecast for a node, forecast history versus actuals.
- Risk: current and per-horizon risk for all nodes.
- Flood: flood layers for a time step, impact summary.
- Scenarios: create and run, list, fetch one, compare with baseline.
- Alerts: list with filters, acknowledge one or many, alert detail.
- System: health, mode, replay start/stop, reset demo, about-data metadata.

### 2.8 Error handling and resilience

- Validation errors return clear messages and are logged.
- Forecast failures (too little recent data) return an explicit "insufficient data" state, never a made-up number.
- Failed scenario runs keep the previous result on the frontend.
- A fallback virtual node substitutes if the physical node stops reporting.

---

## 3. AI / ML Layer

**Tech tags:** `Python` · `scikit-learn` · `XGBoost (optional)` · `pandas` · `NumPy` · `joblib`· `matplotlib`· `tensorflow`

### 3.1 What the ML layer must do

Predict the water level at each sensor at +15, +30 and +60 minutes, with a confidence range where available, then hand the predictions to the risk scorer and the flood simulation engine.

### 3.2 Models

- **Persistence baseline:** future level equals current level. Every other model is reported against it.
- **Linear Regression:** simple, fast baseline.
- **Random Forest Regressor (primary):** quick to train, handles non-linear relationships, easy to explain through feature importance. Recommended for the demo.
- **XGBoost (optional):** stronger alternative if time allows.
- **LSTM (optional experiment only):** not on the critical path.

Multi-horizon strategy: train a separate model per horizon (three targets), keeping each simple and independently evaluable.

Confidence range: use the spread of predictions across the Random Forest's individual trees (for example 10th to 90th percentile) as lower and upper bounds. This is an approximate uncertainty band and is labelled as such.

### 3.3 Features

- Current water level.
- Lagged levels (1, 2, 3 and 6 readings ago).
- Rise rate (difference between consecutive readings, and averaged over a short window).
- Current rainfall intensity.
- Rolling accumulated rainfall over recent intervals.
- Upstream node water level where available.
- Soil moisture where available (otherwise missing-value handling, such as a constant fill plus an availability flag).
- Node identifier, encoded.

### 3.4 Training data

Three sources, each labelled in the data and in the UI:
1. Historical observations where they can be obtained (rainfall records, any public level records).
2. Synthetic scenarios generated from clearly stated rules (rain event shapes, drainage-limited response, lag between upstream and downstream).
3. The prototype's own recorded data from the physical station and virtual nodes.

A `data_origin` column marks each row as historical, synthetic or prototype. A few minutes of prototype data cannot establish real-world accuracy, and the product says so.

### 3.5 Pipeline

1. **Collect:** export telemetry and synthetic runs into a unified DataFrame.
2. **Clean:** drop invalid-flagged readings, resample to a fixed interval, fill short gaps conservatively.
3. **Confirm sampling interval:** decide how many rows correspond to 15, 30 and 60 minutes before building targets (for example at a 10-second interval that is 90, 180 and 360 rows; at a coarser aggregation interval, fewer).
4. **Build features:** lags, rise rates, rolling rainfall, upstream level.
5. **Build targets:** shift the water level column forward by the row count for each horizon.
6. **Split chronologically:** training, validation, test in time order. Never shuffle.
7. **Train:** fit Linear Regression and Random Forest (and XGBoost if time allows) for each horizon; tune a few hyperparameters on validation (number of trees, max depth, minimum samples per leaf).
8. **Evaluate:** MAE and RMSE on the test set, always next to the persistence baseline.
9. **Select and save:** serialise the best model per horizon with joblib and register it with version, training data description, metrics and training time.
10. **Serve:** the forecast service loads models at startup and predicts whenever new readings arrive.
11. **Log:** store each prediction with the time it was made and the time it applies to, then later compare with actuals.

### 3.6 Serving behaviour

- Input: the recent window of readings for a node (plus upstream node).
- Output: predicted level per horizon, lower and upper bounds, model version.
- If there is too little recent data, return an explicit "insufficient data" response.
- Predictions are triggered automatically on new readings and on demand via REST.
- Scenario runs reuse the same forecast-to-risk-to-simulation path with scenario-generated input curves.

### 3.7 Honesty and evaluation notes

- The model must beat, or be honestly reported against, the persistence baseline.
- Metrics are shown in the UI beside the baseline.
- Model version, training data description and training time are visible in the interface.
- Risk weights and thresholds are decision rules, not validated coefficients.

### 3.8 Optional extensions (not on the critical path)

- Satellite image analysis for flood extent.
- Hydraulic modelling with HEC-RAS 2D.
- LSTM or sequence models.

---

## 4. IoT Layer

**Tech tags:** `ESP32` · `C++ (Arduino framework)` · `MQTT` · `Wi-Fi`

### 4.1 Hardware components

- **ESP32 DevKit V1:** microcontroller with built-in Wi-Fi.
- **JSN-SR04T waterproof ultrasonic sensor:** measures distance from sensor to water surface.
- **Tipping-bucket rain gauge with reed switch:** each tip produces one pulse.
- **0.96 inch OLED display (I2C):** shows level, rainfall, Wi-Fi status and node name.
- **Buzzer, red LED, green LED (with 220 ohm resistors):** local warning outputs.
- **Resistor divider on the ECHO line:** brings a possible 5 V echo signal down to about 3.3 V for the ESP32.
- **USB power supply, breadboard, jumper wires.**
- **Miniature river channel:** transparent container or acrylic channel with the sensor mounted above it.

### 4.2 Pin allocation

- GPIO 5: ultrasonic trigger
- GPIO 18: ultrasonic echo (through voltage divider)
- GPIO 19: rain gauge pulse input (internal pull-up)
- GPIO 23: buzzer
- GPIO 25: red LED
- GPIO 26: green LED
- GPIO 21: OLED SDA
- GPIO 22: OLED SCL

Verify the real board pinout and the sensor module voltage before wiring.

### 4.3 Firmware behaviour

**Water level**
- Take several distance readings per cycle and use the median.
- Water level = calibrated sensor height above channel floor − measured distance + calibration offset.
- Discard impossible readings and flag them invalid rather than sending them as real values.

**Rainfall**
- Count bucket tips with an interrupt, debounced (ignore pulses closer together than a short minimum interval).
- Rainfall (mm) = tips × mm per tip for that gauge (for example 0.2).
- Intensity (mm/hr) = rainfall over a rolling time window scaled to one hour.

**Sampling and sending**
- Sample every 5 to 10 seconds during demonstration.
- Publish JSON over MQTT with node code, water level, rainfall intensity, timestamp and source tag `iot`. HTTP POST is the fallback.
- If Wi-Fi drops, keep measuring, show readings locally, store a ring buffer of readings in memory and send them when the connection returns.
- Time sync via NTP so timestamps are consistent.

**Local warning (works with no network)**
- OLED shows level, rainfall intensity, Wi-Fi status, node name, refreshed each cycle.
- Green LED on below the warning threshold.
- Red LED and buzzer on at or above the threshold, off once the level falls below threshold minus a hysteresis margin so the buzzer does not toggle at the edge.
- Repeated invalid readings produce a sensor-fault message on the OLED and a distinct LED fault pattern, so a broken sensor is never read as safe.
- Thresholds are configurable constants in firmware.

### 4.4 Example telemetry message (fields)

- node_code
- water_level_cm
- rainfall_mm_hr
- timestamp (UTC)
- source (`iot`)
- optional: battery_voltage, soil_moisture, flow_rate
- valid flag (invalid readings are flagged, not sent as normal values)

### 4.5 Connectivity setup

- Local Wi-Fi hotspot or router with the laptop running the MQTT broker and backend, so the hardware demo works without internet.
- MQTT broker (Mosquitto) on the laptop; the ESP32 connects to its local IP.
- Optional device-side logging for later replay.

### 4.6 Virtual sensors (software IoT)

- Three to five virtual nodes with codes, locations, roles (upstream, midstream, downstream) and baseline levels.
- Publish to the same MQTT topic format at the same cadence, with source tag `simulated`.
- React to the physical node using the delay and attenuation configuration described in Section 2.3.

### 4.7 Demo readiness

- Calibrate the sensor height and offset with a ruler before the demo.
- Test pouring water and hand-tipping the rain gauge to confirm the pipeline end to end.
- Keep a spare ESP32 and sensor, and rely on a fallback virtual node and Replay mode if hardware fails.

---

## 5. Data Sources

All datasets are preprocessed before the demo and stored locally, so the demo never depends on a live download. Check each provider's current access method, licence and terms before use.

### 5.1 Live and generated data

- **ESP32 physical station:** primary live source for water level and local rainfall. Tag: `iot`.
- **Virtual sensor engine:** simulated readings. Tag: `simulated`.
- **User scenarios:** what-if inputs entered in the simulator. Tag: `scenario`.

### 5.2 External sources (one-time download and preprocessing)

**Terrain elevation**
- Copernicus GLO-30 DEM (about 30 m) and NASA SRTM (about 30 m or 90 m) are the candidate elevation sources. Accessible through OpenTopography or the providers' own portals; some require a free account.
- Use: build the terrain grid for the flood simulation and the 3D terrain fallback.
- Limitation: coarse resolution cannot represent individual streets, drains or building thresholds. This is stated in the assumptions panel.

**Roads, buildings and river geometry**
- OpenStreetMap data, queried through the Overpass API or a regional extract (for example from Geofabrik), limited to the study-area bounding box.
- Use: road network, building footprints (with height or levels tags where present, otherwise a default height), river or waterway centreline, and the base map.
- Limitation: building heights and some features may be missing or incomplete.

**Map tiles and 3D**
- OpenStreetMap tiles for the Leaflet base map (observe the tile usage policy; consider a self-hosted or cached alternative for the demo).
- Cesium ion (free tier) for Cesium World Terrain and optional OSM Buildings, which requires an access token. A preprocessed local terrain model is the fallback.

**Historical and reference rainfall**
- NASA GPM IMERG satellite rainfall (about 10 km resolution), used only for training-data context and regional reference. It requires a NASA Earthdata account.
- India Meteorological Department (IMD) gridded or station rainfall records, where obtainable, for historical rain-event shapes.
- Limitation: too coarse for street-level rainfall, so the local rain gauge remains the primary live rainfall input.

**Optional forecast rainfall context**
- Open-Meteo API (no key required for basic use) can supply short-term rainfall forecasts as an optional extra feature or context display. It is not required for the core demo and must be cached or disabled in offline mode.

### 5.3 Data we do not assume

- A public live Mithi River water-level API. None is assumed to exist.
- Official BMC drainage network data. If obtainable it can improve the connectivity mask; otherwise drainage is represented by the stated simplified assumptions.

### 5.4 Labelling

Each dataset is registered in the data sources table with its type (real-time physical, historical or reference, simulated), provider, resolution, licence note and limitation text. The About This Data panel reads from this table.

---

## 6. Database Schema

**Database:** PostgreSQL with the PostGIS extension. All timestamps are stored in UTC as `timestamptz`. Geometry uses SRID 4326 unless noted.

The schema has 16 tables, grouped by purpose.

- **Reference and configuration (3):** study_area, sensor_nodes, node_relationships
- **Time-series and quality (2):** telemetry_readings, rejected_readings
- **AI/ML (3):** model_registry, forecasts, risk_assessments
- **Geospatial base data (2):** roads, buildings
- **Simulation and scenarios (3):** scenarios, flood_layers, flood_impacts
- **Alerts (1):** alerts
- **Demo support and metadata (2):** replay_recordings, data_sources

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
```

### 6.1 study_area

Purpose: stores the single corridor definition (also mirrored in the shared config file). One row.

```sql
CREATE TABLE study_area (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    min_lat         DOUBLE PRECISION NOT NULL,
    min_lon         DOUBLE PRECISION NOT NULL,
    max_lat         DOUBLE PRECISION NOT NULL,
    max_lon         DOUBLE PRECISION NOT NULL,
    boundary        GEOMETRY(POLYGON, 4326) NOT NULL,
    river_line      GEOMETRY(LINESTRING, 4326),
    grid_cell_size_m DOUBLE PRECISION NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 6.2 sensor_nodes

Purpose: registry of every physical and virtual sensor.

```sql
CREATE TABLE sensor_nodes (
    id                  SERIAL PRIMARY KEY,
    node_code           TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    node_type           TEXT NOT NULL CHECK (node_type IN ('physical','virtual')),
    role                TEXT CHECK (role IN ('upstream','midstream','downstream')),
    location            GEOMETRY(POINT, 4326) NOT NULL,
    reference_elevation_m DOUBLE PRECISION,
    calibration_offset_cm DOUBLE PRECISION NOT NULL DEFAULT 0,
    baseline_level_cm   DOUBLE PRECISION,
    warning_threshold_cm DOUBLE PRECISION,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_nodes_location ON sensor_nodes USING GIST (location);
```

### 6.3 node_relationships

Purpose: documents the simplified upstream/downstream propagation rule between nodes.

```sql
CREATE TABLE node_relationships (
    id               SERIAL PRIMARY KEY,
    upstream_node_id INTEGER NOT NULL REFERENCES sensor_nodes(id),
    downstream_node_id INTEGER NOT NULL REFERENCES sensor_nodes(id),
    delay_seconds    INTEGER NOT NULL,
    attenuation      DOUBLE PRECISION NOT NULL CHECK (attenuation >= 0),
    distance_m       DOUBLE PRECISION,
    notes            TEXT,
    UNIQUE (upstream_node_id, downstream_node_id)
);
```

### 6.4 telemetry_readings

Purpose: main time-series table of accepted readings.

```sql
CREATE TABLE telemetry_readings (
    id                BIGSERIAL PRIMARY KEY,
    node_id           INTEGER NOT NULL REFERENCES sensor_nodes(id),
    recorded_at       TIMESTAMPTZ NOT NULL,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    water_level_cm    DOUBLE PRECISION NOT NULL,
    rainfall_mm_hr    DOUBLE PRECISION NOT NULL DEFAULT 0,
    flow_rate         DOUBLE PRECISION,
    soil_moisture     DOUBLE PRECISION,
    battery_voltage   DOUBLE PRECISION,
    source            TEXT NOT NULL CHECK (source IN ('iot','simulated','replay')),
    data_mode         TEXT NOT NULL DEFAULT 'live' CHECK (data_mode IN ('live','replay','scenario'))
);
CREATE INDEX idx_telemetry_node_time ON telemetry_readings (node_id, recorded_at DESC);
```

### 6.5 rejected_readings

Purpose: audit log of invalid readings, so bad data is logged, not silently dropped. Also feeds the sensor fault alert.

```sql
CREATE TABLE rejected_readings (
    id            BIGSERIAL PRIMARY KEY,
    node_code     TEXT,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_payload   JSONB NOT NULL,
    reject_reason TEXT NOT NULL
);
CREATE INDEX idx_rejected_node_time ON rejected_readings (node_code, received_at DESC);
```

### 6.6 model_registry

Purpose: versions of trained models with their metrics and training descriptions.

```sql
CREATE TABLE model_registry (
    id                    SERIAL PRIMARY KEY,
    model_name            TEXT NOT NULL,
    version               TEXT NOT NULL,
    horizon_minutes       INTEGER NOT NULL CHECK (horizon_minutes IN (15,30,60)),
    algorithm             TEXT NOT NULL,
    artifact_path         TEXT NOT NULL,
    training_data_description TEXT NOT NULL,
    mae                   DOUBLE PRECISION,
    rmse                  DOUBLE PRECISION,
    baseline_mae          DOUBLE PRECISION,
    baseline_rmse         DOUBLE PRECISION,
    trained_at            TIMESTAMPTZ NOT NULL,
    is_active             BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (model_name, version, horizon_minutes)
);
```

### 6.7 forecasts

Purpose: every prediction, with when it was made and when it applies, for later comparison with actuals.

```sql
CREATE TABLE forecasts (
    id               BIGSERIAL PRIMARY KEY,
    node_id          INTEGER NOT NULL REFERENCES sensor_nodes(id),
    model_id         INTEGER REFERENCES model_registry(id),
    scenario_id      INTEGER REFERENCES scenarios(id),
    made_at          TIMESTAMPTZ NOT NULL,
    target_time      TIMESTAMPTZ NOT NULL,
    horizon_minutes  INTEGER NOT NULL CHECK (horizon_minutes IN (15,30,60)),
    predicted_level_cm DOUBLE PRECISION NOT NULL,
    lower_bound_cm   DOUBLE PRECISION,
    upper_bound_cm   DOUBLE PRECISION,
    input_source     TEXT NOT NULL CHECK (input_source IN ('live','simulated','replay','scenario')),
    status           TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','insufficient_data'))
);
CREATE INDEX idx_forecasts_node_time ON forecasts (node_id, made_at DESC);
```

### 6.8 risk_assessments

Purpose: risk score and level per node and horizon (horizon 0 means current).

```sql
CREATE TABLE risk_assessments (
    id               BIGSERIAL PRIMARY KEY,
    node_id          INTEGER NOT NULL REFERENCES sensor_nodes(id),
    forecast_id      BIGINT REFERENCES forecasts(id),
    scenario_id      INTEGER REFERENCES scenarios(id),
    assessed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    horizon_minutes  INTEGER NOT NULL CHECK (horizon_minutes IN (0,15,30,60)),
    risk_score       DOUBLE PRECISION NOT NULL CHECK (risk_score BETWEEN 0 AND 1),
    risk_level       TEXT NOT NULL CHECK (risk_level IN ('low','moderate','high','severe')),
    predicted_level_cm DOUBLE PRECISION,
    level_component  DOUBLE PRECISION,
    rise_rate_component DOUBLE PRECISION,
    rainfall_component DOUBLE PRECISION,
    soil_component   DOUBLE PRECISION,
    primary_driver   TEXT
);
CREATE INDEX idx_risk_node_time ON risk_assessments (node_id, assessed_at DESC);
```

### 6.9 roads

Purpose: preprocessed road geometries for impact counting and map overlay.

```sql
CREATE TABLE roads (
    id          BIGSERIAL PRIMARY KEY,
    osm_id      BIGINT,
    name        TEXT,
    road_type   TEXT,
    geometry    GEOMETRY(LINESTRING, 4326) NOT NULL,
    min_elevation_m DOUBLE PRECISION
);
CREATE INDEX idx_roads_geom ON roads USING GIST (geometry);
```

### 6.10 buildings

Purpose: preprocessed building footprints for impact counting, 2D overlay and 3D extrusion.

```sql
CREATE TABLE buildings (
    id            BIGSERIAL PRIMARY KEY,
    osm_id        BIGINT,
    name          TEXT,
    building_type TEXT,
    height_m      DOUBLE PRECISION,
    height_is_estimated BOOLEAN NOT NULL DEFAULT TRUE,
    ground_elevation_m DOUBLE PRECISION,
    footprint     GEOMETRY(POLYGON, 4326) NOT NULL
);
CREATE INDEX idx_buildings_geom ON buildings USING GIST (footprint);
```

### 6.11 scenarios

Purpose: saved what-if scenarios with their inputs and the assumptions used.

```sql
CREATE TABLE scenarios (
    id                  SERIAL PRIMARY KEY,
    name                TEXT,
    preset              TEXT,
    rainfall_mm_hr      DOUBLE PRECISION NOT NULL,
    initial_level_cm    DOUBLE PRECISION NOT NULL,
    drainage_capacity_pct DOUBLE PRECISION NOT NULL CHECK (drainage_capacity_pct BETWEEN 0 AND 100),
    duration_minutes    INTEGER NOT NULL,
    assumptions         JSONB NOT NULL,
    status              TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','completed','failed')),
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);
```

### 6.12 flood_layers

Purpose: simulation output polygons per time step, for live baseline or a scenario. Consumed by both the 2D and 3D views.

```sql
CREATE TABLE flood_layers (
    id               BIGSERIAL PRIMARY KEY,
    scenario_id      INTEGER REFERENCES scenarios(id),
    generated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    time_step_minutes INTEGER NOT NULL CHECK (time_step_minutes IN (0,15,30,60)),
    layer_type       TEXT NOT NULL CHECK (layer_type IN ('depth','risk','boundary')),
    geojson          JSONB NOT NULL,
    max_depth_m      DOUBLE PRECISION,
    flooded_area_m2  DOUBLE PRECISION,
    input_source     TEXT NOT NULL CHECK (input_source IN ('live','simulated','replay','scenario')),
    grid_resolution_m DOUBLE PRECISION
);
CREATE INDEX idx_flood_layers_lookup ON flood_layers (scenario_id, time_step_minutes, layer_type);
```

### 6.13 flood_impacts

Purpose: per-feature impact results (which roads and buildings are affected, and at what depth) for each layer.

```sql
CREATE TABLE flood_impacts (
    id               BIGSERIAL PRIMARY KEY,
    flood_layer_id   BIGINT NOT NULL REFERENCES flood_layers(id) ON DELETE CASCADE,
    feature_type     TEXT NOT NULL CHECK (feature_type IN ('road','building')),
    feature_id       BIGINT NOT NULL,
    estimated_depth_m DOUBLE PRECISION NOT NULL,
    risk_level       TEXT NOT NULL CHECK (risk_level IN ('low','moderate','high','severe'))
);
CREATE INDEX idx_impacts_layer ON flood_impacts (flood_layer_id, feature_type);
```

### 6.14 alerts

Purpose: all alerts, live and scenario, with acknowledgement and history.

```sql
CREATE TABLE alerts (
    id              BIGSERIAL PRIMARY KEY,
    node_id         INTEGER REFERENCES sensor_nodes(id),
    scenario_id     INTEGER REFERENCES scenarios(id),
    alert_type      TEXT NOT NULL CHECK (alert_type IN
                      ('threshold','predicted_risk','rapid_rise','communication_failure','sensor_fault')),
    severity        TEXT NOT NULL CHECK (severity IN ('information','warning','high','critical')),
    message         TEXT NOT NULL,
    trigger_data    JSONB,
    is_scenario_alert BOOLEAN NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    cleared_at      TIMESTAMPTZ,
    acknowledged    BOOLEAN NOT NULL DEFAULT FALSE,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by TEXT
);
CREATE INDEX idx_alerts_active ON alerts (is_active, severity, created_at DESC);
CREATE INDEX idx_alerts_node_type ON alerts (node_id, alert_type, is_active);
```

### 6.15 replay_recordings

Purpose: recorded sensor sessions and scripted scenarios for Replay mode.

```sql
CREATE TABLE replay_recordings (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    origin          TEXT NOT NULL CHECK (origin IN ('physical_recording','scripted_scenario')),
    duration_seconds INTEGER NOT NULL,
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 6.16 data_sources

Purpose: registry of every dataset, powering the About This Data panel and source labelling.

```sql
CREATE TABLE data_sources (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL CHECK (category IN ('real_time_physical','historical_reference','simulated')),
    provider        TEXT,
    resolution      TEXT,
    licence_note    TEXT,
    limitation      TEXT NOT NULL,
    last_updated    TIMESTAMPTZ
);
```

### 6.17 Relationships in words

- A sensor node has many telemetry readings, forecasts, risk assessments and alerts.
- A node can be linked to other nodes through node_relationships, which defines upstream and downstream propagation.
- A forecast belongs to one node and optionally one model version and one scenario; a risk assessment optionally references the forecast it was computed from.
- A scenario has many flood layers, forecasts, risk assessments and scenario alerts. Rows with a null scenario_id represent live or replay baseline data.
- A flood layer has many flood impact rows, each pointing to a road or building by identifier.
- Roads, buildings and the study area are static preprocessed geospatial data.

### 6.18 Retention and housekeeping notes

- Raw telemetry is kept for the hackathon duration; a demo reset action clears telemetry, forecasts, risk, alerts, flood layers and scenarios and restores clean demo starting state while keeping static geospatial and registry data.
- Redis holds only cacheable or transient state, never the single source of truth.

---

## 7. Overall System Architecture

This final section ties the layers together as a single system design, described in words.

### 7.1 Layers at a glance

1. **Edge layer:** the ESP32 station (with OLED, LEDs, buzzer) and the virtual sensor generator.
2. **Messaging layer:** an MQTT broker (Mosquitto) carrying telemetry, alert and status topics.
3. **Application layer:** the Python backend (FastAPI) containing ingestion, forecasting, risk, simulation, scenarios, alerts, realtime gateway and replay control.
4. **Data layer:** PostgreSQL with PostGIS for durable and geospatial data, Redis for caching and pub/sub, local files for the DEM, terrain grids and serialised ML models.
5. **Presentation layer:** the React and TypeScript web app with the dashboard, prediction page, Leaflet 2D map, CesiumJS 3D twin, simulator and alerts pages.

### 7.2 End-to-end flow for the key demo moment (physical sensor to virtual world)

1. A judge pours water into the miniature channel.
2. The ESP32 reads the ultrasonic distance, applies the median filter and calibration, and updates the OLED and LEDs immediately. If the warning threshold is crossed, the red LED and buzzer activate locally with no network required.
3. The ESP32 publishes the reading over MQTT to the local broker.
4. The ingestion service validates and stores the reading in PostgreSQL and caches the latest value in Redis.
5. The virtual sensor engine sees the physical change and, after the configured delay and with attenuation, adjusts the downstream virtual nodes.
6. The forecast service builds features from the recent window and predicts +15, +30 and +60 minute levels for each active node.
7. The risk service computes the score and level for now and each horizon, with the primary driver.
8. The flood simulation engine converts the forecast levels to water-surface elevation, interpolates along the river, computes depths, applies the connectivity mask, classifies risk, vectorises the layers for Now, +15, +30, +60, and counts affected roads and buildings. Results are cached in Redis and stored in PostgreSQL.
9. The alert engine evaluates its rules and creates, deduplicates or clears alerts.
10. The realtime gateway pushes the new reading, forecasts, risk, flood layer updates and any alert to the browser over WebSocket.
11. The dashboard chart advances, the 2D map flood layer updates, the 3D twin water surface and affected buildings change, and the alert appears with a toast and badge, all without a page refresh.

### 7.3 What-if scenario flow

1. The user adjusts rainfall, initial level, drainage capacity and duration, or picks a preset, then clicks Run Scenario.
2. The frontend sends the inputs to the scenario endpoint and shows a loading state.
3. The backend creates a scenario record, builds a scenario water-level curve from the stated simplified assumptions, and runs it through the forecast-compatible pipeline (risk, flood simulation, impact counting).
4. Results are stored with the scenario identifier and returned with the layers, depths, risk categories, timestamps and the assumptions used.
5. The frontend shows the scenario banner, renders the layers on the 2D map and 3D twin through the same timeline control, and lists the assumptions.
6. Scenario Comparison runs the baseline through the same engine and reports differences in peak level, flooded area, roads, buildings and highest risk.
7. Scenario alerts are flagged and kept separate from live alerts. Reset returns the interface to the live baseline.

### 7.4 Operating modes

- **Live mode:** real ESP32 and virtual sensor data flow through the pipeline.
- **Replay mode:** recorded data from the replay table is published through the same ingestion path, flagged with source and data mode `replay`, so the full demo works with no hardware. The mode indicator shows Replay at all times.
- **Scenario mode:** what-if results are displayed with a banner; live data keeps flowing in the background but is not mixed with scenario outputs.

### 7.5 Deployment for the hackathon

- One laptop acts as the local server running the MQTT broker, the Python backend, PostgreSQL with PostGIS, and Redis (for example through Docker Compose).
- The React app is served as static files by the backend or a dev server on the same laptop.
- A local Wi-Fi hotspot connects the ESP32 to the laptop so no internet is needed for the hardware demo.
- All terrain, roads, buildings and trained models are preloaded locally.
- Cesium ion and OpenStreetMap tiles need internet; offline fallbacks are the local terrain model and cached tiles, and the 2D map continues to work on its own.

### 7.6 Reliability and fallback design

- Device level: local warnings work without network; readings are buffered during outages.
- Pipeline level: invalid data is rejected and logged; the system continues running.
- Sensor level: a virtual fallback node can replace the physical node; stale and offline states are shown, never hidden.
- Model level: an explicit "insufficient data" state replaces any made-up forecast.
- Simulation level: terrain grids are precomputed and cached; a coarser grid is used if a run would be slow.
- Frontend level: automatic WebSocket reconnect with a visible indicator; 3D falls back to local terrain, then to the 2D map.
- Demo level: Replay mode, a single reset action, and a prepared backup demo video as the final safety net.

### 7.7 Build order (aligned with the PRD)

1. Vertical slice first: ESP32 reading, MQTT, ingestion, storage, forecast, and a changed layer on the 2D map.
2. Then the real-time dashboard, virtual sensors, risk scoring and the timeline control.
3. Then the what-if simulator and comparison.
4. Then the 3D twin polish, the alert system and alerts page.
5. Finally Replay mode, fallbacks, reset and the backup video.

### 7.8 Honesty statements built into the architecture

- Every reading and layer carries a source field: live hardware, simulated, historical or scenario.
- The flood simulation is a simplified elevation-based inundation estimate, not a hydraulic model, and does not compute flow velocity.
- Elevation and satellite rainfall resolutions are coarse and limit accuracy; this is stated in the interface.
- Risk weights and thresholds are starting assumptions requiring local calibration.
- Prototype data cannot establish real-world forecast accuracy.
- The outputs are a preliminary scenario visualization, not a street-level prediction.