'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { CheckCheck, ChevronDown } from 'lucide-react'
import api, { describeApiError, isAuthError } from '@/lib/api'
import { ALERT_SEVERITY_ORDER, RISK_META, riskLevel } from '@/lib/constants'
import { EM_DASH, fmtIsoHm, isNum, timeAgo } from '@/lib/format'
import { describeTrigger } from '@/lib/alertTrigger'
import { useAppState } from '@/hooks/useFloodguard'
import {
  DataSourceBadge,
  EmptyState,
  ErrorState,
  KpiCard,
  MetricValue,
  PageHeader,
  PanelTitle,
  SkeletonRows,
} from '@/components/ui/Primitives'
import type { Alert, NetworkHealth } from '@/types'

const ALERT_TYPES = ['', 'threshold', 'rapid_rise', 'predicted_risk', 'communication_failure', 'sensor_fault', 'scenario']

type Health = { nodes: NetworkHealth[]; summary?: Record<string, number> }

export default function AlertsPage() {
  const { alerts } = useAppState()
  const [history, setHistory] = useState<Alert[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [nodeFilter, setNodeFilter] = useState('')
  const [sevFilter, setSevFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)

  const reload = useCallback(() => {
    api
      .alertHistory({ limit: 100 })
      .then((r) => {
        setHistory(r.alerts ?? [])
        setError(null)
      })
      .catch((err: Error) => {
        setHistory([])
        setError(`Alert history unavailable: ${describeApiError(err)}`)
      })
      .finally(() => setLoading(false))
    api
      .networkHealth()
      .then((r) => setHealth(r as Health))
      .catch((err: Error) => {
        setHealth(null)
        // 401/403 is a configuration problem, not "no health data".
        if (isAuthError(err)) setError(describeApiError(err))
      })
  }, [])

  useEffect(reload, [reload])

  const ack = async (id: number) => {
    setBusy(true)
    try {
      await api.acknowledgeAlert(id)
      reload()
    } catch (err) {
      setError(describeApiError(err))
    } finally {
      setBusy(false)
    }
  }

  const ackAll = async () => {
    setBusy(true)
    try {
      await api.acknowledgeAll()
      reload()
    } catch (err) {
      setError(describeApiError(err))
    } finally {
      setBusy(false)
    }
  }

  const match = useCallback(
    (a: Alert) =>
      (!nodeFilter || a.node_code === nodeFilter) &&
      (!sevFilter || a.severity === sevFilter) &&
      (!typeFilter || a.alert_type === typeFilter),
    [nodeFilter, sevFilter, typeFilter],
  )

  // The store holds `/api/alerts/active`, which is already the active set. The
  // response carries no `is_active` flag, so filtering on it would empty this
  // list.
  const active = useMemo(
    () => (alerts ?? []).filter((a) => match(a)),
    [alerts, match],
  )
  const hist = useMemo(() => history.filter(match), [history, match])

  const counts = useMemo(() => {
    const c = { critical: 0, unacked: 0, active: active.length, resolved: 0 }
    for (const a of alerts ?? []) if (!a.acknowledged) c.unacked++
    for (const a of history) if (a.acknowledged) c.resolved++
    for (const a of active) if (!a.acknowledged && a.severity === 'critical') c.critical++
    return c
  }, [alerts, active, history])

  const nodeCodes = useMemo(
    () => Array.from(new Set([...(alerts ?? []), ...history].map((a) => a.node_code).filter(Boolean) as string[])),
    [alerts, history],
  )

  return (
    <>
      <PageHeader
        eyebrow="ALERTS & MONITORING"
        title="Alert Centre"
        subtitle="Active thresholds, sensor faults and event history"
        actions={
          <button type="button" className="button secondary" onClick={ackAll} disabled={busy}>
            <CheckCheck size={12} aria-hidden="true" /> Acknowledge all
          </button>
        }
      />

      {error ? (
        <div style={{ marginBottom: 12 }}>
          <ErrorState title="ALERT FEED DEGRADED" detail={error} onRetry={reload} />
        </div>
      ) : null}

      <div className="kpi-row kpi-row-4">
        <KpiCard label="ACTIVE" value={String(counts.active)} tone={counts.active ? 'warn' : 'good'} />
        <KpiCard label="CRITICAL" value={String(counts.critical)} tone={counts.critical ? 'bad' : 'good'} />
        <KpiCard label="UNACKNOWLEDGED" value={String(counts.unacked)} />
        <KpiCard label="RESOLVED (HISTORY)" value={String(counts.resolved)} />
      </div>

      <section className="panel filter-bar">
        <label className="field field-inline">
          <span>Node</span>
          <select value={nodeFilter} onChange={(e) => setNodeFilter(e.target.value)}>
            <option value="">All</option>
            {nodeCodes.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="field field-inline">
          <span>Severity</span>
          <select value={sevFilter} onChange={(e) => setSevFilter(e.target.value)}>
            <option value="">All</option>
            {ALERT_SEVERITY_ORDER.map((s) => (
              <option key={s} value={s}>
                {RISK_META[riskLevel(s)].label}
              </option>
            ))}
          </select>
        </label>
        <label className="field field-inline">
          <span>Type</span>
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            {ALERT_TYPES.map((t) => (
              <option key={t || 'all'} value={t}>
                {t ? t.replace(/_/g, ' ') : 'All'}
              </option>
            ))}
          </select>
        </label>
      </section>

      <div className="alerts-grid">
        <section className="panel">
          <PanelTitle right={<span className="muted">{active.length}</span>}>ACTIVE ALERTS</PanelTitle>
          {loading ? (
            <SkeletonRows rows={4} />
          ) : active.length ? (
            <div className="alert-stack">
              {active.map((a) => (
                <AlertCard
                  key={a.id}
                  alert={a}
                  expanded={expanded === a.id}
                  onToggle={() => setExpanded(expanded === a.id ? null : a.id)}
                  onAck={() => ack(a.id)}
                  busy={busy}
                />
              ))}
            </div>
          ) : (
            <EmptyState title="NO ACTIVE ALERTS" hint="All monitored thresholds are within limits." />
          )}
        </section>

        <section className="panel">
          <PanelTitle
            right={
              health?.summary ? (
                <span className="muted">
                  {health.summary.online} online · {health.summary.stale} stale ·{' '}
                  {health.summary.offline} offline
                </span>
              ) : null
            }
          >
            NETWORK HEALTH
          </PanelTitle>
          <div className="sensor-list">
            {(health?.nodes ?? []).map((n) => {
              const online = n.status === 'online'
              return (
                <div key={n.node_code} className="sensor-row is-static">
                  <span
                    className="sensor-dot"
                    style={{ background: online ? RISK_META.low.color : RISK_META.unknown.color }}
                    aria-hidden="true"
                  />
                  <span className="sensor-id">
                    {n.node_code}
                    <span className="sensor-kind">{n.node_type ?? 'unknown'}</span>
                  </span>
                  <span className="muted">
                    {isNum(n.minutes_since_last_reading)
                      ? `${n.minutes_since_last_reading.toFixed(1)}m ago`
                      : EM_DASH}
                  </span>
                  <span className={`status-chip ${online ? 'is-ok' : 'is-bad'}`}>
                    {String(n.status).toUpperCase()}
                  </span>
                </div>
              )
            })}
            {!health?.nodes?.length ? <EmptyState title="NO HEALTH DATA" /> : null}
          </div>
        </section>

        <section className="panel">
          <PanelTitle right={<span className="muted">{hist.length}</span>}>EVENT HISTORY</PanelTitle>
          {loading ? (
            <SkeletonRows rows={4} />
          ) : hist.length ? (
            <div className="history-list">
              {hist.slice(0, 40).map((a) => {
                const m = RISK_META[riskLevel(a.severity)]
                return (
                  <div key={`h-${a.id}`} className="history-row">
                    <span className="history-sev" style={{ color: m.color }}>
                      {m.label}
                    </span>
                    <div className="history-body">
                      <b>{a.message}</b>
                      <span className="muted">
                        {a.node_code ?? 'system'} · {a.alert_type.replace(/_/g, ' ')} ·{' '}
                        {timeAgo(a.created_at) ?? a.created_at}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <EmptyState title="NO MATCHING HISTORY" />
          )}
        </section>
      </div>
    </>
  )
}

function AlertCard({
  alert,
  expanded,
  onToggle,
  onAck,
  busy,
}: {
  alert: Alert
  expanded: boolean
  onToggle: () => void
  onAck: () => void
  busy: boolean
}) {
  const m = RISK_META[riskLevel(alert.severity)]
  const reading = describeTrigger(alert)

  return (
    <article className="alert-card" style={{ borderLeftColor: m.color }}>
      <button type="button" className="alert-card-head" onClick={onToggle} aria-expanded={expanded}>
        <span className="alert-card-sev" style={{ color: m.color }}>
          {m.label}
        </span>
        <span className="alert-card-body">
          <b>{alert.message}</b>
          <span className="muted">
            {alert.node_code ?? 'system'} · {fmtIsoHm(alert.created_at)}
            {timeAgo(alert.created_at) ? ` (${timeAgo(alert.created_at)})` : ''}
          </span>
        </span>
        <ChevronDown
          size={14}
          className={expanded ? 'rot' : ''}
          aria-hidden="true"
          style={{ color: m.color, flexShrink: 0 }}
        />
      </button>

      {expanded ? (
        <div className="alert-card-detail">
<MetricValue
              label={reading?.label ?? 'MEASURED VALUE'}
              value={isNum(reading?.value) ? `${round1(reading!.value)} ${reading!.unit}` : EM_DASH}
            />
            <MetricValue
              label="THRESHOLD"
              value={
                isNum(reading?.threshold)
                  ? `${round1(reading!.threshold)} ${reading!.thresholdUnit}`
                  : EM_DASH
              }
              tone="var(--warn)"
            />
            {reading?.detail ? <MetricValue label="HORIZON" value={reading.detail} /> : null}
          <MetricValue label="TYPE" value={alert.alert_type.replace(/_/g, ' ').toUpperCase()} />
          <div className="alert-card-actions">
            {alert.is_scenario_alert ? <DataSourceBadge source="scenario" short /> : null}
            <span className={`status-chip ${alert.acknowledged ? 'is-ok' : 'is-bad'}`}>
              {alert.acknowledged ? 'ACKNOWLEDGED' : 'ACTIVE'}
            </span>
            {!alert.acknowledged ? (
              <button type="button" className="button secondary" onClick={onAck} disabled={busy}>
                Acknowledge
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </article>
  )
}

function round1(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}