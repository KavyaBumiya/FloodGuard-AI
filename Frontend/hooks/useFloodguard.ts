'use client'

import { useCallback, useEffect } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import api from '@/lib/api'
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

  const refreshAll = useCallback(async () => {
    try {
      const [nodes, summary, active, impacts, demo] = await Promise.all([
        api.nodes(),
        api.summary(),
        api.activeAlerts(),
        api.impactSummary(),
        api.demoStatus().catch(() => null),
      ])
      dispatch(actions.nodesLoaded(nodes.nodes ?? []))
      if (nodes.simulation_input) {
        dispatch(actions.simulationInputLoaded(nodes.simulation_input))
      }
      dispatch(actions.summaryLoaded(summary))
      dispatch(actions.alertsLoaded(active.alerts ?? []))
      dispatch(actions.impactsLoaded(impacts))
      if (demo) dispatch(actions.modeChanged(demo.mode))
      return true
    } catch (err) {
      dispatch(actions.errorRaised((err as Error).message))
      return false
    }
  }, [dispatch])

  const refreshRisk = useCallback(
    async (nodeCode: string | null) => {
      if (!nodeCode) return
      try {
        const node = await api.node(nodeCode)
        dispatch(actions.riskLoaded(node.current_risk ?? null))
      } catch {
        /* risk is optional detail — a miss must not break the page */
      }
    },
    [dispatch],
  )

  const setConnection = useCallback(
    (state: ConnectionState) => dispatch(actions.connectionChanged(state)),
    [dispatch],
  )

  useEffect(() => {
    let disposed = false

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
    const handleEvent = () => {
      void refreshAll()
    }

    socket.on('connect', handleConnect)
    socket.on('disconnect', handleDisconnect)
    socket.on('connect_error', handleError)
    SOCKET_EVENTS.forEach((e) => socket.on(e, handleEvent))

    // Safety net: if the socket is quiet (or blocked by the browser) the
    // dashboard still refreshes, so the numbers are never silently frozen.
    const timer = setInterval(() => {
      void refreshAll().then((ok) => {
        if (disposed) return
        if (!ok) setConnection('offline')
        else if (socket.connected) setConnection('live')
        else setConnection('degraded')
      })
    }, POLL_MS)

    return () => {
      disposed = true
      SOCKET_EVENTS.forEach((e) => socket.off(e, handleEvent))
      socket.off('connect', handleConnect)
      socket.off('disconnect', handleDisconnect)
      socket.off('connect_error', handleError)
      clearInterval(timer)
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