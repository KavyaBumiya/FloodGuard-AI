# 07 — Map and 3D Digital Twin: deep dive

---

# PART A — The 2D Flood Map

## A.1 What the map actually renders

**It has no base map tiles.** `FloodMap.tsx:118-120`:

```js
// Keep the map self-contained. The map is rendered from backend-provided
// GeoJSON layers; external tile providers can fail with API-key overlays.
map.getContainer().classList.add('floodguard-vector-map')
```

There is no `L.tileLayer(...)` call anywhere in the file. The map is a bare background
(`background: COLORS.bg`) with five vector layers drawn on it.

> This diverges from `System-Architecture.md:57` ("Leaflet + OpenStreetMap") and from the page-3
> mockup ("Leaflet + OpenStreetMap style view"). The decision is deliberate and documented in code,
> but it means there is **no basemap context** — no street names, no neighbourhood labels. The roads
> and buildings layers are the only geographic reference.

## A.2 Map configuration (`FloodMap.tsx:97-131`)

```js
const { south, west, north, east } = studyArea.bounds
const center = [19.0713, 72.8830]                    // study_area representative point
L.map(el, {
  center, zoom: 14.5, minZoom: 13, maxZoom: 18,
  maxBounds: [[south, west], [north, east]],        // pan is hard-limited to the study area
  zoomControl: false,
})
map.fitBounds([[south, west], [north, east]], { padding: [24, 24] })
```

| Setting | Value | Source |
|---|---|---|
| center | 19.0713, 72.8830 | `GET /api/about/study-area` → `representative_point` |
| initial zoom | 14.5 | hard-coded |
| zoom range | 13 – 18 | hard-coded |
| pan limit | `[[19.05, 72.85], [19.09, 72.90]]` | study-area bounds |
| initial view | `fitBounds(study bounds, padding 24px)` | — |

## A.3 Layer inventory

| # | Layer | Data source | Poll | Style |
|---|---|---|---|---|
| 1 | **Waterways** (Mithi River) | `GET /api/flood/map-layers?layer=waterways` → reads `data/osm/waterways.geojson` from disk | 3 s | `color: COLORS.accent (#16d6d2)`, `weight: 4.5`, `opacity: 0.85`, round caps/joins. Sticky tooltip with the name (falls back to `"Mithi River Course"`) |
| 2 | **Inundation** (flood extent) | `GET /api/flood/layers/{step}?layer_type=depth` | 3 s | Per-feature fill by `properties.risk_level` (below) + `fillOpacity: opacity` prop, `weight: 2`, `opacity: min(1, opacity+0.3)`, `dashArray: '3, 4'` |
| 3 | **Buildings** | `GET /api/flood/map-layers?layer=buildings&bbox=…` | 3 s | `fillColor '#334155'`, `fillOpacity 0.65`, `color '#64748b'`, `weight 1` |
| 4 | **Roads** | `GET /api/flood/map-layers?layer=roads&bbox=…` | 3 s | `color '#94a3b8'`, `weight 2`, `opacity 0.7` |
| 5 | **Sensor markers** | Redux `nodes` | on store change | `L.divIcon` with a pulsing beacon |

### Inundation colour mapping (`FloodMap.tsx:369-387`)

```js
let fillColor = '#0284c7'                     // default blue
if      (risk === 'severe')   fillColor = '#ef4444'   // red
else if (risk === 'high')     fillColor = '#f97316'   // orange
else if (risk === 'moderate') fillColor = '#eab308'   // yellow
else if (risk === 'low')      fillColor = '#22c55e'   // green
```

The comment at lines 371-373 is the design rule: "Color strictly by the backend-assigned
`risk_level`; depth is a **display** value, not a second, divergent risk classifier."

> ⚠️ These 5 hex values are a **second risk palette**, distinct from `RISK_META` in
> `lib/constants.ts` (`#4fd39a/#f5b55c/#f97316/#ef6b62`). Only `high` matches. The map page legend
> (`app/map/page.tsx:150-160`) renders swatches from `RISK_META`, so **the legend colours and the
> polygon colours disagree** for `low`, `moderate` and `severe`.

### Sensor markers (`FloodMap.tsx:227-320`)

```js
nodes.forEach(node => {
  if (!node.location?.lat || !node.location?.lng) return
  const isPhysical = node.node_type === 'physical'
  if (isPhysical) return          // ← see the defect note below
  const color = riskColor(node.current_risk?.risk_level ?? 'unknown')
  ...
})
```

Marker HTML:
```html
<div class="custom-sensor-marker virtual selected?">
  <div class="beacon-pulse" style="background:{color}44; border-color:{color}"></div>
  <div class="beacon-core"  style="background:{color}; border-color:#ffffff">📡</div>
  <div class="beacon-tag">SIM-01</div>          <!-- node_code with "MITHI-" stripped -->
</div>
```
`iconSize [36,36]`, `iconAnchor [18,18]`. `beacon-pulse` runs a 2 s `markerPulse` keyframe
(scale 0.6→1.6, opacity 1→0).

> **🔴 Defect: the map renders zero sensor markers in the current build.**
> `/api/flood/nodes` filters to `node_type == "physical"` (`flood_monitor.py:147-148`), and
> `FloodMap.tsx:232-233` explicitly skips physical nodes. The two conditions cancel out exactly.
> The intended design (visible in the code and in the popup template) is: show **virtual Mithi
> nodes** as markers, because the physical node is in Vadodara, not Mumbai.
> The fix is one of: change the API filter to `node_type != "physical"`, or remove the `isPhysical`
> early-return and add a separate `sensor` map-layers endpoint that returns all nodes
> (that endpoint already exists at `/api/flood/map-layers?layer=sensors` but is **not** used by the
> map).

Popup content per marker: header with node code + a `REAL IoT DEMO INPUT` / `SIMULATED MITHI
LOCATION` badge, the location label, a 2-column grid with WATER LEVEL (or `WAITING FOR SENSOR`) and
RAINFALL (or `NO LIVE IOT DATA`), a risk row with `Driver: …`, and the three `+Nm: N cm` forecasts.
All interpolated strings pass through `escapeHtml()`.

Clicking a marker dispatches `selectedNodeChanged(node_code)`.

### Popups for the vector layers

| Layer | Popup contents |
|---|---|
| Inundation | "Inundation Layer", `Timeline: +N MIN`, `Max Depth: X.XX m`, `Risk Category: <LEVEL>`, footnote *"Elevation-threshold simulation estimate"* |
| Buildings | name (fallback "Building Structure"), `Type: <p.type>`, `Height: <p.height_m> m` or **`Estimated ~12m`** when absent, tag line "OSM Vector Geometry" |
| Roads | name (fallback "Roadway Segment"), `Classification: <p.type>` |
| Waterways | name (fallback "Mithi River Course") + "Waterway Corridor", sticky |

### HUD overlays (absolutely positioned inside the map container)

| Element | Position | Content |
|---|---|---|
| Corridor badge | top-left | `MITHI RIVER CORRIDOR · BKC → KURLA`; `Current Observed State` or `Predicted Extent: +N min`; `{nodes.length} Monitoring Nodes · Peak Depth: X.XXm` or `Safe Levels`; the `layerError` message if set |
| Loading chip | top-right | `Simulating layer...` shown while `isLoadingLayer` |
| Reset button | bottom-left | `🎯 Reset Corridor View` → `map.flyTo(representative_point, 14.5, {duration: 1.2})` |

### Loading / error state

```ts
const isLoadingLayer = `${timelineStep}:${scenarioId ?? 'live'}` !== loadedLayerKey
```
Derived, not `setState`-in-effect. When `api.floodLayer` rejects, the layer is cleared,
`layerError = 'Inundation layer unavailable for this step'` and `loadedLayerKey` is still updated so
the spinner stops. The **previous** layer is therefore dropped rather than held — a deliberate
difference from `useNodeSeries`, which keeps its last good series.

## A.4 Inline `<style jsx global>` block (lines 587-672)

Defines `.custom-sensor-marker`, `.beacon-pulse`, `.beacon-core`, `.beacon-tag`, the `@keyframes
markerPulse` animation, and themed `.floodguard-popup` / `.floodguard-tooltip` overrides for
Leaflet's default white popup chrome:

```css
.floodguard-popup .leaflet-popup-content-wrapper {
  background: #0b1a24; color: #e8f0f4;
  border: 1px solid #203541; border-radius: 8px;
  box-shadow: 0 12px 30px rgba(0,0,0,0.7); padding: 0;
}
.floodguard-tooltip { background:#071219; color:#16d6d2; border:1px solid #16d6d2; }
```

## A.5 `app/map/page.tsx` (303 lines) — the page around the map

Layout: `.map-page` = `.map-stage` (the map, `min-height: 620px`) + `.map-rail` (the right rail).

Floating panels over the stage:

| Panel | Contents |
|---|---|
| `map-float-tl` | `REAL IoT DEMO INPUT` header, *"Vadodara Physical Sensor → Mithi River Digital Twin"*, then a **second** timeline row: `NOW/+15/+30/+60` chips + PLAY/PAUSE + a `FORECAST` chip, then a footer with a `simulated`/`forecast` badge and the text `observed` / `projected +Nm`. When `simulationInput.status !== 'live'` it prints **`WAITING FOR REAL IoT INPUT`** instead of the badge. |
| `map-float-bl` | `LAYERS` — 5 checkboxes: Sensor nodes, Mithi River, Flood extent, Buildings, Roads (defaults: all on except Roads). Plus an `Extent opacity` range slider, min 0.2, max 1, step 0.05, default **0.65**. |
| `map-float-br` | `RISK CLASS` legend — 4 swatches from `RISK_META[k].tint`/`.color`, a cyan line for "River channel", a slate box for "Building footprint". |

Right rail:

1. **`STATION INSPECTOR`** — code, source badge (or `WAITING`), name, then `WATER LEVEL`, `RAINFALL`,
   `WARNING`, `ELEVATION` (as `m ASL`); a `FORECAST` row with `+15m/+30m/+60m`; the last-reading age;
   a `View in 3D Twin` button. Closing line:
   `'SIMULATED MITHI CONDITION · driven by REAL IoT INPUT'` when live, otherwise
   `'WAITING FOR REAL IoT INPUT · no virtual sensor fallback is active'`.
2. **`IMPACT · NOW|+N MIN`** — flooded area, buildings, roads, max depth, with the
   `terrain` badge + *"Model estimate, not an official flood zone."*
3. **`SELECTED NODE`** — one button per node, each with a risk swatch, code, and level (or `WAITING`).

Header subtitle: *"SIMULATED FLOOD EXTENT · Mithi River Mumbai · driven by real IoT demo input"*.

## A.6 Map data flow

```
api.studyArea()                       3 s poll   → bounds + representative point → map init
api.mapLayers('waterways')            3 s poll   → 42 LineStrings
api.mapLayers('buildings', bbox)      3 s poll   → PostGIS buildings, ST_Simplify 0.00004°
api.mapLayers('roads', bbox)          3 s poll   → PostGIS roads,     ST_Simplify 0.00006°
api.floodLayer(timelineStep)          3 s poll   → depth GeoJSON for the current step
Redux nodes / selectedNode                        → markers
timelineStep / opacity / activeLayers             → re-render layers
```

---

# PART B — The 3D Digital Twin

## B.1 Engine and library

**three.js r186** with `OrbitControls` from `three/examples/jsm/controls/OrbitControls.js`.
**Not CesiumJS.** `CESIUM_ION_TOKEN` exists in `.env` and `config.py` but is never read by any code
path (verified by repo-wide search). `next.config.mjs` has no Cesium bundle splitting because there
is nothing to split.

Loading is gated with `next/dynamic` `ssr: false` and a placeholder reading
*"Compiling WebGL shaders and building the corridor mesh…"*.

## B.2 Scene graph

```
THREE.Scene  background #071219, FogExp2(#071219, 0.012)

├── AmbientLight          0x203541, intensity 1.8
├── DirectionalLight      0xdcf8f9, 2.2  at (40, 70, 30), castShadow, 2048² map,
│                                         ortho frustum ±50, near 10, far 160
├── PointLight (BKC)      0x16d6d2, 3.5, distance 60, at (10, 15, -10)
├── PointLight (Kurla)    0xef6b62, 3.0, distance 60, at (-15, 12, 20)

├── Terrain               PlaneGeometry(160, 160, 64, 64), rotateX(-π/2)
│                         vertices sculpted per-frame-0 (see B.3)
│                         MeshStandardMaterial #0c1b24, roughness 0.9, metalness 0.1
│                         receiveShadow = true
├── GridHelper            160, 40 divisions, #1d3d4d / #102530, at y = 0.05

├── Riverbed              TubeGeometry(CatmullRomCurve3(6 points), 64, 5.5, 12, false)
│                         #08212b, scale (1, 0.25, 1), position y = -0.5

├── WaterMesh             PlaneGeometry(160, 160, 48, 48), rotateX(-π/2)
│                         ShaderMaterial (custom GLSL, see B.5), position y = backendWaterHeight
│                         (base offset -0.2)

├── buildingGroup         15 BoxGeometry boxes, pivot translated to the base
│                         each + EdgesGeometry wireframe in #16d6d2 at 0.35 opacity
│                         commercial → #1e3a4b, others → #182c38

├── Bridge ×2             BoxGeometry(22, 1.2, 5), #334e5e, metalness 0.5
│                         BKC bridge at (3, 2.5, -10); Kurla bridge at (8, 2.2, 22)

└── sensorMastsGroup      one CylinderGeometry mast + SphereGeometry beacon + RingGeometry halo
                          per node, positioned from the node's lat/lng bounding box
```

## B.3 Terrain — procedurally sculpted, NOT the DEM

```js
const terrainGeo = new THREE.PlaneGeometry(160, 160, 64, 64)
terrainGeo.rotateX(-Math.PI / 2)
const pos = terrainGeo.attributes.position
for (let i = 0; i < pos.count; i++) {
  const px = pos.getX(i), pz = pos.getZ(i)
  const distToRiver = Math.abs(px - Math.sin(pz * 0.05) * 8)
  let elevation = Math.sin(px * 0.08) * 0.6 + Math.cos(pz * 0.06) * 0.8
  if (distToRiver < 10) elevation -= (10 - distToRiver) * 0.35      // river trough
  pos.setY(i, elevation)
}
terrainGeo.computeVertexNormals()
```

Two sine waves plus a meandering trough. **The Copernicus DEM is not loaded into the 3D scene at
all** — only the 2D flood simulation uses it. The page says so:
> "Buildings are *procedurally placed placeholders*, not surveyed OSM footprints, and the terrain is
> a schematic corridor rather than the Copernicus DEM."

## B.4 The 15 procedural buildings

| Name | x | z | w | d | h | type |
|---|---|---|---|---|---|---|
| BKC ICICI Bank HQ | 22 | -25 | 9 | 7 | 22 | commercial |
| BKC Bandra Kurla One | 34 | -15 | 8 | 8 | 28 | commercial |
| MMRDA Administrative Towers | 18 | -8 | 10 | 6 | 20 | commercial |
| BKC Jio World Centre | 30 | 5 | 14 | 11 | 18 | commercial |
| BKC Diamond Bourse Complex | 25 | 22 | 11 | 9 | 24 | commercial |
| BKC City Interchange Station | 12 | 12 | 12 | 5 | 10 | transit |
| Kalanagar Commercial Hub | 28 | -38 | 7 | 7 | 19 | commercial |
| Kurla West Railway Colony A | -16 | -32 | 8 | 6 | 12 | residential |
| Kurla West Railway Colony B | -24 | -20 | 9 | 5 | 11 | residential |
| Mithi Riverside Settlement Block 1 | -8 | -12 | 6 | 4 | 7 | low_elevation |
| Mithi Riverside Settlement Block 2 | -5 | 2 | 7 | 4 | 8 | low_elevation |
| Kurla Bus Depot & Terminus | -22 | 14 | 14 | 8 | 9 | transit |
| Kurla CST Road Commercial Plaza | -28 | 28 | 8 | 7 | 16 | commercial |
| LBS Marg Infrastructure Ward Office | -14 | 38 | 7 | 6 | 11 | civic |
| Saki Naka Confluence Warehousing | -6 | 52 | 10 | 7 | 8 | industrial |

Total: 8 commercial, 2 residential, 2 low_elevation, 2 transit, 1 civic, 1 industrial — 168 m of
combined height. **None of these correspond to an OSM feature**; they are illustrative placements
chosen to read as "BKC towers on one bank, low Kurla settlements on the other".

> This is the biggest divergence from the mockup (page 4, "CesiumJS terrain, buildings and flood
> overlay"). The mockup implies real footprints; the build is a schematic.

## B.5 The water shader

`ShaderMaterial` uniforms:

| Uniform | Value | Changed by |
|---|---|---|
| `uTime` | 0 | every animation frame (`timer.getElapsed()`) |
| `uColor` | `0x0ea5e9` | `waterPalette(timelineStep)` |
| `uEmissive` | `0x0284c7` | `waterPalette(timelineStep)` |
| `uEmissiveIntensity` | 0.25 | constant |
| `uOpacity` | 0.72 | constant |
| `uRoughness`, `uMetalness` | 0.1, 0.2 | declared but **unused in the fragment shader** |

`waterPalette(step)` → `[base, emissive]`:

| Step | base | emissive |
|---|---|---|
| 0 (NOW) | `0x0ea5e9` sky-500 | `0x0284c7` sky-600 |
| +15 | `0x0284c7` sky-600 | `0x1d4ed8` blue-700 |
| +30 | `0xeab308` yellow-500 | `0xb45309` amber-700 |
| +60 | `0xef4444` red-500 | `0x991b1b` red-800 |

Vertex shader (three superposed Gerstner-style sines — see `05-ALGORITHMS-AND-AI-ML.md` §7.1 for
the full GLSL and the `displaced.z` vs `displaced.y` axis defect).

Fragment shader:

```glsl
vec3 color = uColor;
color += uEmissive * uEmissiveIntensity;
float viewDot  = dot(normalize(vec3(0.0,1.0,0.0)), vec3(0.0,0.0,1.0));
float fresnel  = pow(1.0 - viewDot, 3.0);
color += vec3(0.2, 0.6, 1.0) * fresnel * 0.3;
color += vec3(0.0, 0.1, 0.2) * vElevation * 2.0;
gl_FragColor = vec4(color, uOpacity);
```

## B.6 Camera

```js
const camera = new THREE.PerspectiveCamera(45, width / height, 1, 1000)
camera.position.set(0, 48, 65)

const controls = new OrbitControls(camera, renderer.domElement)
controls.enableDamping = true
controls.dampingFactor = 0.05
controls.maxPolarAngle = Math.PI / 2.05      // never go below the ground plane
controls.minDistance = 15
controls.maxDistance = 160
controls.target.set(0, 2, 0)
```

Camera presets (`DigitalTwin.tsx:544-569`):

| Preset | position | target |
|---|---|---|
| `overview` (default) | (0, 48, 65) | (0, 2, 0) |
| `bkc` | (38, 22, 10) | (22, 6, -5) |
| `iot` | (14, 8, 16) | (6, 3, 5) |
| `kurla` | (-36, 18, 12) | (-15, 3, 10) |
| `river_pov` | (-6, 3.5, -45) | (5, 1, 20) |
| `ortho` | (0, 85, 2) | (0, 0, 0) |

> `ortho` is implemented in the component but **not exposed in the UI** — `app/digital-twin/page.tsx:32-38`
> lists only Overview, BKC, IoT, Kurla, River.

The page also offers an `autoRotate` toggle (`RotateCw` button) → `controls.autoRotate = true`,
`autoRotateSpeed = 0.8`. The comment at `DigitalTwin.tsx:436-439` explains why `autoRotate` is set
in a **separate effect** rather than in the animation loop: the loop closure captures the first
render's `isAutoRotate`, so assigning it per frame would overwrite the toggle effect.

## B.7 Sensor masts

Positions are normalised into the scene's 40 × 80 footprint from the node bounding box:

```js
const p = [
  ((node.location.lng - minLng) / lngRange) * 40 - 20,
  2.8,
  ((maxLat - node.location.lat) / latRange) * 80 - 40,
]
```

Each mast:
- `CylinderGeometry(0.2, 0.3, 6, 8)` translated +3 Y, material `physical ? 0x16d6d2 : 0x8ca1ad`,
  metalness 0.8
- `SphereGeometry(physical ? 0.9 : 0.65, 16, 16)` at local (0, 6.2, 0),
  colour `physical ? 0x16d6d2 : 0xa98cff`, emissive `0x16d6d2 : 0x8459d9`,
  `emissiveIntensity: 2.0`
- `RingGeometry(1.2, 1.4, 24)` rotated flat at local (0, 6.2, 0),
  `MeshBasicMaterial`, `DoubleSide`, opacity 0.7
- `pole.userData = { code, isPhysical }`

Per-frame animation of the rings:

```js
ring.rotation.y = elapsed * 1.5
ring.scale.setScalar(1 + Math.sin(elapsed * 3) * 0.15)
```

Picking (`DigitalTwin.tsx:466-485`): `pointerdown` → NDC mouse → `Raycaster.intersectObjects(
sensorMastsGroup.children, true)` → walk up parents until `userData.code` → `onSelectNode(code)`.

## B.8 Scene-rebuild avoidance

```ts
const nodeLayoutKey = nodes.map(n => `${n.node_code}:${n.location.lat}:${n.location.lng}`).join('|')
// eslint-disable-next-line react-hooks/exhaustive-deps
const layoutNodes = useMemo(() => nodes, [nodeLayoutKey])
```

The comment is explicit: "The key intentionally excludes changing telemetry so the scene is not
rebuilt on every three-second data refresh." The effect dependency array is
`[nodeLayoutKey, layoutNodes, onSelectNode]`, and `onSelectNode` is `useCallback`-wrapped in the page
(`digital-twin/page.tsx:48-52`) with the same reasoning.

## B.9 Animation loop and timing

```js
const timer = new THREE.Timer()
timer.connect(document)      // Page Visibility API — avoids a jump when the tab is restored
const animate = () => {
  timer.update()
  const elapsed = timer.getElapsed()
  /* uTime → water, rings spin/scale, controls.update() */
  renderer.render(scene, camera)
  animFrameRef.current = requestAnimationFrame(animate)
}
```

`THREE.Timer` instead of `THREE.Clock` — the comment at lines 412-413 records why: "Clock has been
deprecated since r183. Timer needs an explicit `update()` per frame before its getters return
meaningful values."

Renderer settings: `antialias: true`, `alpha: true`, `powerPreference: 'high-performance'`,
`setPixelRatio(min(devicePixelRatio, 2))`, `shadowMap.enabled = true`, `shadowMap.type = PCFShadowMap`.

Cleanup cancels the frame, disposes the Timer, removes both listeners, disposes the water
`ShaderMaterial` and calls `renderer.dispose()`.

Resize handler updates `camera.aspect`, `updateProjectionMatrix()` and `renderer.setSize`.

## B.10 Water height from the backend

See `05-ALGORITHMS-AND-AI-ML.md` §7.2 for the exact code. Summary: prefer
`simulationInput.forecasts[horizon]` (or `mithi_water_level_cm` at step 0) when
`simulationInput.status === 'live'`; otherwise take the max across the selected node (or all nodes)
of the matching forecast or latest reading; then `cm / 100` to metres, clamped at 0. If the backend
returns nothing, the target is 0 and the mesh drops to its base offset — **no illustrative curve**,
which is what the "TWIN SPECIFICATION" panel promises.

## B.11 Building flood recolouring

See `05-ALGORITHMS-AND-AI-ML.md` §7.3. Summary: `low_elevation` floods at +15, `transit` at +30,
anything with `x < 0` (the Kurla side) at +60; colour goes amber `0xf59e0b` / emissive `0x78350f`
at +15/+30 and red `0xef4444` / emissive `0x7f1d1d` at +60. Reset colours to
`commercial ? 0x1e3a4b : 0x182c38` with `emissiveIntensity = 0`.

**This is keyed off the timeline slider, not off `flood_impacts`.** The 3D twin's flood colouring
and the 2D map's inundation polygons are computed by different mechanisms and are not guaranteed to
agree.

## B.12 `app/digital-twin/page.tsx` (220 lines)

Overlays:

| Overlay | Position | Content |
|---|---|---|
| 3D HUD (in the component) | top-left | `3D DIGITAL TWIN · WEBGL GPU RENDERER`, `BKC Commercial Hub ↔ Kurla Flood Basin`, `Water Level: Normal Tidal Flow` or `Surge Level +Nm` |
| Camera panel | top-right | `Rotate` toggle + 5 camera buttons |
| Scenario readout | bottom-right | `REAL IoT DEMO INPUT: Vadodara Physical Sensor`; `MITHI RIVER DIGITAL TWIN · DRIVEN BY REAL IoT DEMO INPUT` or **`WAITING FOR REAL IoT INPUT`**; the `OBSERVED` / `PROJECTED +Nm` header with a source badge; `WATER LEVEL`, `FLOODED AREA`, `BUILDINGS` metrics |
| Timeline chips | bottom-left | `NOW/+15/+30/+60` |

Right rail: `BEACON` panel (code, badge, name, water level, rainfall, elevation, warning, last
reading) and a `TWIN SPECIFICATION` panel with the two honesty paragraphs.

---

# PART C — Design mockups (PDF) vs the as-built

`FLOODGUARD_AI_UI_Mockups.pdf` — 7 pages, ReportLab-generated, titled *"FLOODGUARD AI - UI Mockups"*.
Every page footer reads:

> "FLOODGUARD AI UI mockup. All values are sample data for illustration. Badges mark Live Hardware vs
> Simulated sources."

**These numbers are placeholders. None of them are produced by the system.**

| Page | Screen | Mockup says | Build does |
|---|---|---|---|
| 1 | Command Centre Dashboard | 5 nodes `MR-P1` (physical) + `MR-V1..V4`, model `RF v1.0.3`, `Live Hardware` / `Simulated` badges | node codes are whatever is registered; the current build only ever shows physical nodes; the badge says `REAL IoT INPUT`; no `RF v1.0.3` version string exists in the code |
| 2 | AI Prediction | score 0.68 HIGH, "Driven mainly by rapid rise rate", MAE 3.1/2.2, 6.0/3.9, 11.8/7.4, "Trained 04 Oct, 11:20 IST" | all of these are illustrative. Real MAEs are 0.239/0.347, 0.326/0.467, 0.477/0.680. The footer admits "Error values are placeholders. A few minutes of prototype data cannot establish real-world accuracy." |
| 3 | 2D Flood Map | "Leaflet + OpenStreetMap style view", node markers `MR-V1..MR-P1`, "Low spot, not connected: kept dry", affected 18 buildings / 5 roads / 0.6 m | no tile layer; no markers render (see A.3); the connectivity mask is implemented but its effect is not annotated on the map |
| 4 | 3D Digital Twin | "CesiumJS terrain, buildings and flood overlay", building card "Building B-1042", layers incl. Roads | three.js, procedurally placed boxes, no per-building click cards, no road layer |
| 5 | What-If Simulator | runoff coefficient **0.6**, peak 192 cm, 0.42 km², 9 roads, 31 buildings, "SEVERE at +60" | the backend uses runoff coefficient **0.7**; area/roads/buildings/highest-risk are **not returned** by `/compare` |
| 6 | Scenario Comparison | +0.27 km², +7 roads, +14 buildings, one-sentence summary "Under the scenario, 14 additional buildings and 7 more road segments are affected by the +60 minute mark." | only peak/mean level differences and a shorter summary sentence are computed |
| 7 | Alerts & Monitoring | `MR-P1` 4.9 V battery, alert detail with triggering readings, Ack/Map buttons per alert | battery is shown as a % (converted from volts), not volts; there are no per-alert Ack/Map buttons in the list — only the expandable detail + Acknowledge |

**What the mockups got right and the build preserved:** the dark command-centre shell, the
LIVE/SIMULATED badge convention, the shared NOW/+15/+30/+60 timeline, the "not an official flood
zone" disclaimer, the scenario banner wording, and the `RF v1.0.3 / Connected / MODE: LIVE / IST
clock` top-bar layout.

**What the build changed:** node naming (Vadodara demo input), badge wording, the whole 3D engine,
the 2D basemap, and the scope of the scenario comparison.