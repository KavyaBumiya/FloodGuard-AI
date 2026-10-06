'use client'

import dynamic from 'next/dynamic'
import { useCallback, useState } from 'react'
import { useDispatch } from 'react-redux'
import { RotateCw } from 'lucide-react'
import { RISK_META, hasRecentLiveIoTReading, nodeSource, riskLevel } from '@/lib/constants'
import { fmtArea, fmtNum, timeAgo } from '@/lib/format'
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
import type { TimeStep } from '@/types'

// three.js / WebGL must never run on the server.
const DigitalTwin = dynamic(() => import('@/components/DigitalTwin'), {
  ssr: false,
  loading: () => (
    <div className="map-loading">
      <span className="pulse" aria-hidden="true" />
      <span>Compiling WebGL shaders and building the corridor mesh…</span>
    </div>
  ),
})

const CAMERAS = [
  { key: 'overview', label: 'Overview' },
  { key: 'bkc', label: 'BKC' },
  { key: 'iot', label: 'IoT' },
  { key: 'kurla', label: 'Kurla' },
  { key: 'river_pov', label: 'River' },
] as const

export default function DigitalTwinPage() {
  const dispatch = useDispatch()
  const { nodes, selectedNode, timelineStep, impacts, simulationInput } = useAppState()
  const [cameraPreset, setCameraPreset] = useState<string>('overview')
  const [isAutoRotate, setIsAutoRotate] = useState(false)

  // Stable identity: this is a dependency of the scene-setup effect, so an
  // inline arrow would tear down and rebuild the entire WebGL scene on every
  // parent render.
  const handleSelectNode = useCallback(
    (code: string) => dispatch(actions.selectedNodeChanged(code)),
    [dispatch],
  )

  const selected = nodes.find((n) => n.node_code === selectedNode) ?? nodes[0]
  const selectedHasLiveReading = selected?.node_type === 'virtual' || hasRecentLiveIoTReading(selected)
  const stepImpact = impacts?.steps.find((s) => s.time_step_minutes === timelineStep)
  const riskMeta = RISK_META[riskLevel(selected?.current_risk?.risk_level)]

  return (
    <>
      <PageHeader
        eyebrow="3D DIGITAL TWIN"
        title="Mithi River Corridor Twin"
        subtitle="Mumbai simulation · driven by real Vadodara IoT demo input"
      />

      <div className="twin-page">
        <div className="twin-stage">
          <DigitalTwin
            timelineStep={timelineStep}
            selectedNode={selectedNode}
            onSelectNode={handleSelectNode}
            cameraPreset={cameraPreset}
            isAutoRotate={isAutoRotate}
          />

          {/* The scene renders its own top-left HUD (renderer + camera name), so
              the provenance badge lives with the scenario readout instead of
              double-printing over it. */}
          <div className="twin-overlay twin-overlay-tr">
            <button
              type="button"
              className="twin-cams"
              onClick={() => setIsAutoRotate((v) => !v)}
              aria-pressed={isAutoRotate}
            >
              <RotateCw size={10} aria-hidden="true" /> Rotate
            </button>
            <div className="twin-cam-row">
              {CAMERAS.map((c) => (
                <button
                  key={c.key}
                  type="button"
                  className={`twin-cam ${cameraPreset === c.key ? 'is-active' : ''}`}
                  onClick={() => setCameraPreset(c.key)}
                  aria-pressed={cameraPreset === c.key}
                >
                  {c.label}
                </button>
              ))}
            </div>
          </div>

          <div className="twin-overlay twin-overlay-br">
            <div className="twin-scenario">
              <div className="muted">REAL IoT DEMO INPUT: Vadodara Physical Sensor</div>
              <div className="muted">
                {simulationInput?.status === 'live'
                  ? 'MITHI RIVER DIGITAL TWIN · DRIVEN BY REAL IoT DEMO INPUT'
                  : 'WAITING FOR REAL IoT INPUT'}
              </div>
              <div className="float-head">
                {timelineStep === 0 ? 'OBSERVED' : `PROJECTED +${timelineStep}m`}
                {timelineStep === 0 && selected?.node_type === 'physical' && !selectedHasLiveReading ? (
                  <span className="muted">WAITING FOR SENSOR</span>
                ) : (
                  <DataSourceBadge
                    source={timelineStep === 0 ? nodeSource(selected?.node_type) : 'forecast'}
                    short
                  />
                )}
              </div>
              <MetricValue
                label="WATER LEVEL"
                value={selectedHasLiveReading ? fmtNum(selected?.latest_reading?.water_level_cm, '', 0) : 'WAITING'}
                unit={selectedHasLiveReading ? 'cm' : undefined}
                tone={riskMeta.color}
              />
              <MetricValue label="FLOODED AREA" value={fmtArea(stepImpact?.flooded_area_m2)} />
              <MetricValue label="BUILDINGS" value={fmtNum(stepImpact?.affected_building_count, '', 0)} />
            </div>
          </div>

          <div className="twin-overlay twin-overlay-bl">
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
            </div>
          </div>
        </div>

        <aside className="twin-rail" aria-label="Node detail">
          <section className="panel">
            <PanelTitle right={selected ? <RiskBadge level={selected.current_risk?.risk_level} size="sm" /> : null}>
              BEACON
            </PanelTitle>
            {selected ? (
              <>
                <div className="rail-id">
                  <b>{selected.node_code}</b>
                  {selected.node_type === 'physical' && !selectedHasLiveReading ? (
                    <span className="muted">WAITING FOR SENSOR</span>
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
                    label="ELEVATION"
                    value={fmtNum(selected.reference_elevation_m, '', 1)}
                    unit="m ASL"
                  />
                  <MetricValue
                    label="WARNING"
                    value={fmtNum(selected.warning_threshold_cm, '', 0)}
                    unit="cm"
                    tone="var(--warn)"
                  />
                </div>
                <div className="muted rail-fc-note">
                  {selectedHasLiveReading && timeAgo(selected.latest_reading?.timestamp)
                    ? `Last reading ${timeAgo(selected.latest_reading?.timestamp)}`
                    : 'No reading timestamp available'}
                </div>
              </>
            ) : (
              <EmptyState title="NO BEACON SELECTED" />
            )}
          </section>

          <section className="panel twin-spec">
            <PanelTitle>TWIN SPECIFICATION</PanelTitle>
            <p>
              Buildings are <b>procedurally placed placeholders</b>, not surveyed OSM footprints,
              and the terrain is a schematic corridor rather than the Copernicus DEM. The water
              surface is elevation-threshold inundation computed by the backend.
            </p>
            <p className="muted">
              Per-step surface heights follow the backend forecast where one exists and fall back
              to an illustrative curve otherwise — such frames are labelled SIMULATED in the
              viewer.
            </p>
          </section>

          </aside>
      </div>
    </>
  )
}