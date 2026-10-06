import type { ConnectionState, Mode, RiskLevel, SourceType } from '@/types'

/**
 * Single source of truth for the command-centre design language.
 *
 * Everything visual resolves through this module. Previously the same semantic
 * (risk, provenance, connection state) was re-typed in at least four places —
 * `COLORS`, `riskColor()`, hard-coded hex in charts, and hex in TSX — which is
 * how cyan `#16d6d2` and sky `#38bdf8` ended up both meaning "primary data".
 */

export const COLORS = {
  // surfaces
  bg: '#061118',
  bgAlt: '#0A1921',
  panel: '#0D2029',
  panelHi: '#102832',
  // lines
  border: '#1c3038',
  borderStrong: '#25404b',
  // type
  text: '#e8f0f4',
  textDim: '#a8bcc6',
  muted: '#7e97a3',
  dim: '#5c7480',
  // semantics — state, never decoration
  accent: '#16d6d2',
  ok: '#4fd39a',
  warn: '#f5b55c',
  danger: '#ef6b62',
  purple: '#a98cff',
  info: '#5b9dff',
} as const

/** 4px-based spacing scale. Arbitrary padding values are a bug, not a choice. */
export const SPACE = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  '2xl': 24,
  '3xl': 32,
} as const

export const RADIUS = { sm: 4, md: 6, lg: 8, xl: 12 } as const

export const TYPE = {
  pageTitle: '26px',
  sectionTitle: '15px',
  cardTitle: '11px',
  metric: '30px',
  metricLg: '38px',
  body: '13px',
  meta: '11px',
} as const

// ── Risk ────────────────────────────────────────────────────────────────────
// One palette, one severity ordering. `unknown` is deliberately NOT styled as
// "offline" any more: an unassessed node is not a failed node.

export const RISK_ORDER: RiskLevel[] = ['low', 'moderate', 'high', 'severe', 'unknown']

export const RISK_META: Record<
  RiskLevel,
  { label: string; color: string; tint: string; rank: number }
> = {
  low: { label: 'LOW', color: COLORS.ok, tint: 'rgba(79,211,154,0.10)', rank: 1 },
  moderate: { label: 'MODERATE', color: COLORS.warn, tint: 'rgba(245,181,92,0.10)', rank: 2 },
  high: { label: 'HIGH', color: '#f97316', tint: 'rgba(249,115,22,0.10)', rank: 3 },
  severe: { label: 'SEVERE', color: COLORS.danger, tint: 'rgba(239,107,98,0.12)', rank: 4 },
  unknown: { label: 'NO DATA', color: COLORS.muted, tint: 'rgba(126,151,163,0.08)', rank: 0 },
}

function normRisk(level: string | null | undefined): RiskLevel {
  switch ((level ?? '').toLowerCase()) {
    case 'severe':
    case 'critical':
      return 'severe'
    case 'high':
      return 'high'
    case 'moderate':
    case 'warning':
      return 'moderate'
    case 'low':
      return 'low'
    default:
      return 'unknown'
  }
}

export function riskLevel(level: string | null | undefined): RiskLevel {
  return normRisk(level)
}

export function riskColor(level: string | null | undefined): string {
  return RISK_META[normRisk(level)].color
}

/** CSS modifier used by the stylesheet. Kept for the existing `risk-*` rules. */
export function riskClass(level: string | null | undefined): string {
  return `risk-${normRisk(level)}`
}

/** Sort helper: worst first, unknown last. */
export function riskRank(level: string | null | undefined): number {
  return RISK_META[normRisk(level)].rank
}

// ── Provenance ──────────────────────────────────────────────────────────────
// Simulated and forecast data must never be mistakable for a live reading.

export const SOURCE_META: Record<
  SourceType,
  { label: string; short: string; color: string; bg: string }
> = {
  iot: { label: 'REAL IoT INPUT', short: 'IoT', color: COLORS.accent, bg: 'rgba(22,214,210,0.12)' },
  forecast: { label: 'FORECAST', short: 'FCST', color: COLORS.info, bg: 'rgba(91,157,255,0.12)' },
  historical: { label: 'HISTORICAL', short: 'HIST', color: COLORS.textDim, bg: 'rgba(168,188,198,0.10)' },
  simulated: { label: 'SIMULATED', short: 'SIM', color: COLORS.purple, bg: 'rgba(169,140,255,0.12)' },
  scenario: { label: 'SCENARIO', short: 'SCEN', color: COLORS.warn, bg: 'rgba(245,181,92,0.12)' },
  replay: { label: 'REPLAY', short: 'RPLY', color: COLORS.purple, bg: 'rgba(169,140,255,0.10)' },
  terrain: { label: 'TERRAIN / OSM', short: 'DEM', color: COLORS.textDim, bg: 'rgba(168,188,198,0.10)' },
  osm: { label: 'OSM', short: 'OSM', color: COLORS.textDim, bg: 'rgba(168,188,198,0.10)' },
}

export const SOURCE_LABELS: Record<SourceType, string> = Object.fromEntries(
  (Object.keys(SOURCE_META) as SourceType[]).map((k) => [k, SOURCE_META[k].label]),
) as Record<SourceType, string>

/** Provenance implied by a node's physical/virtual nature. */
export function nodeSource(nodeType: string | null | undefined): SourceType {
  return nodeType === 'physical' ? 'iot' : 'simulated'
}

export function hasRecentLiveIoTReading(node: {
  node_type?: string | null
  latest_reading?: { source?: string | null; timestamp?: string | null } | null
} | null | undefined): boolean {
  if (node?.node_type !== 'physical' || node.latest_reading?.source !== 'iot') return false
  if (!node.latest_reading.timestamp) return false
  const ageMs = Date.now() - Date.parse(node.latest_reading.timestamp)
  return Number.isFinite(ageMs) && ageMs <= 5 * 60 * 1000
}

// ── Connection ──────────────────────────────────────────────────────────────
// Exactly one authoritative link state for the whole console. `degraded` is the
// important one: REST is healthy but the realtime socket is down, which is NOT
// the same as "offline" and must not be shown as though the data were stale.

export const CONNECTION_META: Record<
  ConnectionState,
  { label: string; color: string; detail: string }
> = {
  live: { label: 'LIVE', color: COLORS.ok, detail: 'Realtime feed connected' },
  degraded: { label: 'DEGRADED', color: COLORS.warn, detail: 'Polling REST — realtime socket down' },
  connecting: { label: 'CONNECTING', color: COLORS.textDim, detail: 'Establishing link' },
  reconnecting: { label: 'RECONNECTING', color: COLORS.warn, detail: 'Re-establishing realtime feed' },
  offline: { label: 'OFFLINE', color: COLORS.danger, detail: 'Backend unreachable' },
}

export const MODE_META: Record<Mode, { label: string; color: string; bg: string }> = {
  live: { label: 'LIVE', color: COLORS.accent, bg: 'rgba(22,214,210,0.12)' },
  replay: { label: 'REPLAY', color: COLORS.purple, bg: 'rgba(169,140,255,0.12)' },
  scenario: { label: 'SCENARIO', color: COLORS.warn, bg: 'rgba(245,181,92,0.12)' },
}

export const RISK_LABELS: Record<RiskLevel, string> = {
  low: 'Low',
  moderate: 'Moderate',
  high: 'High',
  severe: 'Severe',
  unknown: 'Unknown',
}

// ── Timeline ────────────────────────────────────────────────────────────────

export const TIME_STEPS = [0, 15, 30, 60] as const
export const TIME_STEP_LABELS: Record<number, string> = { 0: 'NOW', 15: '+15m', 30: '+30m', 60: '+60m' }
export const FORECAST_HORIZONS = [15, 30, 60] as const

export const NAV_ITEMS = [
  { href: '/', label: 'Dashboard', short: 'Dashboard' },
  { href: '/prediction', label: 'AI Prediction', short: 'Prediction' },
  { href: '/map', label: '2D Flood Map', short: '2D Map' },
  { href: '/digital-twin', label: '3D Digital Twin', short: '3D Twin' },
  { href: '/scenarios', label: 'What-If Simulator', short: 'Simulator' },
  { href: '/alerts', label: 'Alerts & Monitoring', short: 'Alerts' },
  { href: '/data', label: 'About This Data', short: 'Data' },
] as const

/** Severity ordering for alert triage (most urgent first). */
export const ALERT_SEVERITY_ORDER = ['critical', 'high', 'warning', 'information'] as const

export function alertSeverityRank(s: string): number {
  const i = (ALERT_SEVERITY_ORDER as readonly string[]).indexOf(s)
  return i < 0 ? 99 : i
}