'use client'

import { useCallback, useEffect } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import api, { describeApiError, isAuthError, isNotFound } from '@/lib/api'
import { getSocket } from '@/lib/socket'
import { actions, RootState } from '@/store'
import type { ConnectionState } from '@/types'

const SOCKET_EVENTS = [
  'flood.sensor.updated',
  'flood.risk.updated',
  'flood.forecast.updated',
  'flood.layer.updated',
  'flood.mithi.updated',
  'flood.alert.updated',
  'flood.scenario.updated',
  'flood.mode.changed',
] as const

/** Poll cadence for the REST safety net. */
const POLL_MS = 3000

/**
 * Boots the realtime pipeline for the whole app:
 *
 *   initial REST load -> Socket.IO events -> targeted refetch -> Redux
 *
 * Mount it ONCE (the shell) so every page reads one store instead of each page
 * keeping its own copy of timeline / selection / mode state.
 *
 * Connection semantics matter here. A failed socket while REST still succeeds
 * is `degraded`, not `offline` — the numbers on screen are current, and
 * reporting "Offline" would imply stale data that the operator does not have.
 */
export function useFloodguardData() {
  const dispatch = useDispatch()

  const setConnection = useCallback(
    (state: ConnectionState) => dispatch(actions.connectionChanged(state)),
    [dispatch],
  )

  const refreshAll = useCallback(async () => {
    let problem: string | null = null

    // Core: node list + corridor summary. If either fails the dashboard has
    // nothing trustworthy to render, so it reports the failure instead.
    try {
      const [nodes, summary] = await Promise.all([api.nodes(), api.summary()])
      dispatch(actions.nodesLoaded(nodes.nodes ?? []))
      if (nodes.simulation_input) {
        dispatch(actions.simulationInputLoaded(nodes.simulation_input))
      }
      dispatch(actions.summaryLoaded(summary))
    } catch (err) {
      problem = describeApiError(err)
    }

    // Secondary panels: one failing panel must not blank the dashboard. A 404
    // is "no data for this step" — an honest empty state, not an error. A 401/403
    // is never swallowed (see isAuthError below).
    const [active, impacts, demo] = await Promise.allSettled([
      api.activeAlerts(),
      api.impactSummary(),
      api.demoStatus(),
    ])

    if (active.status === 'fulfilled') {
      dispatch(actions.alertsLoaded(active.value.alerts ?? []))
    } else if (!isNotFound(active.reason)) {
      problem ??= isAuthError(active.reason)
        ? describeApiError(active.reason)
        : `Active alerts unavailable: ${describeApiError(active.reason)}`
      dispatch(actions.alertsLoaded([]))
    }

    if (impacts.status === 'fulfilled') {
      dispatch(actions.impactsLoaded(impacts.value))
    } else if (!isNotFound(impacts.reason)) {
      problem ??= `Impact summary unavailable: ${describeApiError(impacts.reason)}`
    }

    if (demo.status === 'fulfilled') {
      dispatch(actions.modeChanged(demo.value.mode))
    }

    if (problem) {
      dispatch(actions.errorRaised(problem))
      // /healthz decides what the operator is told: a reachable backend with a
      // failing endpoint is DEGRADED; an unreachable one is OFFLINE.
      try {
        await api.healthz()
        setConnection('degraded')
      } catch {
        setConnection('offline')
      }
      return false
    }
    return true
  }, [dispatch, setConnection])

  const refreshRisk = useCallback(
    async (nodeCode: string | null) => {
      if (!nodeCode) return
      try {
        const node = await api.node(nodeCode)
        dispatch(actions.riskLoaded(node.current_risk ?? null))
      } catch (err) {
        // Risk is optional detail — a miss must not break the page, but an
        // auth failure is a configuration problem and must not look like
        // "no risk data".
        if (isAuthError(err)) dispatch(actions.errorRaised(describeApiError(err)))
      }
    },
    [dispatch],
  )

  useEffect(() => {
    let disposed = false
    let refreshTimer: ReturnType<typeof setTimeout> | null = null

    const socket = getSocket()

    // Initial load. If REST succeeds but the socket still has not connected
    // shortly after, the console is degraded (data current, realtime down)
    // rather than offline.
    //
    // The socket.connected guard is load-bearing: without it this timer also
    // fired over a *healthy* connection, briefly reporting DEGRADED on a fully
    // live console ~2.5s after every page load.
    void refreshAll().then((ok) => {
      if (disposed || !ok) return
      setTimeout(() => {
        if (disposed) return
        if (!socket.connected) setConnection('degraded')
      }, 2500)
    })

    const handleConnect = () => {
      if (disposed) return
      setConnection('live')
      dispatch(actions.errorRaised(null))
      void refreshAll()
    }
    const handleDisconnect = () => {
      if (disposed) return
      // The socket dropped, but polling continues — degrade, do not go offline.
      setConnection('degraded')
    }
    const handleError = () => {
      if (disposed) return
      setConnection('degraded')
    }
    const scheduleRefresh = () => {
      // One telemetry reading emits sensor and risk events, and a fast-moving
      // test sensor can emit several readings before the first refresh
      // completes. Coalesce those notifications so they cannot create
      // overlapping full-dashboard request storms.
      if (refreshTimer !== null) return
      refreshTimer = setTimeout(() => {
        refreshTimer = null
        if (!disposed) void refreshAll()
      }, 250)
    }

    socket.on('connect', handleConnect)
    socket.on('disconnect', handleDisconnect)
    socket.on('connect_error', handleError)
    SOCKET_EVENTS.forEach((e) => socket.on(e, scheduleRefresh))

    // Safety net: if the socket is quiet (or blocked by the browser) the
    // dashboard still refreshes, so the numbers are never silently frozen.
    const timer = setInterval(() => {
      void refreshAll().then((ok) => {
        if (disposed) return
        // On failure refreshAll has already probed /healthz and chosen
        // DEGRADED (backend up, endpoint failing) or OFFLINE (backend down).
        if (ok) setConnection(socket.connected ? 'live' : 'degraded')
      })
    }, POLL_MS)

    return () => {
      disposed = true
      SOCKET_EVENTS.forEach((e) => socket.off(e, scheduleRefresh))
      socket.off('connect', handleConnect)
      socket.off('disconnect', handleDisconnect)
      socket.off('connect_error', handleError)
      clearInterval(timer)
      if (refreshTimer !== null) clearTimeout(refreshTimer)
      // Deliberately not disconnecting the socket: it is a module-level
      // singleton, so tearing it down here aborts an in-flight WS handshake on
      // every consumer unmount (and on the StrictMode dev double-invoke).
    }
  }, [dispatch, refreshAll, setConnection])

  const state = useSelector((s: RootState) => s.floodguard)
  return { ...state, refreshAll, refreshRisk }
}

export function useAppState() {
  return useSelector((s: RootState) => s.floodguard)
}