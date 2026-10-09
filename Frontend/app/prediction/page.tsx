'use client'

import { useEffect, useMemo, useState } from 'react'
import { useDispatch } from 'react-redux'
import api, { describeApiError, isAuthError, isNotFound } from '@/lib/api'
import { FORECAST_HORIZONS, RISK_META, hasRecentLiveIoTReading, riskLevel } from '@/lib/constants'
import { EM_DASH, fmtNum, isNum, latestPerHorizon, timeAgo, trend, trendWord } from '@/lib/format'
import { useAppState } from '@/hooks/useFloodguard'
import { useNodeSeries } from '@/hooks/useNodeSeries'
import { actions } from '@/store'
import { ModelMetricsChart, WaterLevelChart } from '@/components/WaterLevelChart'
import {
  DataSourceBadge,
  EmptyState,
  ErrorState,
  PageHeader,
  PanelTitle,
  RiskBadge,
  SkeletonBlock,
  SkeletonRows,
} from '@/components/ui/Primitives'
import type { ModelInfo, RiskResult } from '@/types'

export default function PredictionPage() {
  const dispatch = useDispatch()
  const { nodes, selectedNode, risk } = useAppState()
  const [modelInfo, setModelInfo] = useState<ModelInfo | null>(null)

  const selected = useMemo(
    () => nodes.find((n) => n.node_code === selectedNode) ?? nodes[0],
    [nodes, selectedNode],
  )

  // One shared poller for observed history + forecast (was duplicated here and
  // on the dashboard, with two independent refresh cadences).
  const series = useNodeSeries(selected?.node_code)
  const hasLiveReading =
    selected?.node_type === 'physical'
      ? hasRecentLiveIoTReading(selected)
      : Boolean(selected?.latest_reading)

  useEffect(() => {
    api
      .modelInfo()
      .then(setModelInfo)
      .catch((err: Error) => {
        setModelInfo(null)
        // Missing metadata is normal (em dash / fallback copy); 401/403 is not.
        if (isAuthError(err)) dispatch(actions.errorRaised(describeApiError(err)))
      })
  }, [dispatch])

  // Risk detail for the selected node. The node list already carries
  // `current_risk`, so this only refetches when the selection changes.
  useEffect(() => {
    if (!selected) return
    let dead = false
    api
      .node(selected.node_code)
      .then((detail) => {
        if (!dead) dispatch(actions.riskLoaded((detail.current_risk as RiskResult) ?? null))
      })
      .catch((err: Error) => {
        /* risk is optional detail — the page stays usable without it */
        if (!dead && isAuthError(err)) dispatch(actions.errorRaised(describeApiError(err)))
        if (!dead && isNotFound(err)) dispatch(actions.riskLoaded(null))
      })
    return () => {
      dead = true
    }
  }, [selected, dispatch])

  const models = modelInfo?.models as
    | Record<string, { mae?: number; baseline_mae?: number; [k: string]: unknown }>
    | undefined

  const metricRows = useMemo(
    () =>
      ['15min', '30min', '60min']
        .filter((h) => models?.[h])
        .map((h) => {
          const m = models![h]
          // `?? 0` here rendered "MAE 0.00", which reads as a perfect model.
          // The backend omits these keys when no model is registered, so the
          // honest answer is null and the UI shows an em dash.
          const mae = typeof m.mae === 'number' && Number.isFinite(m.mae) ? m.mae : null
          const baseline =
            typeof m.baseline_mae === 'number' && Number.isFinite(m.baseline_mae)
              ? m.baseline_mae
              : null
          return { label: `+${h.replace('min', '')}m`, mae, baseline }
        }),
    [models],
  )

  const components = useMemo(() => {
    const raw = risk?.components
    if (!raw) return []
    if (Array.isArray(raw)) return raw

    return Object.entries(raw)
      .filter(([, contribution]) => typeof contribution === 'number' && Number.isFinite(contribution))
      .map(([name, contribution]) => ({
        name,
        value: contribution,
        normalized: Math.abs(contribution),
        weight: 1,
        contribution,
        band: 'backend score',
      }))
  }, [risk?.components])
  const maxContrib = Math.max(0.0001, ...components.map((c) => Math.abs(c.contribution)))
  const byHorizon = latestPerHorizon(series.forecasts)

  const current = hasLiveReading ? series.history[series.history.length - 1]?.water_level_cm ?? null : null
  const nextFc = byHorizon.get(15)?.predicted_level_cm ?? null
  const tr = trend(current, nextFc)
  const forecastStatus = selected?.forecast_status ?? (byHorizon.size ? 'ok' : 'waiting_for_iot')
  const nextForecast = byHorizon.get(15)
  const predictionSource =
    selected?.node_type === 'physical' ? 'real IoT input' : 'simulated Mithi telemetry'

  if (!selected) {
    return (
      <>
        <PageHeader eyebrow="AI PREDICTION" title="Water Level Forecast" />
        <div className="panel">
          <EmptyState title="NO NODE SELECTED" hint="Waiting for sensor telemetry from the backend." />
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader
        eyebrow="AI FLOOD PREDICTION"
        title="Projected water levels · next 60 minutes"
        subtitle={`Node ${selected.node_code} · ${selected.name}`}
        actions={
          <label className="node-select">
            <span className="sr-only">Select sensor node</span>
            <select
              value={selected.node_code}
              onChange={(e) => dispatch(actions.selectedNodeChanged(e.target.value))}
            >
              {nodes.map((n) => (
                <option key={n.node_code} value={n.node_code}>
                  {n.node_code} ({n.node_type})
                </option>
              ))}
            </select>
          </label>
        }
      />

      {/* Horizon ladder: current -> +15 -> +30 -> +60 */}
      <div className="horizon-row">
        <div className="horizon-cell is-now">
          <div className="horizon-k">
            CURRENT
            {selected.node_type === 'physical' && !hasLiveReading ? (
              <span className="muted">WAITING FOR SENSOR</span>
            ) : (
              <DataSourceBadge source={selected.node_type === 'physical' ? 'iot' : 'simulated'} short />
            )}
          </div>
          <div className="horizon-v">{hasLiveReading ? fmtNum(current, '', 0) : 'WAITING'}</div>
          <div className="horizon-u">{hasLiveReading ? 'cm observed' : 'NO LIVE IOT DATA'}</div>
        </div>
        {FORECAST_HORIZONS.map((h) => {
          const f = byHorizon.get(h)
          const v = f?.predicted_level_cm
          // Physical probe thresholds are inverted (crossing = falling below);
          // virtual nodes carry ascending Mithi-stage thresholds.
          const over =
            isNum(v) &&
            isNum(selected.warning_threshold_cm) &&
            (selected.node_type === 'physical'
              ? v < selected.warning_threshold_cm
              : v > selected.warning_threshold_cm)
          return (
            <div key={h} className="horizon-cell">
              <div className="horizon-k">
                +{h} MIN
                <DataSourceBadge source="forecast" short />
              </div>
              <div className="horizon-v" style={over ? { color: 'var(--warn)' } : undefined}>
                {isNum(v) ? v.toFixed(0) : EM_DASH}
              </div>
              <div className="horizon-u">
                {isNum(f?.lower_bound_cm) && isNum(f?.upper_bound_cm)
                  ? `${f!.lower_bound_cm!.toFixed(0)}–${f!.upper_bound_cm!.toFixed(0)} cm bounds`
                  : 'no bounds supplied'}
              </div>
            </div>
          )
        })}
      </div>

      <section className="panel prediction-callout">
        <PanelTitle
          right={
            <DataSourceBadge
              source={selected.node_type === 'physical' ? 'iot' : 'simulated'}
            />
          }
        >
          CLEAR PREDICTION
        </PanelTitle>
        {forecastStatus === 'ok' && nextForecast ? (
          <div className="plain-language">
            Based on <b>{predictionSource}</b>, the model predicts water level will be{' '}
            <b>{nextForecast.predicted_level_cm.toFixed(0)} cm</b> in 15 minutes
            {isNum(nextForecast.lower_bound_cm) && isNum(nextForecast.upper_bound_cm)
              ? ` (likely range ${nextForecast.lower_bound_cm.toFixed(0)}–${nextForecast.upper_bound_cm.toFixed(0)} cm)`
              : ''}
            . The level is expected to <b>{trendWord(tr.dir).toLowerCase()}</b>
            {isNum(tr.delta) ? ` by ${Math.abs(tr.delta).toFixed(0)} cm` : ''}.
          </div>
        ) : (
          <EmptyState
            title={
              forecastStatus === 'insufficient_live_iot_data'
                ? 'NOT ENOUGH IoT HISTORY'
                : 'PREDICTION NOT READY'
            }
            hint={
              selected.node_type === 'virtual'
                ? 'The simulated node is visible on the map, but it needs generated telemetry before a forecast can be calculated.'
                : 'Keep the IoT telemetry running until the backend has enough readings for the model.'
            }
          />
        )}
      </section>

      <div className="pred-grid">
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
            OBSERVED + FORECAST WATER LEVEL
          </PanelTitle>
          <div className="panel-sub">
            <DataSourceBadge
              source={selected.node_type === 'physical' ? 'iot' : 'simulated'}
            />
            <DataSourceBadge source="forecast" />
            <span className="muted">solid = observed · dashed = predicted</span>
          </div>
          {series.error && !series.history.length ? (
            <ErrorState title="TELEMETRY UNAVAILABLE" detail={series.error} onRetry={series.reload} />
          ) : series.loading && !series.history.length ? (
            <SkeletonBlock height={300} label="Loading series" />
          ) : !hasLiveReading ? (
            <EmptyState title="WAITING FOR SENSOR" hint="No recent real ESP32 HTTPS reading is available." />
          ) : (
            <WaterLevelChart
              history={series.history}
              forecasts={series.forecasts}
              warningThreshold={selected.warning_threshold_cm}
              height={318}
            />
          )}
        </section>

        <section className="panel">
          <PanelTitle right={risk ? <RiskBadge level={risk.risk_level} /> : null}>
            CURRENT RISK ASSESSMENT
          </PanelTitle>
          {risk ? (
            <>
              <div className="score-block">
                <div
                  className="score-ring"
                  style={{
                    ['--score' as string]: String(Math.round(risk.risk_score * 100)),
                    borderColor: RISK_META[riskLevel(risk.risk_level)].color,
                    color: RISK_META[riskLevel(risk.risk_level)].color,
                  }}
                  aria-label={`Risk score ${risk.risk_score.toFixed(2)} of 1`}
                >
                  {Math.round(risk.risk_score * 100)}
                </div>
                <div className="score-meta">
                  <b style={{ color: RISK_META[riskLevel(risk.risk_level)].color }}>
                    {RISK_META[riskLevel(risk.risk_level)].label}
                  </b>
                  <span className="muted">driver: {risk.primary_driver ?? EM_DASH}</span>
                  <span className="muted">
                    {risk.model ?? 'rule-based'} v{risk.model_version ?? EM_DASH}
                  </span>
                </div>
              </div>

              <div className="contrib-list">
                <div className="contrib-head">RISK CONTRIBUTORS</div>
                {components.map((c) => (
                  <div key={c.name} className="contrib">
                    <span className="contrib-k">
                      {c.name.replace(/_/g, ' ')}
                      <span className="muted"> ({c.band})</span>
                    </span>
                    <span className="contrib-track">
                      <span
                        className="contrib-fill"
                        style={{ width: `${(Math.abs(c.contribution) / maxContrib) * 100}%` }}
                      />
                    </span>
                    <span className="contrib-v">{c.contribution.toFixed(2)}</span>
                  </div>
                ))}
                {!components.length ? (
                  <span className="muted">No component breakdown supplied by the model.</span>
                ) : null}
              </div>

              <div className="trend-line">
                <span className="muted">TREND</span>
                <b>{trendWord(tr.dir)}</b>
                {isNum(tr.delta) ? (
                  <span className={tr.delta > 0 ? 'is-up' : 'is-down'}>
                    {tr.delta > 0 ? '+' : ''}
                    {tr.delta.toFixed(0)} cm to +15m
                  </span>
                ) : null}
              </div>
            </>
          ) : (
            <EmptyState title="NO RISK ASSESSMENT" hint="The model has not scored this node yet." />
          )}
        </section>

        <section className="panel">
          <PanelTitle right={<span className="muted">vs persistence baseline</span>}>
            MODEL PERFORMANCE
          </PanelTitle>
          {metricRows.length ? (
            <ModelMetricsChart rows={metricRows} />
          ) : (
            <SkeletonRows rows={3} />
          )}
          <div className="detail-list">
            {metricRows.map((r) => (
              <div key={r.label}>
                <span>{r.label}</span>
                <span>
MAE{' '}
                <b>{r.mae === null ? '—' : r.mae.toFixed(2)}</b> vs baseline{' '}
                <span className="muted">
                  {r.baseline === null ? '—' : r.baseline.toFixed(2)}
                </span>
                </span>
              </div>
            ))}
          </div>
          <p className="caveat">
            {String(
              models?.['15min']?.training_data_description ??
                'Prototype trained on synthetic water levels — not operationally validated.',
            )}
          </p>
        </section>

        <section className="panel">
          <PanelTitle right={<span className="muted">deterministic, not an LLM</span>}>
            PLAIN-LANGUAGE SUMMARY
          </PanelTitle>
          <div className="plain-language">
            {risk ? (
              <>
                Risk is <b>{risk.risk_level}</b> (score {risk.risk_score.toFixed(2)}), driven mainly by{' '}
                <b>{risk.primary_driver ?? 'overall conditions'}</b>. Water level is{' '}
                <b>{trendWord(tr.dir).toLowerCase()}</b>
                {isNum(tr.delta)
                  ? ` by ${Math.abs(tr.delta).toFixed(0)} cm`
                  : ''}{' '}
                over the next 15 minutes. This is a rule-based prototype assessment, not a validated
                hydraulic result.
              </>
            ) : (
              'No risk assessment available for this node yet.'
            )}
          </div>
          {risk?.assumptions?.length ? (
            <div className="assumptions">
              <div className="contrib-head">MODEL ASSUMPTIONS</div>
              {risk.assumptions.map((a) => (
                <div key={a}>• {a}</div>
              ))}
            </div>
          ) : null}
        </section>
      </div>
    </>
  )
}