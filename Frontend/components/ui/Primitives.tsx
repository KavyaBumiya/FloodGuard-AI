import type { ReactNode } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import {
  COLORS,
  RISK_META,
  SOURCE_META,
  riskLevel,
} from '@/lib/constants'
import { cx } from '@/lib/format'
import type { SourceType } from '@/types'

/**
 * Provenance badge. Every number whose origin matters gets one.
 *
 * This component enforces the project's core honesty rule: a simulated value
 * must never read as a live measurement. Colour is never the only cue — the
 * label text always names the class, so it survives greyscale and
 * colour-blindness.
 */
export function DataSourceBadge({
  source,
  short = false,
  note,
}: {
  source: SourceType
  short?: boolean
  note?: string
}) {
  const meta = SOURCE_META[source] ?? SOURCE_META.terrain
  return (
    <span
      className="src-badge"
      style={{ color: meta.color, background: meta.bg, borderColor: meta.color }}
      title={note ?? `Data class: ${meta.label}`}
    >
      {short ? meta.short : meta.label}
    </span>
  )
}

/**
 * Risk badge. Always renders the word — never colour alone.
 */
export function RiskBadge({
  level,
  score,
  size = 'md',
}: {
  level: string | null | undefined
  score?: number | null
  size?: 'sm' | 'md' | 'lg'
}) {
  const meta = RISK_META[riskLevel(level)]
  return (
    <span
      className={cx('risk-badge', `risk-${size}`)}
      style={{ color: meta.color, background: meta.tint, borderColor: meta.color }}
    >
      <span className="risk-badge-dot" style={{ background: meta.color }} aria-hidden="true" />
      {meta.label}
      {typeof score === 'number' && <span className="risk-badge-score">{score.toFixed(2)}</span>}
    </span>
  )
}

export function PanelTitle({
  children,
  right,
  icon,
}: {
  children: ReactNode
  right?: ReactNode
  icon?: ReactNode
}) {
  return (
    <div className="panel-head">
      <span className="panel-title">
        {icon}
        {children}
      </span>
      {right ? <span className="panel-head-right">{right}</span> : null}
    </div>
  )
}

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow: string
  title: string
  subtitle?: string
  actions?: ReactNode
}) {
  return (
    <header className="page-title">
      <div className="page-title-text">
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {subtitle ? <p className="page-subtitle">{subtitle}</p> : null}
      </div>
      {actions ? <div className="page-title-actions">{actions}</div> : null}
    </header>
  )
}

/**
 * KPI tile. Compact by default: label, value + unit, one supporting line.
 */
export function KpiCard({
  label,
  value,
  unit,
  hint,
  tone = 'default',
  badge,
  accent,
}: {
  label: string
  value: string
  unit?: string
  hint?: ReactNode
  tone?: 'default' | 'good' | 'warn' | 'bad' | 'accent'
  badge?: ReactNode
  accent?: ReactNode
}) {
  return (
    <div className={cx('kpi', `kpi-${tone}`)}>
      <div className="kpi-label">
        <span>{label}</span>
        {accent ? <span className="kpi-accent">{accent}</span> : null}
      </div>
      <div className="kpi-value">
        {value}
        {unit ? <span className="kpi-unit">{unit}</span> : null}
      </div>
      <div className="kpi-foot">
        {badge}
        {hint ? <span className="kpi-hint">{hint}</span> : null}
      </div>
    </div>
  )
}

export function MetricValue({
  label,
  value,
  unit,
  tone,
}: {
  label: string
  value: string
  unit?: string
  tone?: string
}) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value" style={tone ? { color: tone } : undefined}>
        {value}
        {unit ? <span className="metric-unit">{unit}</span> : null}
      </div>
    </div>
  )
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty-state">
      <div className="empty-title">{title}</div>
      {hint ? <div className="empty-hint">{hint}</div> : null}
    </div>
  )
}

/**
 * Error state that says what failed, when the last good data was, and offers a
 * retry. A bare "Error fetching data" tells an operator nothing actionable.
 */
export function ErrorState({
  title = 'DATA UNAVAILABLE',
  detail,
  lastGood,
  onRetry,
}: {
  title?: string
  detail?: string | null
  lastGood?: string | null
  onRetry?: () => void
}) {
  return (
    <div className="error-state" role="alert">
      <AlertTriangle size={16} style={{ color: COLORS.danger, flexShrink: 0 }} aria-hidden="true" />
      <div className="error-body">
        <div className="error-title">{title}</div>
        {detail ? <div className="error-detail">{detail}</div> : null}
        {lastGood ? <div className="error-lastgood">Last successful reading: {lastGood}</div> : null}
      </div>
      {onRetry ? (
        <button type="button" className="button secondary" onClick={onRetry}>
          <RefreshCw size={12} aria-hidden="true" /> Retry
        </button>
      ) : null}
    </div>
  )
}

function Bar({ w, h = 9 }: { w: number | string; h?: number }) {
  return <span className="skel" style={{ display: 'block', height: h, width: w }} aria-hidden="true" />
}

export function SkeletonKpi() {
  return (
    <div className="kpi" aria-busy="true" aria-label="Loading metric">
      <Bar w="62%" />
      <Bar w="44%" h={26} />
      <Bar w="70%" h={8} />
    </div>
  )
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading list">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skel-row">
          <Bar w={10} h={10} />
          <div style={{ flex: 1 }}>
            <Bar w={`${45 + ((i * 13) % 30)}%`} />
          </div>
          <Bar w={44} />
        </div>
      ))}
    </div>
  )
}

export function SkeletonBlock({ height = 240, label = 'Loading' }: { height?: number; label?: string }) {
  return (
    <div className="skel-block" style={{ height }} role="status" aria-label={label}>
      <span className="skel skel-fill" aria-hidden="true" />
      <span className="skel-label">{label}…</span>
    </div>
  )
}