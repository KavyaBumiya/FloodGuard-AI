'use client'

import Link from 'next/link'
import { useMemo } from 'react'
import { useDispatch } from 'react-redux'
import { ArrowRight, Droplets, Radio } from 'lucide-react'
import {
  FORECAST_HORIZONS,
  RISK_META,
  hasRecentLiveIoTReading,
  nodeSource,
  riskLevel,
  riskRank,
} from '@/lib/constants'
import {
  EM_DASH,
  fmtArea,
  fmtIsoHm,
  fmtNum,
  isNum,
  timeAgo,
  trend,
  trendGlyph,
  trendWord,
} from '@/lib/format'
import { useAppState } from '@/hooks/useFloodguard'
import { useNodeSeries } from '@/hooks/useNodeSeries'
import { actions } from '@/store'
import { WaterLevelChart } from '@/components/WaterLevelChart'
import {
  DataSourceBadge,
  EmptyState,
  ErrorState,
  KpiCard,
  MetricValue,
  PageHeader,
  PanelTitle,
  RiskBadge,
  SkeletonKpi,
  SkeletonRows,
} from '@/components/ui/Primitives'
import type { FloodNode } from '@/types'

export default function DashboardPage() {
  const dispatch = useDispatch()
  const {
    nodes,
    summary,
    alerts,
    impacts,
    selectedNode,
    timelineStep,
    loading,
    lastUpdatedAt,
    simulationInput,
  } = useAppState()

  const selected: FloodNode | undefined = useMemo(
    () => nodes.find((n) => n.node_code === selectedNode) ?? nodes[0],
    [nodes, selectedNode],
  )

  const series = useNodeSeries(selected?.node_code)

  const reading = selected?.latest_reading
  const hasLiveReading = selected?.node_type === 'virtual' || hasRecentLiveIoTReading(selected)
  const level = reading?.water_level_cm ?? null
  const rain = reading?.rainfall_mm_hr ?? null
  const threshold = selected?.warning_threshold_cm ?? null

  const fc15 = series.byHorizon.get(15)?.predicted_level_cm ?? null
  const fc60 = series.byHorizon.get(60)?.predicted_level_cm ?? null
  const tr = trend(level, fc15)

  const stepImpact = impacts?.steps.find((s) => s.time_step_minutes === timelineStep)
  const riskMeta = RISK_META[riskLevel(selected?.current_risk?.risk_level)]

  const ranked = useMemo(
    () => [...nodes].sort((a, b) => riskRank(b.current_risk?.risk_level) - riskRank(a.current_risk?.risk_level)),
    [nodes],
  )

  // `/api/alerts/active` already returns the active set; the response has no
  // `is_active` field, so filtering on it would drop every alert.
  const activeAlerts = useMemo(
    () => (alerts ?? []).filter((a) => !a.acknowledged),
    [alerts],
  )

  if (loading && !nodes.length) {
    return (
      <>
        <PageHeader eyebrow="COMMAND CENTRE" title="Situation Overview" />
        <div className="kpi-row">
          {Array.from({ length: 5 }).map((_, i) => (
            <SkeletonKpi key={i} />
          ))}
        </div>
        <div className="dash-grid">
          <div className="panel">
            <PanelTitle>WATER LEVEL</PanelTitle>
            <SkeletonRows rows={6} />
          </div>
          <div className="panel">
            <PanelTitle>SENSOR NETWORK</PanelTitle>
            <SkeletonRows rows={6} />
          </div>
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader
        eyebrow="COMMAND CENTRE · MITHI RIVER BKC → KURLA"
        title="Current Situation"
        actions={
          <div className="hero-source">
            {selected?.node_type === 'physical' && !hasLiveReading ? (
              <span className="src-badge">WAITING FOR SENSOR</span>
            ) : (
              <DataSourceBadge
                source={nodeSource(selected?.node_type)}
                note={selected?.node_type === 'physical' ? 'Recent ESP32 MQTT telemetry' : 'Simulated virtual node'}
              />
            )}
            <span className="muted">
              {selected?.node_code ?? '—'}
              {timeAgo(reading?.timestamp) ? ` · ${timeAgo(reading?.timestamp)}` : ''}
            </span>
          </div>
        }
      />

      <section className="panel" style={{ marginBottom: 16 }}>
        <PanelTitle>REAL IoT INPUT → MITHI RIVER DIGITAL TWIN</PanelTitle>
        <div className="rail-metrics">
          <MetricValue
            label="VADODARA DEMO WATER LEVEL"
            value={simulationInput?.status === 'live' ? fmtNum(simulationInput.water_level_cm, '', 1) : 'WAITING'}
            unit={simulationInput?.status === 'live' ? 'cm' : undefined}
          />
          <MetricValue
            label="MITHI SIMULATED LEVEL"
            value={simulationInput?.status === 'live' ? fmtNum(simulationInput.mithi_water_level_cm, '', 1) : 'WAITING'}
            unit={simulationInput?.status === 'live' ? 'cm' : undefined}
            tone="var(--accent)"
          />
          <MetricValue
            label="MITHI STATUS"
            value={simulationInput?.status === 'live' ? 'CONNECTED' : 'WAITING FOR REAL IoT INPUT'}
          />
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          DEMO CALIBRATION · Physical IoT Input → Mithi Simulation. This is not a Vadodara-to-Mumbai hydraulic relationship.
        </div>
      </section>

      {/* ── HERO: what is happening right now ───────────────────────────── */}
      <section className="hero" style={{ borderColor: riskMeta.color }}>
        <div className="hero-main">
          <div className="hero-eyebrow">CURRENT SITUATION</div>
          <div className="hero-risk" style={{ color: riskMeta.color }}>
            {riskMeta.label}
            {selected?.current_risk?.risk_score != null ? (
              <span className="hero-score">score {selected.current_risk.risk_score.toFixed(2)}</span>
            ) : null}
          </div>
          <div className="hero-node">
            {selected?.node_code ?? EM_DASH}
            <span className="muted"> · {selected?.name ?? 'no node reporting'}</span>
          </div>
        </div>

        <div className="hero-metrics">
          <MetricValue label="WATER LEVEL" value={hasLiveReading ? fmtNum(level, '', 0) : 'WAITING'} unit={hasLiveReading ? 'cm' : undefined} tone={riskMeta.color} />
          <MetricValue label="RAINFALL" value={hasLiveReading ? fmtNum(rain, '', 1) : 'WAITING'} unit={hasLiveReading ? 'mm/hr' : undefined} tone="var(--accent)" />
          <MetricValue
            label="WARNING THRESHOLD"
            value={fmtNum(threshold, '', 0)}
            unit="cm"
            tone="var(--warn)"
          />
        </div>

        <div className="hero-forecast">
          <div className="hero-eyebrow">
            FORECAST <DataSourceBadge source="forecast" short />
          </div>
          <div className="hero-fc-row">
            {FORECAST_HORIZONS.map((h) => {
              const v = series.byHorizon.get(h)?.predicted_level_cm
              return (
                <div key={h} className="hero-fc">
                  <span className="hero-fc-k">+{h}m</span>
                  <span className="hero-fc-v">{isNum(v) ? v.toFixed(0) : EM_DASH}</span>
                </div>
              )
            })}
          </div>
          <div className={`hero-trend is-${tr.dir}`}>
            <span aria-hidden="true">{trendGlyph(tr.dir)}</span>
            {trendWord(tr.dir)}
            {isNum(tr.delta) ? ` ${tr.delta > 0 ? '+' : ''}${tr.delta.toFixed(0)} cm to +15m` : ''}
          </div>
        </div>
      </section>

      {/* ── KPI strip ────────────────────────────────────────────────────── */}
      <div className="kpi-row">
        <KpiCard
          label="ACTIVE SENSORS"
          value={String(summary?.active_sensors ?? 0)}
          unit={`/ ${summary?.total_sensors ?? nodes.length}`}
          tone={(summary?.offline_stale_sensors ?? 0) > 0 ? 'warn' : 'good'}
          hint={`${summary?.offline_stale_sensors ?? 0} offline / stale`}
        />
        <KpiCard
          label="CURRENT WATER LEVEL"
          value={fmtNum(level, '', 0)}
          unit="cm"
          tone="accent"
          hint={isNum(tr.delta) ? `${trendGlyph(tr.dir)} ${tr.delta > 0 ? '+' : ''}${tr.delta.toFixed(0)} cm → +15m` : 'no trend available'}
          badge={
            selected?.node_type === 'physical' && !hasLiveReading ? (
              <span className="muted">WAITING FOR SENSOR</span>
            ) : (
              <DataSourceBadge source={nodeSource(selected?.node_type)} short />
            )
          }
        />
        <KpiCard
          label="RAINFALL"
          value={fmtNum(rain, '', 1)}
          unit="mm/hr"
          hint={isNum(summary?.current_rainfall_mm_hr) ? 'corridor mean' : 'no corridor reading'}
        />
        <KpiCard
          label="HIGHEST RISK"
          value={summary?.highest_risk ? RISK_META[riskLevel(summary.highest_risk)].label : EM_DASH}
          tone={summary?.highest_risk === 'severe' ? 'bad' : 'default'}
          hint={summary?.highest_risk_node ?? '—'}
          accent={summary?.highest_risk ? <RiskBadge level={summary.highest_risk} size="sm" /> : null}
        />
        <KpiCard
          label="FORECAST PEAK"
          value={fmtNum(fc60, '', 0)}
          unit="cm"
          tone={isNum(fc60) && isNum(threshold) && fc60 > threshold ? 'bad' : 'default'}
          hint="+60 min"
          badge={<DataSourceBadge source="forecast" short />}
        />
      </div>

      {/* ── Chart + network ──────────────────────────────────────────────── */}
      <div className="dash-grid">
        <section className="panel">
          <PanelTitle
            right={
              series.error ? (
                <span className="panel-warn">stale — {series.error}</span>
              ) : timeAgo(series.lastGoodAt) ? (
                <span className="muted">updated {timeAgo(series.lastGoodAt)}</span>
              ) : null
            }
          >
            WATER LEVEL · {selected?.node_code ?? EM_DASH}
          </PanelTitle>
          <div className="panel-sub">
            {selected?.node_type === 'physical' && !hasLiveReading ? (
              <span className="muted">NO LIVE IOT DATA</span>
            ) : (
              <DataSourceBadge source={nodeSource(selected?.node_type)} />
            )}
            <span className="muted">
              {selected?.node_type === 'physical' ? 'ESP32 telemetry' : 'virtual sensor — simulated'}
            </span>
            {selected?.node_type === 'physical' ? (
              <Radio size={11} style={{ color: 'var(--accent)' }} aria-label="Hardware node" />
            ) : null}
          </div>
          {series.error && !series.history.length ? (
            <ErrorState
              title="TELEMETRY UNAVAILABLE"
              detail={series.error}
              lastGood={series.lastGoodAt ? fmtIsoHm(series.lastGoodAt) : null}
              onRetry={series.reload}
            />
          ) : selected?.node_type === 'physical' && !hasLiveReading ? (
            <EmptyState title="NO LIVE IOT DATA" hint="Waiting for a recent ESP32 MQTT reading." />
          ) : (
            <WaterLevelChart
              history={series.history}
              forecasts={series.forecasts}
              warningThreshold={threshold}
              criticalThreshold={null}
              height={288}
            />
          )}
        </section>

        <section className="panel">
          <PanelTitle
            right={
              <span className="muted">
                {summary?.active_sensors ?? nodes.length}/{summary?.total_sensors ?? nodes.length} active
              </span>
            }
          >
            SENSOR NETWORK
          </PanelTitle>
          <div className="sensor-list" aria-label="Sensor nodes">
            {nodes.map((n) => {
              const rm = RISK_META[riskLevel(n.current_risk?.risk_level)]
              const isSel = n.node_code === selected?.node_code
              return (
                <button
                  type="button"
                  key={n.node_code}
                  className={`sensor-row ${isSel ? 'is-selected' : ''}`}
                  onClick={() => dispatch(actions.selectedNodeChanged(n.node_code))}
                  aria-pressed={isSel}
                >
                  <span className="sensor-dot" style={{ background: rm.color }} aria-hidden="true" />
                  <span className="sensor-id">
                    {n.node_code}
                    <span className="sensor-kind">
                      {n.node_type === 'physical' ? 'ESP32' : 'VIRTUAL'}
                    </span>
                  </span>
                  <span className="sensor-vals">
                    <b>{n.node_type === 'physical' && !hasRecentLiveIoTReading(n) ? 'WAITING' : fmtNum(n.latest_reading?.water_level_cm, '', 0)}</b>
                    <span className="muted">
                    {n.node_type === 'physical' && !hasRecentLiveIoTReading(n) ? 'NO LIVE IOT DATA' : `${fmtNum(n.latest_reading?.rainfall_mm_hr, '', 1)} mm/hr`}
                    </span>
                  </span>
                  <RiskBadge level={n.current_risk?.risk_level} size="sm" />
                </button>
              )
            })}
            {!nodes.length ? <EmptyState title="NO NODES REPORTING" /> : null}
          </div>
        </section>
      </div>

      {/* ── Impact + alerts + risk drivers ───────────────────────────────── */}
      <div className="dash-grid dash-grid-3">
        <section className="panel">
          <PanelTitle
            right={
              <Link href="/map" className="text-link">
                Open map <ArrowRight size={10} aria-hidden="true" />
              </Link>
            }
          >
            PREDICTED IMPACT · {timelineStep === 0 ? 'NOW' : `+${timelineStep} MIN`}
          </PanelTitle>
          {stepImpact ? (
            <div className="impact-grid">
              <MetricValue label="FLOODED AREA" value={fmtArea(stepImpact.flooded_area_m2)} />
              <MetricValue label="BUILDINGS" value={String(stepImpact.affected_building_count)} />
              <MetricValue label="ROADS" value={String(stepImpact.affected_road_count)} />
              <MetricValue label="MAX DEPTH" value={fmtNum(stepImpact.max_depth_m, '', 2)} unit="m" />
            </div>
          ) : (
            <EmptyState title="NO LAYER FOR THIS STEP" hint="Run a simulation to generate flood extent." />
          )}
          <div className="panel-foot">
            <DataSourceBadge source="terrain" short />
            <span className="muted">Model estimate, not an official flood zone.</span>
          </div>
        </section>

        <section className="panel">
          <PanelTitle
            right={
              <Link href="/alerts" className="text-link">
                View all <ArrowRight size={10} aria-hidden="true" />
              </Link>
            }
          >
            ACTIVE ALERTS
          </PanelTitle>
          <div className="alert-list">
            {activeAlerts.slice(0, 4).map((a) => {
              const am = RISK_META[riskLevel(a.severity)]
              return (
                <div key={a.id} className="alert-row" style={{ borderLeftColor: am.color }}>
                  <span className="alert-row-sev" style={{ color: am.color }}>
                    {am.label}
                  </span>
                  <span className="alert-row-msg">{a.message}</span>
                  <span className="muted">{timeAgo(a.created_at) ?? ''}</span>
                </div>
              )
            })}
            {!activeAlerts.length ? <EmptyState title="NO ACTIVE ALERTS" /> : null}
          </div>
        </section>

        <section className="panel">
          <PanelTitle
            right={
              <Link href="/prediction" className="text-link">
                Explain <ArrowRight size={10} aria-hidden="true" />
              </Link>
            }
          >
            RISK DRIVERS
          </PanelTitle>
          <div className="risk-list">
            {ranked.slice(0, 5).map((n, i) => {
              const rm = RISK_META[riskLevel(n.current_risk?.risk_level)]
              return (
                <div key={n.node_code} className="risk-item">
                  <span className="rank">{i + 1}</span>
                  <span className="risk-item-body">
                    <b>{n.node_code}</b>
                    <span className="muted">
                      {n.current_risk?.primary_driver ?? 'no driver reported'}
                    </span>
                  </span>
                  <span className="risk-item-score" style={{ color: rm.color }}>
                    {isNum(n.current_risk?.risk_score)
                      ? n.current_risk!.risk_score.toFixed(2)
                      : EM_DASH}
                  </span>
                </div>
              )
            })}
            {!ranked.length ? <EmptyState title="NO RISK ASSESSMENT" /> : null}
          </div>
        </section>
      </div>

      <footer className="page-foot">
        <Droplets size={11} aria-hidden="true" />
        <span>
          Corridor data last refreshed {timeAgo(lastUpdatedAt) ?? 'never'}. Flood extent is a
          model estimate — not an official flood zone, and not an emergency warning system.
        </span>
      </footer>
    </>
  )
}