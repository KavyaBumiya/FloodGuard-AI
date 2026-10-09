import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  CONNECTION_META,
  RISK_META,
  SOURCE_META,
  alertSeverityRank,
  nodeSource,
  riskClass,
  riskColor,
  riskLevel,
  riskLevelForWaterLevel,
  riskRank,
} from './constants.ts'
import type { RiskLevel, SourceType } from '@/types'

/**
 * These guard the semantics the console depends on: risk must never be
 * conflated with connectivity, simulated data must never be labelled live, and
 * "unknown" must not be rendered as "offline".
 */

test('riskLevel normalises aliases and defaults to unknown', () => {
  assert.equal(riskLevel('severe'), 'severe')
  assert.equal(riskLevel('SEVERE'), 'severe')
  assert.equal(riskLevel('critical'), 'severe', 'critical is a synonym for severe')
  assert.equal(riskLevel('high'), 'high')
  assert.equal(riskLevel('moderate'), 'moderate')
  assert.equal(riskLevel('warning'), 'moderate')
  assert.equal(riskLevel('low'), 'low')
  assert.equal(riskLevel('nonsense'), 'unknown')
  assert.equal(riskLevel(null), 'unknown')
  assert.equal(riskLevel(undefined), 'unknown')
})

test('an unassessed node is NOT styled as offline', () => {
  // Regression: unknown used to map to the offline class, which told an
  // operator a node had failed when it had simply not been scored.
  assert.equal(riskLevel(undefined), 'unknown')
  assert.equal(riskClass(undefined), 'risk-unknown')
  assert.notEqual(riskClass(undefined), 'risk-offline')
  assert.equal(RISK_META.unknown.label, 'NO DATA')
})

test('every risk class has a distinct colour and tint', () => {
  const levels: RiskLevel[] = ['low', 'moderate', 'high', 'severe', 'unknown']
  const colors = levels.map((l) => RISK_META[l].color)
  assert.equal(new Set(colors).size, levels.length, 'risk colours must be distinct')
  for (const l of levels) {
    assert.ok(RISK_META[l].tint.length > 0, `${l} needs a tint`)
  }
})

test('riskRank orders worst-first and sinks unknown to the bottom', () => {
  assert.ok(riskRank('severe') > riskRank('high'))
  assert.ok(riskRank('high') > riskRank('moderate'))
  assert.ok(riskRank('moderate') > riskRank('low'))
  assert.ok(riskRank('low') > riskRank('unknown'))

  const sorted = ['low', 'unknown', 'severe', 'moderate', 'high']
    .sort((a, b) => riskRank(b) - riskRank(a))
  assert.deepEqual(sorted, ['severe', 'high', 'moderate', 'low', 'unknown'])
})

test('riskColor is driven by the same table as the badge', () => {
  for (const l of ['low', 'moderate', 'high', 'severe', 'critical'] as const) {
    assert.equal(riskColor(l), RISK_META[riskLevel(l)].color)
  }
})

test('riskLevelForWaterLevel matches the IoT device bands', () => {
  assert.equal(riskLevelForWaterLevel(0), 'low')
  assert.equal(riskLevelForWaterLevel(19.99), 'low')
  assert.equal(riskLevelForWaterLevel(20), 'moderate')
  assert.equal(riskLevelForWaterLevel(21.99), 'moderate')
  assert.equal(riskLevelForWaterLevel(22), 'high')
  assert.equal(riskLevelForWaterLevel(24.99), 'high')
  assert.equal(riskLevelForWaterLevel(25), 'severe')
  assert.equal(riskLevelForWaterLevel(30), 'severe')
  assert.equal(riskLevelForWaterLevel(null), 'unknown')
})

test('simulated data is never labelled live', () => {
  assert.equal(SOURCE_META.iot.label, 'REAL IoT INPUT')
  assert.equal(SOURCE_META.simulated.label, 'SIMULATED')
  assert.notEqual(SOURCE_META.simulated.label, SOURCE_META.iot.label)
  // Distinct colours too, so the two cannot be confused at a glance.
  assert.notEqual(SOURCE_META.simulated.color, SOURCE_META.iot.color)
  assert.notEqual(SOURCE_META.forecast.label, SOURCE_META.iot.label)
})

test('every SourceType has a badge definition', () => {
  const all: SourceType[] = [
    'iot', 'forecast', 'historical', 'simulated',
    'scenario', 'replay', 'terrain', 'osm',
  ]
  for (const s of all) {
    const meta = SOURCE_META[s]
    assert.ok(meta, `${s} missing from SOURCE_META`)
    assert.ok(meta.label.length > 0)
    assert.ok(meta.short.length > 0, `${s} needs a short form`)
  }
})

test('nodeSource maps physical to hardware and everything else to simulated', () => {
  assert.equal(nodeSource('physical'), 'iot')
  assert.equal(nodeSource('virtual'), 'simulated')
  assert.equal(nodeSource(null), 'simulated')
  assert.equal(nodeSource(undefined), 'simulated')
})

test('degraded is distinct from offline', () => {
  // A working REST feed with a dead socket is NOT offline: the numbers on
  // screen are current, and saying "Offline" would imply stale data.
  assert.ok(CONNECTION_META.degraded)
  assert.notEqual(CONNECTION_META.degraded.label, CONNECTION_META.offline.label)
  assert.notEqual(CONNECTION_META.degraded.color, CONNECTION_META.offline.color)
  assert.equal(CONNECTION_META.live.label, 'LIVE')
  for (const key of ['live', 'degraded', 'connecting', 'reconnecting', 'offline'] as const) {
    assert.ok(CONNECTION_META[key]?.detail, `${key} needs an operator-facing detail`)
  }
})

test('alert severities sort most urgent first', () => {
  const order = ['critical', 'high', 'warning', 'information']
    .sort((a, b) => alertSeverityRank(a) - alertSeverityRank(b))
  assert.deepEqual(order, ['critical', 'high', 'warning', 'information'])
  assert.ok(alertSeverityRank('unknown_type') > alertSeverityRank('information'))
})