'use client'

import { CONNECTION_META, MODE_META } from '@/lib/constants'
import { cx } from '@/lib/format'
import type { ConnectionState, Mode } from '@/types'

/**
 * The ONE authoritative link indicator for the console.
 *
 * Previously the app rendered `ModeIndicator` and `ConnectionIndicator`
 * together in the top bar, a second `ConnectionIndicator` in the sidebar, a
 * third on the dashboard, plus a "LIVE" mode card — so "LIVE" and "Offline"
 * appeared as competing truths on one screen. Connection and mode are different
 * facts and are now rendered as two separate, unambiguous fields.
 */
export function ConnectionPill({
  state,
  compact = false,
}: {
  state: ConnectionState
  compact?: boolean
}) {
  const meta = CONNECTION_META[state] ?? CONNECTION_META.connecting
  return (
    <span
      className={cx('conn-pill', state === 'live' && 'is-live', compact && 'is-compact')}
      style={{ color: meta.color, borderColor: meta.color, background: `${meta.color}14` }}
      title={meta.detail}
      role="status"
      aria-live="polite"
    >
      <span className={cx('conn-dot', state === 'live' && 'is-pulsing')} style={{ background: meta.color }} aria-hidden="true" />
      {meta.label}
    </span>
  )
}

/**
 * Data-mode indicator (live hardware feed vs replay vs scenario). Deliberately
 * not conflated with connection state.
 */
export function ModePill({ mode }: { mode: Mode }) {
  const meta = MODE_META[mode] ?? MODE_META.live
  return (
    <span
      className="mode-pill"
      style={{ color: meta.color, borderColor: meta.color, background: meta.bg }}
    >
      {meta.label}
    </span>
  )
}