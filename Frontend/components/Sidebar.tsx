'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  Bell,
  BrainCircuit,
  Box,
  Database,
  LayoutDashboard,
  Map as MapIcon,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import { NAV_ITEMS } from '@/lib/constants'
import { cx } from '@/lib/format'

const ICONS = {
  '/': LayoutDashboard,
  '/prediction': BrainCircuit,
  '/map': MapIcon,
  '/digital-twin': Box,
  '/scenarios': SlidersHorizontal,
  '/alerts': Bell,
  '/data': Database,
} as const

/**
 * Compact command-centre navigation rail (220px on desktop, drawer below
 * 1100px).
 *
 * Previously the rail was 246px of mostly-empty surface with no icons and a
 * large "MODE" card, so navigation visually outweighed the flood situation.
 * Icons carry the meaning, the label is secondary, and the status block is
 * condensed to three lines.
 */
export function Sidebar({
  open,
  onClose,
  unacked,
  children,
}: {
  open: boolean
  onClose: () => void
  unacked: number
  children?: React.ReactNode
}) {
  const pathname = usePathname()

  return (
    <>
      {open ? (
        <div className="nav-scrim" onClick={onClose} aria-hidden="true" />
      ) : null}
      <aside className={cx('sidebar', open && 'open')} aria-label="Primary">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            F
          </span>
          <span className="brand-text">
            <span className="brand-name">
              FLOODGUARD <b>AI</b>
            </span>
            <span className="brand-sub">Mithi River · BKC → Kurla</span>
          </span>
          <button
            type="button"
            className="close-nav"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>

        <nav className="nav-list">
          {NAV_ITEMS.map((item) => {
            const Icon = ICONS[item.href as keyof typeof ICONS]
            const active = pathname === item.href
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cx('nav-item', active && 'is-active')}
                aria-current={active ? 'page' : undefined}
                onClick={onClose}
              >
                <Icon size={15} className="nav-icon" aria-hidden="true" />
                <span className="nav-label">{item.label}</span>
                {item.href === '/alerts' && unacked > 0 ? (
                  <span className="nav-count" aria-label={`${unacked} unacknowledged alerts`}>
                    {unacked}
                  </span>
                ) : null}
              </Link>
            )
          })}
        </nav>

        {children ? <div className="sidebar-foot">{children}</div> : null}
      </aside>
    </>
  )
}