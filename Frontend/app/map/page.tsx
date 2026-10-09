'use client'

import dynamic from 'next/dynamic'
import Link from 'next/link'
import { useState } from 'react'
import { useDispatch } from 'react-redux'
import { Box, Layers, Pause, Play } from 'lucide-react'
import {
  RISK_META,
  hasRecentLiveIoTReading,
  nodeSource,
  riskLevel,
  riskLevelForWaterLevel,
} from '@/lib/constants'
import { EM_DASH, fmtArea, fmtNum, timeAgo } from '@/lib/format'
import { useAppState } from '@/hooks/useFloodguard'
import { actions } from '@/store'
import {
  DataSourceBadge,
  EmptyState,
  MetricValue,
  PageHeader,
  PanelTitle,
  RiskBadge,
} from '@/components/ui/Primitives'
import { forecastArray } from '@/types'
import type { TimeStep } from '@/types'

// Leaflet touches `window` at import time; keep it off the server entirely.
const FloodMap = dynamic(() => import('@/components/FloodMap'), {
  ssr: false,
  loading: () => (
    <div className="map-loading">
      <span className="pulse" aria-hidden="true" />
      <span>Initialising Leaflet GIS engine…</span>
    </div>
  ),
})

const LAYERS = [
  { key: 'sensors', label: 'Sensor nodes' },
  { key: 'waterways', label: 'Mithi River' },
  { key: 'inundation', label: 'Flood extent' },
  { key: 'buildings', label: 'Buildings' },
  { key: 'roads', label: 'Roads' },
] as const

type LayerKey = (typeof LAYERS)[number]['key']

export default function MapPage() {
  const dispatch = useDispatch()
  const { nodes, selectedNode, timelineStep, timelinePlaying, impacts, simulationInput } = useAppState()

  const [activeLayers, setActiveLayers] = useState<Record<LayerKey, boolean>>({
    sensors: true,
    waterways: true,
    inundation: true,
    buildings: true,
    roads: false,
  })
  const [opacity, setOpacity] = useState(0.65)

  const stepImpact = impacts?.steps.find((s) => s.time_step_minutes === timelineStep)
  const selected = nodes.find((n) => n.node_code === selectedNode) ?? nodes[0]
  const selectedHasLiveReading = selected?.node_type === 'virtual' || hasRecentLiveIoTReading(selected)

  const toggle = (k: LayerKey) => setActiveLayers((s) => ({ ...s, [k]: !s[k] }))

  return (
    <>
      <PageHeader
        eyebrow="GEOSPATIAL SITUATION ROOM"
        title="2D Flood Risk & Inundation Map"
        subtitle="SIMULATED FLOOD EXTENT · Mithi River Mumbai · driven by live IoT input"
      />

      <div className="map-page">
        {/* Map dominates; controls float over it. */}
        <div className="map-stage">
          <FloodMap activeLayers={activeLayers} opacity={opacity} />

          <div className="map-float map-float-tl">
            <div className="float-head">LIVE IoT INPUT</div>
            <div className="muted" style={{ marginBottom: 8 }}>
              Registered physical sensors → Mithi River Digital Twin
            </div>
            <div className="float-head">TIMELINE</div>
            <div className="float-row">
              {([0, 15, 30, 60] as TimeStep[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`float-chip ${timelineStep === t ? 'is-active' : ''}`}
                  onClick={() => dispatch(actions.timelineChanged(t))}
                  aria-pressed={timelineStep === t}
                >
                  {t === 0 ? 'NOW' : `+${t}`}
                </button>
              ))}
              <button
                type="button"
                className={`float-chip ${timelinePlaying ? 'is-active' : ''}`}
                onClick={() => dispatch(actions.timelineToggled())}
                aria-pressed={timelinePlaying}
                aria-label={timelinePlaying ? 'Pause timeline' : 'Play timeline'}
              >
                {timelinePlaying ? <Pause size={10} /> : <Play size={10} />}
                {timelinePlaying ? 'PAUSE' : 'PLAY'}
              </button>
              <span className="map-forecast-chip">FORECAST</span>
            </div>
            <div className="float-foot">
              {simulationInput?.status !== 'live' ? (
                <span className="muted">WAITING FOR REAL IoT INPUT</span>
              ) : (
                <DataSourceBadge source={timelineStep === 0 ? 'simulated' : 'forecast'} short />
              )}
              <span className="muted">
                {timelineStep === 0 ? 'observed' : `projected +${timelineStep}m`}
              </span>
            </div>
          </div>

          <div className="map-float map-float-bl">
            <div className="float-head">
              <Layers size={11} aria-hidden="true" /> LAYERS
            </div>
            <div className="float-toggles">
              {LAYERS.map((l) => (
                <label key={l.key} className="float-toggle">
                  <input
                    type="checkbox"
                    checked={activeLayers[l.key]}
                    onChange={() => toggle(l.key)}
                  />
                  <span>{l.label}</span>
                </label>
              ))}
            </div>
            <label className="float-slider">
              <span>
                Extent opacity <b>{Math.round(opacity * 100)}%</b>
              </span>
              <input
                type="range"
                min={0.2}
                max={1}
                step={0.05}
                value={opacity}
                onChange={(e) => setOpacity(parseFloat(e.target.value))}
              />
            </label>
          </div>

          <div className="map-float map-float-br">
            <div className="float-head">RISK CLASS</div>
            <ul className="legend-list">
              {(['low', 'moderate', 'high', 'severe'] as const).map((k) => (
                <li key={k}>
                  <span
                    className="legend-swatch"
                    style={{ background: RISK_META[k].tint, borderColor: RISK_META[k].color }}
                    aria-hidden="true"
                  />
                  {RISK_META[k].label}
                </li>
              ))}
              <li>
                <span className="legend-line" style={{ background: 'var(--accent)' }} aria-hidden="true" />
                River channel
              </li>
              <li>
                <span className="legend-line" style={{ background: '#334155', border: '1px solid #64748b' }} aria-hidden="true" />
                Building footprint
              </li>
            </ul>
          </div>
        </div>

        {/* Right rail: station inspector + impact. */}
        <aside className="map-rail" aria-label="Station detail and impact">
          <section className="panel">
            <PanelTitle
              right={selected ? <RiskBadge level={selected.current_risk?.risk_level} size="sm" /> : null}
            >
              STATION INSPECTOR
            </PanelTitle>
            {selected ? (
              <>
                <div className="rail-id">
                  <b>{selected.node_code}</b>
                  {selected.node_type === 'physical' && !selectedHasLiveReading ? (
                    <span className="muted">WAITING</span>
                  ) : (
                    <DataSourceBadge source={nodeSource(selected.node_type)} short />
                  )}
                </div>
                <div className="muted rail-name">{selected.name}</div>

                <div className="rail-metrics">
                  <MetricValue
                    label="WATER LEVEL"
                    value={selectedHasLiveReading ? fmtNum(selected.latest_reading?.water_level_cm, '', 0) : 'WAITING'}
                    unit={selectedHasLiveReading ? 'cm' : undefined}
                    tone="var(--accent)"
                  />
                  <MetricValue
                    label="RAINFALL"
                    value={selectedHasLiveReading ? fmtNum(selected.latest_reading?.rainfall_mm_hr, '', 1) : 'WAITING'}
                    unit={selectedHasLiveReading ? 'mm/hr' : undefined}
                  />
                  <MetricValue
                    label="WARNING"
                    value={fmtNum(selected.warning_threshold_cm, '', 0)}
                    unit="cm"
                    tone="var(--warn)"
                  />
                  <MetricValue
                    label="ELEVATION"
                    value={fmtNum(selected.reference_elevation_m, '', 1)}
                    unit="m ASL"
                  />
                </div>

                <div className="rail-fc">
                  <div className="contrib-head">FORECAST</div>
                  <div className="rail-fc-row">
                    {[15, 30, 60].map((h) => {
                      const f = forecastArray(selected.forecasts).find((x) => x.horizon_minutes === h)
                      return (
                        <div key={h}>
                          <span className="muted">+{h}m</span>
                          <b>{f ? `${f.predicted_level_cm.toFixed(0)} cm` : EM_DASH}</b>
                        </div>
                      )
                    })}
                  </div>
                  <div className="muted rail-fc-note">
                    {selectedHasLiveReading && timeAgo(selected.latest_reading?.timestamp)
                      ? `Last reading ${timeAgo(selected.latest_reading?.timestamp)}`
                      : 'No reading timestamp available'}
                  </div>
                </div>

                <Link href="/digital-twin" className="button primary rail-cta">
                  <Box size={12} aria-hidden="true" /> View in 3D Twin
                </Link>
              </>
            ) : (
              <EmptyState title="NO STATION SELECTED" hint="Click a node on the map." />
            )}
            <p className="muted" style={{ marginTop: 12 }}>
              {simulationInput?.status === 'live'
                ? 'SIMULATED MITHI CONDITION · driven by REAL IoT INPUT'
                : 'WAITING FOR REAL IoT INPUT · no virtual sensor fallback is active'}
            </p>
          </section>

          <section className="panel">
            <PanelTitle>
              IMPACT · {timelineStep === 0 ? 'NOW' : `+${timelineStep} MIN`}
            </PanelTitle>
            {stepImpact ? (
              <>
                <div className="rail-metrics">
                  <MetricValue label="FLOODED AREA" value={fmtArea(stepImpact.flooded_area_m2)} />
                  <MetricValue label="BUILDINGS" value={String(stepImpact.affected_building_count)} tone="var(--warn)" />
                  <MetricValue label="ROADS" value={String(stepImpact.affected_road_count)} tone="var(--danger)" />
                  <MetricValue label="MAX DEPTH" value={fmtNum(stepImpact.max_depth_m, '', 2)} unit="m" />
                </div>
                <div className="panel-foot">
                  <DataSourceBadge source="terrain" short />
                  <span className="muted">Model estimate, not an official flood zone.</span>
                </div>
              </>
            ) : (
              <EmptyState title="NO LAYER FOR THIS STEP" />
            )}
          </section>

          <section className="panel">
            <PanelTitle>SELECTED NODE</PanelTitle>
            <div className="rail-nodes">
              {nodes.map((n) => (
                <button
                  key={n.node_code}
                  type="button"
                  className={`rail-node ${n.node_code === selected?.node_code ? 'is-active' : ''}`}
                  onClick={() => dispatch(actions.selectedNodeChanged(n.node_code))}
                  aria-pressed={n.node_code === selected?.node_code}
                >
                  <span
                    className="legend-swatch"
                    style={{
                      background: RISK_META[
                        n.node_type === 'physical'
                          ? riskLevelForWaterLevel(n.latest_reading?.water_level_cm)
                          : riskLevel(n.current_risk?.risk_level)
                      ].tint,
                      borderColor: RISK_META[
                        n.node_type === 'physical'
                          ? riskLevelForWaterLevel(n.latest_reading?.water_level_cm)
                          : riskLevel(n.current_risk?.risk_level)
                      ].color,
                    }}
                    aria-hidden="true"
                  />
                  <span>{n.node_code}</span>
                  <b>{n.node_type === 'physical' && !hasRecentLiveIoTReading(n) ? 'WAITING' : fmtNum(n.latest_reading?.water_level_cm, '', 0)}</b>
                </button>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </>
  )
}