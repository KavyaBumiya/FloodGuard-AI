'use client'

import { usePathname } from 'next/navigation'
import { Menu } from 'lucide-react'
import { NAV_ITEMS } from '@/lib/constants'
import { ConnectionPill, ModePill } from './ui/StatusPill'
import type { ConnectionState, Mode } from '@/types'

/**
 * Command bar.
 *
 * Carries exactly ONE connection truth (`ConnectionPill`) plus the independent
 * data-mode fact (`ModePill`). The old bar rendered mode and connection
 * side-by-side with no labels, so "LIVE" and "Offline" read as competing
 * statuses for the same thing.
 */
export function CommandBar({
  connection,
  mode,
  clock,
  lastUpdate,
  onOpenNav,
}: {
  connection: ConnectionState
  mode: Mode
  clock: string
  lastUpdate: string | null
  onOpenNav: () => void
}) {
  const pathname = usePathname()
  const current = NAV_ITEMS.find((i) => i.href === pathname)

  return (
    <div className="topbar">
      <button
        type="button"
        className="menu-button"
        onClick={onOpenNav}
        aria-label="Open navigation"
      >
        <Menu size={15} aria-hidden="true" />
      </button>

      <nav className="crumbs" aria-label="Breadcrumb">
        <span className="crumb-root">FLOODGUARD AI</span>
        <span className="crumb-sep" aria-hidden="true">
          /
        </span>
        <span className="crumb-mid">MITHI RIVER</span>
        <span className="crumb-sep" aria-hidden="true">
          /
        </span>
        <span className="crumb-cur">{current?.label ?? 'Command Centre'}</span>
      </nav>

      <div className="topbar-status">
        {mode !== 'live' ? <ModePill mode={mode} /> : null}
        <ConnectionPill state={connection} />
        <div className="clock" title="Last successful data refresh">
          <span className="clock-label">LAST UPDATE</span>
          <span className="clock-time">{clock}</span>
          {lastUpdate ? <span className="clock-sub">{lastUpdate}</span> : null}
        </div>
      </div>
    </div>
  )
}