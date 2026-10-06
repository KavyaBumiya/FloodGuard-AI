'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { useDispatch } from 'react-redux'
import { Info, Play, RotateCcw } from 'lucide-react'
import api from '@/lib/api'
import { COLORS, RISK_META, riskLevel } from '@/lib/constants'
import { EM_DASH, fmtArea, fmtNum, isNum } from '@/lib/format'
import { useAppState } from '@/hooks/useFloodguard'
import { actions } from '@/store'
import {
  DataSourceBadge,
  EmptyState,
  MetricValue,
  PageHeader,
  PanelTitle,
} from '@/components/ui/Primitives'
import type { Scenario, ScenarioComparison, ScenarioPreset } from '@/types'

interface Slider {
  key: 'rainfall_mm_hr' | 'initial_level_cm' | 'drainage_capacity_pct' | 'duration_minutes'
  label: string
  min: number
  max: number
  step: number
  unit: string
  scale: [string, string, string]
}

const SLIDERS: Slider[] = [
  {
    key: 'rainfall_mm_hr',
    label: 'Rainfall intensity',
    min: 5,
    max: 150,
    step: 5,
    unit: 'mm/hr',
    scale: ['5 drizzle', '75 heavy', '150 cloudburst'],
  },
  {
    key: 'initial_level_cm',
    label: 'Initial water level',
    min: 60,
    max: 240,
    step: 5,
    unit: 'cm',
    scale: ['60 low', '150 mean', '240 high'],
  },
  {
    key: 'drainage_capacity_pct',
    label: 'Drainage capacity',
    min: 10,
    max: 100,
    step: 5,
    unit: '%',
    scale: ['10 choked', '50 silt', '100 full'],
  },
  {
    key: 'duration_minutes',
    label: 'Duration',
    min: 15,
    max: 120,
    step: 15,
    unit: 'min',
    scale: ['15', '60 standard', '120 extended'],
  },
]

export default function ScenariosPage() {
  const dispatch = useDispatch()
  const { activeScenarioId, scenarioRunning, nodes, summary } = useAppState()

  const [presets, setPresets] = useState<Record<string, ScenarioPreset>>({})
  const [assumptions, setAssumptions] = useState<Record<string, unknown> | null>(null)
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [values, setValues] = useState<{
    rainfall_mm_hr: number
    initial_level_cm: number
    drainage_capacity_pct: number
    duration_minutes: number
  } | null>(null)
  const [active, setActive] = useState<Scenario | null>(null)
  const [comparison, setComparison] = useState<ScenarioComparison | null>(null)
  const [past, setPast] = useState<Scenario[]>([])
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const applyPreset = useCallback((key: string, p: ScenarioPreset) => {
    setSelectedPreset(key)
    setName(p.name)
    setValues({
      rainfall_mm_hr: p.rainfall_mm_hr,
      initial_level_cm: p.initial_level_cm,
      drainage_capacity_pct: p.drainage_capacity_pct,
      duration_minutes: p.duration_minutes,
    })
  }, [])

  const loadPast = useCallback(() => {
    api
      .listScenarios(10)
      .then((r) => setPast(r.scenarios ?? []))
      .catch(() => setPast([]))
  }, [])

  useEffect(() => {
    let dead = false
    api
      .scenarioPresets()
      .then((res) => {
        if (dead) return
        setPresets(res.presets ?? {})
        setAssumptions(res.assumptions ?? null)
        const firstPreset = Object.entries(res.presets ?? {})[0]
        if (firstPreset) applyPreset(firstPreset[0], firstPreset[1])
      })
      .catch(() => setError('Scenario presets unavailable from the backend.'))
    loadPast()
    return () => {
      dead = true
    }
  }, [applyPreset, loadPast])

  const run = async () => {
    if (!values || !selectedPreset || !name.trim()) {
      setError('Load a backend scenario preset before running a simulation.')
      return
    }
    dispatch(actions.scenarioRunningChanged(true))
    setError(null)
    setStatus('Running hydraulic model on the backend…')
    try {
      const res = await api.runScenario({
        name,
        preset: selectedPreset,
        ...values,
      })
      const id = res.scenario_id
      dispatch(actions.scenarioSelected(id))

      // The engine is asynchronous; poll briefly rather than guessing a delay.
      let detail: Record<string, unknown> | null = null
      for (let i = 0; i < 12; i++) {
        await new Promise((r) => setTimeout(r, 900))
        try {
          detail = await api.scenario(id)
          if (detail?.status && detail.status !== 'running') break
        } catch {
          /* keep polling */
        }
      }
      setActive((detail ?? null) as Scenario | null)
      const cmp = await api.compareScenario(id).catch(() => null)
      setComparison(cmp)
      setStatus(cmp ? 'Scenario computed.' : 'Scenario stored, but no comparison was returned.')
      loadPast()
    } catch (err) {
      setError((err as Error).message)
      setStatus(null)
    } finally {
      dispatch(actions.scenarioRunningChanged(false))
    }
  }

  const reset = () => {
    dispatch(actions.scenarioSelected(null))
    setActive(null)
    setComparison(null)
    setStatus('Restored to the live sensor baseline.')
  }

const num = (v: unknown) => (isNum(Number(v)) ? Number(v) : null)

  return (
    <>
      <PageHeader
        eyebrow="WHAT-IF SIMULATION ENGINE"
        title="Scenario Planning & Stress Testing"
        subtitle="Compare a user-defined scenario against the live baseline"
        actions={
          activeScenarioId ? (
            <button type="button" className="button secondary" onClick={reset}>
              <RotateCcw size={12} aria-hidden="true" /> Reset to baseline
            </button>
          ) : (
            <DataSourceBadge source="scenario" />
          )
        }
      />

      {activeScenarioId ? (
        <div className="strip strip-scenario" role="status">
          <b>ACTIVE SCENARIO</b>
          <span>
            Viewing simulated projections. Live hardware telemetry continues on its own channel.
          </span>
          <Link href="/map" className="strip-action">
            Inspect on 2D map
          </Link>
        </div>
      ) : null}

      <div className="sim-grid">
        {/* Controls */}
        <section className="panel">
          <PanelTitle right={<span className="muted">monsoon stress model</span>}>
            SCENARIO CONFIGURATION
          </PanelTitle>

          <div className="preset-row">
            {Object.entries(presets).map(([k, p]) => (
              <button
                key={k}
                type="button"
                className={`preset ${selectedPreset === k ? 'is-active' : ''}`}
                onClick={() => applyPreset(k, p)}
                aria-pressed={selectedPreset === k}
              >
                <b>{p.name}</b>
                <span>
                  {p.rainfall_mm_hr} mm/hr · {p.drainage_capacity_pct}% pump
                </span>
              </button>
            ))}
            {!Object.keys(presets).length ? <span className="muted">Loading presets…</span> : null}
          </div>

          <div className="form-fields">
            <label className="field">
              <span>Scenario name</span>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
            </label>

            {SLIDERS.map((s) => (
              <label key={s.key} className="field">
                <span className="field-head">
                  {s.label}
                  <output>
                    {values ? values[s.key] : '—'}
                    {s.unit}
                  </output>
                </span>
                <input
                  type="range"
                  min={s.min}
                  max={s.max}
                  step={s.step}
                  value={values?.[s.key] ?? ''}
                  disabled={!values}
                  onChange={(e) => {
                    setValues((v) => (v ? { ...v, [s.key]: Number(e.target.value) } : v))
                    setSelectedPreset('custom')
                  }}
                />
                <span className="field-scale">
                  {s.scale.map((x) => (
                    <span key={x}>{x}</span>
                  ))}
                </span>
              </label>
            ))}
          </div>

          <div className="form-actions">
            <button
              type="button"
              className="button primary"
              onClick={run}
              disabled={scenarioRunning || !values || !selectedPreset || !name.trim()}
            >
              {scenarioRunning ? (
                <>
                  <span className="pulse" aria-hidden="true" /> Running…
                </>
              ) : (
                <>
                  <Play size={13} aria-hidden="true" /> Run what-if simulation
                </>
              )}
            </button>
          </div>

          {status ? <p className="status-line">{status}</p> : null}
          {error ? <p className="status-line is-error">{error}</p> : null}
        </section>

        {/* Baseline vs scenario */}
        <section className="panel">
          <PanelTitle right={<DataSourceBadge source="scenario" short />}>
            BASELINE vs SCENARIO
          </PanelTitle>

          {comparison ? (
            <>
              <div className="compare">
                <div className="compare-col">
                  <div className="compare-head">BASELINE</div>
                  <MetricValue
                    label="PEAK LEVEL"
                    value={fmtNum(num(comparison.baseline_peak_level_cm), '', 0)}
                    unit="cm"
                  />
                  <MetricValue
                    label="MEAN LEVEL"
                    value={fmtNum(num(comparison.baseline_mean_level_cm), '', 0)}
                    unit="cm"
                  />
                  <span className="muted">Live sensor baseline</span>
                </div>
                <div className="compare-col is-scenario">
                  <div className="compare-head">SCENARIO</div>
                  <MetricValue
                    label="PEAK LEVEL"
                    value={fmtNum(num(comparison.scenario_peak_level_cm), '', 0)}
                    unit="cm"
                    tone={COLORS.danger}
                  />
                  <MetricValue
                    label="MEAN LEVEL"
                    value={fmtNum(num(comparison.scenario_mean_level_cm), '', 0)}
                    unit="cm"
                    tone={COLORS.warn}
                  />
                  <span className="muted">Simulated</span>
                </div>
              </div>

              <div className="delta-list">
                <div>
                  <span>Peak increase</span>
                  <b style={{ color: COLORS.danger }}>
                    {fmtNum(num(comparison.peak_level_difference_cm), '', 1)} cm
                  </b>
                </div>
                <div>
                  <span>Mean increase</span>
                  <b style={{ color: COLORS.warn }}>
                    {fmtNum(num(comparison.average_level_difference_cm), '', 1)} cm
                  </b>
                </div>
                {num(comparison.scenario_flooded_area_m2) !== null ? (
                  <div>
                    <span>Scenario flooded area</span>
                    <b>{fmtArea(num(comparison.scenario_flooded_area_m2))}</b>
                  </div>
                ) : null}
              </div>

              {comparison.summary ? (
                <p className="plain-language">{String(comparison.summary)}</p>
              ) : null}
            </>
          ) : (
            <EmptyState
              title="NO COMPARISON YET"
              hint="Projections come from the backend after a run — nothing is estimated in the browser."
            />
          )}

          {active ? (
            <p className="muted sim-note">
              Scenario #{active.id} · {active.status}
              {active.error_message ? ` · ${active.error_message}` : ''}
            </p>
          ) : null}
        </section>

        {/* Assumptions + history */}
        <section className="panel">
          <PanelTitle right={<span className="muted">from /api/scenarios</span>}>
            SCENARIO ASSUMPTIONS
          </PanelTitle>
          <div className="assumptions">
            {assumptions
              ? Object.entries(assumptions).map(([k, v]) => (
                  <div key={k}>
                    <b>{k.replace(/_/g, ' ')}: </b>
                    {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                  </div>
                ))
              : (
                  <span className="muted">
                    <Info size={11} aria-hidden="true" /> Loading assumptions…
                  </span>
                )}
          </div>
        </section>

        <section className="panel">
          <PanelTitle right={<span className="muted">{past.length} saved</span>}>
            SAVED SCENARIOS
          </PanelTitle>
          <div className="history-list">
            {past.slice(0, 6).map((sc) => (
              <div key={sc.id} className={`history-row ${sc.id === activeScenarioId ? 'is-active' : ''}`}>
                <div>
                  <b>{sc.name ?? `Scenario #${sc.id}`}</b>
                  <span className="muted">
                    {sc.rainfall_mm_hr} mm/hr · {sc.drainage_capacity_pct}% pump · {sc.status}
                  </span>
                </div>
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => {
                    dispatch(actions.scenarioSelected(sc.id))
                    setActive(sc)
                    setStatus(`Loaded scenario #${sc.id}.`)
                  }}
                >
                  Activate
                </button>
              </div>
            ))}
            {!past.length ? <EmptyState title="NO SAVED SCENARIOS" /> : null}
          </div>
        </section>
      </div>

      <footer className="page-foot">
        Scenario outputs are elevation-threshold simulations from the backend. Highest corridor
        risk right now: {RISK_META[riskLevel(summary?.highest_risk)].label}. Node count:{' '}
        {nodes.length || EM_DASH}.
      </footer>
    </>
  )
}