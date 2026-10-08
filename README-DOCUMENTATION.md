# FLOODGUARD AI — Project Documentation

Complete, file-by-file analysis of this repository. Every statement was verified by reading the
source, and every number was measured (file contents, raster metadata, NetCDF dimensions, joblib
bundles, GeoJSON feature counts, test runs).

**Start here → [`docs/00-OVERVIEW.md`](docs/00-OVERVIEW.md)**

---

## What this project is

An IoT-powered flood monitoring, prediction and 3D-visualisation system for the **Mithi River
corridor, BKC → Kurla, Mumbai**. A real ESP32 sensor drives a mapped simulation; three trained
Random Forest regressors forecast water level at +15/+30/+60 minutes; a rule-based risk engine and an
elevation-threshold inundation engine turn that into explainable risk scores and GeoJSON flood
polygons; a React/Next.js command-centre console renders it on a dashboard, a Leaflet map, a
three.js twin, a what-if simulator and an alerts page.

Stack: **FastAPI · SQLAlchemy(async) · PostGIS · Redis · NumPy/SciPy/
rasterio/shapely · scikit-learn/joblib** on the back; **Next.js 16 · React 19 · TypeScript · Redux
Toolkit · Leaflet · three.js · Recharts · Socket.IO** on the front.

**Telemetry ingress is HTTP-only** — one authenticated HTTPS endpoint, no broker. See
[`12-REALTIME-AND-ALERTING.md`](docs/12-REALTIME-AND-ALERTING.md) §A.7.

---

## Documentation index

### Orientation

| # | File | What is inside |
|---|---|---|
| 00 | [`OVERVIEW`](docs/00-OVERVIEW.md) | What it is, the real stack, the 5 pipelines, what is and isn't AI/ML, the single most important architectural fact |
| 01 | [`PROJECT-STRUCTURE`](docs/01-PROJECT-STRUCTURE.md) | **Every file** in the repo with purpose and size |
| 02 | [`DATA-INVENTORY`](docs/02-DATA-INVENTORY.md) | **Every dataset** you provided and every one the code generates, with measured numbers, plus a real/simulated verdict per file |

### Deep dives

| # | File | What is inside |
|---|---|---|
| 03 | [`BACKEND-ANALYSIS`](docs/03-BACKEND-ANALYSIS.md) | Every backend module, every endpoint's behaviour, every design decision and its stated reason |
| 04 | [`DATA-PIPELINES`](docs/04-DATA-PIPELINES.md) | The 5 pipelines step by step: preprocess → geo load → train → live telemetry → scenario |
| 05 | [`ALGORITHMS-AND-AI-ML`](docs/05-ALGORITHMS-AND-AI-ML.md) | **Every algorithm with its actual formula and parameters**: risk scoring, Random Forest training/inference, reservoir hydrology, raster inundation, connectivity mask, river interpolation, scenario math, the Gerstner shader |
| 06 | [`FRONTEND-ANALYSIS`](docs/06-FRONTEND-ANALYSIS.md) | Every page, component, hook, store slice, lib module, types and CSS structure |
| 07 | [`MAP-AND-3D-DETAILS`](docs/07-MAP-AND-3D-DETAILS.md) | Deep dive on the Leaflet map and the three.js scene, plus the UI mockups vs the as-built |
| 11 | [`DATABASE-SCHEMA`](docs/11-DATABASE-SCHEMA.md) | All 16 tables, every column, 21 indexes, both migrations, the FK graph |
| 10 | [`API-REFERENCE`](docs/10-API-REFERENCE.md) | Every endpoint with parameters and response shapes, plus a smoke-test script |
| 12 | [`REALTIME-AND-ALERTING`](docs/12-REALTIME-AND-ALERTING.md) | HTTPS ingress → Redis → Socket.IO → browser, all 8 events, all 5 alert rules with their config |

### The specific things you asked about

| # | File | What is inside |
|---|---|---|
| 08 | [`DEMO-AND-SIMULATED-DATA`](docs/08-DEMO-AND-SIMULATED-DATA.md) | **Exactly where every simulated / demo / test / orphan dataset is used**, and a table of whether each visible number is real |
| 09 | [`CONFIGURATION-AND-ENV`](docs/09-CONFIGURATION-AND-ENV.md) | Every `.env` variable, every `Settings` field, both config JSONs, all build configs, and a change-impact table |
| 13 | [`TESTING-AND-QUALITY`](docs/13-TESTING-AND-QUALITY.md) | The test suites, **live test results**, dead code, and **20 ranked defects** — 7 now fixed |
| 14 | [`HONESTY-AND-LIMITATIONS`](docs/14-HONESTY-AND-LIMITATIONS.md) | Every disclaimer in the product, collected verbatim, plus what is *not* disclosed |
| 15 | [`HOW-TO-RUN`](docs/15-HOW-TO-RUN.md) | Exact commands, plus a troubleshooting table |
| 16 | [`DEPLOYMENT`](docs/16-DEPLOYMENT.md) | **Part A: Docker on your PC + Cloudflare Tunnel + frontend on Vercel** (verified live). Part B: Render as a managed-PaaS reference |

### Deployment

| File | Purpose |
|---|---|
| `docker-compose.yml` | The whole server: PostGIS + Redis + one-shot `migrate` + the `backend` API |
| `scripts/start-tunnel.ps1` | Opens the Cloudflare Tunnel and prints the exact Vercel env value |
| `backend/Dockerfile` | Production image |
| `.dockerignore` | Keeps `backend/.env` out of the image; context 1.14 GB → 98 MB |
| `backend/scripts/release.py` | Waits for the DB, enables PostGIS, `alembic upgrade head`, optional OSM load |
| `render.yaml`, `backend/Procfile` | Only for the managed-PaaS path (Part B of `docs/16`) |

```bash
docker compose up -d --build       # backend live on :8000
.\scripts\start-tunnel.ps1         # public URL, ready for Vercel
```

---

## Frontend on Vercel

Vercel serves from the public internet; the API is on your private LAN address, so a tunnel is
required. Set in Vercel, then **redeploy** (`NEXT_PUBLIC_*` is inlined at build time):

```
NEXT_PUBLIC_API_BASE=https://<your tunnel url>
```

Add the Vercel origin to `FRONTEND_ORIGIN` in a root `.env` and `docker compose up -d backend`.
Full walkthrough, the quick-tunnel URL caveat, and the security implications:
[`docs/16-DEPLOYMENT.md`](docs/16-DEPLOYMENT.md) Part A.

---

## The ten things to know

1. **One real input drives everything.** A physical ESP32 registered via
   `POST /api/iot/devices/register`. Its water level is mapped linearly onto a Mithi reference level
   by 5 configurable parameters (`0→30 cm` physical ⇒ `118→220 cm` Mithi by default). This is
   disclosed as **"CONFIGURED CALIBRATION / SIMULATION MAPPING"** everywhere it appears.

2. **No live IoT reading ⇒ "WAITING FOR REAL IoT INPUT".** Never invented numbers. Enforced by a
   5-minute freshness gate at four layers (backend, API response shape, frontend helper, UI copy).

3. **The only AI/ML is three scikit-learn `RandomForestRegressor`s**, one per horizon, trained
   chronologically 70/15/15 with **zero shuffling**, against a persistence baseline.

4. **The rainfall forcing is real; the training water levels are not.** Real IMD gridded daily
   rainfall for 2019/2023/2024 (1,096 days, ~25 km grid) drives a documented single-reservoir
   storage-routing model. The resulting MAE of 0.24 cm is *not* real-world accuracy — and the
   project says so in the artefact, the metadata, the DB and the UI.

5. **The 3D twin is three.js, not CesiumJS.** No Cesium token is used. Buildings are procedurally
   placed boxes; the terrain is a sculpted sine-wave plane, not the DEM. The page discloses both.

6. **The flood model is elevation-threshold raster inundation** with a breadth-first connectivity
   mask — not a hydraulic solver. No flow velocity, no momentum, no mass conservation.

7. **The real terrain is real.** Copernicus GLO-30 / SRTM at ~30.9 m, clipped to 108 × 90 over the
   study area. All 4,521 buildings and 4,376 roads are real OSM (though 96.5 % of building heights are
   *estimated*; only 7 buildings carry a real `height` tag).

8. **Provenance is enforced in the UI.** 8 source classes, 5 connection states, and a hard rule that
   a missing measurement renders as `—`, never as `0`. Both directions are covered by tests.

9. **The risk engine is rule-based, not ML** — a weighted sum (0.35 / 0.30 / 0.20 / 0.15) over four
   banded inputs, with the primary driver named so nothing is a black box.

10. **There are 20 catalogued defects, 7 of them now fixed.** The one that mattered most — the
    Random Forest never actually ran on live data — is fixed: forecasts now return differentiated
    +15/+30/+60 values instead of silently degrading to persistence while labelled `FORECAST`. The
    remaining high-impact one is that the 2D map previously rendered zero sensor markers; that is
    also fixed. See [`13-TESTING-AND-QUALITY.md`](docs/13-TESTING-AND-QUALITY.md) §5.

---

## Data you provided

| Path | Size | Kind | Used by |
|---|---|---|---|
| `data/overpass turbo.geojson` | 8.3 MB | 🟢 OSM extract, 9,099 features | `preprocess_data.py` → 3 split layers |
| `data/IMD/gridded rainfall 19.nc` | 25.4 MB | 🟢 IMD NetCDF, 365 days | training rainfall forcing |
| `data/IMD/gridded rainfall 23.nc` | 25.4 MB | 🟢 IMD NetCDF, 365 days | ″ |
| `data/IMD/gridded rainfall 24.nc` | 25.5 MB | 🟢 IMD NetCDF, 366 days | ″ |
| `data/terrain/_copernicus_N19E072.tif` | 13.3 MB | 🟢 Copernicus GLO-30, 1° × 1° | source for the clip |
| `data/terrain/dem_mithi.tif` | 39 KB | 🟢 the 108 × 90 study-area clip | **the flood simulation's terrain** |
| `data/terrain/sensor_elevations.json` | 1 KB | 🟡 documented elevations | ⚪ **orphaned** |
| `data/terrain/terrain_metadata.json` | 550 B | 🟢 DEM properties | ⚪ documentation only |
| `data/osm/{buildings,roads,waterways}.geojson` | 3.4 MB | 🟡 derived from your extract | the 2D map + impact counting |
| `data/rainfall/imd/processed/mumbai_rainfall.json` | 30 KB | 🟡 derived from the NetCDFs | training rainfall forcing |
| `data/ml/synthetic_water_levels.csv` | 4.9 MB | 🔴 synthetic | ⚪ **orphaned** |
| `models/flood/rf_{15,30,60}min.joblib` | 98.6 MB | 🟡 trained | forecasting |
| `models/flood/model_metadata.json` | 2.6 KB | 🟡 training report | `/prediction`, `/data` |

Generated at runtime (never shipped): `telemetry_readings`, `risk_assessments`, `forecasts`,
`flood_layers`, `flood_impacts`, `alerts`, `scenarios`, `replay_recordings`.

---

## Quick start

The backend runs entirely in Docker — Postgres, Redis, migrations and the API:

```bash
docker compose up -d --build
curl http://localhost:8000/healthz        # {"status":"ok",...}
```

Four services start in a guaranteed order: `db` and `redis` must be healthy, then `migrate` runs
PostGIS + `alembic upgrade head` + the OSM layer load and exits, and only then does `backend` boot.
Nothing needs to be launched from the host.

Then register a device and send 8 readings over HTTP — full script in
[`15-HOW-TO-RUN.md`](docs/15-HOW-TO-RUN.md) §5. (8, not 5: forecasts need
`MIN_READINGS_FOR_FORECAST = max([1,2,3,6]) + 2` readings before they appear.)

The frontend is either run locally, or deployed to Vercel:

```bash
cd Frontend && npm install && npm run dev   # http://localhost:3000
```

For Vercel, open the tunnel first and set `NEXT_PUBLIC_API_BASE` — see the Vercel section above.

---

## Test status

```
Frontend   23 tests → 22 pass, 1 fail
Backend    22 tests → 22 pass  (4 DeprecationWarnings, see below)
Typecheck  clean
```

The one failure is a stale assertion: `constants.test.ts:72` expects
`SOURCE_META.iot.label === 'LIVE HARDWARE'` but the label is now `'REAL IoT INPUT'`. One-line fix.
Details in [`13-TESTING-AND-QUALITY.md`](docs/13-TESTING-AND-QUALITY.md) §2.

The four warnings are all from `app/__init__.py:20`:
`asyncio.WindowsSelectorEventLoopPolicy` and `asyncio.set_event_loop_policy` are deprecated in
Python 3.14 and slated for removal in 3.16. The policy is still required for `sqlalchemy[asyncio]`
on Windows, so this is a known forward-compatibility issue, not a defect in the current build.