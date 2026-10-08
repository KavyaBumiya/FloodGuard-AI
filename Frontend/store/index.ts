import { createSlice, configureStore, PayloadAction } from '@reduxjs/toolkit'
import type {
  Alert,
  ConnectionState,
  CurrentSummary,
  FloodNode,
  ImpactSummaryResponse,
  Mode,
  RiskResult,
  SimulationInput,
  TimeStep,
} from '@/types'

export interface AppState {
  // navigation / selection
  selectedNode: string | null
  timelineStep: TimeStep
  /**
   * Playback lives in the store (not in page-local state) so that PLAY keeps
   * running — and stays in sync — while the operator moves between routes.
   */
  timelinePlaying: boolean
  // mode
  mode: Mode
  connection: ConnectionState
  // live data
  nodes: FloodNode[]
  simulationInput: SimulationInput | null
  summary: CurrentSummary | null
  risk: RiskResult | null
  impacts: ImpactSummaryResponse | null
  alerts: Alert[]
  // scenario
  activeScenarioId: number | null
  scenarioRunning: boolean
  // status
  lastUpdatedAt: string | null
  error: string | null
  loading: boolean
}

const initialState: AppState = {
  selectedNode: null,
  timelineStep: 0,
  timelinePlaying: false,
  mode: 'live',
  connection: 'connecting',
  nodes: [],
  simulationInput: null,
  summary: null,
  risk: null,
  impacts: null,
  alerts: [],
  activeScenarioId: null,
  scenarioRunning: false,
  lastUpdatedAt: null,
  error: null,
  loading: true,
}

const slice = createSlice({
  name: 'floodguard',
  initialState,
  reducers: {
    selectedNodeChanged(state, action: PayloadAction<string | null>) {
      state.selectedNode = action.payload
    },
    timelineChanged(state, action: PayloadAction<TimeStep>) {
      state.timelineStep = action.payload
    },
    timelinePlayingChanged(state, action: PayloadAction<boolean>) {
      state.timelinePlaying = action.payload
    },
    timelineToggled(state) {
      state.timelinePlaying = !state.timelinePlaying
    },
    modeChanged(state, action: PayloadAction<Mode>) {
      state.mode = action.payload
    },
    connectionChanged(state, action: PayloadAction<ConnectionState>) {
      state.connection = action.payload
    },
    nodesLoaded(state, action: PayloadAction<FloodNode[]>) {
      state.nodes = action.payload
      state.loading = false
      state.error = null
      state.lastUpdatedAt = new Date().toISOString()
      const stillThere =
        state.selectedNode != null &&
        action.payload.some((n) => n.node_code === state.selectedNode)
      if (!state.selectedNode || !stillThere) {
        // Prefer the most-recent live physical node so the hero card never
        // lands on a retired device (e.g. FLOOD-ESP32-01) while FLOOD-ESP32-02
        // is streaming. Falls back to nodes[0] when nothing is live.
        const live = action.payload
          .filter(
            (n) =>
              n.node_type === 'physical' &&
              n.latest_reading?.source === 'iot' &&
              n.latest_reading?.timestamp &&
              Number.isFinite(Date.parse(n.latest_reading.timestamp)),
          )
          .sort(
            (a, b) =>
              Date.parse(b.latest_reading!.timestamp) -
              Date.parse(a.latest_reading!.timestamp),
          )
        state.selectedNode =
          live[0]?.node_code ?? action.payload[0]?.node_code ?? null
      }
    },
    simulationInputLoaded(state, action: PayloadAction<SimulationInput>) {
      state.simulationInput = action.payload
    },
    summaryLoaded(state, action: PayloadAction<CurrentSummary>) {
      state.summary = action.payload
    },
    riskLoaded(state, action: PayloadAction<RiskResult | null>) {
      state.risk = action.payload
    },
    impactsLoaded(state, action: PayloadAction<ImpactSummaryResponse>) {
      state.impacts = action.payload
    },
    alertsLoaded(state, action: PayloadAction<Alert[]>) {
      state.alerts = action.payload
    },
    scenarioSelected(state, action: PayloadAction<number | null>) {
      state.activeScenarioId = action.payload
    },
    scenarioRunningChanged(state, action: PayloadAction<boolean>) {
      state.scenarioRunning = action.payload
    },
    errorRaised(state, action: PayloadAction<string | null>) {
      state.error = action.payload
      state.loading = false
    },
    loadingChanged(state, action: PayloadAction<boolean>) {
      state.loading = action.payload
    },
  },
})

export const actions = slice.actions

export const store = configureStore({
  reducer: { floodguard: slice.reducer },
  middleware: (getDefault) => getDefault({ serializableCheck: false }),
})

export type RootState = ReturnType<typeof store.getState>
export type AppDispatch = typeof store.dispatch
