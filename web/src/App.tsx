import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  Entity,
  EntityResult,
  EntityStatus,
  PipelineEvent,
  Report,
  StageKey,
  StageStatus,
  TitleVerdict,
} from './types'
import { StageRail } from './components/StageRail'
import { EntityCard } from './components/EntityCard'
import { ReportPanel } from './components/ReportPanel'
import { TitleGuard } from './components/TitleGuard'
import { streamSSE } from './lib/stream'

const INITIAL_STAGES: Record<StageKey, StageStatus> = {
  breakdown: 'idle',
  research: 'idle',
  assess: 'idle',
  report: 'idle',
}

type Mode = 'clearance' | 'truestory'

const MODES: Record<Mode, {
  label: string
  tagline: string
  endpoint: string
  button: string
  itemsLabel: string
  reportTitle: string
}> = {
  clearance: {
    label: '🎬 Script Clearance',
    tagline: 'brands · music · artwork · clips · locations',
    endpoint: '/api/clearance/run',
    button: '🎬 RUN CLEARANCE',
    itemsLabel: '🎞 clearance items',
    reportTitle: 'FINAL CLEARANCE REPORT',
  },
  truestory: {
    label: '⚖️ True-Story Shield',
    tagline: 'defamation fact-check for “based on a true story”',
    endpoint: '/api/truestory/run',
    button: '⚖️ FACT-CHECK SCRIPT',
    itemsLabel: '⚖ factual claims',
    reportTitle: 'DEFAMATION EXPOSURE REPORT',
  },
}

export default function App() {
  const [mode, setMode] = useState<Mode>('clearance')
  const [script, setScript] = useState('')
  const [title, setTitle] = useState('UNTITLED SCRIPT')
  const [running, setRunning] = useState(false)
  const [mock, setMock] = useState({ gemini: false, parallel: false })
  const [stages, setStages] = useState(INITIAL_STAGES)
  const [entities, setEntities] = useState<Entity[]>([])
  const [statuses, setStatuses] = useState<Record<string, EntityStatus>>({})
  const [results, setResults] = useState<Record<string, EntityResult>>({})
  const [report, setReport] = useState<Report | null>(null)
  const [ticker, setTicker] = useState<{ searches: number; sources: number } | null>(null)
  const [tgVerdict, setTgVerdict] = useState<TitleVerdict | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Research/assess failures the backend emits as `warning` frames. Previously
  // unhandled, so a failed live search vanished with no signal on screen.
  const [warnings, setWarnings] = useState<string[]>([])
  const boardRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch(`/api/sample?mode=${mode}`)
      .then((r) => r.json())
      .then((d) => {
        setScript(d.script)
        setTitle(d.title)
        setEntities([])
        setStatuses({})
        setResults({})
        setReport(null)
        setTicker(null)
        setTgVerdict(null)
        setStages(INITIAL_STAGES)
        setWarnings([])
      })
      .catch(() => setError('Backend not reachable — is the API server running?'))
  }, [mode])

  useEffect(() => {
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
      case 'ticker':
        setTicker({ searches: ev.searches, sources: ev.sources })
        break
      case 'warning':
        setWarnings((w) => (w.includes(ev.message) ? w : [...w, ev.message]))
        break
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
    setTicker(null)
    setWarnings([])
    boardRef.current?.scrollIntoView({ behavior: 'smooth' })

    try {
      await streamSSE<PipelineEvent>(MODES[mode].endpoint, { script }, handleEvent)
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }, [script, mode, handleEvent])

  const doneCount = Object.values(statuses).filter((s) => s === 'done').length

  return (
    <div className="grain min-h-screen">
      {/* Header */}
      <header className="border-b border-stone-800 bg-stone-950/80 backdrop-blur sticky top-0 z-40">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-y-1 px-6 py-3">
          <div className="flex items-baseline gap-3">
            <h1 className="font-display text-3xl tracking-wide text-amber-400">
              CLEARANCE<span className="text-stone-100">ROOM</span>
            </h1>
            <span className="hidden font-mono text-[11px] uppercase tracking-[0.25em] text-stone-500 sm:inline">
              every frame cleared
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-3 font-mono text-[11px] uppercase tracking-widest">
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
            <span className="hidden text-stone-500 md:inline">gemini × parallel</span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        <p className="mb-6 max-w-3xl text-[14px] leading-relaxed text-stone-400">
          Script clearance in minutes, not weeks. A deterministic Gemini agent breaks your
          screenplay into clearable items, researches each one on the live web with Parallel
          Search, and hands you an E&amp;O-ready clearance report.
        </p>
        {/* Mode switch */}
        <div className="mb-6 flex flex-wrap items-center gap-2">
          {(Object.keys(MODES) as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              disabled={running}
              className={[
                'rounded-lg border px-4 py-2 text-left transition disabled:cursor-not-allowed disabled:opacity-50',
                mode === m
                  ? 'border-amber-400/70 bg-amber-400/10'
                  : 'border-stone-800 bg-stone-900/40 hover:border-stone-700',
              ].join(' ')}
            >
              <div
                className={`font-display text-xl tracking-wide ${mode === m ? 'text-amber-400' : 'text-stone-300'}`}
              >
                {MODES[m].label}
              </div>
              <div className="font-mono text-[9px] uppercase tracking-widest text-stone-500">
                {MODES[m].tagline}
              </div>
            </button>
          ))}
        </div>

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
              {running ? 'ROLLING…' : MODES[mode].button}
            </button>
            {error && (
              <p className="mt-3 rounded border border-red-500/40 bg-red-950/40 p-3 font-mono text-xs text-red-300">
                {error}
              </p>
            )}
            {mode === 'clearance' && (
              <TitleGuard key={title} initialTitle={title} onVerdict={setTgVerdict} />
            )}
          </section>

          {/* Pipeline board */}
          <section ref={boardRef}>
            <StageRail stages={stages} mode={mode} />
            {warnings.length > 0 && (
              <div className="mt-3 rounded border border-amber-500/40 bg-amber-950/30 p-3 font-mono text-[11px] leading-snug text-amber-300">
                {warnings.map((w, i) => (
                  <p key={i}>⚠ {w}</p>
                ))}
              </div>
            )}
            {entities.length > 0 && (
              <div className="mb-3 mt-6 flex items-center justify-between">
                <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-stone-400">
                  {MODES[mode].itemsLabel}
                </h2>
                <span className="font-mono text-[10px] text-stone-500">
                  {ticker && (
                    <span className={running ? 'pulse-soft mr-4 text-sky-400' : 'mr-4 text-sky-500'}>
                      🔍 {ticker.searches} live searches · {ticker.sources} sources
                    </span>
                  )}
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
            {entities.length === 0 && running && (
              <div className="mt-6 rounded-lg border border-stone-800 p-10 text-center">
                <p className="pulse-soft font-display text-2xl tracking-wide text-amber-400">
                  {mode === 'truestory'
                    ? 'GEMINI IS READING FOR CLAIMS…'
                    : 'GEMINI IS BREAKING DOWN THE SCRIPT…'}
                </p>
                <p className="mt-1 font-mono text-xs text-stone-500">
                  clearable items land here as they're found
                </p>
              </div>
            )}
            {entities.length === 0 && !running && (
              <div className="mt-6 rounded-lg border border-dashed border-stone-800 p-10 text-center">
                <p className="font-display text-2xl tracking-wide text-stone-600">
                  THE BOARD IS DARK
                </p>
                <p className="mt-1 font-mono text-xs text-stone-600">
                  run clearance to light it up
                </p>
                <div className="mx-auto mt-5 grid max-w-md gap-1.5 text-left font-mono text-[11px] text-stone-500">
                  <p>01 · gemini extracts every clearable item from the script</p>
                  <p>02 · parallel search researches each one on the live web</p>
                  <p>03 · gemini grades risk · you get an e&amp;o-ready report</p>
                </div>
              </div>
            )}
          </section>
        </div>

        {report && (
          <ReportPanel
            report={report}
            title={title}
            heading={MODES[mode].reportTitle}
            titleVerdict={tgVerdict}
          />
        )}
      </main>

      <footer className="border-t border-stone-800 py-4 text-center font-mono text-[10px] uppercase tracking-widest text-stone-600">
        clearanceroom · gemini on vertex ai · google adk · parallel search api
      </footer>
    </div>
  )
}
