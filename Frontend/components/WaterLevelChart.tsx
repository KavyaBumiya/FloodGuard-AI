'use client'

import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { COLORS } from '@/lib/constants'
import { fmtNum, isNum, latestPerHorizon } from '@/lib/format'
import type { ForecastPoint, SensorReading } from '@/types'

export interface ChartPoint {
  label: string
  observed?: number | null
  forecast?: number | null
  lower?: number | null
  band?: number | null
  rain?: number | null
  kind: 'observed' | 'now' | 'forecast'
}

const fmtTime = (iso: string) => {
  const d = new Date(iso)
  if (!Number.isFinite(d.getTime())) return '--:--'
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(d)
}

const TOOLTIP_BG = '#0b1c25'

/**
 * Observed water level vs forecast, with warning/critical thresholds.
 *
 * Design rules this enforces:
 *  - OBSERVED is a solid cyan line, FORECAST is dashed and visually detached.
 *  - The uncertainty band is drawn only where the backend actually supplied
 *    bounds. No bounds means no band — never an invented confidence envelope.
 *  - A vertical NOW rule makes the observed/forecast boundary unmissable.
 *  - Threshold lines are labelled in words, not only by colour.
 */
export function WaterLevelChart({
  history,
  forecasts,
  warningThreshold,
  criticalThreshold,
  height = 300,
  showSummary = true,
}: {
  history: SensorReading[]
  forecasts: ForecastPoint[]
  warningThreshold?: number | null
  criticalThreshold?: number | null
  height?: number
  showSummary?: boolean
}) {
  const points: ChartPoint[] = []
  // The x-axis is categorical, so every point costs the same width. Showing the
// full 60-reading history left the three forecast horizons squeezed into ~5% of
// the plot, which hid exactly the part the operator is judging. 26 observed
// samples keeps the recent trend while giving the forecast ~11% of the width.
const HISTORY_WINDOW = 26
const tail = history.slice(-HISTORY_WINDOW)

  tail.forEach((r) =>
    points.push({
      label: fmtTime(r.timestamp),
      observed: r.water_level_cm,
      rain: r.rainfall_mm_hr,
      kind: 'observed',
    }),
  )

  const last = tail[tail.length - 1]
  const nowIndex = points.length
  if (last) {
    points.push({
      label: 'NOW',
      observed: last.water_level_cm,
      forecast: last.water_level_cm,
      rain: last.rainfall_mm_hr,
      kind: 'now',
    })
  }

  const byHorizon = latestPerHorizon(forecasts)

  ;[15, 30, 60].forEach((h) => {
    const f = byHorizon.get(h)
    if (!f) return
    const lo = f.lower_bound_cm ?? null
    const hi = f.upper_bound_cm ?? null
    points.push({
      label: `+${h}m`,
      forecast: f.predicted_level_cm,
      observed: null,
      lower: lo,
      band: isNum(lo) && isNum(hi) ? Math.max(0, hi - lo) : null,
      kind: 'forecast',
    })
  })

  const current = last?.water_level_cm ?? null
  const series = points
    .flatMap((p) => [p.observed, p.forecast, p.lower, p.band])
    .filter(isNum) as number[]

  if (!points.length) {
    return (
      <div className="chart-empty" style={{ height }}>
        No readings received for this node yet.
      </div>
    )
  }

  const bounds = [...series, warningThreshold, criticalThreshold].filter(isNum)
  const lo = Math.min(...bounds, current ?? Infinity)
  const hi = Math.max(...bounds, current ?? -Infinity)
  const pad = Math.max(12, (hi - lo) * 0.18)
  const yMin = Math.max(0, Math.floor((lo - pad) / 10) * 10)
  const yMax = Math.ceil((hi + pad) / 10) * 10

  const hasBand = points.some((p) => isNum(p.band))

  return (
    <div className="chart-stack">
      <div className="chart-wrap" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
              data={points}
              margin={{ top: 10, right: 14, left: 0, bottom: 0 }}
            >
            <defs>
              <linearGradient id="wlBand" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLORS.info} stopOpacity={0.28} />
                <stop offset="100%" stopColor={COLORS.info} stopOpacity={0.05} />
              </linearGradient>
            </defs>

            <CartesianGrid strokeDasharray="2 4" stroke="rgba(140,161,173,0.14)" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: COLORS.muted }}
              tickLine={false}
              axisLine={{ stroke: COLORS.border }}
              interval="preserveStartEnd"
              minTickGap={8}
            />
            <YAxis
              domain={[yMin, yMax]}
              tick={{ fontSize: 10, fill: COLORS.muted }}
              tickLine={false}
              axisLine={false}
              width={52}
              unit=" cm"
            />
            <Tooltip
              contentStyle={{
                background: TOOLTIP_BG,
                border: `1px solid ${COLORS.borderStrong}`,
                borderRadius: 6,
                fontSize: 12,
                padding: '8px 10px',
              }}
              labelStyle={{ color: COLORS.textDim, fontSize: 11, marginBottom: 4 }}
              formatter={(v: unknown, n: unknown) => {
                const val = typeof v === 'number' ? v : Number(v)
                const name = String(n ?? '')
                if (!Number.isFinite(val)) return ['—', name]
                if (name === 'band') return [null, null]
                return [`${val.toFixed(1)} cm`, name]
              }}
            />

            {hasBand ? (
              <Area
                type="monotone"
                dataKey="lower"
                stackId="band"
                stroke="none"
                fill="transparent"
                connectNulls
                isAnimationActive={false}
                legendType="none"
              />
            ) : null}
            {hasBand ? (
              <Area
                type="monotone"
                dataKey="band"
                stackId="band"
                stroke="none"
                fill="url(#wlBand)"
                connectNulls
                isAnimationActive={false}
                legendType="none"
              />
            ) : null}

            {isNum(criticalThreshold) ? (
              <ReferenceLine
                y={criticalThreshold}
                stroke={COLORS.danger}
                strokeDasharray="6 4"
                strokeWidth={1.25}
                label={{
                  value: 'CRITICAL',
                  fill: COLORS.danger,
                  fontSize: 9,
                  fontWeight: 700,
                  position: 'insideTopRight',
                }}
              />
            ) : null}
            {isNum(warningThreshold) ? (
              <ReferenceLine
                y={warningThreshold}
                stroke={COLORS.warn}
                strokeDasharray="6 4"
                strokeWidth={1.25}
                label={{
                  value: 'WARNING',
                  fill: COLORS.warn,
                  fontSize: 9,
                  fontWeight: 700,
                  position: 'insideTopRight',
                }}
              />
            ) : null}

            {last ? (
              <ReferenceLine
                x={points[nowIndex]?.label}
                stroke={COLORS.accent}
                strokeWidth={1.25}
                strokeOpacity={0.7}
                label={{ value: 'NOW', fill: COLORS.accent, fontSize: 9, fontWeight: 700, position: 'top' }}
              />
            ) : null}

            {/* OBSERVED — solid */}
            <Line
              type="monotone"
              dataKey="observed"
              stroke={COLORS.accent}
              strokeWidth={2}
              dot={false}
              connectNulls={false}
              name="OBSERVED"
              isAnimationActive={false}
            />
            {/* FORECAST — dashed, detached */}
            <Line
              type="monotone"
              dataKey="forecast"
              stroke={COLORS.info}
              strokeWidth={2}
              strokeDasharray="5 4"
              dot={{ r: 3, fill: COLORS.info, strokeWidth: 0 }}
              connectNulls={false}
              name="FORECAST"
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="chart-legend" aria-hidden="true">
        <span className="lg">
          <i className="lg-line" style={{ background: COLORS.accent }} /> OBSERVED
        </span>
        <span className="lg">
          <i className="lg-dash" style={{ borderColor: COLORS.info }} /> FORECAST
        </span>
        {hasBand ? (
          <span className="lg">
            <i className="lg-swatch" style={{ background: 'rgba(91,157,255,0.25)' }} /> BOUNDS
          </span>
        ) : null}
        {isNum(warningThreshold) ? (
          <span className="lg">
            <i className="lg-dash" style={{ borderColor: COLORS.warn }} /> WARNING {warningThreshold} cm
          </span>
        ) : null}
        {isNum(criticalThreshold) ? (
          <span className="lg">
            <i className="lg-dash" style={{ borderColor: COLORS.danger }} /> CRITICAL {criticalThreshold} cm
          </span>
        ) : null}
      </div>

      {showSummary ? (
        <div className="fcst-strip">
          <div className="fcst-cell is-now">
            <div className="fcst-k">CURRENT</div>
            <div className="fcst-v">{fmtNum(current, 'cm', 0)}</div>
          </div>
          {[15, 30, 60].map((h) => {
            const f = byHorizon.get(h)
            const v = f?.predicted_level_cm
            return (
              <div key={h} className="fcst-cell">
                <div className="fcst-k">+{h} MIN</div>
                <div className="fcst-v">{isNum(v) ? `${v.toFixed(0)} cm` : '—'}</div>
                {isNum(f?.lower_bound_cm) && isNum(f?.upper_bound_cm) ? (
                  <div className="fcst-r">
                    {f!.lower_bound_cm!.toFixed(0)}–{f!.upper_bound_cm!.toFixed(0)}
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}

/** Model accuracy: MAE vs the persistence baseline, per horizon. */
export function ModelMetricsChart({
  rows,
}: {
  /** `null` = the backend reported no figure for this horizon. Rows with a null
   *  metric are dropped rather than charted as 0, which would read as a perfect
   *  model. The caller shows an em dash in the table beside this chart. */
  rows: { label: string; mae: number | null; baseline: number | null }[]
}) {
  const plottable = rows.filter((r) => r.mae !== null || r.baseline !== null)
  if (!plottable.length) {
    return (
      <p className="muted" style={{ padding: '12px 0' }}>
        No measured accuracy yet. The backend reports metrics only once a model is registered and
        evaluated.
      </p>
    )
  }
  const data = plottable.map((r) => ({
    label: r.label,
    mae: r.mae,
    baseline: r.baseline,
  }))
  return (
    <div className="chart-wrap" style={{ height: 190 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid strokeDasharray="2 4" stroke="rgba(140,161,173,0.14)" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: COLORS.muted }} tickLine={false} axisLine={{ stroke: COLORS.border }} />
          <YAxis tick={{ fontSize: 10, fill: COLORS.muted }} tickLine={false} axisLine={false} width={40} />
          <Tooltip
            contentStyle={{
              background: TOOLTIP_BG,
              border: `1px solid ${COLORS.borderStrong}`,
              borderRadius: 6,
              fontSize: 11,
            }}
            formatter={(v: unknown, n: unknown) => [
              typeof v === 'number' ? v.toFixed(2) : '—',
              String(n ?? ''),
            ]}
          />
          <Bar dataKey="mae" fill={COLORS.accent} name="Model MAE" radius={[3, 3, 0, 0]} />
          <Bar dataKey="baseline" fill="#2f4854" name="Persistence MAE" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}