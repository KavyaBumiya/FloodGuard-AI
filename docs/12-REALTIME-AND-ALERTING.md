# 12 — Real-time Delivery and the Alert Engine

---

# PART A — The real-time chain

```
  ESP32 / curl
      │  HTTPS  POST /api/iot/telemetry      (Authorization: Bearer <device token>)
      ▼
  ┌───────────────────────────────────────────────────────────────────────────────┐
  │ api/iot.py (Pydantic)  →  services/ingest.py  →  telemetry.ingest_telemetry… │
  │                        (validate · resolve node · audit · idempotency)        │
  └───────────────────────────────────────────────────────────────────────────────┘
      │ writes DB, commits
      ▼
  domain services publish envelopes
      │   telemetry.py        → flood.sensor.updated , flood.risk.updated
      │   forecast_service.py → flood.forecast.updated  (×3)
      │   alert_engine.py     → flood.alert.updated
      │   scenario_service.py → flood.scenario.updated
      │   demo_controller.py  → flood.mode.changed
      │   mithi_demo_pipeline → flood.mithi.updated
      ▼
  publish_realtime_event(event)                     websocket.py:33-38
      │   await redis.publish("floodguard:realtime", json.dumps(event))
      ▼
  Redis pub/sub
      ▼
  EventBridge._run()                               event_bridge.py:32-45
      │   pubsub.subscribe("floodguard:realtime")
      │   async for message in pubsub.listen():
      │        event = json.loads(message["data"])
      │        await sio.emit(event["event"], event, room="global_feed")
      │        if payload.node_code:
      │            await sio.emit(event["event"], event, room=f"node_{node_code}")
      ▼
  Socket.IO  /socket.io
      ▼
  Frontend lib/socket.ts getSocket()
      │   transports: ['websocket','polling'], infinite reconnect, 500 ms→5 s backoff
      ▼
  useFloodguard.ts  → 8 listeners  →  refreshAll()  →  Redux  →  every page
```

---

## A.1 The envelope

```json
{
  "event":   "flood.sensor.updated",
  "payload": { … domain-specific … },
  "timestamp":"2026-10-06T03:45:02.113+00:00",
  "source":  "iot"
}
```

The bridge forwards the envelope **verbatim** as the Socket.IO payload (with the event name as the
Socket.IO event), so a client handler receives the whole envelope.

## A.2 The event catalogue

| Event | Emitted by | Payload keys |
|---|---|---|
| `flood.sensor.updated` | `telemetry.py:158` | `node_code`, `timestamp`, `water_level_cm`, `rainfall_mm_hr`, `flow_rate`, `soil_moisture`, `battery_voltage`, `risk_level`, `risk_score`, `source` |
| `flood.risk.updated` | `telemetry.py:175` | `node_code`, `horizon_minutes` (=0), `risk_level`, `risk_score`, `primary_driver`, `predicted_level_cm`, `components[]` with `{name, value, normalized, weight, contribution, band}` |
| `flood.forecast.updated` | `forecast_service.py:484` | `node_id`, `horizon_minutes`, `predicted_level_cm`, `lower_bound_cm`, `upper_bound_cm`, `target_time`, `status` |
| `flood.alert.updated` | `alert_engine.py:1005` | `alert_id`, `node_id`, `scenario_id`, `alert_type`, `severity`, `message`, `is_active`, `acknowledged`, `action` ∈ `created\|updated\|cleared\|acknowledged` |
| `flood.scenario.updated` | `scenario_service.py:386` | `scenario_id`, `status` ∈ `completed\|failed`, `data` |
| `flood.mode.changed` | `demo_controller.py:263` | `mode`, `replay_speed`, `recording` |
| `flood.mithi.updated` | `mithi_demo_pipeline.py:90` | `input_source: "iot"`, `simulation_source: "iot-demo"`, `label`, `steps: {0:{flooded_area_m2,max_depth_m}, 15:…, 30:…, 60:…}`, `updated_at` |
| `flood.layer.updated` | **declared but never published** — no emitter exists in the codebase | — |

> ⚠️ `flood.layer.updated` is documented in `websocket.py:9` and typed in `lib/socket.ts:17`, and the
> frontend subscribes to it — but nothing in `app/` ever calls `publish_realtime_event` with that
> name. The map still refreshes because it polls every 3 s.

> ⚠️ `flood.mithi.updated` is emitted and subscribed but is **missing from the `RealtimeEventName`
> union** in `lib/socket.ts:14-21`.

## A.3 Socket.IO server

```python
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=_settings.is_origin_allowed,   # same check as REST CORS
    logger=False, engineio_logger=False,
)
GLOBAL_ROOM = "global_feed"
```

| Handler | Behaviour |
|---|---|
| `connect(sid, environ, auth)` | auto-joins `global_feed`, then emits `connected` with `{message, room, hint}`. The comment (lines 55-58) records why: "Every emitter targets room `global_feed`, so a client that never sent an explicit `subscribe` would silently receive nothing and look like a broken live feed." |
| `disconnect(sid)` | logs |
| `subscribe(sid, data)` | `enter_room(sid, data.room ?? "global_feed")`, emits `subscribed` |
| `unsubscribe(sid, data)` | `leave_room` |

Per-node rooms `node_<node_code>` are emitted by the bridge but the frontend never subscribes to them.

## A.4 Mounting

`main.py:213-227`:

```python
socket_app = socketio.ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io")
app = socket_app
```

Both `uvicorn app.main:app` and `uvicorn app.main:socket_app` now serve REST **and** the socket, and
`engineio.ASGIApp` forwards the `lifespan` scope so the FastAPI startup still runs.

## A.5 The Redis connection split

| Client | Where | Why |
|---|---|---|
| cache singleton | `integrations/cache.py:31` `get_redis()` — module-level, `decode_responses=True` | shared for `GET`/`SETEX` |
| event bridge | `event_bridge.py:68` — a **dedicated** connection, also `decode_responses=True` | comment: "a client in subscribe mode cannot issue other commands, so it must not be the shared cache client." |
| `/health` probe | `main.py:169` — a short-lived client per call, closed with `aclose()` | health must not poison the pool |

## A.6 Client-side connection semantics

`hooks/useFloodguard.ts` — five states, and the distinction matters:

| State | Meaning | Trigger |
|---|---|---|
| `connecting` | initial | store default |
| `live` | REST **and** socket both healthy | `connect` event, or a successful poll with `socket.connected` |
| `degraded` | REST healthy, socket down | `disconnect`, `connect_error`, or a successful poll without `socket.connected` |
| `reconnecting` | reconnecting | typed in `CONNECTION_META` but **never dispatched** by the hook |
| `offline` | REST failing | a failed poll |

The design intent, stated at `constants.ts:147-149` and `types/index.ts:289-293`:
> "`degraded` is deliberately distinct from `offline`: REST polling still returns fresh data while
> the realtime socket is unavailable. Reporting that as 'Offline' would imply the numbers on screen
> are stale when they are not."

### The 3-second safety net

```ts
const timer = setInterval(() => {
  void refreshAll().then((ok) => {
    if (disposed) return
    if (!ok) setConnection('offline')
    else if (socket.connected) setConnection('live')
    else setConnection('degraded')
  })
}, 3000)
```

Five parallel REST calls per tick: `/api/flood/nodes`, `/api/flood/current-summary`,
`/api/alerts/active`, `/api/flood/impact-summary`, `/api/demo/status` (the last one is
`.catch(() => null)` so a demo-status failure cannot break the refresh).

Combined with the DOM-timed handlers, the effective behaviour is: **the UI is at most ~3 s stale
regardless of the socket.** The socket is a latency optimisation, not the data path.

## A.7 Telemetry ingress (HTTP only)

There is one ingestion path. The broker, its config file, its background engine and the `paho-mqtt`
dependency have all been removed; see [`09-CONFIGURATION-AND-ENV.md`](09-CONFIGURATION-AND-ENV.md)
§7 and [`16-DEPLOYMENT.md`](16-DEPLOYMENT.md) §3.

### Request shape — `POST /api/iot/telemetry`

```json
{ "node_code": "VAD-ESP32-01",     // must match the bearer token's device
  "water_level_cm": 14.5,          // required
  "rainfall_mm_hr": 6.2,           // optional, defaults 0.0
  "timestamp": "2026-10-06T03:45:00Z",   // required, UTC
  "valid": true,
  "flow_rate": null, "soil_moisture": null, "battery_voltage": null,
  "event_id": "evt-0001" }
```

`source` is **not** accepted from the device — the server sets `source="iot"` for authenticated
hardware, so a client cannot claim to be a simulated or replay source.

> Node identity is taken from the **bearer token** (`get_current_device`), and the body's
> `node_code` must agree or the request is `403`. This is the guarantee the old broker could not
> give: an anonymous publisher could name itself `MR-V…` and self-register a node.

### Response

The endpoint declares `status_code=201`, so **every success is 201** and the body distinguishes
them:

| Status | Body | Meaning |
|---|---|---|
| `201` | `{"id":N,"status":"accepted",…}` | stored and processed |
| `201` | `{"id":N,"status":"duplicate",…}` | same `event_id` already stored — **the retry-safe path**, `N` is the original row |
| `422` | `{"detail":[…]}` | **Pydantic schema** failure: missing field, wrong type, or a `ge`/`le` range breach (`water_level_cm` 0–500, `rainfall_mm_hr` 0–500). Rejected before `ingest.py` runs, so these are *not* written to `rejected_readings` |
| `400` | `{"detail":"…"}` | **ingest-level** rejection: `valid:false`, timestamp stale or unparseable, non-finite number. Written to `rejected_readings` |
| `401` | — | missing/malformed bearer token — raised by the auth dependency, before rate limiting |
| `403` | — | token's node ≠ body `node_code`, or the node is deactivated |
| `429` | `{"detail":"…"}` + `Retry-After` | per-device rate limit (`IOT_TELEMETRY_RATE_LIMIT_PER_MINUTE`, default 12/min) |

Note the field is **`id`**, not `reading_id`. The response is
`{id, node_code, status, risk_level, risk_score, received_at}`.

### Validation — `services/ingest.py`

| Check | Failure reason string |
|---|---|
| `float(payload["water_level_cm"])` | `Invalid numeric field: …` |
| `float(payload.get("rainfall_mm_hr", 0.0))` | same |
| `timestamp` present | `Missing timestamp` |
| `datetime.fromisoformat(ts.replace("Z","+00:00"))` | `Invalid timestamp format` |
| within ±24 h of server time | `Timestamp outside ±24h of server time: …` |
| `source in {iot,simulated,replay,scenario}` | `Invalid source: {source}` |
| `0 <= water_level_cm <= 500` | `Water level out of range: {x}` |
| `0 <= rainfall_mm_hr <= 500` | `Rainfall out of range: {x}` |
| `0 <= soil_moisture <= 1` | `Soil moisture out of range: {x}` |
| `payload["valid"] is not False` | `Sensor reported invalid reading` |

Invalid payloads go to `rejected_readings` and are **logged, never silently dropped** (PRD Feature 6
acceptance criterion).

### Node auto-creation policy

```python
if node exists: return node
source = payload.get("source", "iot")
if source not in ("simulated", "scenario", "replay"):
    logger.warning("unregistered_node_rejected", …,
                   hint="register the device via POST /api/iot/devices/register first")
    return None
# else create a virtual node at payload lat/lon (default 72.8830 / 19.0713)
```

Real hardware must be registered first; only `simulated` / `scenario` / `replay` readings may create
a virtual node. With HTTPS this is no longer a security boundary — it is a data-hygiene rule —
but it is retained because the replay and demo paths still depend on it.

### What was removed

| Removed | Replaced by |
|---|---|
| `services/mqtt_ingestion.py` | `services/ingest.py` (`ingest_payload()`) |
| `backend/deploy/mosquitto/mosquitto.conf` | — (nothing) |
| `mqtt` compose service, ports 1883/9001 | — |
| `MQTT_BROKER_*`, `MQTT_TOPIC_PREFIX`, `MQTT_ENABLED` | — |
| `paho.Client` / `connect_async` / `loop_start()` reconnect loop | request lifecycle, handled by uvicorn |
| `MQTTService` start/stop in `main._start_engines()` | — |

### Operational effect

| | With a broker | HTTP only |
|---|---|---|
| Extra dependency at demo time | broker process + credentials | none |
| Duplicate delivery | re-ingested as new rows | rejected as `duplicate` via `event_id` |
| In-place node metadata updates | not possible | supported (optional `lat`/`lng`/`warning_threshold_cm`) |
| Ingress on a PaaS (Render) | needs a public port | works over the normal HTTPS port |
| Per-connection auth | none | SHA-256 bearer token, constant-time compare |

The `app/__init__.py` selector-loop policy is **kept** even though paho is gone — `sqlalchemy[asyncio]`
and `aiosqlite` need it on Windows.

---

# PART B — The Alert Engine

`backend/app/services/alert_engine.py` (1,121 lines). A 30-second loop over every active node.

## B.1 Loop

```python
self._eval_interval_seconds = 30
_run():  while self._running: evaluate_all(); await asyncio.sleep(30)
evaluate_all(scenario_id=None):
    SELECT * FROM sensor_nodes WHERE is_active IS TRUE
    for node: _evaluate_node(db, node, scenario_id)
    COMMIT
```

`_evaluate_node` runs five checks in order:

1. `_check_threshold_alert` (needs a reading)
2. `_check_rapid_rise_alert` (needs a reading + a previous reading)
3. `_check_predicted_risk_alert` (reads `forecasts` directly)
4. `_check_communication_alert` (reads the newest `recorded_at`)
5. `_check_sensor_fault_alert` (reads `rejected_readings`)

Checks 2, 4 and 5 **return immediately when `scenario_id is not None`** — scenario runs get
threshold and predicted-risk alerts only.

## B.2 Rule 1 — `threshold`

**Config:** `warning = sensor_nodes.warning_threshold_cm or 150.0`, `critical = 200.0`.

| Condition | Severity | Threshold type |
|---|---|---|
| `level >= 200` | `critical` | critical |
| `level >= warning` | `high` | warning |
| else | — | **clear** all active `threshold` alerts for this node |

Message:
```
{node_code} water level {level:.0f} cm exceeds {type} threshold ({value:.0f} cm)
```
`trigger_data = {"level_cm": level, "threshold_cm": threshold_value}`

Existing alert → severity is upgraded/downgraded and `created_at` is **reset to now** (line 364-366)
so the UI shows "just now" on a real severity change. No alert → create + flush + publish.

> **Hysteresis is configured (`threshold_hysteresis_cm: 10`) but not applied.** The clear branch is
> a plain `level < warning_thresh`. `HYSTERESIS["water_level_cm"]` is only ever read… nowhere.
> Rapid-rise does use its own hysteresis.

## B.3 Rule 2 — `rapid_rise`

Skipped for scenarios. Finds the previous `iot`/`simulated` reading before the current one.

```python
interval_seconds = (reading.recorded_at - previous.recorded_at).total_seconds()
if interval_seconds <= 0: return
rise_rate = calculate_rise_rate(cur, prev, interval_seconds)      # cm per 5 min, ≥ 0
threshold = 5.0
if rise_rate >= threshold:
    severity = high if rise_rate < threshold*2 else critical
    … create/update, trigger_data={"rise_rate_cm_per_5min": rise_rate}
else:
    if rise_rate < threshold - HYSTERESIS["rapid_rise_cm_per_5min"]:   # < 0.0
        _clear_alert(...)
```

> ⚠️ Because `HYSTERESIS["rapid_rise_cm_per_5min"]` is 5.0 and `threshold` is 5.0, the clear
> condition is `rise_rate < 0.0` — **never true**, since `calculate_rise_rate` clamps at 0. So a
> rapid-rise alert, once raised, can **never clear** except by acknowledging. Confirmed against
> `config/risk_thresholds.json`: `rapid_rise_cm_per_interval: 5` and the code default for the
> hysteresis is 5.0.

## B.4 Rule 3 — `predicted_risk`

```sql
SELECT * FROM forecasts
WHERE node_id = ? AND scenario_id = ? AND horizon_minutes IN (15,30,60) AND status = 'ok'
ORDER BY horizon_minutes
```

| Predicted level | Severity |
|---|---|
| `>= 200` | `critical` |
| `>= 150` | `high` |
| else | skip (no alert, **and no clearing** — see below) |

Message: `{node_code} forecast +{h}min: {pred:.0f} cm ({SEVERITY})`
`trigger_data = {"horizon_minutes": h, "predicted_level_cm": pred}`

Dedup is per horizon: `_get_active_alert(..., horizon_minutes=h)` filters on
`trigger_data["horizon_minutes"]`, matching in Python to avoid a join
(comment at lines 828-830: "Horizon matching is done against trigger_data to avoid unnecessary joins
against Forecast").

> ⚠️ **No auto-clear.** If the forecast drops back below 150, nothing clears the existing alert. It
> stays active until the next cycle re-evaluates… which `continue`s past it. So a predicted-risk
> alert is effectively permanent once raised. Only a `POST /api/demo/reset` or the
> `min(pred-10, pred+10)` bound drifting down removes it.

## B.5 Rule 4 — `communication_failure`

Skipped for scenarios. Reads the newest `recorded_at` across `iot`/`simulated`. **Never-reported
nodes are skipped** (returns early) — so a registered device that has never sent anything produces
no alert.

| Age | Severity | State |
|---|---|---|
| `>= 15 min` (`offline_minutes`) | `critical` | offline |
| `>= 5 min` (`stale_minutes`) | `warning` | stale |
| else | — | **clear** |

Message: `{node_code} communication {state}: no data for {minutes:.0f} minutes`
`trigger_data = {"minutes_since_last_reading": minutes}`

Because the message embeds the elapsed minutes and the comparison also checks
`existing.message != message`, this alert's row is **rewritten on every cycle** while the node is
down. Combined with `ORDER BY severity DESC, created_at DESC`, it appears correctly ordered.

## B.6 Rule 5 — `sensor_fault`

Skipped for scenarios.

```sql
SELECT * FROM rejected_readings
WHERE node_code = ? AND received_at >= now() - 1 hour
ORDER BY received_at DESC LIMIT 3
```

If 3 rows exist → one `high` alert: `{node_code} sensor fault: {n} invalid readings in last hour`,
`trigger_data = {"rejected_count": n}`. If the alert already exists → return (no duplicate, no update).

## B.7 Deduplication (`_get_active_alert`, line 815)

```python
query = SELECT Alert WHERE node_id = ? AND alert_type = ? AND is_active IS TRUE AND scenario_id = ?
ORDER BY created_at DESC, id DESC
rows = list(result.scalars().all())
if horizon_minutes is not None:
    rows = [r for r in rows if (r.trigger_data or {}).get("horizon_minutes") == horizon_minutes]
if not rows: return None
rows.sort(key=lambda r: (severity_order[r.severity], r.created_at), reverse=True)
kept = rows[0]
for duplicate in rows[1:]:
    duplicate.is_active = False
    duplicate.cleared_at = now()
    duplicate.trigger_data["superseded_by"] = kept.id
    duplicate.trigger_data["superseded_reason"] = "deduplication: kept more severe/recent alert"
return kept
```

Severity order: `critical 4 > high 3 > warning 2 > information 1 > other 0`.

**Nothing is deleted.** Superseded rows become inactive with an audit trail pointing at the survivor.

## B.8 Clearing (`_clear_alert`, line 927)

```python
SELECT * FROM Alert WHERE node_id = ? AND alert_type = ? AND is_active IS TRUE AND scenario_id = ?
for alert in alerts:
    alert.is_active = False
    alert.cleared_at = now()
    publish("flood.alert.updated", action="cleared")
```

The `db.commit()` happens in `evaluate_all` (line 181), not per-clear.

## B.9 Acknowledgement

```python
async def acknowledge_alert(alert_id, acknowledged_by):
    alert = await db.get(Alert, alert_id)
    if alert:
        alert.acknowledged    = True
        alert.acknowledged_at = now(UTC)
        alert.acknowledged_by = acknowledged_by
        commit
        publish(action="acknowledged")
        return alert
```

Acknowledged alerts **remain active** — acknowledgement is an audit flag, not a resolution.
`acknowledged_by` defaults to `"operator"` from the route; `auth/device.py` can also attribute a
device token's request to its node code via `request.state.operator` (unused by the alerts router,
which takes the plain query parameter).

## B.10 Scenario separation

Every rule either skips scenarios outright (rapid rise, communication, sensor fault) or writes
`is_scenario_alert = (scenario_id is not None)` and `scenario_id = <id>`. Every read path filters
`scenario_id IS NULL` for live views, so a scenario alert can never appear in the live alert list.

`_get_latest_reading(scenario_id=…)` fabricates a `PseudoReading` from the scenario's
horizon-0 forecast (`water_level_cm = predicted_level_cm`, `rainfall_mm_hr = 0.0`,
`recorded_at = made_at`, `source = "scenario"`), cast to `TelemetryReading` via `typing.cast`.

## B.11 Alert-type → severity matrix

| Type | warning | high | critical |
|---|---|---|---|
| `threshold` | — (below the warning threshold = cleared) | `level >= node.warning_threshold_cm` (default 150) | `level >= 200` |
| `rapid_rise` | — | `5 <= rise < 10` cm/5min | `rise >= 10` cm/5min |
| `predicted_risk` | — | forecast `>= 150` at +15/+30/+60 | forecast `>= 200` |
| `communication_failure` | `5 <= age < 15` min | — | `age >= 15` min |
| `sensor_fault` | — | `>= 3` rejections in 1 h | — |

`information` is defined in the enum and counted by the API but **never assigned by any rule**.

## B.12 The complete list of `alerts.trigger_data` keys

| Rule | Keys |
|---|---|
| threshold | `level_cm`, `threshold_cm` |
| rapid_rise | `rise_rate_cm_per_5min` |
| predicted_risk | `horizon_minutes`, `predicted_level_cm` |
| communication_failure | `minutes_since_last_reading` |
| sensor_fault | `rejected_count` |
| superseded | `+ superseded_by`, `superseded_reason` |

The frontend reads `current_value` / `value` / `threshold` / `threshold_cm`. Only `threshold_cm`
matches, so **the alert strip and the alert detail card never show a measured value** for any rule.