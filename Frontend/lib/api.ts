/**
 * FLOODGUARD AI — single API client.
 *
 * Every page goes through here; no page invents its own fetch. The backend is
 * the source of truth for forecast, risk, flood simulation and impact numbers.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || 'http://127.0.0.1:8000'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function fetchJson<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE}${path}`
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  }

  let response: Response
  try {
    response = await fetch(url, { ...options, headers })
  } catch (err) {
    throw new ApiError(0, `Network error calling ${path}: ${(err as Error).message}`)
  }

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body?.detail || detail
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
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
  // ──────────────────────────────────────────────────────── Monitoring
  nodes: () => get<{ nodes: FloodNode[]; simulation_input: SimulationInput }>('/api/flood/nodes'),
  node: (code: string) => get<FloodNode>(`/api/flood/nodes/${code}`),
  summary: () => get<CurrentSummary>('/api/flood/current-summary'),
  forecasts: (code: string) =>
    get<{ node_code: string; forecasts: import('@/types').ForecastPoint[] }>(
      `/api/flood/forecasts/${code}`,
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
