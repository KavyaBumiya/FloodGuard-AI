import type { Alert, AlertType } from '@/types'

/**
 * Reading an alert's `trigger_data` honestly.
 *
 * `trigger_data` is a JSON blob whose shape depends on the alert type, and so
 * does its unit. The console previously read `current_value` / `value` /
 * `threshold`, which the engine has never emitted, so every alert showed a
 * threshold at best and nothing at all for four of the five types. Worse, the
 * value is not always centimetres - it is cm, cm/5min, minutes, or a count.
 *
 * Everything here reads real keys and returns an explicit unit. Anything
 * genuinely absent comes back null so the UI renders an em dash rather than a
 * fabricated zero.
 */
export interface TriggerReading {
  /** What the measured value IS, e.g. 'CURRENT LEVEL'. */
  label: string
  value: number | null
  unit: string
  threshold: number | null
  thresholdUnit: string
  /** Extra context worth showing, e.g. the horizon for a forecast alert. */
  detail: string | null
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

const BY_TYPE: Record<
  AlertType,
  (t: Record<string, unknown>) => Omit<TriggerReading, 'label' | 'unit'> & {
    label: string
    unit: string
  }
> = {
  threshold: (t) => ({
    label: 'CURRENT LEVEL',
    unit: 'cm',
    value: num(t.level_cm),
    threshold: num(t.threshold_cm),
    thresholdUnit: 'cm',
    detail: null,
  }),

  rapid_rise: (t) => ({
    label: 'RISE RATE',
    unit: 'cm/5min',
    value: num(t.rise_rate_cm_per_5min),
    // The engine puts the 5 cm/5min rule only in the message string, so the
    // threshold is genuinely unavailable as data. Report it as unknown rather
    // than restating the message.
    threshold: null,
    thresholdUnit: 'cm/5min',
    detail: null,
  }),

  predicted_risk: (t) => ({
    label: 'PREDICTED LEVEL',
    unit: 'cm',
    value: num(t.predicted_level_cm),
    threshold: null,
    thresholdUnit: 'cm',
    detail: num(t.horizon_minutes) !== null ? `at +${t.horizon_minutes} min` : null,
  }),

  communication_failure: (t) => ({
    label: 'SILENT FOR',
    unit: 'min',
    value: num(t.minutes_since_last_reading),
    threshold: null,
    thresholdUnit: 'min',
    detail: null,
  }),

  sensor_fault: (t) => ({
    label: 'REJECTED READINGS',
    unit: 'in last hour',
    value: num(t.rejected_count),
    threshold: null,
    thresholdUnit: '',
    detail: null,
  }),
}

/** Describe an alert's measured trigger, or null when the shape is unknown. */
export function describeTrigger(alert: Alert): TriggerReading | null {
  const build = BY_TYPE[alert.alert_type]
  const data = (alert.trigger_data ?? {}) as Record<string, unknown>
  if (!build) return null
  try {
    return build(data)
  } catch {
    return null
  }
}

/** "187.4 cm", or null when there is no real measurement to show. */
export function formatTrigger(
  reading: TriggerReading | null,
  opts: { withThreshold?: boolean } = {},
): string | null {
  if (!reading || reading.value === null) return null
  const head = `${round(reading.value)} ${reading.unit}`.trim()
  if (!opts.withThreshold || reading.threshold === null) return head
  return `${head} / ${round(reading.threshold)} ${reading.thresholdUnit}`.trim()
}

function round(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}