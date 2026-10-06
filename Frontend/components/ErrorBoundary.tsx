'use client'

import { Component, ErrorInfo, ReactNode } from 'react'
import { COLORS } from '@/lib/constants'

interface Props {
  children: ReactNode
  fallback?: ReactNode
  onError?: (error: Error, errorInfo: ErrorInfo) => void
}

interface State {
  hasError: boolean
  error: Error | null
}

/**
 * Error Boundary component to catch React component errors
 * and display a fallback UI instead of crashing the entire app.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = {
    hasError: false,
    error: null,
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('ErrorBoundary caught an error:', error, errorInfo)
    this.props.onError?.(error, errorInfo)
  }

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback
      }

      return (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '300px',
          padding: '24px',
          background: '#0b1220',
          border: '1px solid #ef4444',
          borderRadius: '8px',
          color: '#ef4444',
          fontFamily: 'system-ui, sans-serif',
        }}>
          <h2 style={{ margin: '0 0 12px', fontSize: '18px' }}>Something went wrong</h2>
          <p style={{ margin: '0 0 16px', color: COLORS.muted, fontSize: '14px' }}>
            An unexpected error occurred in this component. The development team has been notified.
          </p>
          <details style={{ width: '100%', color: COLORS.muted, fontSize: '12px', fontFamily: 'monospace' }}>
            <summary style={{ cursor: 'pointer', marginBottom: '8px' }}>Error Details</summary>
            <pre style={{ overflow: 'auto', whiteSpace: 'pre-wrap' }}>
              {this.state.error?.message}
            </pre>
          </details>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            style={{
              marginTop: '16px',
              padding: '8px 16px',
              background: COLORS.accent,
              color: '#071220',
              border: 'none',
              borderRadius: '4px',
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Try Again
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
