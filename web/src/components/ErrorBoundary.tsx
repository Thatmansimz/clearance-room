import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * A crash while drawing one card must not blank the whole board mid-demo.
 * A malformed source URL or an unexpected event shape throws in render; without
 * a boundary that unmounts the entire app and the run you just watched is gone.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ClearanceRoom render error:', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="min-h-screen bg-stone-950 p-6">
        <div className="mx-auto max-w-3xl rounded-lg border border-red-500/40 bg-red-950/30 p-6">
          <h1 className="font-display text-3xl tracking-wide text-red-400">RENDER FAULT</h1>
          <p className="mt-2 text-sm text-stone-300">
            The interface hit an error while drawing the board. Reload to start a fresh run.
          </p>
          <pre className="mt-4 overflow-x-auto rounded border border-stone-800 bg-stone-950 p-3 font-mono text-[11px] text-red-300">
            {error.message}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            className="mt-4 rounded border border-stone-700 px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest text-stone-300 hover:border-amber-500/60 hover:text-amber-400"
          >
            try rendering again
          </button>
        </div>
      </div>
    )
  }
}
