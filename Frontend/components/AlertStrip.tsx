'use client'

import Link from 'next/link'
import { AlertTriangle, ArrowRight } from 'lucide-react'
import { RISK_META, riskLevel } from '@/lib/constants'
import { timeAgo } from '@/lib/format'
import type { Alert, CurrentSummary } from '@/types'

/**
 * Compact alert strip.
 *
 * Communicates WHAT / WHERE / WHEN / SEVERITY / ACTION on one line. The old
 * banner was a full-width coloured slab carrying only a message string, so an
 * operator could not tell which node, how bad, or what to do next.
 */
export function AlertStrip({
  alerts,
  summary,
}: {
  alerts: Alert[]
  summary: CurrentSummary | null
}) {
  const open = alerts.filter((a) => !a.acknowledged && a.is_active)
  if (!open.length) return null

  const worst = [...open].sort((a, b) => {
    const rank = (s: string) => RISK_META[riskLevel(s)].rank
    return rank(b.severity) - rank(a.severity)
  })[0]

  const meta = RISK_META[riskLevel(worst.severity)]
  const trigger = worst.trigger_data ?? {}
  const value =
    typeof trigger.current_value === 'number'
      ? trigger.current_value
      : typeof trigger.value === 'number'
        ? trigger.value
        : null
  const threshold =
    typeof trigger.threshold === 'number'
      ? trigger.threshold
      : typeof trigger.threshold_cm === 'number'
        ? trigger.threshold_cm
        : null

  return (
    <div
      className="alert-strip"
      style={{ borderColor: meta.color, background: meta.tint }}
      role="alert"
      aria-label={`${meta.label} alert`}
    >
      <span className="strip-icon" style={{ color: meta.color }} aria-hidden="true">
        <AlertTriangle size={14} />
      </span>

      <span className="strip-sev" style={{ color: meta.color, borderColor: meta.color }}>
        {meta.label}
      </span>

      <span className="strip-msg">{worst.message}</span>

      <span className="strip-meta">
        {worst.node_code ? <span className="strip-node">{worst.node_code}</span> : null}
        {value !== null ? (
          <span>
            {value}
            {threshold !== null ? ` / ${threshold}` : ''} cm
          </span>
        ) : null}
        {timeAgo(worst.created_at) ? <span>{timeAgo(worst.created_at)}</span> : null}
      </span>

      <Link href="/prediction" className="strip-action">
        View prediction <ArrowRight size={11} aria-hidden="true" />
      </Link>

      {open.length > 1 ? (
        <span className="strip-count">
          +{open.length - 1} more
        </span>
      ) : null}

      {summary?.highest_risk ? (
        <span className="sr-only">Corridor highest risk: {summary.highest_risk}</span>
      ) : null}
    </div>
  )
}