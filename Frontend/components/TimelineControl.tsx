'use client'

import { useEffect, useRef } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { Pause, Play } from 'lucide-react'
import { actions, RootState } from '@/store'
import { TIME_STEPS, TIME_STEP_LABELS } from '@/lib/constants'
import { cx } from '@/lib/format'
import type { TimeStep } from '@/types'

/**
 * ONE timeline for the whole product.
 *
 * The 2D map, 3D twin, charts, impact figures and risk summary all read
 * `timelineStep` from the store, so they cannot drift onto different steps.
 * Playback drives that same store value, so scrubbing by hand and autoplay can
 * never disagree about which horizon is displayed.
 */
export function TimelineControl({ label = 'Timeline' }: { label?: string }) {
  const dispatch = useDispatch()
  const step = useSelector((s: RootState) => s.floodguard.timelineStep) as TimeStep
  const playing = useSelector((s: RootState) => s.floodguard.timelinePlaying)

  return (
    <div className="timeline" role="group" aria-label={label}>
      <div className="timeline-track" role="radiogroup" aria-label="Forecast horizon">
        {TIME_STEPS.map((t) => {
          const active = step === t
          return (
            <button
              key={t}
              type="button"
              role="radio"
              aria-checked={active}
              className={cx('timeline-btn', active && 'is-active')}
              onClick={() => dispatch(actions.timelineChanged(t as TimeStep))}
              title={t === 0 ? 'Current observed conditions' : `Forecast horizon +${t} minutes`}
            >
              {TIME_STEP_LABELS[t]}
            </button>
          )
        })}
      </div>

      <button
        type="button"
        className={cx('timeline-play', playing && 'is-playing')}
        onClick={() => dispatch(actions.timelineToggled())}
        aria-pressed={playing}
        aria-label={playing ? 'Pause timeline playback' : 'Play timeline'}
      >
        {playing ? (
          <Pause size={11} aria-hidden="true" />
        ) : (
          <Play size={11} aria-hidden="true" />
        )}
        {playing ? 'PAUSE' : 'PLAY'}
      </button>
    </div>
  )
}

/**
 * Autoplay driver. Mount exactly ONCE (in the shell) so a single interval owns
 * the cycle; mounting it per-page previously meant each page kept its own
 * private copy of the current step in a ref, which is what caused the map and
 * the twin to disagree after navigating between them.
 */
export function useTimelineAutoplay(intervalMs = 3500) {
  const dispatch = useDispatch()
  const playing = useSelector((s: RootState) => s.floodguard.timelinePlaying)
  const step = useSelector((s: RootState) => s.floodguard.timelineStep) as TimeStep

  // Latest step for the interval callback, without mutating a ref during render.
  const stepRef = useRef<TimeStep>(step)
  useEffect(() => {
    stepRef.current = step
  }, [step])

  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => {
      const i = TIME_STEPS.indexOf(stepRef.current)
      const next = TIME_STEPS[(i + 1) % TIME_STEPS.length]
      dispatch(actions.timelineChanged(next))
    }, intervalMs)
    return () => clearInterval(id)
  }, [playing, intervalMs, dispatch])
}