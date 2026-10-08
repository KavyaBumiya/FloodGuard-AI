// FLOODGUARD AI — frontend types.
// These mirror the backend response schemas exactly. The backend is the single
// source of truth for forecast, risk, flood extent and impact calculations.

export type SourceType =
  | 'iot'
  | 'historical'
  | 'forecast'
  | 'simulated'
  | 'terrain'
  | 'osm'
  | 'scenario'
  | 'replay'
export type RiskLevel = 'low' | 'moderate' | 'high' | 'severe' | 'unknown'
export type Mode = 'live' | 'replay' | 'scenario'
export type TimeStep = 0 | 15 | 30 | 60

export interface GeoPoint {
  lat: number
  lng: number
}

export interface SensorReading {
  water_level_cm: number
  rainfall_mm_hr: number
  timestamp: string
  source: 'iot' | 'simulated' | 'replay'
  flow_rate?: number | null
  soil_moisture?: number | null
  battery_voltage?: number | null
  risk_level?: string | null
  risk_score?: number | null
}

export interface LatestReading {
  node_code: string
  water_level_cm: number
  rainfall_mm_hr: number
  flow_rate?: number | null
  soil_moisture?: number | null
  battery_voltage?: number | null
  timestamp: string
  received_at: string
  age_seconds?: number | null
  risk_level?: string | null
  risk_score?: number | null
  source: string
  node_status?: string | null
  node_type?: string | null
}

export interface RiskComponent {
  name: string
  value: number
  normalized: number
  weight: number
  contribution: number
  band: string
}

export interface RiskResult {
  risk_level: RiskLevel
  risk_score: number
  horizon_minutes: number
  components?: RiskComponent[]
  predicted_level_cm?: number | null
  primary_driver?: string
  generated_at?: string
  model?: string
  model_version?: string
  assumptions?: string[]
}

export interface ForecastPoint {
  horizon_minutes: number
  predicted_level_cm: number
  lower_bound_cm?: number | null
  upper_bound_cm?: number | null
  target_time?: string
  made_at?: string
  status?: string
}

export interface FloodNode {
  node_code: string
  name: string
  node_type: 'physical' | 'virtual'
  role?: string | null
  location: GeoPoint
  reference_elevation_m?: number | null
  calibration_offset_cm?: number | null
  baseline_level_cm?: number | null
  warning_threshold_cm?: number | null
  latest_reading?: SensorReading | null
  current_risk?: RiskResult | null
  risk_by_horizon?: Record<number, RiskResult>
  // `/api/flood/forecasts/{code}` returns a flat array; `/api/flood/nodes/{code}`
  // keys the same objects by horizon. Use `forecastArray()` to read either.
  forecasts?: ForecastPoint[] | Record<number, ForecastPoint>
  forecast_status?: 'ok' | 'insufficient_live_iot_data' | 'waiting_for_iot'
}

/** Normalises either forecasts shape to a horizon-sorted array. */
export function forecastArray(
  input: ForecastPoint[] | Record<number, ForecastPoint> | null | undefined,
): ForecastPoint[] {
  if (!input) return []
  if (Array.isArray(input)) return input
  return Object.keys(input)
    .map((k) => input[Number(k)])
    .filter((f): f is ForecastPoint => Boolean(f))
    .sort((a, b) => a.horizon_minutes - b.horizon_minutes)
}

export interface SimulationInput {
  status: 'live' | 'waiting'
  label: string
  location_label: string
  simulation_label: string
  driver_label: string
  node_code?: string | null
  water_level_cm?: number | null
  rainfall_mm_hr?: number | null
  mithi_water_level_cm?: number | null
  risk?: { risk_level: string; risk_score?: number | null } | null
  forecasts?: ForecastPoint[]
  timestamp?: string | null
  forecast_status?: 'ok' | 'insufficient_live_iot_data' | 'waiting_for_iot'
  mapping: {
    label: string
    physical_min_cm: number
    physical_max_cm: number
    mithi_min_cm: number
    mithi_max_cm: number
    rainfall_factor: number
    configured_node_code?: string | null
    note: string
  }
}

export interface CurrentSummary {
  active_sensors: number
  total_sensors: number
  offline_stale_sensors: number
  highest_risk: RiskLevel
  highest_risk_node?: string | null
  current_rainfall_mm_hr: number
  timestamp: string
}

export interface GeoJsonGeometry {
  type: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  coordinates: any
}

export interface GeoJsonFeature {
  type: 'Feature'
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  properties: Record<string, any>
  geometry: GeoJsonGeometry
}

export interface GeoJsonFeatureCollection {
  type: 'FeatureCollection'
  features: GeoJsonFeature[]
}

export interface ImpactSummary {
  time_step_minutes: number
  flooded_area_m2: number | null
  max_depth_m: number | null
  affected_building_count: number
  affected_road_count: number
  depth_range_m: [number, number] | null
  buildings_by_risk?: Record<string, number>
  roads_by_risk?: Record<string, number>
  input_source: string
  scenario_id?: number | null
  generated_at: string
  note?: string
}

export interface FloodLayerResponse {
  time_step_minutes: number
  layer_type: 'depth' | 'risk' | 'boundary'
  geojson: GeoJsonFeatureCollection
  max_depth_m: number | null
  flooded_area_m2: number | null
  input_source: string
  grid_resolution_m: number
  generated_at: string
  scenario_id: number | null
  impact?: ImpactSummary
  note?: string
}

export interface ImpactSummaryResponse {
  scenario_id: number | null
  steps: ImpactSummary[]
  source: string | null
  generated_at: string
}

export interface ScenarioPreset {
  name: string
  rainfall_mm_hr: number
  initial_level_cm: number
  drainage_capacity_pct: number
  duration_minutes: number
}

export interface Scenario {
  id: number
  name: string | null
  preset: string | null
  rainfall_mm_hr: number
  initial_level_cm: number
  drainage_capacity_pct: number
  duration_minutes: number
  assumptions: Record<string, unknown>
  status: 'running' | 'completed' | 'failed'
  error_message?: string | null
  created_at: string
  completed_at?: string | null
}

export interface ScenarioComparison {
  baseline?: Record<string, unknown>
  scenario?: Record<string, unknown>
  comparison?: Record<string, unknown>
  summary?: Record<string, unknown>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [key: string]: any
}

/** The five rules the alert engine implements. */
export type AlertType =
  | 'threshold'
  | 'rapid_rise'
  | 'predicted_risk'
  | 'communication_failure'
  | 'sensor_fault'

export interface Alert {
  id: number
  node_code?: string | null
  node_id?: number | null
  scenario_id?: number | null
  /**
   * Each type carries a different `trigger_data` shape AND a different unit.
   * Read it through `lib/alertTrigger.describeTrigger` rather than guessing keys.
   */
  alert_type: AlertType
  severity: 'information' | 'warning' | 'high' | 'critical'
  message: string
  trigger_data?: Record<string, unknown> | null
  is_scenario_alert: boolean
  acknowledged: boolean
  created_at: string
  /**
   * Returned by `/api/alerts/active`, `/api/alerts/history` and the collection
   * endpoint. The active endpoint filters on it server-side, so it is always
   * true there.
   */
  is_active?: boolean
  acknowledged_at?: string | null
  acknowledged_by?: string | null
  cleared_at?: string | null
}

export interface NetworkHealth {
  node_code: string
  status: 'online' | 'stale' | 'offline' | 'no_data'
  last_seen_at?: string | null
  age_seconds?: number | null
  battery_pct?: number | null
  node_type?: string
  role?: string
  minutes_since_last_reading?: number
  [key: string]: unknown
}

export interface DataSource {
  id?: number
  name: string
  category: string
  provider: string
  resolution: string
  licence_note?: string
  limitation: string
}

export interface ModelInfo {
  models?: Record<string, { mae: number; baseline_mae: number; rmse: number; baseline_rmse: number }>
  horizon?: number
  [key: string]: unknown
}

export interface DemoStatus {
  mode: Mode
  replay_speed: number
  replay_recording: string | null
  replay_active: boolean
}

export interface HealthStatus {
  status: 'ok' | 'degraded'
  service?: string
  version?: string
  database?: string
  redis?: string
  /** Telemetry transport. Always `http` — there is no broker. */
  ingest?: string
  open_meteo?: string
}

/**
 * `degraded` is deliberately distinct from `offline`: REST polling still
 * returns fresh data while the realtime socket is unavailable. Reporting that
 * as "Offline" would imply the numbers on screen are stale when they are not.
 */
export type ConnectionState = 'connecting' | 'live' | 'degraded' | 'reconnecting' | 'offline'
