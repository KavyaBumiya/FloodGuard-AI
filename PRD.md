# FLOODGUARD AI — Product Requirements Document

Event: IIT Bombay Hackathon
Build window: 36 to 48 hours
Project: IoT-Powered AI Flood Prediction and 3D Digital Twin for Mumbai
Tagline: Predict the flood. Visualize the impact. Prepare before disaster strikes.
Document status: Hackathon build version 1.0

---

## 1. Purpose of This Document

This document defines what FLOODGUARD AI must do. It describes the problem we are solving and every feature of the product in detail, including the small ones, so that every team member (hardware, AI/ML, GIS, backend, frontend) can build in parallel without waiting on each other.

This is a hackathon PRD. That means three things shape every decision in it:

1. Time is the scarcest resource. Everything is scoped to be achievable in 36 to 48 hours by a team of 3 to 5 people.
2. The demo is the product. A judge should be able to watch a physical event (water poured into a miniature channel) change a virtual world (dashboard, 2D map, 3D twin) and then change the predicted future.
3. Honesty matters. Where something is simplified, simulated, or illustrative, the product labels it as such. A smaller thing that is truthful and works beats a bigger thing that is faked.

---

## 2. The Problem We Are Solving

### 2.1 Problem statement

Flood management authorities in dense, flood-prone cities like Mumbai mostly see floods as they are happening. They can see that a river or drain is already high, but they cannot easily see what it will look like in the next 15, 30 or 60 minutes, which roads and buildings will be affected as the water rises, or how the situation would change if rainfall suddenly became more intense.

The core problem is that flood monitoring is reactive, scattered and hard to interpret, when decisions need to be predictive, connected and visual.

### 2.2 Breaking the problem into its parts

**Problem A: Monitoring tells you the present, not the near future.**
A water-level reading of 145 cm is just a number. Nobody on the ground knows from that number alone whether the level will be 150 cm or 200 cm in an hour. Authorities lose the lead time they need to issue warnings, close roads or move people.

**Problem B: A water level does not translate into a map of impact.**
Even when a future water level is known, there is no quick way to convert it into which streets, low-lying areas and buildings will actually be under water. Officials end up relying on experience and guesswork rather than a spatial picture.

**Problem C: Authorities cannot rehearse "what if" scenarios.**
Planning for a 50 mm/hr downpour versus an 80 mm/hr downpour, or for blocked drains versus working drains, usually means static reports and past-event memory. There is no interactive way to ask "what happens if conditions get worse?" and see an answer.

**Problem D: Sensor data, rainfall data and geography live in separate silos.**
Rain gauges, river sensors, terrain maps and building footprints are normally viewed in different tools. Nobody sees them as one connected, continuously updating picture of a place.

**Problem E: Raw data is not trustworthy unless its source is clear.**
In many dashboards it is unclear which numbers are real measurements, which are historical records and which are simulated. That makes decision-makers either over-trust or under-trust the system.

### 2.3 Who feels this problem

**Primary user: Disaster management officer or control-room operator.**
Sits in a monitoring room, needs to know what is happening now, what will happen soon, and which locations need attention first. Needs a clear view, not a data dump.

**Secondary user: Urban planner or civic infrastructure engineer.**
Wants to test how changes in rainfall or drainage capacity affect flooding in a corridor, so they can plan interventions before monsoon season.

**Tertiary user: Hackathon judge (for this build).**
Needs to understand within minutes what the product does, see it working live, and be able to interact with it themselves by changing a parameter.

### 2.4 Our focus area

We do not attempt to solve flooding for all of Mumbai. We solve it for one compact, well-chosen corridor: a 1 to 2 km section of the Mithi River around the BKC and Kurla region. This area gives us a real river, nearby roads and buildings, low-lying zones, and a natural upstream/downstream relationship between sensors. A small area done well makes a far more convincing demonstration than a large area done shallowly.

### 2.5 What success looks like for the problem

A user can look at one screen and understand, in this order: what the water is doing right now, what it is predicted to do over the next hour, which parts of the map are at risk because of that, and how the picture changes if conditions get worse. And at every step, the user knows which data is real, historical or simulated.

---

## 3. Feature Overview

FLOODGUARD AI is made of the following features. Each is described in full detail in Section 4.

1. Study area definition (the Mithi River BKC–Kurla corridor)
2. Physical IoT sensor station
3. Local on-device warning (OLED display, LEDs, buzzer)
4. Virtual sensor network
5. Data source labeling (real, historical, simulated)
6. Telemetry ingestion and storage
7. Real-time data delivery to the frontend
8. Command centre dashboard
9. Water-level forecasting (15, 30 and 60 minutes)
10. Flood-risk scoring
11. AI prediction page
12. Flood simulation engine (elevation-based inundation)
13. 2D flood map
14. 3D digital twin
15. Forecast timeline control
16. What-if scenario simulator
17. Scenario comparison
18. Alert system
19. Alerts and monitoring page
20. Demo mode, replay and fallbacks

---

## 4. Detailed Feature Specifications

### Feature 1: Study Area Definition

**What it is.** A fixed, pre-selected geographical corridor along the Mithi River in the BKC–Kurla region, 1 to 2 km long, which is the single area the whole product operates on.

**Why we need it.** Every other feature (sensor placement, terrain, flood simulation, 2D map, 3D twin) depends on a bounded area. Without a fixed corridor, the GIS work becomes unbounded and the 3D rendering becomes too heavy.

**Detailed behaviour.**
- The area is defined once as a bounding box (minimum and maximum latitude and longitude) stored in a single configuration file used by all modules.
- The area contains three kinds of geographical data: terrain elevation, roads and buildings, and the river geometry.
- The terrain, roads and buildings are preprocessed before the hackathon demo and stored locally, so the demo does not depend on a live download.
- The map initially opens centred on this corridor at a zoom level where the whole corridor is visible.
- Map interaction (panning and zooming) is limited to a reasonable range around the corridor so a user cannot get lost.

**Notes and limits.**
- A coarse elevation model (for example 30 m or 90 m resolution) is good for regional context but cannot represent individual streets, drains or building thresholds. The product must state this limitation in the assumptions panel.
- We do not claim street-level accuracy. We claim a preliminary scenario visualization.

**Acceptance criteria.**
- One configuration value defines the area, and every module reads from it.
- The 2D map, the 3D twin and the simulation all cover the same area and line up with each other.

---

### Feature 2: Physical IoT Sensor Station

**What it is.** One real, working sensor node built from an ESP32 microcontroller, a waterproof ultrasonic sensor and a tipping-bucket rain gauge, placed over a miniature river channel (a transparent container or acrylic channel).

**Why we need it.** This is the centrepiece of the demo. It proves the system is connected to the physical world. When water is poured in, the digital world must visibly react.

**Components.**
- ESP32 DevKit V1 as the controller with Wi-Fi.
- JSN-SR04T waterproof ultrasonic sensor mounted above the channel to measure the distance to the water surface.
- Tipping-bucket rain gauge with a reed switch to count rainfall pulses.
- 0.96 inch OLED display for local readings.
- Buzzer and two LEDs (red and green) for local warnings.
- USB power supply, breadboard and jumper wires.
- A resistor divider on the ultrasonic ECHO line to bring a possible 5 V signal safely down to about 3.3 V for the ESP32 input.

**Pin allocation (for the build team).**
- GPIO 5: ultrasonic trigger
- GPIO 18: ultrasonic echo (through the voltage divider)
- GPIO 19: rain gauge pulse input (internal pull-up enabled)
- GPIO 23: buzzer control
- GPIO 25: red LED (with 220 ohm resistor)
- GPIO 26: green LED (with 220 ohm resistor)
- GPIO 21: OLED SDA
- GPIO 22: OLED SCL

The team must verify the real board pinout and the sensor module voltage before wiring.

**Water-level measurement.**
- The ultrasonic sensor measures the distance from the sensor to the water surface.
- The firmware converts distance to water level using a calibrated reference: water level equals the calibrated sensor height above the channel floor minus the measured distance.
- A calibration offset is stored in configuration so the level can be corrected without rewriting firmware.
- Several readings are taken per cycle and the median is used, so a single noisy reading does not cause a false spike.
- Readings outside a sensible range (for example impossible distances) are discarded and flagged as invalid rather than sent as real values.

**Rainfall measurement.**
- Every bucket tip creates a pulse that the firmware counts.
- Rainfall is calculated as the number of tips multiplied by the millimetres per tip for that specific gauge (for example 0.2 mm per tip, so 10 tips equals 2 mm).
- The firmware also converts the tip count over a time window into a rainfall intensity in millimetres per hour.
- Pulse input is debounced so a single tip is not counted multiple times.

**Sampling and sending.**
- The device samples every 5 to 10 seconds during testing and demonstration.
- Each reading is sent to the backend over Wi-Fi (MQTT or HTTP) with the node code, water level, rainfall intensity, a timestamp and the source tag "iot".
- If Wi-Fi drops, the device keeps measuring and shows readings locally, and sends buffered readings once the connection returns where memory allows.

**Acceptance criteria.**
- Pouring water into the channel changes the reported water level within one sampling cycle.
- Tipping the rain gauge by hand increments the rainfall count and is reflected in the backend.
- Invalid readings never reach the dashboard as normal values.

---

### Feature 3: Local On-Device Warning

**What it is.** A warning system on the sensor station itself that works even if the backend or internet is down.

**Detailed behaviour.**
- The OLED display shows the current water level, current rainfall intensity, Wi-Fi status and node name, refreshed every sampling cycle.
- The green LED is on when the level is below the warning threshold.
- The red LED turns on when the level crosses the configured warning threshold.
- The buzzer sounds when the threshold is crossed and stops when the level drops back below it (with a small margin, so the buzzer does not rapidly toggle on and off at the edge).
- The thresholds are configurable constants in firmware.
- If the sensor produces invalid readings repeatedly, the OLED shows a sensor-fault message and both LEDs indicate a fault state, so a broken sensor is never mistaken for a safe situation.

**Why it matters for the demo.** A judge pouring water into the channel hears and sees the physical station react immediately, before the dashboard even updates. It reinforces that this is real hardware.

**Acceptance criteria.**
- The red LED and buzzer activate at the threshold without any network connection.
- The OLED always shows the latest valid reading or a clear fault message.

---

### Feature 4: Mithi Simulation State

**What it is.** A simulated Mithi River state derived from the one real physical
input. The Vadodara demo sensor is not a Mithi station, and the application does
not create virtual sensor telemetry in live mode.

**Why we need it.** We cannot build five hardware stations in 48 hours, but we need to demonstrate multi-location intelligence, including how upstream conditions affect downstream conditions.

**Detailed behaviour.**
- The physical node's reading is converted by the configurable Demo Calibration /
  Simulation Mapping into a Mithi reference water level.
- Forecast, risk, flood extent, 2D map, and 3D twin all use that mapped level.
- No random sensor values or virtual sensor records are generated in live mode.

**Notes.** The upstream and downstream relationship is documented in a small configuration file so the team can explain it honestly: it is a simplified propagation rule, not a hydraulic model.

**Live hackathon input contract.** The physical station is located in the
Vadodara demo setup and is not a Mithi River station. When a recent `source =
"iot"` reading exists, the backend applies the configurable **Demo Calibration
/ Simulation Mapping** (`DEMO_INPUT_*` settings) to create the Mithi reference
level. The mapped value drives the simulated Mithi nodes, forecasts, risk, and
persisted flood layers. The original physical reading remains attached to the
Vadodara node; all Mithi node readings remain `source = "simulated"`. If no
recent physical reading exists, the interface says **WAITING FOR REAL IoT
INPUT** rather than presenting fallback values as live hardware data.

**Acceptance criteria.**
- The live application shows one real Vadodara input and a separate simulated Mithi state.
- A change at the physical node causes a visible change in mapped level, risk,
  forecast, flood extent, and the Digital Twin.
- The mapped Mithi state is labeled simulated and never labeled as hardware.

---

### Feature 5: Data Source Labeling

**What it is.** A consistent system that tags every reading and every map layer with where it came from, and shows that tag in the interface.

**The three categories.**
- Real-time physical: measured by the ESP32 station (source tag "iot").
- Historical or reference: rainfall records, terrain, roads, buildings and river geometry from external datasets.
- Simulated: virtual sensor readings and scenario results.

**Detailed behaviour.**
- Every database reading carries a source field.
- On the dashboard, each sensor card shows a small badge: Live Hardware or Simulated.
- On the maps, sensor markers use different outlines or icons for physical versus simulated nodes, and a legend explains them.
- Prediction and scenario outputs show a short "How this was produced" line stating whether the input was live, simulated or a user-entered scenario.
- A single "About this data" panel lists the datasets used and their known limitations, including the coarse resolution of satellite rainfall data and elevation data.

**Why it matters.** It protects the credibility of the whole project. It also directly answers a judge's most likely question: "Which of this is real?"

**Acceptance criteria.**
- No reading or layer is ever displayed without a clear indication of its source type.

---

### Feature 6: Telemetry Ingestion and Storage

**What it is.** The backend service that receives sensor readings, checks them, and stores them for use by the forecasting model, dashboard and simulation.

**Detailed behaviour.**
- The backend accepts readings from the ESP32 and from the virtual sensor generator through the same telemetry endpoint or the same message topic.
- A reading contains: node code, water level in cm, rainfall in mm per hour, optional flow rate, optional soil moisture, optional battery voltage, a timestamp, and the source tag.
- The backend validates each reading: the node must be registered, numeric values must be within plausible ranges, and the timestamp must be present and sensible. Invalid readings are rejected with a clear error and are logged, not silently dropped.
- Accepted readings are stored in the time-series table along with the time received.
- All timestamps are stored in UTC and converted to Indian Standard Time only when displayed.
- Sensor nodes are registered with a code, type, location (latitude and longitude), active status and calibration offset.
- The database is PostgreSQL with PostGIS, because the product needs both time-series data and geographical data.
- Data is indexed by sensor and timestamp so charts and model features load quickly.

**Supporting endpoints.**
- Register a sensor node.
- Submit a telemetry reading.
- List all sensor nodes.
- Fetch historical readings for one node.

**Acceptance criteria.**
- A reading from the ESP32 appears in the database within about a second of being sent.
- Bad data is rejected and logged, and the system keeps running.

---

### Feature 7: Real-Time Data Delivery to the Frontend

**What it is.** The mechanism that pushes new readings, predictions and alerts to the browser instantly, without the user refreshing the page.

**Detailed behaviour.**
- The ESP32 publishes readings to the backend through MQTT using a clear topic structure (one topic per node for telemetry, one for alerts, one for system status).
- The backend subscribes to those topics, stores the readings, and forwards updates to the web app through WebSockets (or Server-Sent Events).
- The frontend reconnects automatically if the connection drops and shows a visible "Reconnecting" indicator while it does.
- Each node shows a "last updated" time. If a node has not reported within a set period, it is shown as stale or offline.

**Acceptance criteria.**
- The dashboard graph advances without a page refresh when new data arrives.
- A dropped connection is visible to the user and recovers without a manual reload.

---

### Feature 8: Command Centre Dashboard

**What it is.** The home page. A single control-room style screen that summarises the state of the whole corridor.

**What it shows.**
- Number of active sensors and number offline or stale.
- A card for each sensor node with current water level, rainfall intensity, current risk level, source badge (Live Hardware or Simulated), and last-updated time.
- A live water-level line chart for the selected node, advancing in real time.
- A current rainfall indicator.
- A list of the highest-risk locations right now, ordered by risk.
- The latest alerts (a short list, linking to the full alerts page).
- A compact forecast summary for the selected node showing the 15, 30 and 60-minute predicted levels.
- A system status strip showing connection state, model version in use, and data mode (live, replay or scenario).

**Interaction details.**
- Clicking a sensor card selects it and updates the chart and forecast summary.
- Risk levels use a consistent colour scheme (low, moderate, high, severe) that is reused on every page.
- Colour is never the only signal: each level also has a text label so the interface is readable for colour-blind users and on projectors.
- The page uses a dark, high-contrast command-centre style so it is readable on a large screen during the demo.

**Acceptance criteria.**
- A new user can understand the current situation of the whole corridor within five seconds of opening the page.

---

### Feature 9: Water-Level Forecasting

**What it is.** The machine learning module that predicts the water level at each sensor 15, 30 and 60 minutes into the future.

**Inputs (features).**
- Current water level.
- Previous water levels at several lags (for example one, two, three and six readings ago).
- Rise rate (change in water level between readings).
- Current rainfall intensity.
- Rainfall accumulated over recent intervals.
- Upstream sensor water level, where an upstream node exists.
- Soil moisture, where available.
- Sensor or location identifier.

**Models.**
- Persistence baseline: the future level equals the current level. Every model must be compared against this.
- Linear Regression as a simple baseline.
- Random Forest as the main recommended model, because it trains quickly and is easy to explain.
- XGBoost as an optional stronger alternative if time allows.
- LSTM is an optional experiment only, and is not on the critical path.

**Training and evaluation rules.**
- Data is split chronologically into training, validation and test sets. Samples are never randomly shuffled, because that would leak future information into training.
- A separate target is built for each horizon: 15, 30 and 60 minutes. The team must confirm the sampling interval before choosing how many rows to shift for each target.
- Metrics reported are Mean Absolute Error and Root Mean Squared Error, always shown next to the persistence baseline.
- Training data is a combination of historical observations where they can be obtained, synthetic scenarios built from clearly stated rules, and the prototype's own recorded data. All synthetic data is labeled.
- A few minutes of prototype data cannot establish real-world accuracy, and the product says so.

**Serving behaviour.**
- A prediction service loads the saved model and returns forecasts for a node on request, and automatically whenever new readings arrive.
- The response contains the predicted level for each horizon, the model version, and a confidence range (lower and upper bound) where available.
- Predictions are stored with the time they were made and the time they apply to, so they can be compared with what actually happened.
- If the model cannot produce a forecast (for example too little recent data), the product says so clearly instead of showing a made-up number.

**Acceptance criteria.**
- Forecasts for 15, 30 and 60 minutes are available for every active node.
- The model beats or is honestly reported against the persistence baseline.
- The model version is visible in the interface.

---

### Feature 10: Flood-Risk Scoring

**What it is.** An explainable scoring method that turns forecast numbers into a risk category for each location.

**How it works.**
- Four values are each normalised between 0 and 1: water-level severity, rate of water-level increase, rainfall severity and soil saturation.
- The risk score is a weighted sum. The starting weights are 0.35 for water level, 0.30 for rise rate, 0.20 for rainfall and 0.15 for soil saturation.
- The score is mapped to four categories: Low, Moderate, High and Severe, using configurable cut-off values.
- Where soil moisture is not available, the soil saturation input uses a documented default and the weights are renormalised, so the score still works.

**Honesty rules.**
- The weights and thresholds are starting assumptions and prototype decision rules, not scientifically validated coefficients. The interface says this in the assumptions panel.
- Real thresholds would need calibration for each location.

**Output.**
- For each node and horizon: the risk score, the risk level, the predicted water level and the forecast horizon in minutes.

**Explainability detail.**
- Each risk result can show which of the four inputs contributed most to the score (for example "driven mainly by rapid rise rate"). This helps a user trust the result rather than see a black box.

**Acceptance criteria.**
- Every sensor has a risk level for the current time and for each forecast horizon.
- A user can see why a location is marked High.

---

### Feature 11: AI Prediction Page

**What it is.** The dedicated page for understanding the forecasts in depth.

**What it shows.**
- A chart of historical water level followed by predicted water level, with a clear visual break between past and future.
- Markers for the 15, 30 and 60-minute forecast points.
- A shaded forecast uncertainty band around the predicted values.
- The current risk score and risk level, with the contribution breakdown from Feature 10.
- Model evaluation metrics (MAE and RMSE) shown beside the persistence baseline.
- Model name, model version, training data description and when it was last trained.
- A node selector so the user can look at any sensor.

**Detailed behaviour.**
- The chart updates in real time as new readings arrive and predictions are regenerated.
- A short plain-language sentence summarises the situation, for example "Water level at this node is forecast to rise over the next hour; risk is expected to reach High in about 30 minutes."
- A note makes it clear when a node's data is simulated.

**Acceptance criteria.**
- The user can see past, present and predicted future in one chart for any node.
- Metrics and model version are visible on the page.

---

### Feature 12: Flood Simulation Engine

**What it is.** The backend module that turns predicted water levels into geographical flood maps. This is the bridge between "the water level will rise" and "these streets and buildings are affected".

**Approach for the hackathon.** A simplified elevation-based raster model. A more advanced hydraulic model (HEC-RAS 2D) is optional and is not a dependency.

**Step-by-step behaviour.**

*Grid creation.* The study area is divided into grid cells (for example 100 by 100 cells at 10 m resolution, if the elevation data supports it). Each cell stores a cell identifier, latitude, longitude, ground elevation, current water depth and risk level.

*Water surface estimate.* The forecast water level at each sensor is converted into a water-surface elevation using that station's calibrated reference elevation. Between stations, the water-surface elevation is interpolated along the river so the whole corridor has a continuous water surface.

*Depth calculation.* For each cell, depth equals the greater of zero and the water-surface elevation minus the ground elevation.

*Connectivity mask.* A low-lying cell is only flooded if it is hydraulically connected to the water source. The engine uses the river boundary, drainage connections and terrain barriers to avoid flooding disconnected low spots, which would look visually wrong.

*Risk classification.* Cells are classified by depth into risk levels, producing both a depth layer and a risk layer.

*Time steps.* The engine produces a separate flood layer for each of: current conditions, plus 15 minutes, plus 30 minutes and plus 60 minutes.

*Output format.* Each layer is exported as GeoJSON polygons (or a raster) that the 2D map and 3D twin both consume, so both views always agree with each other.

*Impact estimate.* For each time step, the engine counts the roads and buildings overlapping flooded cells and reports how many are affected and at what depth range.

*Performance.* Terrain grids are precomputed and cached. A scenario run must complete within a few seconds so the demo does not stall. If it would take longer, a coarser grid is used.

**Limitations that must be stated in the product.**
- This is a simplified inundation estimate, not a full hydraulic model. It does not solve momentum or mass conservation and does not calculate flow velocity.
- It is a preliminary scenario visualization.
- Accuracy is limited by the elevation data resolution.

**Acceptance criteria.**
- Given a set of forecast water levels, the engine returns flood layers for all four time steps.
- Disconnected low areas are not flooded.
- The same layers drive the 2D and 3D views.

---

### Feature 13: 2D Flood Map

**What it is.** An interactive map page built with Leaflet and OpenStreetMap that shows sensors, current flooding and predicted flooding.

**Map contents.**
- Base map of the corridor.
- Sensor markers, styled differently for the physical station and simulated stations, each coloured by current risk level.
- The flood-risk heatmap or depth polygons from the simulation engine.
- Road overlay and building overlay.
- The current flood boundary and the predicted flood boundaries for 15, 30 and 60 minutes.

**Interaction details.**
- Clicking a sensor marker opens a popup with node name, source badge, current water level, rainfall, risk level and the three forecast values.
- Clicking a flooded road or building shows its estimated flood depth and the time step.
- A layer control lets the user turn each layer on or off: sensors, heatmap, roads, buildings and flood boundaries.
- A legend explains the colours and the marker types.
- Roads or buildings that become affected in the selected time step are highlighted, and a side summary lists how many are affected.
- The map is linked to the forecast timeline control (Feature 15).

**Acceptance criteria.**
- A change in sensor data or in the selected time step visibly updates the flood layer.
- Layer toggles work independently and instantly.

---

### Feature 14: 3D Digital Twin

**What it is.** A 3D view of the corridor built with CesiumJS where the flood is visualised on real terrain with buildings and roads.

**Contents.**
- 3D terrain of the study area.
- The river channel.
- 3D extruded buildings from OpenStreetMap footprints.
- Roads draped on the terrain.
- Sensor stations placed as visible markers at their real positions.
- A semi-transparent water surface or flood overlay generated from the simulation layers, with depth reflected in the colour or height of the water.
- Predicted inundation for each time step.

**Interaction details.**
- Standard orbit, zoom and pan controls.
- Camera preset buttons: overview of the corridor, close-up of the physical sensor location, and a view along the river.
- Clicking a sensor or building shows a small information card (for a building: affected or not, and estimated depth; for a sensor: current values and risk).
- Affected buildings change colour when the flood layer reaches them.
- The 3D view is synchronised with the same forecast timeline as the 2D map, so changing the time step updates both.
- The water level and flood boundary change when new physical sensor readings arrive, which creates the key moment: the physical sensor has changed the state of the virtual world.

**Performance and fallback rules.**
- The 3D study area is kept small so it renders smoothly on the demo laptop.
- If Cesium or terrain loading is slow or unavailable, the product falls back to a preprocessed local terrain model, and if that also fails, the 2D map remains fully functional on its own.
- The team must not spend a disproportionate share of the build time on 3D polish before the sensor-to-backend-to-map pipeline works.

**Acceptance criteria.**
- The 3D view shows terrain, buildings, roads, sensors and a flood overlay for the selected time step.
- It stays in sync with the timeline and with live data.

---

### Feature 15: Forecast Timeline Control

**What it is.** A shared time control that lets the user move between now and the future.

**Detailed behaviour.**
- Four positions: Now, plus 15 minutes, plus 30 minutes, plus 60 minutes.
- Selecting a position updates the 2D map, the 3D twin, the affected roads and buildings count, and the forecast summary together.
- A play button steps automatically through the four positions as a short animation, so a presenter can show how the flood grows without clicking.
- The selected time step is always displayed in text (for example "Showing predicted conditions at +30 minutes") so the user never confuses a forecast with the current state.
- Forecast views carry a visible "Forecast" label and current views carry a "Current" label.

**Acceptance criteria.**
- Changing the time step updates both map views at once.
- A user can always tell whether they are looking at current or predicted conditions.

---

### Feature 16: What-If Scenario Simulator

**What it is.** The panel where an authority changes environmental conditions and sees a new flood scenario. This is the main innovation of the project and the strongest demo moment.

**Adjustable inputs.**
- Rainfall intensity, in millimetres per hour (for example 50).
- Initial water level, in centimetres (for example 150).
- Drainage capacity assumption, as a percentage (for example 70 percent).
- Simulation duration in minutes (for example 60).
- Optional scenario name.

**Controls.**
- Sliders with numeric entry for each input, with sensible minimum and maximum values and units displayed.
- A Run Scenario button.
- Preset scenario buttons for quick use during a demo: Normal Monsoon Day, Heavy Rain, Extreme Rainfall With Poor Drainage.
- A Reset button that returns to the live baseline.

**Behaviour.**
- When Run Scenario is clicked, the inputs are sent to the backend. The backend creates a scenario record, runs the forecasting and flood simulation using the scenario's assumptions, and returns the scenario identifier, the simulated flood layers, estimated depths, risk categories, forecast timestamps and the list of assumptions used.
- A loading state shows while it runs. If it fails, a clear message is shown and the previous result is kept.
- While a scenario is active, a clear banner states that the user is viewing a simulated what-if scenario and not live conditions.
- The scenario result appears on the 2D map, the 3D twin and the summary panel, using the same timeline control.
- Past scenarios are saved so they can be reopened.

**Honesty rules.**
- The simulator must not simply multiply rainfall by a percentage and claim it represents hydraulic flood propagation.
- It uses stated, simplified assumptions, and an Assumptions panel lists them in plain words (for example how rainfall and drainage capacity change the water level and how the water spreads).

**Acceptance criteria.**
- A judge can change rainfall and see a new flood boundary within a few seconds.
- The scenario banner and assumptions list are always visible when a scenario is shown.

---

### Feature 17: Scenario Comparison

**What it is.** A side-by-side or overlay comparison between the baseline (current or default conditions) and a what-if scenario.

**What it shows.**
- Predicted water-level curves for baseline and scenario on one chart.
- A split or toggleable map showing the baseline flood boundary and the scenario flood boundary, with the additional area flooded under the scenario highlighted.
- Summary numbers: peak predicted water level, difference in flooded area, number of additional roads affected, number of additional buildings affected, and the highest risk level reached in each case.
- A one-sentence automatic summary (for example "Under the scenario, 14 additional buildings and 3 more road segments are affected by the +60 minute mark").

**Acceptance criteria.**
- The user can see in one view exactly how much worse (or better) the scenario is than the baseline.

---

### Feature 18: Alert System

**What it is.** The rules and delivery logic that raise warnings when conditions become dangerous.

**Alert types.**
- Threshold alert: current water level crosses a configured level.
- Predicted risk alert: the forecast says a node will reach High or Severe within a forecast horizon.
- Rapid rise alert: the rise rate exceeds a configured limit.
- Communication failure alert: a node has stopped reporting.
- Sensor fault alert: a node keeps sending invalid readings.

**Severity levels.** Information, Warning, High and Critical, using the same colour and text scheme as the risk levels.

**Detailed behaviour.**
- Each alert has a node, a type, a severity, a plain-language message, a creation time and an acknowledged flag.
- Alerts are created automatically by the backend when rules trigger.
- To avoid alert floods, the same alert for the same node is not repeated while it is still active, and an alert clears automatically when the condition ends (it remains in the history).
- New alerts appear instantly in the dashboard through real-time delivery, with a visible badge count and a short on-screen notification.
- Critical alerts trigger a stronger visual treatment (for example a banner at the top of every page).
- An authority can acknowledge an alert, and acknowledged alerts stay in history with the acknowledgement time.
- Alerts created inside a what-if scenario are clearly marked as scenario alerts and never mixed up with real ones.

**Acceptance criteria.**
- Raising the physical water level past the threshold produces an alert on the dashboard without a refresh.
- Alerts can be acknowledged and remain in history.

---

### Feature 19: Alerts and Monitoring Page

**What it is.** The page that gives full detail on warnings and the health of the monitoring network.

**What it shows.**
- A list of active critical warnings.
- Predicted flood risks that are expected soon.
- Sensor communication failures and sensor faults.
- Acknowledgement status for each alert.
- An event history with filters by node, severity, type and time range.
- A network health overview showing each node, its status (online, stale or offline), last reading time and battery voltage where available.

**Interaction details.**
- Clicking an alert opens a detail view with the node, the readings that triggered it and a link to that node on the map.
- A user can acknowledge one alert or several at once.
- The history can be searched and sorted.

**Acceptance criteria.**
- A user can find any past alert and see what triggered it.
- Offline or faulty sensors are clearly visible, never hidden.

---

### Feature 20: Demo Mode, Replay and Fallbacks

**What it is.** The set of safeguards that make sure the live demonstration works even if something fails, because hackathon demos often fail from small causes (Wi-Fi, a loose wire, a slow laptop).

**Detailed behaviour.**
- Replay mode plays back previously recorded sensor data (from the physical station and from scripted scenarios) through the same pipeline as live data, so the whole product can be shown without the hardware.
- A visible mode indicator shows whether the system is in Live, Replay or Scenario mode, so replayed data is never passed off as live.
- A fallback virtual node can substitute for the physical node if the sensor stops working.
- Local logging on the device and a local gateway or local network setup lets the hardware demo work without internet.
- Simulation grids and terrain are precomputed so the demo does not depend on downloads.
- A prepared backup demo video is recorded as the final safety net.
- A single reset action returns all data and scenarios to a clean demo starting state.

**Acceptance criteria.**
- The full demo story can be performed in Replay mode with no hardware connected.
- The mode indicator is always accurate.

---

## 5. Hackathon Scope and Priorities

### 5.1 Must have (the six core features that define the demo)

- Live ESP32 sensor readings (Features 2, 3, 6)
- Real-time dashboard (Features 7, 8)
- Water-level forecasting (Feature 9, with 10 and 11)
- 2D flood heatmap (Features 12, 13)
- 3D terrain and flood visualization (Feature 14)
- What-if simulation (Feature 16, with 17)

Supporting must-haves: the virtual sensor network (Feature 4), data source labeling (Feature 5), the forecast timeline (Feature 15), and the demo safeguards (Feature 20).

### 5.2 Should have

- Alert system and alerts page (Features 18 and 19), at least with threshold alerts and acknowledgement.
- Scenario comparison details beyond the basic baseline-versus-scenario chart.

### 5.3 Optional

- Advanced hydraulic modelling with HEC-RAS 2D.
- Satellite image AI analysis.
- XGBoost or LSTM experiments.

### 5.4 Future extensions (not built now)

- Autonomous rescue resource allocation.
- Fully autonomous LoRa mesh sensor network.
- Multiple full hardware stations.

### 5.5 Build order rule

First get one complete vertical slice working: one sensor reading travels to the backend, is stored, produces a forecast, and changes the 2D map. Only after that works should the team improve the 3D visuals, the simulator and the polish.

---

## 6. Out of Scope for This Build

- Simulating all of Mumbai.
- A production-grade, calibrated hydraulic model.
- Claims of validated real-world prediction accuracy.
- Reliance on a public live Mithi River water-level API (we do not assume one exists).
- Street-level rainfall from satellite data (the satellite rainfall grid is about 10 km and too coarse for this; the local rain gauge is the primary live rainfall source).
- Mobile apps, user accounts and public notification delivery (SMS or push) beyond dashboard alerts.

---

## 7. Product Principles

1. Physical-to-digital: a real change in the channel must visibly change the virtual world.
2. Prediction-to-impact: a forecast must always lead to a map of consequences, not stop at a number.
3. Preparation: a user must be able to change conditions and see a different future.
4. Honesty: every number and layer shows its source, and every simplification is stated.
5. Clarity: one glance should tell the user what is happening now, what is coming and where.