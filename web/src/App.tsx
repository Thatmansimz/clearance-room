import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  Entity,
  EntityResult,
  EntityStatus,
  PipelineEvent,
  Report,
  StageKey,
  StageStatus,
} from './types'
import { StageRail } from './components/StageRail'
import { EntityCard } from './components/EntityCard'
import { ReportPanel } from './components/ReportPanel'

const INITIAL_STAGES: Record<StageKey, StageStatus> = {
  breakdown: 'idle',
  research: 'idle',
  assess: 'idle',
  report: 'idle',
}

export default function App() {
  const [script, setScript] = useState('')
  const [title, setTitle] = useState('UNTITLED SCRIPT')
  const [running, setRunning] = useState(false)
  const [mock, setMock] = useState({ gemini: false, parallel: false })
  const [stages, setStages] = useState(INITIAL_STAGES)
  const [entities, setEntities] = useState<Entity[]>([])
  const [statuses, setStatuses] = useState<Record<string, EntityStatus>>({})
  const [results, setResults] = useState<Record<string, EntityResult>>({})
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState<string | null>(null)
  const boardRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch('/api/sample')
      .then((r) => r.json())
      .then((d) => {
        setScript(d.script)
        setTitle(d.title)
      })
      .catch(() => setError('Backend not reachable — is the API server running?'))
    fetch('/api/health')
      .then((r) => r.json())
      .then((d) => setMock({ gemini: d.mock_gemini, parallel: d.mock_parallel }))
      .catch(() => {})
  }, [])

  const handleEvent = useCallback((ev: PipelineEvent) => {
    switch (ev.type) {
      case 'stage':
        setStages((s) => ({ ...s, [ev.stage]: ev.status === 'start' ? 'running' : 'done' }))
        break
      case 'entity_found':
        setEntities((es) => [...es, ev.entity])
        setStatuses((s) => ({ ...s, [ev.entity.id]: 'queued' }))
        break
      case 'entity_status':
        setStatuses((s) => ({ ...s, [ev.id]: ev.status }))
        break
      case 'entity_result': {
        const { type: _t, id, ...result } = ev
        setStatuses((s) => ({ ...s, [id]: 'done' }))
        setResults((r) => ({ ...r, [id]: result as EntityResult }))
        break
      }
      case 'report': {
        const { type: _t, ...rep } = ev
        setReport(rep as Report)
        break
      }
      case 'done':
        setRunning(false)
        break
      case 'error':
        setError(ev.message)
        setRunning(false)
        break
    }
  }, [])

  const run = useCallback(async () => {
    setRunning(true)
    setError(null)
    setStages(INITIAL_STAGES)
    setEntities([])
    setStatuses({})
    setResults({})
    setReport(null)
    boardRef.current?.scrollIntoView({ behavior: 'smooth' })

    try {
      const resp = await fetch('/api/clearance/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ script }),
      })
      if (!resp.ok || !resp.body) throw new Error(`API ${resp.status}`)
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const chunks = buffer.split('\n\n')
        buffer = chunks.pop() ?? ''
        for (const chunk of chunks) {
          const line = chunk.trim()
          if (line.startsWith('data: ')) {
            handleEvent(JSON.parse(line.slice(6)) as PipelineEvent)
          }
        }
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }, [script, handleEvent])

  const doneCount = Object.values(statuses).filter((s) => s === 'done').length

  return (
    <div className="grain min-h-screen">
      {/* Header */}
      <header className="border-b border-stone-800 bg-stone-950/80 backdrop-blur sticky top-0 z-40">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-3">
          <div className="flex items-baseline gap-3">
            <h1 className="font-display text-3xl tracking-wide text-amber-400">
              CLEARANCE<span className="text-stone-100">ROOM</span>
            </h1>
            <span className="font-mono text-[11px] uppercase tracking-[0.25em] text-stone-500">
              every frame cleared
            </span>
          </div>
          <div className="flex items-center gap-3 font-mono text-[11px] uppercase tracking-widest">
            {mock.gemini && (
              <span className="rounded border border-fuchsia-500/50 px-2 py-0.5 text-fuchsia-400">
                gemini mock
              </span>
            )}
            {mock.parallel ? (
              <span className="rounded border border-fuchsia-500/50 px-2 py-0.5 text-fuchsia-400">
                parallel mock
              </span>
            ) : (
              <span className="rounded border border-emerald-500/50 px-2 py-0.5 text-emerald-400">
                parallel live
              </span>
            )}
            {running && (
              <span className="flex items-center gap-1.5 text-red-400">
                <span className="blink inline-block h-2 w-2 rounded-full bg-red-500" /> rec
              </span>
            )}
            <span className="text-stone-500">gemini × parallel</span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="grid gap-8 lg:grid-cols-[minmax(320px,2fr)_3fr]">
          {/* Script panel */}
          <section>
            <div className="mb-2 flex items-center justify-between">
              <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-stone-400">
                📄 the script — {title}
              </h2>
              <span className="font-mono text-[10px] text-stone-600">
                {script.split('\n').length} lines
              </span>
            </div>
            <textarea
              value={script}
              onChange={(e) => setScript(e.target.value)}
              spellCheck={false}
              className="h-[420px] w-full resize-none rounded-lg border border-stone-800 bg-stone-900/60 p-4 font-mono text-[12px] leading-relaxed text-stone-300 outline-none focus:border-amber-500/50"
            />
            <button
              onClick={run}
              disabled={running || !script.trim()}
              className="mt-3 w-full rounded-lg bg-amber-400 py-3 font-display text-2xl tracking-wider text-stone-950 transition hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {running ? 'ROLLING…' : '🎬 RUN CLEARANCE'}
            </button>
            {error && (
              <p className="mt-3 rounded border border-red-500/40 bg-red-950/40 p-3 font-mono text-xs text-red-300">
                {error}
              </p>
            )}
          </section>

          {/* Pipeline board */}
          <section ref={boardRef}>
            <StageRail stages={stages} />
            {entities.length > 0 && (
              <div className="mb-3 mt-6 flex items-center justify-between">
                <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-stone-400">
                  🎞 clearance items
                </h2>
                <span className="font-mono text-[10px] text-stone-500">
                  {doneCount}/{entities.length} assessed
                </span>
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              {entities.map((e) => (
                <EntityCard
                  key={e.id}
                  entity={e}
                  status={statuses[e.id] ?? 'queued'}
                  result={results[e.id]}
                />
              ))}
            </div>
            {entities.length === 0 && !running && (
              <div className="mt-6 rounded-lg border border-dashed border-stone-800 p-10 text-center">
                <p className="font-display text-2xl tracking-wide text-stone-600">
                  THE BOARD IS DARK
                </p>
                <p className="mt-1 font-mono text-xs text-stone-600">
                  run clearance to light it up
                </p>
              </div>
            )}
          </section>
        </div>

        {report && <ReportPanel report={report} title={title} />}
      </main>

      <footer className="border-t border-stone-800 py-4 text-center font-mono text-[10px] uppercase tracking-widest text-stone-600">
        clearanceroom · gemini on vertex ai · google adk · parallel search api
      </footer>
    </div>
  )
}
