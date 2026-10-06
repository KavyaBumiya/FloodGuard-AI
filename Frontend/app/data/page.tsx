'use client'

import { useEffect, useState } from 'react'
import api from '@/lib/api'
import type { StudyAreaResponse } from '@/lib/api'
import { SOURCE_META } from '@/lib/constants'
import { DataSourceBadge, ErrorState, PageHeader, PanelTitle, SkeletonRows } from '@/components/ui/Primitives'
import type { DataSource, SourceType } from '@/types'

const CATEGORY_SOURCE: Record<string, SourceType> = {
  live_hardware: 'iot',
  historical_reference: 'historical',
  forecast: 'forecast',
  terrain_osm: 'terrain',
  terrain: 'terrain',
  simulated: 'simulated',
  scenario: 'scenario',
  replay: 'replay',
}

const LIMITATIONS: [string, string][] = [
  ['DEM resolution', 'Surface elevation at ~30 m cannot resolve bunds, kerbs or drain thresholds.'],
  ['Rainfall spatial resolution', 'IMD 0.25° is a ~25 km grid — never street-level, never a live reading.'],
  ['Building heights estimated', 'Where OSM has no height, levels × floor height is an estimate, flagged as such.'],
  ['Live sensor network', 'No virtual sensor network is active. The live application waits for the registered physical IoT input.'],
  ['Risk weights are prototype', 'The 35/30/20/15 weighting is an assumption set, not a calibrated model.'],
  ['Flood simulation is simplified', 'Elevation-threshold inundation with connectivity masking — not a hydraulic solver.'],
  ['ML is prototype-grade', 'Trained on synthetic water levels; performance is not operationally validated.'],
  ['Not an emergency warning system', 'Built for a hackathon demonstration. Do not use for life-safety decisions.'],
]

export default function DataPage() {
  const [sources, setSources] = useState<DataSource[]>([])
  const [assumptions, setAssumptions] = useState<Record<string, unknown> | null>(null)
  const [honesty, setHonesty] = useState('')
  const [studyArea, setStudyArea] = useState<StudyAreaResponse | null>(null)
  const [modelInfo, setModelInfo] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let dead = false
    api
      .dataSources()
      .then((r) => {
        if (dead) return
        setSources(Array.isArray(r?.data_sources) ? r.data_sources : [])
      })
      .catch((err: Error) => !dead && setError(`Data sources unavailable: ${err.message}`))

    api.assumptions().then((a) => !dead && setAssumptions(a)).catch(() => {})
    api
      .honestyNote()
      .then((r) => !dead && setHonesty(r?.note ?? ''))
      .catch(() => {})
    api.studyArea().then((s) => !dead && setStudyArea(s)).catch(() => {})
    api
      .modelInfo()
      .then((m) => !dead && setModelInfo(m as Record<string, unknown>))
      .catch(() => {})
      .finally(() => !dead && setLoading(false))
    return () => {
      dead = true
    }
  }, [])

  return (
    <>
      <PageHeader
        eyebrow="TRANSPARENCY"
        title="About this data"
        subtitle="Every dataset is labelled by class. Simulated data is never presented as live."
      />

      {error ? (
        <div style={{ marginBottom: 12 }}>
          <ErrorState title="PROVENANCE UNAVAILABLE" detail={error} />
        </div>
      ) : null}

      <section className="panel">
        <PanelTitle right={<span className="muted">every dataset labelled by class</span>}>
          DATA SOURCES & PROVENANCE
        </PanelTitle>
        {loading ? (
          <SkeletonRows rows={4} />
        ) : (
          <div className="source-cards">
            {sources.map((s) => (
              <article key={s.name} className="source-card">
                <div className="source-card-head">
                  <b>{s.name}</b>
                  <DataSourceBadge source={CATEGORY_SOURCE[s.category] ?? 'terrain'} short />
                </div>
                <div className="muted source-card-meta">
                  {s.provider} · {s.resolution}
                </div>
                <p className="source-card-limit">{s.limitation}</p>
              </article>
            ))}
            {!sources.length ? <p className="muted">No data sources registered by the backend.</p> : null}
          </div>
        )}
      </section>

      <div className="about-grid">
        <section className="panel">
          <PanelTitle>HOW EACH LABEL IS USED</PanelTitle>
          <div className="detail-list">
            {(Object.keys(SOURCE_META) as SourceType[]).map((k) => (
              <div key={k}>
                <DataSourceBadge source={k} />
                <span className="muted">{k}</span>
              </div>
            ))}
          </div>
          <p className="caveat">
            Simulated data is never shown as live. Forecast is never shown as observed. Model output
            is always labelled a predicted flood extent, never an official flood zone.
          </p>
        </section>

        <section className="panel">
          <PanelTitle>MODEL</PanelTitle>
          <div className="detail-list">
            {modelInfo?.models ? (
              Object.entries(
                modelInfo.models as Record<string, { mae?: number; baseline_mae?: number }>,
              ).map(([k, v]) => (
                <div key={k}>
                  <span>{k}</span>
                  <span>
                    MAE{' '}
                    <b>
                      {typeof v.mae === 'number' && Number.isFinite(v.mae)
                        ? v.mae.toFixed(2)
                        : '—'}
                    </b>{' '}
                    <span className="muted">
                      vs baseline{' '}
                      {typeof v.baseline_mae === 'number' && Number.isFinite(v.baseline_mae)
                        ? v.baseline_mae.toFixed(2)
                        : '—'}
                    </span>
                  </span>
                </div>
              ))
            ) : (
              <span className="muted">Model metadata unavailable.</span>
            )}
          </div>
        </section>

        <section className="panel">
          <PanelTitle>MODEL ASSUMPTIONS</PanelTitle>
          <div className="assumptions">
            {assumptions ? (
              Object.entries(assumptions).map(([k, v]) => (
                <div key={k}>
                  <b>{k.replace(/_/g, ' ')}: </b>
                  {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                </div>
              ))
            ) : (
              <span className="muted">Loading assumptions…</span>
            )}
          </div>
        </section>

        <section className="panel">
          <PanelTitle>STUDY AREA</PanelTitle>
          <div className="detail-list">
            {studyArea ? (
              Object.entries(studyArea).map(([k, v]) => (
                <div key={k}>
                  <span>{k.replace(/_/g, ' ')}</span>
                  <b>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</b>
                </div>
              ))
            ) : (
              <span className="muted">Study area metadata unavailable.</span>
            )}
          </div>
        </section>

        <section className="panel about-limit">
          <PanelTitle>KNOWN LIMITATIONS</PanelTitle>
          <ul className="limit-list">
            {LIMITATIONS.map(([t, d]) => (
              <li key={t}>
                <b>{t}</b>
                <span>{d}</span>
              </li>
            ))}
          </ul>
        </section>

        {honesty ? (
          <section className="panel">
            <PanelTitle>HONESTY NOTE</PanelTitle>
            <p className="plain-language">{honesty}</p>
          </section>
        ) : null}
      </div>
    </>
  )
}