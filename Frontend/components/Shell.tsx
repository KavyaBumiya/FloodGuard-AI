'use client'

import { useEffect, useState } from 'react'
import { useFloodguardData } from '@/hooks/useFloodguard'
import { CONNECTION_META } from '@/lib/constants'
import { fmtClock, timeAgo } from '@/lib/format'
import { Sidebar } from './Sidebar'
import { CommandBar } from './CommandBar'
import { AlertStrip } from './AlertStrip'
import { TimelineControl, useTimelineAutoplay } from './TimelineControl'

/**
 * Global application shell.
 *
 * Owns exactly one realtime pipeline (REST boot + Socket.IO + poll safety net),
 * one clock, one timeline driver, and one set of status indicators. Pages read
 * the store; they never open their own socket or poll loop.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const { connection, mode, alerts, summary, nodes, error, lastUpdatedAt } = useFloodguardData()
  const [navOpen, setNavOpen] = useState(false)
  // Empty on the server AND on the first client render, then filled in by the
  // effect below. Seeding this with fmtClock() renders different text on the
  // server and the client, which is a hydration mismatch (React error #418).
  const [clock, setClock] = useState('')

  // Single autoplay driver for the whole console.
  useTimelineAutoplay()

  useEffect(() => {
    const tick = () => setClock(fmtClock())
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  const unacked = (alerts ?? []).filter((a) => !a.acknowledged).length
  const activeCount = summary?.active_sensors ?? nodes.filter((n) => n.latest_reading).length
  const totalCount = summary?.total_sensors ?? nodes.length
  const offlineCount = summary?.offline_stale_sensors ?? 0

  return (
    <div className="app-shell">
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} unacked={unacked}>
        <div className="status-block">
          <div className="status-block-head">
            <span>SYSTEM STATUS</span>
          </div>
          <div className="status-block-state">
            <span
              className="conn-dot"
              style={{ background: CONNECTION_META[connection]?.color }}
              aria-hidden="true"
            />
            <span style={{ color: CONNECTION_META[connection]?.color }}>
              {CONNECTION_META[connection]?.label}
            </span>
            <span className="status-clock">{clock || '--:--'}</span>
          </div>
          <div className="status-block-grid">
            <div>
              <b>{activeCount}</b>
              <span>/{totalCount} ACTIVE</span>
            </div>
            <div>
              <b className={offlineCount > 0 ? 'is-warn' : undefined}>{offlineCount}</b>
              <span>OFFLINE</span>
            </div>
          </div>
        </div>
        <p className="sidebar-note">
          Predicted extent — model estimate, not an official flood zone.
        </p>
      </Sidebar>

      <main className="main">
        <CommandBar
          connection={connection}
          mode={mode}
          clock={clock}
          lastUpdate={timeAgo(lastUpdatedAt)}
          onOpenNav={() => setNavOpen(true)}
        />

        {connection === 'offline' || error ? (
          <div className="strip strip-degraded" role="status">
            <b>DEGRADED</b>
            <span>
              {error
                ? `Backend unreachable — showing last loaded state. ${error}`
                : 'Backend unreachable — showing last loaded state.'}
            </span>
            {timeAgo(lastUpdatedAt) ? <span className="strip-dim">as of {timeAgo(lastUpdatedAt)}</span> : null}
          </div>
        ) : null}

        {mode === 'replay' ? (
          <div className="strip strip-replay" role="status">
            <b>REPLAY</b>
            <span>Recorded data being replayed — not live conditions.</span>
          </div>
        ) : mode === 'scenario' ? (
          <div className="strip strip-scenario" role="status">
            <b>SIMULATED SCENARIO</b>
            <span>User-defined scenario — not live conditions.</span>
          </div>
        ) : null}

        <AlertStrip alerts={alerts ?? []} summary={summary} />

        <div className="page-content">
          <div className="page-toolbar">
            <TimelineControl />
          </div>
          {children}
        </div>
      </main>
    </div>
  )
}