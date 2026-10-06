'use client'

/**
 * Socket.IO client for the FLOODGUARD backend.
 *
 * The backend publishes every domain event on the Redis channel which the
 * EventBridge re-emits into the Socket.IO `global_feed` room, so subscribing to
 * that one room is enough for telemetry, forecast, risk, flood, alert, scenario
 * and mode changes.
 */
import { io, Socket } from 'socket.io-client'
import { API_BASE } from './api'

export type RealtimeEventName =
  | 'flood.sensor.updated'
  | 'flood.forecast.updated'
  | 'flood.layer.updated'
  | 'flood.alert.updated'
  | 'flood.mode.changed'
  | 'flood.scenario.updated'
  | 'flood.risk.updated'

export interface RealtimeEvent {
  event: RealtimeEventName
  payload: Record<string, unknown>
  timestamp?: string
  source?: string
}

let socket: Socket | null = null

export function getSocket(): Socket {
  if (!socket) {
    socket = io(API_BASE, {
      path: '/socket.io',
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 500,
      reconnectionDelayMax: 5000,
      timeout: 8000,
    })
  }
  return socket
}
