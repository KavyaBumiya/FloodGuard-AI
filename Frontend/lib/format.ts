/**
 * Display formatters.
 *
 * The hard rule for this console: a missing measurement renders as an explicit
 * placeholder, never as a plausible-looking zero. `(x ?? 0).toFixed(1)` turns
 * "no reading" into "0.0 mm/hr", which an operator reads as a real measurement.
 */

export const EM_DASH = '—'

export function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

/** Number with unit, or a placeholder when absent. */
export function fmtNum(v: number | null | undefined, unit = '', decimals = 0): string {
  if (!isNum(v)) return EM_DASH
  return `${v.toFixed(decimals)}${unit ? ` ${unit}` : ''}`
}

/** Value + unit rendered as separate spans so the unit can be de-emphasised. */
export function splitValue(v: number | null | undefined, decimals = 0): [string, string] {
  if (!isNum(v)) return [EM_DASH, '']
  return [v.toFixed(decimals), '']
}

export function fmtPct(v: number | null | undefined, decimals = 1): string {
  return isNum(v) ? `${(v * 100).toFixed(decimals)}%` : EM_DASH
}

/** km² from m², or placeholder. */
export function fmtArea(m2: number | null | undefined): string {
  return isNum(m2) ? `${(m2 / 1_000_000).toFixed(3)} km²` : EM_DASH
}

export function fmtInt(v: number | null | undefined): string {
  return isNum(v) ? String(Math.round(v)) : EM_DASH
}

/** Compact relative age. Returns null when there is no timestamp at all. */
export function timeAgo(iso: string | null | undefined): string | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return null
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 10) return 'just now'
  if (s < 60) return `${Math.round(s)}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

/** HH:MM:SS in IST — the operational clock format for this console. */
export function fmtClock(d: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(d)
}

/** HH:MM IST. */
export function fmtHm(d: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(d)
}

/** HH:MM from an ISO instant. */
export function fmtIsoHm(iso: string | null | undefined): string {
  if (!iso) return EM_DASH
  const d = new Date(iso)
  if (!Number.isFinite(d.getTime())) return EM_DASH
  return fmtHm(d)
}

/**
 * Trend between two levels, in cm.
 * Returns null when either value is missing so callers must handle "unknown"
 * rather than rendering a confident arrow.
 */
export function trend(
  from: number | null | undefined,
  to: number | null | undefined,
): { dir: 'rising' | 'falling' | 'steady' | 'unknown'; delta: number | null } {
  if (!isNum(from) || !isNum(to)) return { dir: 'unknown', delta: null }
  const delta = to - from
  if (Math.abs(delta) < 1) return { dir: 'steady', delta }
  return { dir: delta > 0 ? 'rising' : 'falling', delta }
}

export function trendGlyph(dir: 'rising' | 'falling' | 'steady' | 'unknown'): string {
  switch (dir) {
    case 'rising':
      return '▲'
    case 'falling':
      return '▼'
    case 'steady':
      return '▬'
    default:
      return '•'
  }
}

export function trendWord(dir: 'rising' | 'falling' | 'steady' | 'unknown'): string {
  switch (dir) {
    case 'rising':
      return 'Rising'
    case 'falling':
      return 'Falling'
    case 'steady':
      return 'Steady'
    default:
      return 'Unknown'
  }
}

/**
 * Pick the newest record per horizon. The backend returns a forecast history
 * (one row per `made_at`), so the latest issuance must win — otherwise the UI
 * silently shows a stale prediction.
 */
export function latestPerHorizon<T extends { horizon_minutes: number; made_at?: string | null }>(
  rows: T[],
): Map<number, T> {
  const m = new Map<number, T>()
  for (const r of rows) {
    const cur = m.get(r.horizon_minutes)
    if (!cur || (r.made_at ?? '') > (cur.made_at ?? '')) m.set(r.horizon_minutes, r)
  }
  return m
}

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}