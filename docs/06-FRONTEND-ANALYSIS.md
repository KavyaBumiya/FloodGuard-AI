# 06 — Frontend Analysis, file by file

Stack: **Next.js 16 App Router · React 19 · TypeScript 5.7 (strict) · Redux Toolkit · Leaflet ·
three.js · Recharts · socket.io-client · Tailwind CSS 4 · lucide-react**

---

## 1. The application graph

```
app/layout.tsx
 └─ <Providers>            react-redux Provider with the imported singleton store
     └─ <ErrorBoundary>    class component; catches render errors, offers "Try Again"
         └─ <Shell>        ONE realtime pipeline, ONE clock, ONE timeline autoplay driver
             ├─ <Sidebar>          nav rail + status block (children injection)
             ├─ <CommandBar>       breadcrumb, ModePill, ConnectionPill, LAST UPDATE clock
             ├─ degraded strip     shown when connection==="offline" || error
             ├─ replay / scenario strip
             ├─ <AlertStrip>       worst open alert, one line
             ├─ <TimelineControl>  NOW/+15/+30/+60 + PLAY   ← mounted HERE, once, app-wide
             └─ {children}         the page
```

**Everything below is a page that reads the store. No page opens its own socket or runs its own
top-level refresh loop.** That was an explicit architectural fix; see `useFloodguard.ts:24-35` and
`TimelineControl.tsx:63-68`.

---

## 2. `app/layout.tsx` (40 lines)

```tsx
export const metadata: Metadata = {
  title: 'FLOODGUARD AI · Command Centre',
  description: 'Predict the flood. Visualize the impact. Prepare before disaster strikes.',
  icons: { icon: [light-32x32 (prefers-color-scheme: light),
                  dark-32x32  (prefers-color-scheme: dark),
                  icon.svg], apple: '/apple-icon.png' },
}
export const viewport: Viewport = { colorScheme: 'light dark', themeColor: [light→white, dark→black] }
```

`colorScheme: 'light dark'` in metadata while `globals.css` hard-codes `color-scheme: dark` in
`:root` — the console is dark-only in practice.

---

## 3. `lib/api.ts` (176 lines) — the only HTTP client

```ts
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://127.0.0.1:8000'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}
```

`fetchJson<T>(path, options)`:
- prefix `API_BASE`
- always set `Content-Type: application/json`
- network throw → `ApiError(0, "Network error calling <path>: <msg>")`
- `!response.ok` → try to read `body.detail`, fall back to `response.statusText`; throw `ApiError(status, detail)`
- `204` → `undefined`
- else `await response.json()`

`qs()` builds a query string, dropping `undefined`, `null` and `''`.

### The 24 exported methods

| Group | Methods |
|---|---|
| Monitoring | `nodes()`, `node(code)`, `summary()`, `forecasts(code)` |
| Telemetry | `deviceHistory(code, limit=60)` |
| Flood | `floodLayer(timeStep, {layerType, scenarioId})`, `impactSummary(scenarioId?)`, `mapLayers(layer, bbox?)` |
| Scenarios | `scenarioPresets()`, `runScenario(params)`, `scenario(id)`, `compareScenario(id)`, `listScenarios(limit=20)` |
| Alerts | `activeAlerts(scenarioId?)`, `alertHistory(params)`, `acknowledgeAlert(id)`, `acknowledgeAll()`, `networkHealth()` |
| Demo | `demoStatus()` |
| Transparency | `dataSources()`, `modelInfo()`, `assumptions()`, `studyArea()`, `honestyNote()` |

`StudyAreaResponse` is defined here (not in `types/index.ts`) and imported by both `FloodMap.tsx`
and `app/data/page.tsx`.

---

## 4. `lib/constants.ts` (198 lines) — design language + semantics

The docstring records why it exists: the same semantic used to be re-typed in four places, "which is
how cyan `#16d6d2` and sky `#38bdf8` ended up both meaning 'primary data'."

### `COLORS`

| Group | Keys |
|---|---|
| surfaces | `bg #061118`, `bgAlt #0A1921`, `panel #0D2029`, `panelHi #102832` |
| lines | `border #1c3038`, `borderStrong #25404b` |
| type | `text #e8f0f4`, `textDim #a8bcc6`, `muted #7e97a3`, `dim #5c7480` |
| semantics | `accent #16d6d2`, `ok #4fd39a`, `warn #f5b55c`, `danger #ef6b62`, `purple #a98cff`, `info #5b9dff` |

`globals.css` declares all of these as CSS custom properties with identical values, plus legacy
aliases `--cyan/--amber/--red/--green/--violet/--panel2` that `FloodMap.tsx` and `DigitalTwin.tsx`
still reference via inline `style` objects.

### `RISK_META` — 5 entries (not 4)

| Level | Label | Colour | Tint | Rank |
|---|---|---|---|---|
| `low` | LOW | `#4fd39a` | `rgba(79,211,154,0.10)` | 1 |
| `moderate` | MODERATE | `#f5b55c` | `rgba(245,181,92,0.10)` | 2 |
| `high` | HIGH | `#f97316` | `rgba(249,115,22,0.10)` | 3 |
| `severe` | SEVERE | `#ef6b62` | `rgba(239,107,98,0.12)` | 4 |
| `unknown` | **NO DATA** | `#7e97a3` | `rgba(126,151,163,0.08)` | **0** |

`normRisk` accepts case-insensitively and aliases `critical → severe`, `warning → moderate`.
The `unknown` entry exists specifically so "an unassessed node is not a failed node" — the comment at
lines 59-60 records that `unknown` used to be styled as "offline", "which told an operator a node had
failed when it had simply not been scored."

### `SOURCE_META` — 8 provenance classes

| Key | Label | Short | Colour |
|---|---|---|---|
| `iot` | **REAL IoT INPUT** | `IoT` | `#16d6d2` cyan |
| `forecast` | FORECAST | `FCST` | `#5b9dff` blue |
| `historical` | HISTORICAL | `HIST` | `#a8bcc6` |
| `simulated` | SIMULATED | `SIM` | `#a98cff` purple |
| `scenario` | SCENARIO | `SCEN` | `#f5b55c` amber |
| `replay` | REPLAY | `RPLY` | `#a98cff` purple |
| `terrain` | TERRAIN / OSM | `DEM` | `#a8bcc6` |
| `osm` | OSM | `OSM` | `#a8bcc6` |

> ⚠️ **Live test failure.** `constants.test.ts:72` asserts
> `SOURCE_META.iot.label === 'LIVE HARDWARE'`, but the value is `'REAL IoT INPUT'`. The label was
> renamed (to match the "Vadodara demo input" framing used everywhere in the pages) and the test was
> not updated. See `13-TESTING-AND-QUALITY.md`.

### `CONNECTION_META` — 5 states

| State | Label | Colour | Detail |
|---|---|---|---|
| `live` | LIVE | `#4fd39a` | "Realtime feed connected" |
| `degraded` | DEGRADED | `#f5b55c` | "Polling REST — realtime socket down" |
| `connecting` | CONNECTING | `#a8bcc6` | "Establishing link" |
| `reconnecting` | RECONNECTING | `#f5b55c` | "Re-establishing realtime feed" |
| `offline` | OFFLINE | `#ef6b62` | "Backend unreachable" |

`degraded` is the semantically important one — the comment at lines 147-149:
> "`degraded` is the important one: REST is healthy but the realtime socket is down, which is NOT the
> same as 'offline' and must not be shown as though the data were stale."

### Other exports

- `MODE_META`: `live` → LIVE/cyan, `replay` → REPLAY/purple, `scenario` → SCENARIO/amber
- `TIME_STEPS = [0, 15, 30, 60]`, `TIME_STEP_LABELS = {0:'NOW', 15:'+15m', 30:'+30m', 60:'+60m'}`
- `FORECAST_HORIZONS = [15, 30, 60]`
- `NAV_ITEMS` — 7 routes with `label` + `short`
- `ALERT_SEVERITY_ORDER = ['critical','high','warning','information']`, `alertSeverityRank` (unknown → 99)
- `nodeSource(nodeType)` → `'physical' ? 'iot' : 'simulated'`
- **`hasRecentLiveIoTReading(node)`** — the client-side mirror of the backend's
  `is_recent_physical_reading`:
  ```ts
  if (node?.node_type !== 'physical' || node.latest_reading?.source !== 'iot') return false
  if (!node.latest_reading.timestamp) return false
  const ageMs = Date.now() - Date.parse(node.latest_reading.timestamp)
  return Number.isFinite(ageMs) && ageMs <= 5 * 60 * 1000      // 5 minutes, hard-coded
  ```
- `SPACE` (4–32 px, 4 px scale), `RADIUS` (4/6/8/12), `TYPE` (7 sizes)

---

## 5. `lib/format.ts` (141 lines) — the honesty formatters

The header states the rule:
> "a missing measurement renders as an explicit placeholder, never as a plausible-looking zero.
> `(x ?? 0).toFixed(1)` turns 'no reading' into '0.0 mm/hr', which an operator reads as a real
> measurement."

```ts
export const EM_DASH = '—'
export function isNum(v: unknown): v is number { return typeof v === 'number' && Number.isFinite(v) }
```

| Function | Behaviour | Missing input |
|---|---|---|
| `fmtNum(v, unit='', decimals=0)` | `"156 cm"` | `"—"` |
| `fmtInt(v)` | `String(Math.round(v))` | `"—"` |
| `fmtArea(m2)` | `` `${(m2/1e6).toFixed(3)} km²` `` | `"—"` |
| `fmtPct(v, decimals=1)` | `` `${(v*100).toFixed(1)}%` `` | `"—"` |
| `splitValue(v, decimals)` | `[valueString, unitString]` tuple | `['—', '']` |
| `timeAgo(iso)` | `just now` / `Ns ago` / `Nm ago` / `Nh ago` / `Nd ago`; clamps future timestamps to `just now` | **`null`** (not `"—"`, so callers must branch) |
| `fmtClock(d)` | `HH:MM:SS` in `Asia/Kolkata` via `Intl.DateTimeFormat('en-GB', {hour12:false})` | — |
| `fmtHm(d)` | `HH:MM` IST | — |
| `fmtIsoHm(iso)` | IST `HH:MM` from an ISO instant | `"—"` |
| `trend(from, to)` | `{dir:'rising'\|'falling'\|'steady'\|'unknown', delta}`; `\|delta\| < 1` → steady | `dir:'unknown', delta:null` |
| `trendGlyph(dir)` | `▲` `▼` `▬` `•` | — |
| `trendWord(dir)` | `Rising` `Falling` `Steady` `Unknown` | — |
| **`latestPerHorizon(rows)`** | `Map<horizon, row>` keeping the **newest `made_at`** | empty Map |
| `cx(...)` | class-name joiner | — |

`latestPerHorizon` is critical: "The backend returns a forecast history (one row per `made_at`), so the
latest issuance must win — otherwise the UI silently shows a stale prediction."

---

## 6. `lib/socket.ts` (45 lines)

```ts
export type RealtimeEventName =
  | 'flood.sensor.updated' | 'flood.forecast.updated' | 'flood.layer.updated'
  | 'flood.alert.updated' | 'flood.mode.changed'   | 'flood.scenario.updated'
  | 'flood.risk.updated'

export interface RealtimeEvent {
  event: RealtimeEventName
  payload: Record<string, unknown>
  timestamp?: string
  source?: string
}

export function getSocket(): Socket {
  socket ??= io(API_BASE, {
    path: '/socket.io',
    transports: ['websocket', 'polling'],
    reconnection: true, reconnectionAttempts: Infinity,
    reconnectionDelay: 500, reconnectionDelayMax: 5000, timeout: 8000,
  })
  return socket
}
```

> ⚠️ `flood.mithi.updated` is emitted by the backend (`mithi_demo_pipeline.py:92`) and **is** listened
> for in `useFloodguard.ts:16`, but it is **not** in the `RealtimeEventName` union here. A type
> mismatch that would surface if anyone annotated an event handler as `RealtimeEvent`.

---

## 7. `store/index.ts` (131 lines) — Redux Toolkit

Single slice `floodguard`. `configureStore({reducer:{floodguard: slice.reducer},
middleware: g => g({serializableCheck:false})})`.

| Field | Type | Initial | Set by |
|---|---|---|---|
| `selectedNode` | `string \| null` | `null` | `selectedNodeChanged` — auto-selects `nodes[0]` on first `nodesLoaded` |
| `timelineStep` | `TimeStep` | `0` | `timelineChanged` |
| `timelinePlaying` | `boolean` | `false` | `timelinePlayingChanged`, `timelineToggled` |
| `mode` | `Mode` | `'live'` | `modeChanged` |
| `connection` | `ConnectionState` | `'connecting'` | `connectionChanged` |
| `nodes` | `FloodNode[]` | `[]` | `nodesLoaded` (also sets `loading=false`, `lastUpdatedAt`) |
| `simulationInput` | `SimulationInput \| null` | `null` | `simulationInputLoaded` |
| `summary` | `CurrentSummary \| null` | `null` | `summaryLoaded` |
| `risk` | `RiskResult \| null` | `null` | `riskLoaded` |
| `impacts` | `ImpactSummaryResponse \| null` | `null` | `impactsLoaded` |
| `alerts` | `Alert[]` | `[]` | `alertsLoaded` |
| `activeScenarioId` | `number \| null` | `null` | `scenarioSelected` |
| `scenarioRunning` | `boolean` | `false` | `scenarioRunningChanged` |
| `lastUpdatedAt` | `string \| null` | `null` | `nodesLoaded` |
| `error` | `string \| null` | `null` | `errorRaised` (also clears `loading`) |
| `loading` | `boolean` | `true` | `nodesLoaded`, `errorRaised`, `loadingChanged` |

Note `timelinePlaying` lives in the store, not page state, with the comment at lines 19-22:
> "Playback lives in the store (not in page-local state) so that PLAY keeps running — and stays in
> sync — while the operator moves between routes."

---

## 8. `hooks/useFloodguard.ts` (155 lines) — the single realtime pipeline

```ts
const SOCKET_EVENTS = ['flood.sensor.updated','flood.risk.updated','flood.forecast.updated',
                       'flood.layer.updated','flood.mithi.updated','flood.alert.updated',
                       'flood.scenario.updated','flood.mode.changed'] as const
const POLL_MS = 3000
```

### `refreshAll()` — 5 parallel REST calls

```ts
const [nodes, summary, active, impacts, demo] = await Promise.all([
  api.nodes(), api.summary(), api.activeAlerts(), api.impactSummary(),
  api.demoStatus().catch(() => null),        // demo status is optional
])
```
Dispatches in order: `nodesLoaded`, `simulationInputLoaded` (if present), `summaryLoaded`,
`alertsLoaded`, `impactsLoaded`, `modeChanged`. Returns `true`/`false`.

### Connection decision logic

| Trigger | Resulting state |
|---|---|
| `connect` event | `live`, clear error, `refreshAll()` |
| `disconnect` event | **`degraded`** (not offline) — polling continues |
| `connect_error` | `degraded` |
| any of the 8 domain events | `refreshAll()` |
| 2.5 s after a successful initial load, `!socket.connected` | `degraded` |
| every 3 s poll: ok + `socket.connected` | `live` |
| every 3 s poll: ok + `!socket.connected` | `degraded` |
| every 3 s poll: not ok | **`offline`** |

The `socket.connected` guard on the 2.5 s timer is called out at lines 90-92 as load-bearing: without
it the timer "also fired over a *healthy* connection, briefly reporting DEGRADED on a fully live
console ~2.5s after every page load."

### Cleanup — deliberate socket retention

```ts
// Deliberately not disconnecting the socket: it is a module-level
// singleton, so tearing it down here aborts an in-flight WS handshake on
// every consumer unmount (and on the StrictMode dev double-invoke).
```

Also exported: `useAppState()` — a thin `useSelector` over `s.floodguard`.

---

## 9. `hooks/useNodeSeries.ts` (91 lines)

```ts
const POLL_MS = 3000
export function useNodeSeries(nodeCode: string | null | undefined, pollMs = POLL_MS): NodeSeries
```

Returns `{ history, forecasts, byHorizon, loading, error, lastGoodAt, reload }`.

Per cycle:
```ts
const [h, f] = await Promise.all([ api.deviceHistory(nodeCode, 60), api.forecasts(nodeCode) ])
setHistory(Array.isArray(h) ? h : [])
setForecasts(Array.isArray(f?.forecasts) ? f.forecasts : [])
```

- `loading` is **derived** (`requestKey !== null && settled !== requestKey`), never `setState` inside
  the effect body — the comment at lines 40-41 explains why: "calling setState synchronously in an
  effect causes a cascading render."
- On failure the **previous series is kept on screen** — "a transient failure should not blank a chart
  the operator is reading."
- The 60-reading limit is the backend's default; `WaterLevelChart` then takes the last 26.
- `byHorizon = latestPerHorizon(forecasts)`.

Rationale for existing at all (lines 22-28): the Dashboard and Prediction page each ran their own
`setInterval` hitting the same two endpoints, "duplicated requests, duplicated failure handling, and
two independent refresh cadences that could show the chart and the risk panel disagreeing."

---

## 10. `components/Shell.tsx` (120 lines)

State: `navOpen`, `clock` (string, seeded as `''`).

```tsx
const unacked      = alerts.filter(a => !a.acknowledged).length
const activeCount  = summary?.active_sensors ?? nodes.filter(n => n.latest_reading).length
const totalCount   = summary?.total_sensors  ?? nodes.length
const offlineCount = summary?.offline_stale_sensors ?? 0
```

Rendered:
1. `<Sidebar unacked={unacked}>` — with a `status-block` child (connection dot + label + IST clock,
   then an `ACTIVE n/m` grid and an `OFFLINE n` cell) and a fixed footnote
   *"Predicted extent — model estimate, not an official flood zone."*
2. `<CommandBar connection mode clock lastUpdate={timeAgo(lastUpdatedAt)} onOpenNav />`
3. **Degraded strip** when `connection === 'offline' || error`:
   `"DEGRADED — Backend unreachable — showing last loaded state."` + `as of <age>`
4. **Replay strip** when `mode === 'replay'`:
   `"REPLAY — Recorded data being replayed — not live conditions."`
5. **Scenario strip** when `mode === 'scenario'`:
   `"SIMULATED SCENARIO — User-defined scenario — not live conditions."`
6. `<AlertStrip alerts summary />`
7. `.page-toolbar` → `<TimelineControl />`
8. `{children}`

The clock: `useEffect` sets `fmtClock()` immediately, then every 1 000 ms.

---

## 11. `components/Sidebar.tsx` (104 lines)

`ICONS` maps each `NAV_ITEMS` href to a lucide component: `LayoutDashboard`, `BrainCircuit`,
`Map as MapIcon`, `Box`, `SlidersHorizontal`, `Bell`, `Database`. Active state from `usePathname()`
with `aria-current="page"`. Brand block shows `FLOODGUARD AI` and `Mithi River · BKC → Kurla`. Below
1100 px it becomes a drawer with a `.nav-scrim`. Renders `{children}` into `.sidebar-foot`.

---

## 12. `components/CommandBar.tsx` (67 lines)

Breadcrumb `FLOODGUARD AI / MITHI RIVER / <current page label>`, then:
- `<ModePill mode>` **only when `mode !== 'live'`** (so LIVE is never shown as a competing status)
- `<ConnectionPill state>`
- LAST UPDATE clock with `timeAgo(lastUpdatedAt)` as the subtitle

---

## 13. `components/AlertStrip.tsx` (89 lines)

```ts
const open = alerts.filter(a => !a.acknowledged && a.is_active)
```
Sorts by `RISK_META[riskLevel(severity)].rank` descending, takes the worst. Renders: severity chip,
`worst.message`, node code, `value / threshold cm` (from `trigger_data.current_value` ? `.value` and
`.threshold` ? `.threshold_cm`), `timeAgo(created_at)`, a link to `/prediction`, and `+N more` when
there are extras.

---

## 14. `components/TimelineControl.tsx` (89 lines)

```tsx
{TIME_STEPS.map(t => <button role="radio" aria-checked={active}
   title={t === 0 ? 'Current observed conditions' : `Forecast horizon +${t} minutes`}>)}
<button className="timeline-play" aria-pressed={playing}>{playing ? <Pause/> : <Play/>}{playing ? 'PAUSE' : 'PLAY'}</button>
```

`useTimelineAutoplay(intervalMs = 3500)`:
```ts
const stepRef = useRef(step); useEffect(() => { stepRef.current = step }, [step])
useEffect(() => {
  if (!playing) return
  const id = setInterval(() => {
    const i = TIME_STEPS.indexOf(stepRef.current)
    dispatch(actions.timelineChanged(TIME_STEPS[(i + 1) % TIME_STEPS.length]))
  }, intervalMs)
  return () => clearInterval(id)
}, [playing, intervalMs, dispatch])
```
The ref indirection avoids mutating a ref during render.

---

## 15. `components/WaterLevelChart.tsx` (363 lines)

### Data assembly

```ts
const HISTORY_WINDOW = 26            // see comment lines 70-72
const tail = history.slice(-HISTORY_WINDOW)
```
The comment explains: "The x-axis is categorical, so every point costs the same width. Showing the full
60-reading history left the three forecast horizons squeezed into ~5% of the plot, which hid exactly
the part the operator is judging."

Points: `tail` observed → one synthetic **`NOW`** point carrying both `observed` and `forecast` =
the last observed value → `+15m`, `+30m`, `+60m` with `forecast` and a **stacked** band
(`lower` = `lower_bound_cm`, `band` = `max(0, upper - lower)`).

### Chart elements

| Element | Type | Style |
|---|---|---|
| Uncertainty band | `Area` × 2, `stackId="band"` | gradient `wlBand`, `stopOpacity` 0.28 → 0.05; only rendered if `hasBand` |
| `CRITICAL` reference | `ReferenceLine` | `COLORS.danger`, dash `6 4`, label `insideTopRight` |
| `WARNING` reference | `ReferenceLine` | `COLORS.warn`, dash `6 4` |
| `NOW` rule | `ReferenceLine` on the x label | `COLORS.accent` |
| **OBSERVED** | `Line` `dataKey="observed"` | solid, `COLORS.accent`, width 2, `connectNulls=false` |
| **FORECAST** | `Line` `dataKey="forecast"` | dashed `5 4`, `COLORS.info`, dots r=3, `connectNulls=false` |

Y domain: `min/max` over all numeric series **plus** the thresholds, padded by
`max(12, (hi-lo)*0.18)` and snapped to 10 cm, floored at 0. Unit suffix `" cm"`.

Time labels use `Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Kolkata', hour:'2-digit', minute:'2-digit', hour12:false})`.

Below the chart: a legend (OBSERVED / FORECAST / BOUNDS / WARNING / CRITICAL) and a `.fcst-strip`
with CURRENT +15 +30 +60 and the bounds range.

### `ModelMetricsChart`

`BarChart`, height 190, two `Bar`s: `mae` (`COLORS.accent`, "Model MAE") and `baseline`
(`#2f4854`, "Persistence MAE"). Used on `/prediction`.

---

## 16. `components/ui/Primitives.tsx` (247 lines)

| Component | Contract |
|---|---|
| `DataSourceBadge({source, short, note})` | Colour + `bg` + `borderColor` from `SOURCE_META`; `title` is the note. Docstring: "This component enforces the project's core honesty rule: a simulated value must never read as a live measurement. Colour is never the only cue — the label text always names the class." |
| `RiskBadge({level, score, size})` | Always renders `meta.label` — never colour alone; optional `.risk-badge-score` |
| `PanelTitle({children, right, icon})` | `.panel-head` with a `.panel-head-right` slot |
| `PageHeader({eyebrow, title, subtitle, actions})` | `.eyebrow` + `<h1>` + optional `<p>` + `.page-title-actions` |
| `KpiCard({label, value, unit, hint, tone, badge, accent})` | tone ∈ `default\|good\|warn\|bad\|accent` → `.kpi-{tone}` |
| `MetricValue({label, value, unit, tone})` | label / big value / de-emphasised unit |
| `EmptyState({title, hint})` | `.empty-title` + `.empty-hint` |
| `ErrorState({title, detail, lastGood, onRetry})` | Icon + title + detail + **"Last successful reading: `<IST time>`"** + Retry button. Docstring: "A bare 'Error fetching data' tells an operator nothing actionable." |
| `SkeletonKpi`, `SkeletonRows`, `SkeletonBlock` | `aria-busy="true"` shimmer placeholders |

---

## 17. `components/ui/StatusPill.tsx` (52 lines)

`ConnectionPill({state, compact})` — coloured pill + pulsing dot when `state === 'live'`,
`role="status" aria-live="polite"`.
`ModePill({mode})` — plain coloured label.

The docstring records the fix this replaced: the app used to render a mode indicator and a connection
indicator together in the top bar, a second connection indicator in the sidebar and a third on the
dashboard, "so 'LIVE' and 'Offline' appeared as competing truths on one screen."

---

## 18. `components/ErrorBoundary.tsx` (87 lines)

Class component. `getDerivedStateFromError` sets `{hasError, error}`; `componentDidCatch` logs and
calls the optional `onError`. Fallback UI shows "Something went wrong", an expandable
`<details><pre>{error.message}</pre></details>`, and a "Try Again" button that resets state.

---

## 19. `app/page.tsx` — Command Centre Dashboard (446 lines)

Structure top to bottom:

1. **Loading skeleton** when `loading && !nodes.length` — 5 `SkeletonKpi` + 2 `SkeletonRows`.
2. **`PageHeader`** — eyebrow `COMMAND CENTRE · MITHI RIVER BKC → KURLA`, title `Current Situation`,
   actions = provenance badge + node code + `timeAgo`. A physical node with no live reading shows a
   `WAITING FOR SENSOR` badge instead.
3. **"REAL IoT INPUT → MITHI RIVER DIGITAL TWIN" panel** — 3 `MetricValue`s:
   - `VADODARA DEMO WATER LEVEL` → `fmtNum(simulationInput.water_level_cm)` or `WAITING`
   - `MITHI SIMULATED LEVEL` → `fmtNum(simulationInput.mithi_water_level_cm)` or `WAITING`, tone accent
   - `MITHI STATUS` → `CONNECTED` or **`WAITING FOR REAL IoT INPUT`**
   Footer: *"DEMO CALIBRATION · Physical IoT Input → Mithi Simulation. This is not a
   Vadodara-to-Mumbai hydraulic relationship."*
4. **Hero block** — border colour = selected node's risk colour.
   - Left: `CURRENT SITUATION` / risk label + `score N.NN` / node code + name
   - Middle: `WATER LEVEL`, `RAINFALL`, `WARNING THRESHOLD` metrics
   - Right: `FORECAST` + `DataSourceBadge source="forecast"`, a row of `+15m/+30m/+60m` values, and a
     trend line (glyph + word + `±N cm to +15m`)
5. **KPI row** — `ACTIVE SENSORS`, `CURRENT WATER LEVEL`, `RAINFALL`, `HIGHEST RISK`,
   `FORECAST PEAK` (+60 min).
6. **Two-column grid**
   - Left `WATER LEVEL · <code>` — `DataSourceBadge` + "ESP32 telemetry" or "virtual sensor —
     simulated" + a `Radio` icon for physical nodes; then `WaterLevelChart` (height 288) with
     `warningThreshold` and `criticalThreshold={null}`. Falls back to `ErrorState` or
     `EmptyState("NO LIVE IOT DATA")`.
   - Right `SENSOR NETWORK` — one `<button class="sensor-row">` per node: risk dot, code, an
     `ESP32`/`VIRTUAL` kind tag, level + rainfall, `RiskBadge`. Clicking dispatches
     `selectedNodeChanged`. A physical node without a recent IoT reading renders `WAITING` /
     `NO LIVE IOT DATA` instead of a number.
7. **Three-column grid**
   - `PREDICTED IMPACT · NOW|+N MIN` — flooded area (km²), buildings, roads, max depth; footer with a
     `terrain` badge and *"Model estimate, not an official flood zone."*
   - `ACTIVE ALERTS` — top 4, each with a severity-coloured left border, message, `timeAgo`
   - `RISK DRIVERS` — top 5 nodes sorted by `riskRank` descending, with `primary_driver` and score
8. **Footer** — `Droplets` icon + *"Corridor data last refreshed … Flood extent is a model estimate —
   not an official flood zone, and not an emergency warning system."*

`activeAlerts` deliberately filters only on `!acknowledged` (comment lines 83-84): "`/api/alerts/active`
already returns the active set; the response has no `is_active` field, so filtering on it would drop
every alert."

---

## 20. `app/prediction/page.tsx` — AI Prediction (320 lines)

1. `PageHeader` with a node `<select>` bound to `selectedNode`.
2. **Horizon ladder** — 4 cells: `CURRENT` (with `WAITING FOR SENSOR` / `DataSourceBadge`) then
   `+15/+30/+60 MIN` each with a `forecast` badge, the value (warn-coloured when
   `> warning_threshold_cm`) and the bounds text `"120–180 cm bounds"` or `"no bounds supplied"`.
3. **2×2 grid**
   - `OBSERVED + FORECAST WATER LEVEL` — two `DataSourceBadge`s (observed + forecast) plus the legend
     text *"solid = observed · dashed = predicted"*; `WaterLevelChart` height 318.
   - `CURRENT RISK ASSESSMENT` — a `.score-ring` with the score × 100, the risk label, `driver:` and
     `model vX`; then `RISK CONTRIBUTORS` — one bar per component, width =
     `|contribution| / max|contribution| × 100%`, value `contribution.toFixed(2)`, and the component
     name + `(band)`. Then a `TREND` line.
   - `MODEL PERFORMANCE` — `ModelMetricsChart` (MAE vs persistence) + a `detail-list` of
     `MAE <v> vs baseline <b>` per horizon + the `training_data_description` as a `caveat` paragraph.
   - `PLAIN-LANGUAGE SUMMARY` — a template sentence assembled from `risk` + `trend`, ending with
     *"This is a rule-based prototype assessment, not a validated hydraulic result."* The panel header
     explicitly reads **"deterministic, not an LLM"**. Then `MODEL ASSUMPTIONS` listing
     `risk.assumptions[]` as bullets.
4. Risk detail is refetched only when the selection changes (`useEffect` on `[selected, dispatch]`).

---

## 21. `app/scenarios/page.tsx` — What-If Simulator (429 lines)

Four sliders with documented ranges:

| Slider | min | max | step | unit | scale labels |
|---|---|---|---|---|---|
| Rainfall intensity | 5 | 150 | 5 | mm/hr | `5 drizzle` · `75 heavy` · `150 cloudburst` |
| Initial water level | 60 | 240 | 5 | cm | `60 low` · `150 mean` · `240 high` |
| Drainage capacity | 10 | 100 | 5 | % | `10 choked` · `50 silt` · `100 full` |
| Duration | 15 | 120 | 15 | min | `15` · `60 standard` · `120 extended` |

Flow:
- On mount: `api.scenarioPresets()` → presets + assumptions; the **first** preset is auto-applied.
  Also `api.listScenarios(10)` for the saved list.
- `applyPreset(key, p)` sets the preset key, the name and all four slider values.
- **Run is blocked unless a backend preset is loaded**: "Load a backend scenario preset before
  running a simulation." and the button is disabled without `values && selectedPreset && name.trim()`.
- After `POST /run`, it **polls up to 12 times at 900 ms** for `status !== 'running'`
  ("The engine is asynchronous; poll briefly rather than guessing a delay"), then fetches the
  comparison.
- `reset()` clears `activeScenarioId` and shows *"Restored to the live sensor baseline."*
- Sections: `SCENARIO CONFIGURATION`, `BASELINE vs SCENARIO`, `SCENARIO ASSUMPTIONS` (renders the
  backend's assumptions object verbatim), `SAVED SCENARIOS` (top 6 with an `Activate` button).

> ⚠️ The `BASELINE vs SCENARIO` panel reads `comparison.baseline_peak_level_cm`,
> `baseline_mean_level_cm`, `scenario_peak_level_cm`, `scenario_mean_level_cm`,
> `scenario_flooded_area_m2`. **The backend returns none of these keys** — it returns
> `node_comparisons`, `peak_level_difference_cm`, `average_level_difference_cm`, `summary`,
> `assumptions`. So `PEAK LEVEL`, `MEAN LEVEL` and `SCENARIO FLOODED AREA` always render `—`.
> `peak_level_difference_cm` and `average_level_difference_cm` (the delta list) **do** render.

---

## 22. `app/alerts/page.tsx` — Alert Centre (333 lines)

4 KPI cards (`ACTIVE`, `CRITICAL`, `UNACKNOWLEDGED`, `RESOLVED (HISTORY)`), a filter bar
(node / severity / type), then a 3-column grid:

1. `ACTIVE ALERTS` — `AlertCard` per alert; expandable (`ChevronDown` rotates) to reveal
   `CURRENT VALUE`, `THRESHOLD`, `TYPE`, a scenario badge, an `ACTIVE`/`ACKNOWLEDGED` chip and an
   `Acknowledge` button.
2. `NETWORK HEALTH` — per node: dot (`RISK_META.low` when online, `.unknown` otherwise), code,
   `node_type`, `Nm ago`, and a status chip.
3. `EVENT HISTORY` — top 40, severity-coloured, `message` + `node · type · timeAgo`.

`ALERT_TYPES` filter list: `['', 'threshold', 'rapid_rise', 'predicted_risk',
'communication_failure', 'sensor_fault', 'scenario']`.

The `active` list filters only `match(a)` and never `is_active` (comment lines 89-95: the store holds
`/api/alerts/active`, which has no `is_active` flag).

---

## 23. `app/data/page.tsx` — About This Data (197 lines)

`CATEGORY_SOURCE` maps backend categories to badge sources:
`live_hardware→iot`, `historical_reference→historical`, `forecast→forecast`,
`terrain_osm→terrain`, `terrain→terrain`, `simulated→simulated`, `scenario→scenario`, `replay→replay`.

Six panels: `DATA SOURCES & PROVENANCE` (one card per `DataSource` row: name, badge, provider ·
resolution, and the `limitation` text), `HOW EACH LABEL IS USED`, `MODEL` (MAE vs baseline per
horizon), `MODEL ASSUMPTIONS`, `STUDY AREA`, `KNOWN LIMITATIONS`.

A hard-coded `LIMITATIONS` list of 8 items is rendered on this page (lines 21-30), including:
> "Live sensor network — No virtual sensor network is active. The live application waits for the
> registered physical IoT input."

---

## 24. `app/globals.css` (3,473 lines)

Structure: `@import "tailwindcss"` + `@import "tw-animate-css"`, then 46 commented sections.

Design tokens mirror `lib/constants.ts` exactly, plus:

```css
:root {
  color-scheme: dark;
  --background: var(--bg);  --foreground: var(--text);
  --cyan: var(--accent);    --amber: var(--warn);
  --red: var(--danger);     --green: var(--ok);  --violet: var(--purple);
  --panel2: var(--panel-hi);
  --s1..--s8: 4px..32px;   --r-sm..--r-xl: 4px..12px;
  --focus: 0 0 0 2px var(--bg), 0 0 0 4px var(--accent);
}
* { box-sizing: border-box; }
html,body { background: var(--background); color: var(--foreground);
            font-family: Arial, Helvetica, sans-serif; }
```

Base font stack is **Arial/Helvetica sans-serif**, not a webfont — deliberate for offline demo
reliability.

Section list (line numbers): Design Tokens 3 · Base 57 · App Shell 78 · Sidebar 84 · Sidebar
Navigation 175 · Sidebar Footer 216 · Main Column 249 · Topbar 254 · Global Alert 324 · Page Layout
353 · Buttons 391 · Stat Grid 412 · Semantic Colors 452 · Dashboard Grid 464 · Panels 470 · Charts
502 · Sensor List 536 · Badges 601 · Risk Levels 650 · Scenario 655 · Alert List 699 · Text Button
722 · Forecast 734 · Form Select 762 · Prediction 771 · System Status 846 · Bar Chart 869 · Plain
Language 874 · Timeline 905 · Map 932 · Map Sidebar 1077 · Map Layers 1129 · Impact Grid 1147 ·
Banners 1173 · Scenario Simulator 1190 · Result Highlight 1246 · Active Alerts 1306 · System Health
1352 · History 1381 · About 1396 · Utilities 1440 · Map Overrides 1444 · Responsive 1648.

Note `Risk Levels` (line 650) is only 5 lines and `Semantic Colors` (452) only 12 — so the actual
risk/severity colours live primarily in `RISK_META` and are applied as **inline styles**, not CSS
classes. `RiskBadge` still emits `risk-{size}` for sizing only.

---

## 25. Page/component inventory summary

| File | Lines | Kind |
|---|---|---|
| `app/layout.tsx` | 40 | root layout |
| `app/providers.tsx` | 8 | redux provider |
| `app/globals.css` | 3,473 | design system |
| `app/page.tsx` | 446 | Dashboard |
| `app/prediction/page.tsx` | 320 | AI Prediction |
| `app/map/page.tsx` | 303 | 2D Map |
| `app/digital-twin/page.tsx` | 220 | 3D Twin |
| `app/scenarios/page.tsx` | 429 | Simulator |
| `app/alerts/page.tsx` | 333 | Alerts |
| `app/data/page.tsx` | 197 | About |
| `components/Shell.tsx` | 120 | shell |
| `components/Sidebar.tsx` | 104 | nav |
| `components/CommandBar.tsx` | 67 | topbar |
| `components/AlertStrip.tsx` | 89 | alert strip |
| `components/TimelineControl.tsx` | 89 | timeline + autoplay |
| `components/FloodMap.tsx` | 675 | Leaflet map |
| `components/DigitalTwin.tsx` | 641 | three.js scene |
| `components/WaterLevelChart.tsx` | 363 | 2 charts |
| `components/ErrorBoundary.tsx` | 87 | boundary |
| `components/ui/Primitives.tsx` | 247 | 10 primitives |
| `components/ui/StatusPill.tsx` | 52 | 2 pills |
| `hooks/useFloodguard.ts` | 155 | realtime pipeline |
| `hooks/useNodeSeries.ts` | 91 | per-node poller |
| `lib/api.ts` | 176 | API client |
| `lib/constants.ts` | 198 | design language |
| `lib/format.ts` | 141 | formatters |
| `lib/socket.ts` | 45 | socket singleton |
| `lib/constants.test.ts` | 117 | 11 tests |
| `lib/format.test.ts` | 137 | 12 tests |
| `store/index.ts` | 131 | redux slice |
| `types/index.ts` | 294 | TS types |

**Total hand-written frontend: ~8,900 lines.**