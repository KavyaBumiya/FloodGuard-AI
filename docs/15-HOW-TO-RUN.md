# 15 — How to Run

Every command below is taken from the actual project files. Nothing here is invented.

---

## 0. Prerequisites

| Requirement | Why | Notes |
|---|---|---|
| **Python 3.14** | `requirements.txt:2` — "verified working on Python 3.14" | |
| **Node.js 20+ / npm** | Next.js 16 + React 19 | |
| **Docker** (or local PostgreSQL 16 + PostGIS 3.4, Redis 7, Mosquitto 2) | `docker-compose.yml` | |
| `data/terrain/dem_mithi.tif` | flood simulation | already in the repo ✅ |
| `models/flood/rf_*.joblib` | forecasting | already in the repo ✅ |
| A registered physical device (or `curl` loop) | live demo | see §5 |

**No `.env` change is strictly required to boot**, but two things must be fixed for a working system:

1. **The database password.** `backend/.env` uses `floodguard:floodguard@localhost:5432`, but
   `docker-compose.yml:7` creates the DB with password `floodguard_secret`. Pick one:
   - change `backend/.env` → `postgresql+asyncpg://floodguard:floodguard_secret@localhost:5432/floodguard`
     (this is what `.env.example` already says), **or**
   - change `docker-compose.yml` → `POSTGRES_PASSWORD: floodguard`
2. **`IOT_REGISTRATION_TOKEN`.** `backend/.env:16` is the guessable
   `"your-registration-token-here"`. Change it if the API is reachable from anywhere but localhost.

---

## 1. Start the infrastructure

```bash
cd D:\Projects\Flood-Monitoring-System
docker compose up -d
docker compose ps          # wait for db/redis/mqtt to report (healthy)
```

| Service | Expect |
|---|---|
| `floodguard-db` | `healthy` (`pg_isready`) |
| `floodguard-redis` | `healthy` (`redis-cli ping`) |
| `floodguard-mqtt` | `healthy` (`mosquitto_sub … -W 3`), needs ~10 s `start_period` |

If Postgres was created with a different password previously, the named volume `pgdata` keeps the old
credentials — `docker compose down -v` wipes it (destructive).

---

## 2. Backend

```bash
cd backend

python -m venv .venv
.\.venv\Scripts\activate          # Windows
# source .venv/bin/activate       # Linux/macOS

pip install -r requirements.txt
```

### 2.1 Create the PostGIS extension and migrate

```bash
# the extension is also auto-created on startup, but doing it now surfaces errors early
psql "postgresql://floodguard:floodguard_secret@localhost:5432/floodguard" -c "CREATE EXTENSION IF NOT EXISTS postgis;"

alembic upgrade head
```

`alembic.ini:4` is **overridden** at runtime by `alembic/env.py:71`, which reads `get_settings()`,
so your `.env` decides which database is migrated — not `alembic.ini`.

Expect `Running upgrade 046c73cac147 -> 0933a21ed37c` (16 tables, 21 indexes).

Verify: `main._verify_schema()` will log `schema_verified` with the table count at startup. If it logs
`schema_incomplete_missing_tables` with the hint `run \`alembic upgrade head\``, the migration did not
apply.

### 2.2 Load the OSM layers into PostGIS (required for impact counts and the 2D map)

```bash
python -m scripts.load_osm_layers
```

Expected:
```
DEM sampler available: True (data/terrain/dem_mithi.tif)
buildings: +4521 inserted, 0 skipped -> 4521 total
roads:     +4376 inserted, 0 skipped -> 4376 total
```
Re-running skips existing `osm_id`s. `--force` deletes and reloads (**this invalidates historical
`flood_impacts.feature_id`**, since those ids are reissued).

### 2.3 (Optional) Re-run data preprocessing

Only needed if you change the raw sources.

```bash
python -m scripts.preprocess_data                       # DEM validate + IMD + OSM
python -m scripts.preprocess_data --skip-dem --skip-osm  # IMD only
python -m scripts.preprocess_data --skip-imd --skip-osm  # OSM only
```

Outputs land in `data/osm/*.geojson` and `data/rainfall/imd/processed/mumbai_rainfall.json`.

### 2.4 (Optional) Retrain the models

Only needed if you change the IMD data or the training code. **The three `.joblib` artefacts are
already committed** and load correctly.

```bash
python -m scripts.train_forecast_models
```

Expected output (real values from the committed run):
```
[ train] IMD rainfall: 1096 days from 2019-01-01 to 2024-12-31
[ train] simulated series: 315646 steps (1096 days) level min=… max=… cm
[ train] 15min  persistence MAE= 0.347  RF MAE= 0.239  skill=+31.0%  shipped=random_forest
[ train] 30min  persistence MAE= 0.467  RF MAE= 0.326  skill=+30.3%  shipped=random_forest
[ train] 60min  persistence MAE= 0.680  RF MAE= 0.477  skill=+29.8%  shipped=random_forest
[ train] wrote models/flood/model_metadata.json
[ train] registered model metadata in database
```

This also upserts the three `model_registry` rows, which is what `/api/about/model-info` reads.

### 2.5 Start the API

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

`app.main:app` and `app.main:socket_app` are the **same** ASGI app (`main.py:227`), so either serves
REST **and** `/socket.io`.

Verify:
```bash
curl -s http://127.0.0.1:8000/health
```
```json
{"status":"ok","service":"floodguard-ai","version":"1.0.0",
 "database":"ok","redis":"ok","mqtt":"enabled","open_meteo":"ok"}
```

Startup log lines to look for:
```
schema_verified              (tables=…)
seeded_reference_data        (study_area=True, data_sources=5)
event_bridge_subscribed      (channel=floodguard:realtime)
mqtt_subscribed              (topic=floodguard/nodes/+/telemetry)
auto_forecast_started
alert_engine_started
floodguard_started           (env=development, mqtt=True)
```

### 2.6 Disable MQTT (HTTP-only)

```bash
# backend/.env
MQTT_ENABLED=false
```
Then `_start_engines()` skips the MQTT service entirely and `POST /api/iot/telemetry` becomes the
only ingestion path.

---

## 3. Frontend

```bash
cd Frontend

Copy-Item .env.example .env.local      # Windows
# cp .env.example .env.local          # Linux/macOS

npm install
npm run dev
```

Open **http://localhost:3000**.

`NEXT_PUBLIC_API_BASE` defaults to `http://127.0.0.1:8000` even without `.env.local`
(`lib/api.ts:9`), so the copy step is optional locally. It is **required** if the backend is on
another host, and it drives the Socket.IO URL too (`lib/socket.ts:34`).

| Script | Command |
|---|---|
| `npm run dev` | `next dev` |
| `npm run build` | `next build` (fails on type errors) |
| `npm start` | `next start` |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | `eslint .` |
| `npm test` | `node --test lib/*.test.ts` |

> **On Windows:** `app/__init__.py:13-16` forces `WindowsSelectorEventLoopPolicy` because paho-mqtt's
> own socket thread needs a selector loop. You do not need `asyncio.WindowsProactorEventLoopPolicy` or
> `uvloop`.

---

## 4. Bring the whole thing up, in order

```bash
# terminal 1
cd D:\Projects\Flood-Monitoring-System
docker compose up -d

# terminal 2
cd backend
.\.venv\Scripts\activate
alembic upgrade head
python -m scripts.load_osm_layers
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# terminal 3
cd Frontend
npm run dev
```

You should see: the sidebar with 7 nav items, `LIVE` in the CommandBar, `?/1 ACTIVE`, and the
dashboard hero reading **`WAITING FOR REAL IoT INPUT`** — which is **correct** until a device sends
data.

---

## 5. Feed it real data

### 5.1 Register the physical device

```bash
# read the token out of backend/.env
$env:IOT_REGISTRATION_TOKEN = "your-registration-token-here"     # PowerShell
export IOT_REGISTRATION_TOKEN="your-registration-token-here"    # bash

curl -s -X POST http://127.0.0.1:8000/api/iot/devices/register \
  -H "Authorization: Bearer $IOT_REGISTRATION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"node_code":"VAD-ESP32-01","name":"Vadodara Demo Sensor","node_type":"physical",
       "lat":22.3072,"lng":73.1812,"reference_elevation_m":1.2,
       "baseline_level_cm":0,"warning_threshold_cm":25,"battery_pct":100}'
```

Save the returned `token` — **it is shown exactly once**.

### 5.2 Send 8 readings at 5-minute spacing

`MIN_READINGS_FOR_FORECAST = 8` and `FEATURE_WINDOW_SIZE = 12`, so 8 readings unlock forecasts.
Spacing matters: the features are designed for a 5-minute cadence, so back-to-back sends produce a
0-second interval that the model was not trained on.

```bash
TOKEN="<the device token>"

for i in 0 1 2 3 4 5 6 7; do
  LVL=$(python -c "print(round(5 + $i*1.8, 1))")           # 5.0 → 17.6 cm
  TS=$(python -c "from datetime import datetime,timedelta,timezone;print((datetime.now(timezone.utc)-timedelta(minutes=(7-$i)*5)).isoformat())")
  curl -s -X POST http://127.0.0.1:8000/api/iot/telemetry \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "{\"node_code\":\"VAD-ESP32-01\",\"water_level_cm\":$LVL,
         \"rainfall_mm_hr\":6.5,\"timestamp\":\"$TS\",
         \"event_id\":\"boot-$i\",\"battery_voltage\":4.05}"
  sleep 1
done
```

The final reading (17.6 cm) maps to `118 + (17.6/30)×102 = 177.8 cm` on the Mithi scale — already in
the HIGH band (150 cm).

### 5.3 From a real ESP32 over MQTT

Publish JSON to `floodguard/nodes/VAD-ESP32-01/telemetry` on `localhost:1883`:

```json
{ "water_level_cm": 14.5, "rainfall_mm_hr": 6.2,
  "timestamp": "2026-10-06T09:15:00+05:30",
  "source": "iot", "valid": true, "battery_voltage": 4.05, "event_id": "evt-0001" }
```

The node code comes from the **topic**, not the payload. The node must already be registered —
`source: "iot"` on an unknown code is rejected with `unregistered_node_rejected`.

Quick test with `mosquitto_pub` inside the container:
```bash
docker exec floodguard-mqtt mosquitto_pub -h localhost -t 'floodguard/nodes/VAD-ESP32-01/telemetry' \
  -m '{"water_level_cm":14.5,"rainfall_mm_hr":6.2,"timestamp":"2026-10-06T09:15:00+05:30","source":"iot","valid":true}'
```

---

## 6. What you should see

| Surface | Expected |
|---|---|
| CommandBar | `LIVE` (or `DEGRADED` briefly), IST clock ticking, `LAST UPDATE just now` |
| Sidebar | `1/1 ACTIVE`, `0 OFFLINE` |
| Dashboard panel | `VADODARA DEMO WATER LEVEL 17.6 cm`, `MITHI SIMULATED LEVEL 177.8 cm`, `MITHI STATUS CONNECTED` |
| Hero | risk label + `score N.NN`, node code + name, `WATER LEVEL`, `RAINFALL`, `WARNING THRESHOLD 25 cm`, forecast `+15m/+30m/+60m` |
| Sensor network | one row: `VAD-ESP32-01 ESP32 17.6 cm 6.5 mm/hr HIGH` |
| Water-level chart | solid cyan observed line + three dashed blue forecast points with a shaded band |
| Predicted impact | flooded area / buildings / roads / max depth for `NOW` |
| `/map` | the Mithi River centreline (cyan), 4,376 grey roads, 4,521 slate buildings, and coloured inundation polygons for the selected step |
| `/digital-twin` | the WebGL scene; the water mesh rises as the level rises; the timeline chip re-colours it |
| `/alerts` | once the level passes 25 cm the node's `warning_threshold_cm`, a `high` threshold alert appears within 30 s |

### Timeline

Click `+15 / +30 / +60` in the shared `TimelineControl` (top of every page) or press `PLAY` to
auto-advance every 3.5 s. The inundation polygons, the impact figures and the 3D water all follow.

### Scenario

Go to `/scenarios`, pick a preset, press **Run what-if simulation**. The page polls
`GET /api/scenarios/{id}` up to 12 times at 900 ms, then shows the delta list. **Note:** the
`PEAK LEVEL` / `MEAN LEVEL` tiles render `—` — the backend does not return those keys
(see `13-TESTING-AND-QUALITY.md` §5.10).

---

## 7. Replay mode (no hardware)

Requires a prior recording.

```bash
# capture the last 5 minutes of real telemetry
curl -s -X POST "http://127.0.0.1:8000/api/demo/recordings?name=demo&duration_seconds=300"
# → {"id":1,"name":"demo","duration_seconds":300,"message":"Recording created"}

# play it back at 2x
curl -s -X POST "http://127.0.0.1:8000/api/demo/replay/1/start?speed=2.0"
```

The `Shell` then renders the amber strip **"REPLAY — Recorded data being replayed — not live
conditions."** Stop with `POST /api/demo/replay/stop`.

> `POST /api/demo/seed` is a **no-op** in this build, so there is no pre-seeded recording.

---

## 8. Reset to a clean state

```bash
curl -s -X POST http://127.0.0.1:8000/api/demo/reset
```

Deletes `flood_impacts`, `flood_layers`, `alerts`, `risk_assessments`, `forecasts`,
`telemetry_readings`, `scenarios`, and flushes `floodguard:*`, `forecast:*`, `weather:*` from Redis.
**Keeps** `sensor_nodes` (your device stays registered), `study_area`, `roads`, `buildings`,
`model_registry`, `data_sources`, `replay_recordings`.

To also drop the device registration:
```sql
DELETE FROM sensor_nodes WHERE node_code = 'VAD-ESP32-01';
DELETE FROM telemetry_readings;
```

To force the terrain grid to be re-read from the `.tif` after replacing it:
```bash
docker exec floodguard-redis redis-cli DEL "floodguard:terrain_grid:real"
```
(the cache TTL is 24 h, so this is the fast path).

---

## 9. Run the tests

```bash
# Backend — from backend/
pytest -v
```
One test needs a live PostgreSQL and **self-skips only if the DB is unreachable**; a SQL error fails
loudly by design.

```bash
# Frontend — from Frontend/
npm test          # 23 tests
npm run typecheck # tsc --noEmit
npm run lint      # eslint .  (warns on no-explicit-any / unused vars / exhaustive-deps)
```

Expected frontend result on the current tree: **22 pass, 1 fail** —
`constants.test.ts:72` expects `'LIVE HARDWARE'` but the label is `'REAL IoT INPUT'`
(`13-TESTING-AND-QUALITY.md` §2.1).

---

## 10. Hardware notes (for the ESP32 station)

The PRD (`PRD.md:127-146`) specifies:

| Part | Purpose |
|---|---|
| ESP32 DevKit V1 | controller + Wi-Fi |
| JSN-SR04T waterproof ultrasonic | distance to the water surface |
| Tipping-bucket rain gauge + reed switch | one pulse per bucket |
| 0.96" OLED (I2C) | local level/rain/Wi-Fi/node name |
| Buzzer, red + green LED (220 Ω) | local warning |
| Resistor divider on ECHO | 5 V → 3.3 V for the ESP32 |

Pin allocation: `GPIO 5` TRIG · `GPIO 18` ECHO (via divider) · `GPIO 19` rain pulse (internal
pull-up) · `GPIO 23` buzzer · `GPIO 25` red LED · `GPIO 26` green LED · `GPIO 21` OLED SDA ·
`GPIO 22` OLED SCL.

> ⚠️ **No firmware source exists in this repository.** `PRD.md:146` says "The team must verify the
> real board pinout and the sensor module voltage before wiring." The pin map above is the PRD's
> plan, not built code.

Firmware behaviour required by the PRD (§2, `System-Architecture.md:356-379`):
median of several distance readings; `level = calibrated_sensor_height − distance + offset`;
discard impossible values and flag invalid rather than sending them; count + debounce bucket tips;
`rainfall_mm = tips × mm_per_tip`; sample every 5–10 s; publish JSON with `source: "iot"`; buffer on
Wi-Fi loss and resend on reconnect; NTP sync.

**Calibration for this build:** the demo mapping assumes the physical channel reads **0–30 cm**
(`DEMO_INPUT_PHYSICAL_MIN_CM=0`, `DEMO_INPUT_PHYSICAL_MAX_CM=30`), so size the channel so the water
surface stays in that band. A reading of 30 cm saturates the mapping at 220 cm.

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/health` shows `"database":"error: …"` | password mismatch (§0) | align `.env` with `docker-compose.yml` |
| Log `schema_incomplete_missing_tables` | migrations not applied | `alembic upgrade head` |
| Log `unregistered_node_rejected` | MQTT for an unknown node code | register it first (§5.1) |
| Log `terrain_raster_missing` | the `.tif` was not found relative to the repo root | check `TERRAIN_RASTER_PATH`; `resolve_data_path` resolves against the **repo root**, not the CWD |
| Simulation raises `RuntimeError("Terrain unavailable …")` | the raster is genuinely missing | run `python -m scripts.preprocess_data --skip-imd`, or set `ALLOW_SYNTHETIC_TERRAIN=true` (outputs then labelled SIMULATED) |
| Console stuck on `DEGRADED` | Socket.IO not mounted, or the event bridge died | confirm `uvicorn app.main:app` (not a bare `app`); check for `event_bridge_subscribed` at startup |
| Console shows `OFFLINE` | the REST poll is failing | check `/health` and CORS (`FRONTEND_ORIGIN` must match the browser origin) |
| Console shows `WAITING FOR REAL IoT INPUT` | no `source="iot"` reading in the last 5 min | send a reading (§5.2) |
| `insufficient_mapped_iot_history` in the log | fewer than 8 IoT readings exist | send 8 (§5.2) |
| `FORECAST` values identical to the current level | the RF raises on feature count and falls back to persistence | see `13-TESTING-AND-QUALITY.md` §5.1; look for `mithi_forecast_prediction_failed` |
| No flood polygons on the map | no layer stored for the step, or < 0.1 m depth everywhere | check `GET /api/flood/layers/0`; verify the water level reaches the terrain |
| Affected buildings/roads = 0 | `roads`/`buildings` empty | `python -m scripts.load_osm_layers`; verify with `GET /api/flood/map-layers?layer=roads` |
| MQTT will not connect on Windows | event-loop policy | handled by `app/__init__.py`; make sure you import `app.*` (not run a bare script from `backend/`) |
| `pytest` cannot import `app.services.flood_simulation` | deps not installed / wrong CWD | run from `backend/`, with the venv active |
| Frontend `Module type of file:… is not specified` warning | `package.json` has no `"type":"module"` | harmless; `node --test` reparses as ESM |

---

## 12. Environment variables reference (quick)

| Var | Default | Change it when |
|---|---|---|
| `DATABASE_URL` | `…floodguard@db:5432/floodguard` | local Docker → `localhost` + the right password |
| `REDIS_URL` | `redis://redis:6379/0` | local Docker → `localhost` |
| `IOT_REGISTRATION_TOKEN` | `""` | **always**, before exposing the API |
| `MQTT_BROKER_HOST` / `_PORT` | `mqtt` / `1883` | local Docker → `localhost` |
| `MQTT_TOPIC_PREFIX` | `floodguard` | changing it changes the topic grammar |
| `MQTT_ENABLED` | `true` | set `false` for HTTP-only |
| `STUDY_AREA_MIN_LAT` … `STUDY_AREA_MAX_LON` | 19.05 / 72.85 / 19.09 / 72.90 | relocating the corridor |
| `TERRAIN_RASTER_PATH` | `data/terrain/dem_mithi.tif` | a different DEM |
| `IMD_PROCESSED_DIR` | `data/rainfall/imd/processed` | — |
| `OSM_{BUILDINGS,ROADS,WATERWAYS}_GEOJSON` | `data/osm/*.geojson` | — |
| `FLOOD_MODEL_DIR`, `FLOOD_MODEL_{15,30,60}MIN` | `models/flood`, `rf_*.joblib` | different artefacts |
| `FLOOD_GRID_RESOLUTION_M` | `10.0` | only affects the **synthetic** grid; the real DEM's own resolution wins |
| `FLOOD_MAX_DEPTH_M` | `5.0` | the depth clamp |
| `FLOOD_CONNECTIVITY_BUFFER_M` | `50.0` | the river-seed radius for the connectivity mask |
| `FLOOD_DEPTH_RISK_{LOW,MODERATE,HIGH,SEVERE}_M` | 0.1 / 0.3 / 0.6 / 1.0 | depth→risk classification |
| `DEMO_INPUT_NODE_CODE` | `""` | pin the demo to one physical node |
| `DEMO_INPUT_PHYSICAL_MIN_CM` / `_MAX_CM` | 0 / 30 | match your physical channel |
| `DEMO_INPUT_MITHI_MIN_CM` / `_MAX_CM` | 118 / 220 | tune the mapping |
| `DEMO_INPUT_MITHI_RAINFALL_FACTOR` | 0.8 | rainfall scaling |
| `ALLOW_SYNTHETIC_TERRAIN` | `false` | only for offline demos; output is SIMULATED |
| `FRONTEND_ORIGIN` | `""` (⇒ `*`) | set to the browser origin for CORS |
| `APP_ENV` | `development` | `production` removes the loopback CORS regex |

Full details: [`09-CONFIGURATION-AND-ENV.md`](09-CONFIGURATION-AND-ENV.md).