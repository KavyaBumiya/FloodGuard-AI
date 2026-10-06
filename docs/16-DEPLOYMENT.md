# 16 — Deployment

Two supported paths. **Path A is the one to use** — the backend runs on your own machine in Docker,
and the frontend is deployed to Vercel. **Path B** (Render) is kept at the end for reference.

| | Path A — Docker + tunnel + Vercel | Path B — Render (appendix) |
|---|---|---|
| Backend runs on | your PC, in Docker | Render's servers |
| Cost | free | ~$27/mo (starter web + Postgres) |
| Public URL | `*.trycloudflare.com`, changes on restart | `*.onrender.com`, stable |
| PC must be on | **yes, the whole time** | no |
| Uptime | as good as your PC + WiFi | provider's |
| Best for | a demo, a hackathon, a deadline | a real always-on service |

Everything here was **verified by building the image, booting the container, pushing real telemetry
through it, and — for Path A — pushing that same telemetry through a live Cloudflare Tunnel and
receiving the realtime event back on a Socket.IO client.** The outputs quoted are real.

---

# PART A — Docker on your PC + frontend on Vercel

## A.1 The problem this solves

Vercel serves your frontend from the **public internet**. Your Dockerised API is on your **private
LAN address** behind a router. There is no route between them:

```
browser -> Vercel (public internet)  ->  ???  ->  192.168.x.x:8000  (your PC, behind a router)
                                                no route: RFC1918 private address
```

The exact address changes with your network — `192.168.*` on a home router, `10.*` on some
institutional WiFi — but it makes no difference. Anything in `10.0.0.0/8`, `172.16.0.0/12` or
`192.168.0.0/16` is unroutable on the public internet by definition.

Setting `NEXT_PUBLIC_API_BASE=http://localhost:8000` on Vercel does **not** work — that resolves to
Vercel's own loopback, not your PC. And the tunnel is what fixes it: `cloudflared` makes an
**outbound** connection to Cloudflare's edge, so Cloudflare can route public traffic back down it.
No router configuration, no port forwarding, no static IP.

## A.2 Start the backend

```bash
docker compose up -d --build
curl http://localhost:8000/healthz
```

Four services, ordered by `depends_on`: `db` and `redis` healthy → `migrate` runs and exits 0 →
`backend` boots. Full detail in [`15-HOW-TO-RUN.md`](15-HOW-TO-RUN.md) §1.

> **Changing WiFi or network?** See §A.8 — it explains why the tunnel keeps working and the one thing
> you do have to update.

## A.3 Open the tunnel

```powershell
.\scripts\start-tunnel.ps1
```

That starts the `tunnel` service (behind a compose profile, so a plain `docker compose up` never
starts it), waits for Cloudflare to issue a URL, verifies it from outside, and prints exactly what
to set in Vercel:

```
Tunnel is live.
  https://discrete-explaining-gel-look.trycloudflare.com

Verifying the public URL answers...
  GET /healthz -> 200 {"status":"ok","service":"floodguard-ai","version":"1.0.0"}
```

Stop it with `.\scripts\start-tunnel.ps1 -Stop`.

### Verified through the tunnel

| Check | Result |
|---|---|
| `GET /healthz` | 200 |
| `GET /health` (DB + Redis, real backend calls) | 200, all `ok` |
| `POST /api/iot/devices/register` | 201 |
| `POST /api/iot/telemetry` | 201 `accepted` |
| Socket.IO **WebSocket** upgrade | succeeded — `transport: websocket`, not a polling fallback |
| `flood.sensor.updated` pushed through the tunnel | **received live on the client** |
| CORS preflight from `https://*.vercel.app` | 200, `Access-Control-Allow-Origin: *` |

That last pair is the one that matters. Socket.IO silently degrades to long-polling when a proxy
blocks the upgrade, and polling still works — so "the console loads" proves nothing. A real
`socket.io-client` was connected through the tunnel, the transport reported `websocket`, and a
reading posted over HTTPS produced a `flood.sensor.updated` event back on that socket.

## A.4 Point Vercel at it

Vercel dashboard → your project → **Settings → Environment Variables**:

| Name | Value | Apply to |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `https://<your tunnel url>` | **Production, Preview *and* Development** |

Then **redeploy** (Deployments → ⋯ → Redeploy).

> **`NEXT_PUBLIC_` values are inlined at build time.** Changing the variable alone does nothing —
> the old URL is already baked into the JS bundle. A redeploy is required, every time.

`Frontend/lib/api.ts` uses it for REST, and `Frontend/lib/socket.ts` uses the same value for the
Socket.IO client, so one variable covers both. No trailing `/api` — the client appends the path.

### If you run the frontend locally too

`Frontend/.env.local`:

```
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

That path needs no tunnel at all and is the fastest loop while you are coding.

## A.5 Let the API accept the Vercel origin

Currently `FRONTEND_ORIGIN` is empty in compose, which means `cors_origins == ["*"]` — every browser
origin is accepted, including your Vercel deployment. That is why the preflight above passed.

Pin it once you know the Vercel URL. Create a **root** `.env` (not `backend/.env` — the image
excludes that file):

```
FRONTEND_ORIGIN=https://floodguard-ai.vercel.app
```

```bash
docker compose up -d backend      # env changes need a recreate, not a restart
```

`is_origin_allowed()` reads the same setting for both the REST CORS middleware and the Socket.IO
handshake, so one value covers both and they cannot drift.

## A.6 The quick-tunnel URL problem

A quick tunnel's URL is **random and changes every time the tunnel restarts.** Because
`NEXT_PUBLIC_API_BASE` is baked in at build time, that means **a Vercel redeploy every time you
restart the tunnel.** Annoying while developing; unusable as anything but a demo.

For a stable URL you need a **named tunnel**, which needs a Cloudflare account and a domain you
control:

1. Cloudflare Zero Trust → Networks → Tunnels → Create → `cloudflared`
2. Add a public hostname, e.g. `api.yourdomain.com` → service `http://backend:8000`
3. Copy the **token**
4. Root `.env`:

   ```
   CLOUDFLARE_TUNNEL_CMD=tunnel --no-autoupdate run
   TUNNEL_TOKEN=<the token>
   FRONTEND_ORIGIN=https://your-frontend.vercel.app
   ```

5. `docker compose --profile tunnel up -d`

Set that URL once in Vercel and it never changes. No domain to point at? ngrok free or Tailscale
Funnel are the alternatives; both are stable, both need an account.

## A.7 Security — read this before using a public tunnel

A quick-tunnel URL is unguessable but **not secret**, and this API has no user authentication:

| Exposure | Consequence |
|---|---|
| Browsers get the anonymous `dashboard` operator (`get_current_operator`) | anyone can read every reading, forecast, alert and flood polygon |
| `POST /api/demo/reset` is destructive and anonymous | anyone who finds the URL can **wipe the database** |
| `POST /api/iot/devices/register` | gated by `IOT_REGISTRATION_TOKEN`, so keep that default unchanged |
| Telemetry ingest | needs a per-device bearer token — that part is fine |

So: **never leave the tunnel running unattended with the default token**, and prefer a named tunnel
with Cloudflare Access in front of it. For a demo, stop the tunnel when you are not presenting.

Ports 5432 and 6379 are published to **loopback only** (`127.0.0.1`) rather than `0.0.0.0`. They
were briefly exposed to the whole LAN in an earlier revision, which would have let anything on your
WiFi try to log in to Postgres as a superuser with password `floodguard` — or reach a Redis with no
password at all. Override in a root `.env` if you genuinely need `psql` from another machine.

## A.8 When the WiFi changes (read this first)

Joining a different network changes your LAN address, and that is the one thing that can break the
setup. **Find your new address:**

```powershell
ipconfig | Select-String "IPv4"
# or, to confirm which adapter carries the default route:
Get-NetRoute -DestinationPrefix 0.0.0.0/0 |
  Where-Object NextHop -ne 0.0.0.0 |
  Select-Object InterfaceAlias, NextHop
```

### What you must change: anything holding your LAN IP

| Consumer | Uses | Change needed? |
|---|---|---|
| **Vercel frontend** | the **tunnel URL** | **No.** The tunnel is an outbound connection from your PC, so it survives any IP change. The URL stays valid. |
| **Docker Compose** | service names (`db`, `redis`, `backend`) over the internal bridge network | **No.** Nothing binds a host IP except `127.0.0.1`, which never moves. |
| **Frontend on `localhost` only** | `http://127.0.0.1:8000` | **No.** |
| **`Frontend/.env.local`** | `NEXT_PUBLIC_API_BASE=http://<lan-ip>:8000` (set for a LAN demo) | **Yes.** Restart `npm run dev` after editing. |
| **root `.env`** | `FRONTEND_ORIGIN` (set for a LAN demo) | **Yes.** Then `docker compose up -d`. |
| **ESP32 firmware** | a hardcoded host | **Yes, if it points at the LAN IP.** Change `HOST` to the new address. |

So if the firmware posts to `http://<old-ip>:8000/api/iot/telemetry`, update it to the new address. If
it posts to the tunnel hostname instead, nothing to do — which is the more robust option, because it
then also works from outside your WiFi.

```cpp
// Option 1: LAN only. Simplest, but breaks when you change WiFi.
const char* HOST = "192.168.0.101";   // <- your current address

// Option 2: the tunnel. Survives IP changes, works off-LAN. Needs TLS.
const char* HOST = "discrete-explaining-gel-look.trycloudflare.com";
```

> With a `trycloudflare.com` host you **must** use HTTPS and verify the certificate — you cannot
> point TLS at a bare IP. Use `https://<host>/api/iot/telemetry` and set the CA cert. With a named
> tunnel on your own domain (`api.yourdomain.com`) this is also the better long-term choice.

### Verify after switching networks

```powershell
# 1. the API is still listening on all interfaces
curl http://localhost:8000/healthz

# 2. and reachable on the NEW address
curl http://<new-ip>:8000/health

# 3. the browser origin CORS answers for (edit FRONTEND_ORIGIN otherwise)
curl -i -H "Origin: http://<new-ip>:3000" http://<new-ip>:8000/health
#    -> access-control-allow-origin: http://<new-ip>:3000

# 4. the databases are still NOT on the LAN
Test-NetConnection <new-ip> -Port 5432   # expect False
```

If step 3 fails, the tunnel process lost its connection — restart it with
`.\scripts\start-tunnel.ps1` and **redeploy Vercel**, because a quick tunnel's URL changes when it
does.

## A.9 Command reference

```bash
docker compose up -d --build                 # db + redis + migrate + backend
docker compose --profile tunnel up -d tunnel # public URL
.\scripts\start-tunnel.ps1                   # ...and print the Vercel value
docker compose logs -f backend
docker compose logs tunnel | Select-String trycloudflare
docker compose down                          # stop, keep data
docker compose down -v                       # stop and DELETE the database
```

| Symptom | Cause | Fix |
|---|---|---|
| Vercel console loads, every fetch fails | `NEXT_PUBLIC_API_BASE` still `localhost` | it is build-time — set it and **redeploy** |
| Fetch fails with no CORS error | tunnel is down, or the URL changed | `docker compose ps tunnel`; redeploy Vercel after a restart |
| CORS error in the browser | origin not allowed | add the Vercel origin to `FRONTEND_ORIGIN`, recreate `backend` |
| Socket.IO connects then drops | WebSocket upgrade blocked | it falls back to polling automatically; check `docker compose logs backend` |
| ESP32 cannot register | it is posting to a LAN IP, not the tunnel URL | point firmware at the public URL, or keep it on the LAN |
| `password authentication failed` | `pgdata` volume predates the password alignment | use the volume's original password, or `docker compose down -v` |

---

# PART B — Render (reference only)

Everything below documents the managed-PaaS path. It is not needed for Path A.
| `backend/Dockerfile` | Production image |
| `.dockerignore` | Root-context exclusions — secrets plus ~1 GB of dev artefacts |
| `backend/scripts/release.py` | Waits for the DB, enables PostGIS, runs `alembic upgrade head`, optionally loads OSM |
| `backend/Procfile` | Fallback for Render's *native* Python service (no Docker) |
| `docs/16-DEPLOY-RENDER.md` | This file |

### Changed files

| File | Change | Why |
|---|---|---|
| `backend/app/core/config.py` | Added `_force_asyncpg` validator, `database_require_ssl`, `database_host`, `database_requires_ssl`, `database_dsn`, `socketio_message_queue` | **Blocker.** Render supplies `DATABASE_URL=postgresql://…`; `create_async_engine` rejects it because SQLAlchemy resolves that scheme to the *blocking* psycopg2 driver |
| `backend/app/core/database.py` | `settings.database_url` → `settings.database_dsn`; added `pool_recycle=1800` | Uses the resolved DSN; recycles connections managed Postgres drops |
| `backend/alembic/env.py` | Deleted its own URL rewriting; now reads `settings.database_dsn` | One source of truth — the two paths could drift |
| `backend/app/main.py` | Added `GET /healthz`; removed the MQTT engine; `/health` now reports `ingest: "http"` | Render health checks must not inherit Open-Meteo's 20 s timeout |
| `backend/app/services/websocket.py` | Added `message_queue` to `AsyncServer` | Without it, an emit on worker A never reaches a client on worker B |
| `backend/requirements.txt` | `sqlalchemy` → `sqlalchemy[asyncio]`; `paho-mqtt` removed | **Blocker** (§2.2); HTTP-only transport |
| `backend/.env.example` | Added the managed-deployment block; corrected the DB password comment | Discoverability |

---

## 2. Two blockers that would each have failed the deploy

These are the reason this work was not just a Dockerfile.

### 2.1 `DATABASE_URL` driver mismatch — would fail immediately

Render, Heroku, Neon and Supabase all hand out a bare `postgresql://`. SQLAlchemy maps that to the
blocking psycopg2 driver, so `create_async_engine` raises
`ImportError: The asyncio extension requires an async driver` before the app can answer `/health`.

This was **invisible locally** because `alembic/env.py` already rewrote the scheme — migrations
connected while the app did not. Fixed once in `Settings`:

```python
@field_validator("database_url")
@classmethod
def _force_asyncpg(cls, value: str) -> str:
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    if value.startswith("postgresql://"):
        value = "postgresql+asyncpg://" + value[len("postgresql://"):]
    return value
```

Verified behaviour:

| Input | `settings.database_dsn` |
|---|---|
| `postgresql://u:p@dpg-abc.oregon.render.internal:5432/floodguard` | `postgresql+asyncpg://…?ssl=require` |
| `postgres://u:p@host/db` | `postgresql+asyncpg://…?ssl=require` |
| `postgresql+asyncpg://…@localhost:5432/floodguard` | unchanged, **no** `ssl=` |
| `postgresql://u:p@host/db?sslmode=disable` | `sslmode=disable` respected |
| + `DATABASE_REQUIRE_SSL=false` | `ssl=` stripped |

Render Postgres hosts look like `dpg-xxxx.region.render.internal`, which is not loopback, so
`ssl=require` is appended automatically — and Render accepts TLS on internal connections.

> **Proven in the container.** With `DATABASE_URL` pointed at a local Postgres through
> `host.docker.internal` (not loopback), the SSL heuristic appended `ssl=require` and the local
> server replied `PostgreSQL server ... rejected SSL upgrade`. Setting
> `DATABASE_REQUIRE_SSL=false` fixed it immediately. That escape hatch is the reason it exists.

### 2.2 `greenlet` missing — only breaks on a clean image

`requirements.txt` had `sqlalchemy>=2.0.30` without the `[asyncio]` extra. On Python 3.14 another
transitive dependency happens to satisfy `greenlet`, so it never surfaced. On the clean Python 3.13
image:

```
ImportError: The SQLAlchemy asyncio module requires that the Python 'greenlet' library is installed.
```

Fixed with `sqlalchemy[asyncio]>=2.0.30`. Confirmed in the rebuilt image:
`greenlet-3.5.6` is now installed.

### 2.3 `libexpat.so.1` missing — only breaks on a clean image

`debian-slim` has no libexpat, and the GDAL bundled inside the rasterio wheel links against it:

```
ImportError: libexpat.so.1: cannot open shared object file: No such file or directory
```

Fixed by adding `libpq5 libexpat1 libgomp1` to the apt layer.

> **Lesson:** all three are invisible on a developer machine with a warm, partially-solved
> environment. The container smoke test in §8 is what caught them.

---

## 3. Telemetry is HTTP-only — no broker anywhere

This is now the design, not a workaround. Render never had inbound ports, so a broker was never an
option; the point is that **nothing is lost by removing it.**

| | MQTT (removed) | HTTPS (kept) |
|---|---|---|
| Backend status | deleted | `POST /api/iot/telemetry` |
| Extra service that can be down at demo time | broker + credentials | none |
| Idempotency on `event_id` | no | **yes** |
| In-place location updates | no | **yes** |
| Node identity | taken from an unauthenticated topic | taken from a validated bearer token |

`app/services/ingest.py` is the single transport-neutral entry point. Both the HTTP endpoint and
Replay mode call `ingest_payload()`, which owns validation, node resolution, the
`rejected_readings` audit trail and idempotency. `telemetry.ingest_telemetry_reading()` remains the
shared pipeline beneath it.

```
POST /api/iot/telemetry          Replay mode
        │                              │
        │  bearer token                │  recorded dict
        ▼                              ▼
  api/iot.py (Pydantic)         demo_controller.py
        └──────────┬───────────────────┘
                   ▼
      services/ingest.py   validate · resolve node · audit · idempotency
                   ▼
      services/telemetry.py rise rate → risk → store → Redis/Socket.IO → Mithi pipeline
```

### Verified end to end, HTTP only, inside the container

```
/health -> {"status":"ok", … "database":"ok","redis":"ok","ingest":"http","open_meteo":"ok"}

register -> CTR-01 / active
POST #0   5.0 cm -> id=7760  accepted  risk=low
POST #1   6.8 cm -> id=7761  accepted  risk=low
…
POST #7  17.6 cm -> id=7767  accepted  risk=low

simulation_input.status : live
physical -> MITHI      : 17.6 cm -> 177.84 cm
forecasts               : 3
```

Statuses observed in that run: `201 accepted` ×8, `201 duplicate` on the replayed `event_id`,
`400` for `valid:false`, `400` for a stale timestamp, `422` for an out-of-range level, `401` with a
missing or wrong token, `429` once the 12/min per-device limit was exceeded.

`17.6 cm → 177.84 cm` is exactly the configured calibration:
`118 + (17.6 / 30) × (220 − 118) = 118 + 59.84 = 177.84`.

Also verified: replaying an `event_id` returns **HTTP 201** with the original row
(`status: duplicate`) — the endpoint has a fixed `status_code=201`, so the body is what
distinguishes the two; `valid: false` returns `400 "Sensor reported invalid reading"`; an
out-of-range level is caught by Pydantic and returns `422`; a missing or wrong bearer token
returns `401`; a mismatched `node_code` returns **403**; and 16 rapid POSTs yielded
`201` ×12 then `429` ×4 against the 12/minute limit.

Container logs contain **zero** broker lines.

---

## 4. Deploy in three steps

### Step 1 — create the blueprint

Render dashboard → **New → Blueprint** → select `KavyaBumiya/FloodGuard-AI`, `render.yaml` is
detected automatically. Or from the repo root:

```bash
render blueprint launch
```

Three resources are created:

| Resource | Plan | Notes |
|---|---|---|
| `floodguard-api` | `starter` | Docker image from `backend/Dockerfile` |
| `floodguard-db` | `basic-256mb` | Postgres 16, PostGIS enabled by the release command |
| `floodguard-redis` | `starter` | cache + Socket.IO message queue + event bus |

`plan: free` is deliberately not used: free web services spin down after inactivity, which stalls
the 30-second alert engine and the 60-second forecast loop.

### Step 2 — set the secrets the blueprint cannot guess

Render → `floodguard-api` → **Environment** → **Add**:

| Variable | Required | Notes |
|---|---|---|
| `FRONTEND_ORIGIN` | **yes in prod** | Your deployed frontend host, e.g. `https://floodguard.vercel.app`. Empty = `*` (allow all) |

`IOT_REGISTRATION_TOKEN` is auto-generated by the blueprint (`generateValue: true`). Read it from
the dashboard — you need it to register the ESP32.

> `FRONTEND_ORIGIN` also drives the Socket.IO handshake, because `AsyncServer` is constructed with
> `cors_allowed_origins=settings.is_origin_allowed`. Same check, one setting, no drift.

### Step 3 — first deploy, then load the OSM layers

The first release creates the schema but **not** the ~8,900 road/building rows — the impact counts
would read zero. After the first successful deploy:

```bash
render shell floodguard-api
cd backend && python -m scripts.release       # set RELEASE_LOAD_OSM=true
```

or set `RELEASE_LOAD_OSM=true` in the environment and trigger a redeploy. Leave it `false`
afterwards; re-running against a populated database only wastes deploy time.

---

## 5. Health checks: why `/healthz` and not `/health`

`render.yaml` uses `healthCheckPath: /healthz`.

`/health` probes the database, Redis **and** Open-Meteo over a 20 s `httpx` timeout. Render polls
the health path every few seconds to decide whether to route traffic. Pointing that at a third-party
API means a slow upstream gets a perfectly healthy instance killed. So:

| Endpoint | Touches | Use for |
|---|---|---|
| `/healthz` | nothing | platform liveness, load balancer |
| `/health` | DB + Redis + Open-Meteo | your own diagnostics, dashboards |

Verified in the container:

```
GET /healthz  -> 200  {"status":"ok","service":"floodguard-ai","version":"1.0.0"}
GET /health   -> 200  {"status":"ok", … "ingest":"http"}
GET /socket.io/?EIO=4&transport=polling
              -> 200  0{"sid":"…","upgrades":["websocket"], …}
```

---

## 6. Migrations: why `releaseCommand`, not startup

`render.yaml`:

```yaml
releaseCommand: python -m scripts.release
```

Render runs this **once**, before the new image is promoted. Alembic therefore never races the
outgoing instances that are still serving traffic — which is what happens if you migrate in the
lifespan hook, and why `main.py` only *verifies* the schema and warns instead of migrating.

`scripts/release.py` steps:

1. **Wait for the DB** — managed Postgres is often still booting when the release command fires.
2. **`CREATE EXTENSION IF NOT EXISTS postgis`** — Render ships PostGIS but does not enable it per
   database by default, and every geometry column needs it.
3. **`alembic upgrade head`** — **in a worker thread**, see the note below.
4. **`load_osm_layers`** when `RELEASE_LOAD_OSM=true`.

Every step is idempotent, so re-running a failed release is safe. Failure mode is a clean message,
not a traceback:

```
[release] waiting for db-render.example.internal (up to 1s)
[release]   attempt 1/2: gaierror: [Errno -2] Name or service not known
[release] database never became reachable: [Errno -2] Name or service not known
```

### The nested-`asyncio.run` trap

`alembic/env.py` ends with `asyncio.run(run_migrations_online())`. Calling it from inside an
already-running loop raises:

```
RuntimeError: asyncio.run() cannot be called from a running event loop
```

Since `release.py` is itself async, migrations must run via
`await asyncio.to_thread(run_alembic_upgrade)`. Found by running it; it would have failed **every**
deploy otherwise.

---

## 7. ESP32 firmware

Since the transport is a plain HTTPS POST, the firmware is small:

```cpp
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ArduinoHttpClient.h>

// CA cert for the Render domain (Let's Encrypt -> ISRG Root X1)
const char* CA = R"(-----BEGIN CERTIFICATE-----
MIIFYDCCAoigAwIBAgILBAAAAAABIVhTCKIwDQYJKoZIhvcNAQELBQAwTDEgMB4GA1UE
...
-----END CERTIFICATE-----)";

const char* HOST  = "floodguard-api.onrender.com";
const char* PATH  = "/api/iot/telemetry";
const char* TOKEN = "<device token from registration>";

WiFiClientSecure wcs;
wcs.setCACert(CA);
WiFiClientSecure* secure = new WiFiClientSecure(wcs);   // ArduinoHttpClient takes ownership
ArduinoHttpClient https(*secure);
https.setTimeout(20000);            // see the timing note below

void sendReading(float levelCm, float rainMmHr, uint32_t seq) {
  char body[320];
  snprintf(body, sizeof(body),
    "{\"node_code\":\"VAD-ESP32-01\",\"water_level_cm\":%.2f,\"rainfall_mm_hr\":%.2f,"
    "\"timestamp\":\"%s\",\"event_id\":\"esp32-%lu\",\"battery_voltage\":%.2f,\"valid\":true}",
    levelCm, rainMmHr, isoNowUtc(), (unsigned long)seq, batteryVolts());

  https.addHeader("Authorization", String("Bearer ") + TOKEN);
  https.addHeader("Content-Type", "application/json");
  int code = https.POST(String("https://") + HOST + PATH, body);

  // 201 + status="accepted"  = stored
  // 201 + status="duplicate" = same event_id, already stored (retry-safe)
  // 400 = ingest-level rejection (audited in rejected_readings)
  // 422 = Pydantic schema rejection (bad type or out of range)
  // 403 = node_code does not match this device's token
  // 429 = rate limited; back off for Retry-After seconds
  if (code == 201) { lastOk = millis(); }
}
```

### Timing you must design around

Measured on the real DEM (`flood_simulation.FloodSimulationEngine`):

| Stage | Cost |
|---|---|
| load terrain (Redis cache hit) | 30.6 ms |
| interpolate water surface | 2.4 ms |
| depth arithmetic | 0.8 ms |
| **connectivity mask (BFS + shapely)** | **158.3 ms** |
| risk classification | 12.3 ms |
| vectorise to GeoJSON | 58.1 ms |
| **one flood simulation** | **231.9 ms** |
| **four steps per reading (synchronous, inside the POST)** | **927.6 ms** |

So **the POST takes about one second to return.** Set the client timeout to ≥ 10 s; the common 5 s
default produces phantom timeouts and a retry storm.

At a 5 s cadence this is ~11 % of one core for a single device. A 10 s cadence is comfortable.

### Two things to get right in firmware

1. **`event_id` must be stable per reading** (a counter works). That is what makes a retry after a
   Render cold start return `200 duplicate` instead of inserting twice.
2. **Send unambiguous UTC.** `2026-10-06T02:47:09Z`, not a local time with a hand-written offset.
   During testing, a mislabelled offset made 8 valid readings look 5.5 hours stale — the freshness
   gate correctly rejected them and the UI showed `WAITING FOR REAL IoT INPUT`. That is the system
   working as designed, but it looks like a failure if you do not expect it.

---

## 8. Reproducing the verification locally

```bash
cd D:\Projects\Flood-Monitoring-System

# 1. build (context must be the repo root)
docker build -f backend/Dockerfile -t floodguard-api:test .

# 2. run with a Render-shaped environment
docker run -d --name fg-smoke -p 18000:8000 \
  -e APP_ENV=production \
  -e DATABASE_URL="postgresql://u:p@db.example.internal:5432/floodguard" \
  -e REDIS_URL="redis://cache.example.internal:6379/0" \
  -e FRONTEND_ORIGIN="https://floodguard.example.com" \
  -e IOT_REGISTRATION_TOKEN="smoke-test-token" \
  floodguard-api:test

# 3. check
curl http://127.0.0.1:18000/healthz
curl http://127.0.0.1:18000/health
curl "http://127.0.0.1:18000/socket.io/?EIO=4&transport=polling"

# 4. register a device, then push readings
curl -X POST http://127.0.0.1:18000/api/iot/devices/register \
  -H "Authorization: Bearer smoke-test-token" -H "Content-Type: application/json" \
  -d '{"node_code":"CTR-01","node_type":"physical","lat":22.3072,"lng":73.1812,
       "reference_elevation_m":1.2,"warning_threshold_cm":25}'

# 5. confirm no secret leaked into the image
docker run --rm --entrypoint sh floodguard-api:test \
  -c "find / -name '.env*' -not -path '*/site-packages/*'"

# 6. clean up
docker rm -f fg-smoke && docker rmi floodguard-api:test
```

### Results from the verified run

| Check | Result |
|---|---|
| Build | succeeded, ~110 s |
| Image size | **1.08 GB** |
| `paho-mqtt` in image | **absent** — `greenlet` present |
| `GET /healthz` | 200 |
| `GET /health` | 200, `"ingest": "http"` |
| `schema_verified` | 16 tables |
| Socket.IO handshake | 200, `upgrades: ["websocket"]` |
| Register + 8 telemetry POSTs | all `accepted` |
| `simulation_input.status` | `live`, `17.6 cm → 177.84 cm`, 3 forecasts |
| Broker activity in logs | **none** |
| Model loading in-container | all 3 `.joblib`, `random_forest`, `n_features_in_ = 11` |
| `backend/.env` in image | **absent** — only `.env.example` |
| Backend tests after changes | **22 passed** |
| Frontend typecheck | clean |

### Image size breakdown

| Layer | Size |
|---|---|
| `python:3.13-slim-bookworm` | 85 MB |
| apt (`libpq5`, `libexpat1`, `libgomp1`) | 19 MB |
| `pip install -r requirements.txt` | 557 MB |
| `models/flood/*.joblib` | 98.6 MB |
| `data/` (osm + terrain + rainfall) | 17 MB |
| `backend/` + `config/` | < 1 MB |

The 557 MB is numpy + scipy + scikit-learn + rasterio + netCDF4 + pydantic-core — the honest cost of
the geo/ML stack; nothing here is bloat. Dropping `paho-mqtt` saved ~2 MB.

**Build context** dropped from **1.14 GB → 98 MB** via `.dockerignore`.

**To shrink further**, add to `.dockerignore`:

```
models/flood/
```

That saves ~94 MB and every feature keeps working — `forecast_service.load_models()` falls back to
`PersistenceModel()` and logs `forecast_model_not_found`.

### One deliberate deviation: Python 3.13, not 3.14

`requirements.txt` records 3.14 as "verified working". The image uses 3.13 because rasterio, scipy,
shapely and netCDF4 do not all publish 3.14 wheels on every platform, and a source compile in a
Docker build is a multi-minute failure. Nothing in the code is 3.14-specific.

---

## 9. Environment variable reference for Render

### Managed (set automatically by the blueprint)

| Variable | Source |
|---|---|
| `DATABASE_URL` | `fromDatabase: floodguard-db` |
| `REDIS_URL` | `fromService: floodguard-redis` |

### Set by you

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `production` | Not `development`, so the loopback CORS regex is not added |
| `FRONTEND_ORIGIN` | — | Comma-separated browser origins. **Set this in production** |
| `IOT_REGISTRATION_TOKEN` | generated | Gates `POST /api/iot/devices/register` |

### Optional

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_REQUIRE_SSL` | auto | `false` if your Postgres rejects TLS |
| `SOCKETIO_MESSAGE_QUEUE` | `true` | Only set `false` if Redis is unreachable |
| `RELEASE_DB_WAIT_ATTEMPTS` | `30` | |
| `RELEASE_LOAD_OSM` | `false` | `true` for the first deploy on a fresh DB |
| `ALLOW_SYNTHETIC_TERRAIN` | `false` | **Leave false.** The flood engine should fail loudly rather than report fabricated inundation |
| `DEMO_MODE_ENABLED` | `false` | Unused by the code anyway |
| `OPENTOPOGRAPHY_API_KEY`, `CESIUM_ION_TOKEN`, `OPEN_METEO_API_BASE` | — | Unused by the core product |

There is **no** `MQTT_*` variable. If you see one in an old `.env`, delete it.

---

## 10. Scaling, workers, and what is safe to change

**Leave `--workers 1`.** The async engines are per-process and **not** idempotent:

| Engine | Interval | Consequence of N workers |
|---|---|---|
| `forecast_service` | 60 s | N × 3 forecast rows per node per minute |
| `alert_engine` | 30 s | duplicate alerts (dedup mitigates but the work is duplicated) |

Removing MQTT also removed a per-process hazard: every uvicorn worker used to hold its own paho
connection and re-ingest every message.

`main.py` isolates each engine's startup failure, so raising the worker count will not crash — it
will quietly multiply the write load.

**What *is* safe to raise:** `numInstances` in `render.yaml` (scale the web service horizontally).
Socket.IO fan-out across instances already works, because `app/services/websocket.py` passes
`message_queue=REDIS_URL` to `AsyncServer` and `event_bridge.py` relays Redis pub/sub to
Socket.IO.

---

## 11. Frontend

| Option | Notes |
|---|---|
| **Vercel** | `cd Frontend && vercel`. Set `NEXT_PUBLIC_API_BASE=https://floodguard-api.onrender.com` |
| Render static site | Only with `output: 'export'`, which the App Router supports, but it forces every page to be a client component |
| Any static host | `npm run build` then serve `.next` output |

Whichever you pick, add that origin to `FRONTEND_ORIGIN` on the API — CORS is enforced server-side
and a mismatch will look like "the console loads but every call fails".

---

## 12. Operational notes

| Topic | Reality |
|---|---|
| **Ephemeral filesystem** | Discarded on every deploy. Everything durable already lives elsewhere: telemetry in Postgres, the terrain grid in Redis (24 h TTL), models baked into the image. Nothing is written to local disk at runtime. |
| **Spin-down** | `plan: free` idles out after ~15 min and the first request can take ~50 s. The Socket.IO client reconnects automatically, and the frontend falls back to its 3 s REST poll meanwhile — so the console shows `degraded`, not `offline`. |
| **Cold start** | `load_models()` reads ~94 MB of `.joblib` from disk at startup. The first request after a spin-down pays that once. |
| **POST latency** | ~1 s, dominated by four synchronous flood simulations. Not a bug, but it shapes the firmware timeout (§7). |
| **Database growth** | Unbounded: 3 forecast rows per node per minute, 12 `flood_layers` rows per reading. `POST /api/demo/reset` is the only cleanup. A nightly cron hitting it is the pragmatic answer on Render. |
| **Secrets** | `backend/.env` is gitignored and `.dockerignore`d, and the image was verified to contain only `.env.example`. Rotate `IOT_REGISTRATION_TOKEN` if the old placeholder was ever committed. |
| **Build time** | ~110 s with a warm pip layer; longer on the first build. `requirements.txt` is copied before the application so that layer caches independently. |

---

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ImportError: The asyncio extension requires an async driver` | `DATABASE_URL` arrived as `postgresql://` and the app is on an **old image** | Rebuild — the fix is in `Settings._force_asyncpg` |
| `The SQLAlchemy asyncio module requires … 'greenlet'` | image built before the `[asyncio]` fix | Rebuild |
| `libexpat.so.1: cannot open shared object file` | image built before the apt fix | Rebuild |
| `PostgreSQL server … rejected SSL upgrade` | host is not loopback so `ssl=require` was appended, but the server refuses TLS | `DATABASE_REQUIRE_SSL=false`, or put `?sslmode=disable` in the URL |
| `password authentication failed` | URL password ≠ the database's actual password | check `POSTGRES_PASSWORD` / the volume's original password |
| `asyncio.run() cannot be called from a running event loop` | release script regressed | migrations must use `await asyncio.to_thread(...)` (§6) |
| Release command: `database never became reachable` | Postgres still booting, or wrong host | Raise `RELEASE_DB_WAIT_ATTEMPTS`; check `DATABASE_URL` |
| `CREATE EXTENSION postgis` fails | provider has no PostGIS | Neon/Supabase: enable the extension in the dashboard first |
| `relation "sensor_nodes" does not exist` | migrations never ran | check `releaseCommand` is set; run `python -m scripts.release` in a Shell |
| Affected buildings/roads = 0 | OSM layers not loaded | set `RELEASE_LOAD_OSM=true`, redeploy once |
| `RuntimeError: Terrain unavailable — flood simulation cannot run` | `dem_mithi.tif` missing from the image | confirm `COPY data/terrain/` is not excluded by `.dockerignore` |
| Every POST takes ~1 s and times out at 5 s | four flood simulations per reading | firmware timeout ≥ 10 s (§7) |
| Console shows `WAITING FOR REAL IoT INPUT` | no `source="iot"` reading in the last 5 min, **or** the device sent a timestamp with a wrong UTC offset | send `...Z` timestamps |
| `insufficient_mapped_iot_history` in the log | fewer than 8 IoT readings exist | send 8 at the intended cadence |
| Console shows `DEGRADED` | REST fine, Socket.IO down | check `/health` for a Redis error; confirm `SOCKETIO_MESSAGE_QUEUE=true` |
| CORS error in the browser | origin not allowed | add the frontend origin to `FRONTEND_ORIGIN` |
| 401 on device registration | token mismatch | read the current value from the Render dashboard |
| 400 "Sensor reported invalid reading" | device sent `valid: false` | intentional; audited in `rejected_readings` |
| 403 on telemetry | body `node_code` ≠ the authenticated device | fix the firmware's `node_code` |