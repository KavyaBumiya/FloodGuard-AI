import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  EM_DASH,
  fmtArea,
  fmtInt,
  fmtIsoHm,
  fmtNum,
  isNum,
  latestPerHorizon,
  timeAgo,
  trend,
  trendGlyph,
} from './format.ts'

/**
 * Regression tests for the console's core honesty rule.
 *
 * The bug these guard: `(x ?? 0).toFixed(1)` rendered a MISSING measurement as
 * "0.0 mm/hr", which an operator reads as a real reading of zero. Every
 * formatter below must produce an explicit placeholder instead.
 */

test('isNum rejects null, undefined, NaN and non-numbers', () => {
  assert.equal(isNum(0), true)
  assert.equal(isNum(-12.5), true)
  assert.equal(isNum(null), false)
  assert.equal(isNum(undefined), false)
  assert.equal(isNum(Number.NaN), false)
  assert.equal(isNum(Number.POSITIVE_INFINITY), false)
  assert.equal(isNum('12' as unknown), false)
})

test('a missing measurement never renders as zero', () => {
  for (const missing of [null, undefined, Number.NaN]) {
    assert.equal(fmtNum(missing, 'cm'), EM_DASH, 'fmtNum')
    assert.equal(fmtInt(missing), EM_DASH, 'fmtInt')
    assert.equal(fmtArea(missing), EM_DASH, 'fmtArea')
    // The specific regression:
    assert.notEqual(fmtNum(missing, '', 1), '0.0')
  }
})

test('a real zero IS rendered as zero, not hidden', () => {
  // Zero is a legitimate measurement; only *absent* data becomes a dash.
  assert.equal(fmtNum(0, 'cm'), '0 cm')
  assert.equal(fmtNum(0, '', 1), '0.0')
  assert.equal(fmtInt(0), '0')
  assert.equal(fmtArea(0), '0.000 km²')
})

test('formatters apply units and precision consistently', () => {
  assert.equal(fmtNum(156.4, 'cm'), '156 cm')
  assert.equal(fmtNum(156.44, 'cm', 2), '156.44 cm')
  assert.equal(fmtNum(156), '156')
  assert.equal(fmtInt(3.6), '4')
  assert.equal(fmtArea(351981), '0.352 km²')
})

test('timeAgo returns null (not "no data") when there is no timestamp', () => {
  assert.equal(timeAgo(null), null)
  assert.equal(timeAgo(undefined), null)
  assert.equal(timeAgo(''), null)
  assert.equal(timeAgo('not-a-date'), null)
})

test('timeAgo buckets elapsed time', () => {
  const now = Date.now()
  const at = (msAgo: number) => new Date(now - msAgo).toISOString()
  assert.equal(timeAgo(at(2_000)), 'just now')
  assert.equal(timeAgo(at(30_000)), '30s ago')
  assert.equal(timeAgo(at(5 * 60_000)), '5m ago')
  assert.equal(timeAgo(at(3 * 3_600_000)), '3h ago')
  assert.equal(timeAgo(at(2 * 86_400_000)), '2d ago')
})

test('timeAgo never returns a negative age for clock skew', () => {
  const future = new Date(Date.now() + 60_000).toISOString()
  assert.match(timeAgo(future)!, /ago|just now/)
})

test('fmtIsoHm falls back to a placeholder, never "Invalid Date"', () => {
  assert.equal(fmtIsoHm(null), EM_DASH)
  assert.equal(fmtIsoHm('nonsense'), EM_DASH)
  assert.match(fmtIsoHm(new Date().toISOString()), /\d{2}:\d{2}/)
})

test('trend reports unknown rather than guessing when an endpoint is missing', () => {
  assert.deepEqual(trend(null, 100), { dir: 'unknown', delta: null })
  assert.deepEqual(trend(100, undefined), { dir: 'unknown', delta: null })
  assert.deepEqual(trend(100, 100), { dir: 'steady', delta: 0 })
  assert.deepEqual(trend(100, 130), { dir: 'rising', delta: 30 })
  assert.deepEqual(trend(130, 100), { dir: 'falling', delta: -30 })

  // Sub-centimetre movement is steady, not noise-driven "rising". Compare the
  // delta with a tolerance: 100.4 - 100 is 0.4000000000000057 in binary floats.
  const t = trend(100, 100.4)
  assert.equal(t.dir, 'steady')
  assert.ok(Math.abs(t.delta! - 0.4) < 1e-9, `delta was ${t.delta}`)
})

test('trend glyphs never imply direction when unknown', () => {
  assert.equal(trendGlyph('rising'), '▲')
  assert.equal(trendGlyph('falling'), '▼')
  assert.equal(trendGlyph('steady'), '▬')
  assert.equal(trendGlyph('unknown'), '•')
})

test('latestPerHorizon keeps the newest issuance, not the first row', () => {
  // The backend returns a forecast history (one row per made_at). Showing the
  // first row silently displays a stale prediction.
  const rows = [
    { horizon_minutes: 15, made_at: '2026-01-01T10:00:00Z', predicted_level_cm: 100 },
    { horizon_minutes: 15, made_at: '2026-01-01T10:05:00Z', predicted_level_cm: 174 },
    { horizon_minutes: 30, made_at: '2026-01-01T10:00:00Z', predicted_level_cm: 120 },
    { horizon_minutes: 30, made_at: '2026-01-01T10:05:00Z', predicted_level_cm: 183 },
  ]
  const m = latestPerHorizon(rows)
  assert.equal(m.size, 2)
  assert.equal(m.get(15)!.predicted_level_cm, 174)
  assert.equal(m.get(30)!.predicted_level_cm, 183)
})

test('latestPerHorizon tolerates rows with no made_at', () => {
  const m = latestPerHorizon([
    { horizon_minutes: 15, predicted_level_cm: 1 },
    { horizon_minutes: 15, made_at: null, predicted_level_cm: 2 },
  ])
  // '' >= '' is false, so the first row wins deterministically rather than
  // throwing; either way the horizon must resolve to a real number.
  assert.equal(typeof m.get(15)!.predicted_level_cm, 'number')
})

test('latestPerHorizon returns an empty map for no forecasts', () => {
  assert.equal(latestPerHorizon([]).size, 0)
})