/**
 * FLOODGUARD AI — single API client.
 *
 * Every page goes through here; no page invents its own fetch. The backend is
 * the source of truth for forecast, risk, flood simulation and impact numbers.
 *
 * The base URL is environment-driven (NEXT_PUBLIC_API_BASE). When the variable
 * is missing the client falls back to the deployed Azure backend — never to
 * localhost — so a production build cannot silently point at a dev machine.
 */

/** Deployed backend. Public configuration only; no secrets live here. */
export const PRODUCTION_API_BASE =
  'https://floodguard-backend.ashyplant-04472250.centralindia.azurecontainerapps.io'

const trimTrailingSlash = (url: string) => url.replace(/\/+$/, '')

export const API_BASE = trimTrailingSlash(
  process.env.NEXT_PUBLIC_API_BASE || PRODUCTION_API_BASE,
)

/** Give up on a hung request so the UI can show its error state instead of spinning. */
const REQUEST_TIMEOUT_MS = 20000

export type ApiErrorKind = 'network' | 'auth' | 'not_found' | 'rate_limited' | 'server' | 'http'

function kindFor(status: number): ApiErrorKind {
  if (status === 0) return 'network'
  if (status === 401 || status === 403) return 'auth'
  if (status === 404) return 'not_found'
  if (status === 429) return 'rate_limited'
  if (status >= 500) return 'server'
  return 'http'
}

export class ApiError extends Error {
  status: number
  kind: ApiErrorKind
  constructor(status: number, message: string, kind?: ApiErrorKind) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.kind = kind ?? kindFor(status)
  }
}

export const isApiError = (err: unknown): err is ApiError => err instanceof ApiError

/** 401/403 must never be mistaken for "no data" — callers surface these instead of emptying a panel. */
export const isAuthError = (err: unknown): err is ApiError =>
  isApiError(err) && err.kind === 'auth'

/** True when the backend simply has no record (404) — renders an empty state, not a crash. */
export const isNotFound = (err: unknown): err is ApiError =>
  isApiError(err) && err.kind === 'not_found'

/**
 * The one place any API failure becomes user-facing text. Every panel should
 * render this rather than `err.message` so network, auth, missing-data and
 * backend faults are never conflated.
 */
export function describeApiError(err: unknown): string {
  if (!isApiError(err)) {
    return err instanceof Error ? err.message : 'Unexpected error talking to the backend.'
  }
  switch (err.kind) {
    case 'network':
      return `Cannot reach the FloodGuard API at ${API_BASE}. The backend may be down or this device is offline.`
    case 'auth':
      return `Not authorised (${err.status}): ${err.message}. This session is not allowed to read that data.`
    case 'not_found':
      return `Data not available (404): ${err.message}`
    case 'rate_limited':
      return `Rate limited (429): ${err.message}`
    case 'server':
      return `Backend error (${err.status}): ${err.message}`
    default:
      return err.message
  }
}

/** FastAPI sends `detail` as a string, or as a validation-error array. */
function readDetail(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null
  const detail = (body as { detail?: unknown }).detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => {
      if (!d || typeof d !== 'object') return String(d)
      const { loc, msg } = d as { loc?: unknown; msg?: unknown }
      const where = Array.isArray(loc) ? loc.slice(1).join('.') : ''
      return where && msg ? `${where}: ${msg}` : String(msg ?? JSON.stringify(d))
    })
    if (parts.length) return parts.join('; ')
  }
  return null
}

async function fetchJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE}${path}`
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  }

  const canTimeout = typeof AbortSignal !== 'undefined' && 'timeout' in AbortSignal
  const init: RequestInit = { ...options, headers }
  if (canTimeout && !options.signal) {
    init.signal = AbortSignal.timeout(REQUEST_TIMEOUT_MS)
  }

  let response: Response
  try {
    response = await fetch(url, init)
  } catch (err) {
    const name = (err as Error)?.name
    if (name === 'TimeoutError' || name === 'AbortError') {
      throw new ApiError(0, `Request to ${path} timed out after ${REQUEST_TIMEOUT_MS / 1000}s`, 'network')
    }
    throw new ApiError(0, `Network error calling ${path}: ${(err as Error).message}`, 'network')
  }

  if (!response.ok) {
    let detail = response.statusText || `HTTP ${response.status}`
    try {
      detail = readDetail(await response.json()) ?? detail
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  const text = await response.text()
  if (!text) return undefined as T
  return JSON.parse(text) as T
}

const get = <T>(path: string) => fetchJson<T>(path)
const post = <T>(path: string, body?: unknown) =>
  fetchJson<T>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const search = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') search.set(k, String(v))
  }
  const s = search.toString()
  return s ? `?${s}` : ''
}

import type {
  Alert,
  CurrentSummary,
  DataSource,
  DemoStatus,
  FloodLayerResponse,
  FloodNode,
  ImpactSummaryResponse,
  SimulationInput,
  ModelInfo,
  NetworkHealth,
  Scenario,
  ScenarioComparison,
  ScenarioPreset,
  GeoJsonFeatureCollection,
} from '@/types'

export interface StudyAreaResponse {
  name: string
  bounds: { south: number; west: number; north: number; east: number }
  representative_point: { lat: number; lng: number }
  grid_resolution_m: number
  description: string
}

export const api = {
  // ──────────────────────────────────────────────────────── System / health
  /** Liveness probe. Never touches a dependency, so it is the right call to
   *  tell "backend unreachable" apart from "one endpoint is failing". */
  healthz: () =>
    get<{ status: string; service?: string; version?: string }>('/healthz'),

  // ──────────────────────────────────────────────────────── Monitoring
  nodes: () => get<{ nodes: FloodNode[]; simulation_input: SimulationInput }>('/api/flood/nodes'),
  node: (code: string) => get<FloodNode>(`/api/flood/nodes/${encodeURIComponent(code)}`),
  summary: () => get<CurrentSummary>('/api/flood/current-summary'),
  forecasts: (code: string) =>
    get<{ node_code: string; forecasts: import('@/types').ForecastPoint[] }>(
      `/api/flood/forecasts/${encodeURIComponent(code)}`,
    ),

  // ──────────────────────────────────────────────────────── Per-node telemetry history (for charts)
  deviceHistory: (code: string, limit = 60) =>
    get<import('@/types').SensorReading[]>(
      `/api/iot/devices/${encodeURIComponent(code)}/history${qs({ limit })}`,
    ),

  // ──────────────────────────────────────────────────────── Flood layers / impact
  floodLayer: (timeStep: number, opts: { layerType?: string; scenarioId?: number | null } = {}) =>
    get<FloodLayerResponse>(
      `/api/flood/layers/${timeStep}${qs({
        layer_type: opts.layerType ?? 'depth',
        scenario_id: opts.scenarioId ?? undefined,
      })}`,
    ),
  impactSummary: (scenarioId?: number | null) =>
    get<ImpactSummaryResponse>(
      `/api/flood/impact-summary${qs({ scenario_id: scenarioId ?? undefined })}`,
    ),
  mapLayers: (
    layer: 'roads' | 'buildings' | 'waterways' | 'sensors',
    bbox?: string,
  ) =>
    get<GeoJsonFeatureCollection>(
      `/api/flood/map-layers${qs({ layer, bbox })}`,
    ),

  // ──────────────────────────────────────────────────────── Scenarios
  scenarioPresets: () =>
    get<{ presets: Record<string, ScenarioPreset>; assumptions: Record<string, unknown> }>(
      '/api/scenarios/presets',
    ),
  runScenario: (params: {
    name?: string
    preset?: string
    rainfall_mm_hr: number
    initial_level_cm: number
    drainage_capacity_pct: number
    duration_minutes: number
  }) =>
    post<{ scenario_id: number; status: string; message: string }>(
      `/api/scenarios/run${qs({
        name: params.name,
        preset: params.preset,
        rainfall_mm_hr: params.rainfall_mm_hr,
        initial_level_cm: params.initial_level_cm,
        drainage_capacity_pct: params.drainage_capacity_pct,
        duration_minutes: params.duration_minutes,
      })}`,
    ),
  scenario: (id: number) => get<Record<string, unknown>>(`/api/scenarios/${id}`),
  compareScenario: (id: number) => get<ScenarioComparison>(`/api/scenarios/${id}/compare`),
  listScenarios: (limit = 20) => get<{ scenarios: Scenario[] }>(`/api/scenarios${qs({ limit })}`),

  // ──────────────────────────────────────────────────────── Alerts
  activeAlerts: (scenarioId?: number | null) =>
    get<{ alerts: Alert[]; severity_counts?: Record<string, number> }>(
      `/api/alerts/active${qs({ scenario_id: scenarioId ?? undefined })}`,
    ),
  alertHistory: (params: Record<string, string | number | boolean | undefined> = {}) =>
    get<{ alerts: Alert[] }>(`/api/alerts/history${qs(params)}`),
  acknowledgeAlert: (id: number) => post<{ message: string; alert_id: number }>(`/api/alerts/${id}/acknowledge`),
  acknowledgeAll: () => post<{ ok: boolean }>('/api/alerts/acknowledge-all'),
  networkHealth: () => get<{ nodes: NetworkHealth[] }>('/api/alerts/network-health'),

  // ──────────────────────────────────────────────────────── Demo / replay mode
  demoStatus: () => get<DemoStatus>('/api/demo/status'),

  // ──────────────────────────────────────────────────────── Transparency / about
  dataSources: () => get<{ data_sources: DataSource[] }>('/api/about/data-sources'),
  modelInfo: () => get<ModelInfo>('/api/about/model-info'),
  assumptions: () => get<Record<string, unknown>>('/api/about/assumptions'),
  studyArea: () => get<StudyAreaResponse>('/api/about/study-area'),
  honestyNote: () => get<{ note: string }>('/api/about/honesty-note'),
}

export default api
