'use client'

import { useEffect, useState } from 'react'
import api, { isNotFound } from '@/lib/api'
import { latestPerHorizon } from '@/lib/format'
import type { ForecastPoint, SensorReading } from '@/types'

const POLL_MS = 3000

export interface NodeSeries {
  history: SensorReading[]
  forecasts: ForecastPoint[]
  /** Newest issuance per horizon, keyed by +15/+30/+60. */
  byHorizon: Map<number, ForecastPoint>
  loading: boolean
  error: string | null
  lastGoodAt: string | null
  reload: () => void
}

/**
 * Observed history + forecast for one node, with a single poller.
 *
 * The dashboard and the prediction page each ran their own `setInterval` that
 * fetched the same two endpoints for the same node — duplicated requests,
 * duplicated failure handling, and two independent refresh cadences that could
 * show the chart and the risk panel disagreeing. They now share this hook.
 *
 * Aborts in-flight requests on unmount / node change so a slow response cannot
 * land in state after the operator has already switched nodes.
 */
export function useNodeSeries(nodeCode: string | null | undefined, pollMs = POLL_MS): NodeSeries {
  const [history, setHistory] = useState<SensorReading[]>([])
  const [forecasts, setForecasts] = useState<ForecastPoint[]>([])
  const [settled, setSettled] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastGoodAt, setLastGoodAt] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  // Derived rather than assigned inside the effect body: calling setState
  // synchronously in an effect causes a cascading render.
  const requestKey = nodeCode ? `${nodeCode}:${nonce}` : null
  const loading = requestKey !== null && settled !== requestKey

  useEffect(() => {
    if (!nodeCode) return

    let dead = false
    const key = `${nodeCode}:${nonce}`

    const load = async () => {
      try {
        const [h, f] = await Promise.all([
          api.deviceHistory(nodeCode, 60),
          api.forecasts(nodeCode),
        ])
        if (dead) return
        setHistory(Array.isArray(h) ? h : [])
        setForecasts(Array.isArray(f?.forecasts) ? f.forecasts : [])
        setError(null)
        setLastGoodAt(new Date().toISOString())
      } catch (err) {
        if (dead) return
        if (isNotFound(err)) {
          // The backend has no series for this node yet. That is an empty
          // state ("waiting for telemetry"), not a failure of the feed.
          setHistory([])
          setForecasts([])
          setError(null)
        } else {
          // Keep the previous series on screen; a transient failure should not
          // blank a chart the operator is reading.
          setError((err as Error).message)
        }
      } finally {
        if (!dead) setSettled(key)
      }
    }

    void load()
    const id = setInterval(load, pollMs)
    return () => {
      dead = true
      clearInterval(id)
    }
  }, [nodeCode, pollMs, nonce])

  return {
    // No selected node means no series — derived rather than cleared in the
    // effect body, which would be a synchronous setState.
    history: nodeCode ? history : [],
    forecasts: nodeCode ? forecasts : [],
    byHorizon: latestPerHorizon(nodeCode ? forecasts : []),
    loading,
    error,
    lastGoodAt,
    reload: () => setNonce((n) => n + 1),
  }
}