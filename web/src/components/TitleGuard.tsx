import { useState } from 'react'
import type { TitleAlternate, TitleGuardEvent, TitleVerdict } from '../types'
import { streamSSE } from '../lib/stream'

const STAMP: Record<string, string> = {
  CLEAR: 'border-emerald-400 text-emerald-400',
  CAUTION: 'border-amber-400 text-amber-400',
  BLOCKED: 'border-red-500 text-red-500',
}

const DOT: Record<string, string> = {
  CLEAR: 'bg-emerald-400',
  CAUTION: 'bg-amber-400',
  BLOCKED: 'bg-red-500',
}

const STAGE_LABEL: Record<string, string> = {
  sweep: 'sweeping registrations + open web · parallel',
  assess: 'gemini scoring likelihood of confusion',
  alternates: 'screening cleared alternates',
}

export function TitleGuard({ initialTitle }: { initialTitle: string }) {
  const [title, setTitle] = useState(initialTitle)
  const [running, setRunning] = useState(false)
  const [stage, setStage] = useState<string | null>(null)
  const [verdict, setVerdict] = useState<TitleVerdict | null>(null)
  const [alternates, setAlternates] = useState<TitleAlternate[]>([])
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setRunning(true)
    setError(null)
    setVerdict(null)
    setAlternates([])
    try {
      await streamSSE<TitleGuardEvent>('/api/title/check', { title }, (ev) => {
        switch (ev.type) {
          case 'tg_stage':
            setStage(ev.status === 'start' ? ev.stage : null)
            break
          case 'tg_verdict': {
            const { type: _t, ...v } = ev
            setVerdict(v as TitleVerdict)
            break
          }
          case 'tg_alternate': {
            const { type: _t, ...a } = ev
            setAlternates((prev) => [...prev, a as TitleAlternate])
            break
          }
          case 'error':
            setError(ev.message)
            break
        }
      })
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
      setStage(null)
    }
  }

  return (
    <section className="mt-6 rounded-lg border border-stone-800 bg-stone-900/60 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-stone-400">
          🛡 titleguard — working title check
        </h2>
        <span className="font-mono text-[9px] uppercase tracking-widest text-stone-600">
          registered marks + common-law web sweep
        </span>
      </div>
      <div className="flex gap-2">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          spellCheck={false}
          className="flex-1 rounded-lg border border-stone-800 bg-stone-950/60 px-3 py-2 font-display text-xl tracking-wider text-stone-100 outline-none focus:border-amber-500/50"
        />
        <button
          onClick={run}
          disabled={running || !title.trim()}
          className="rounded-lg border border-amber-400/60 px-4 font-display text-xl tracking-wider text-amber-400 transition hover:bg-amber-400/10 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {running ? 'SWEEPING…' : 'CHECK TITLE'}
        </button>
      </div>

      {stage && (
        <p className="pulse-soft mt-3 font-mono text-[10px] uppercase tracking-widest text-sky-400">
          🔍 {STAGE_LABEL[stage] ?? stage}
        </p>
      )}
      {error && (
        <p className="mt-3 rounded border border-red-500/40 bg-red-950/40 p-2 font-mono text-xs text-red-300">
          {error}
        </p>
      )}

      {verdict && (
        <div className="card-in mt-4 border-t border-stone-800 pt-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <span className="font-display text-2xl tracking-wide text-stone-100">
                {verdict.title}
              </span>
              <span className="ml-3 font-mono text-[10px] text-stone-500">
                confusion risk {verdict.risk_score}
              </span>
            </div>
            <span
              className={`stamp-in shrink-0 rounded border-2 px-2 py-0.5 font-display text-lg tracking-widest ${STAMP[verdict.verdict]}`}
            >
              {verdict.verdict}
            </span>
          </div>
          <p className="mt-2 text-[12px] leading-snug text-stone-300">{verdict.rationale}</p>
          <p className="mt-1 text-[12px] leading-snug text-stone-300">
            <span className="font-mono text-[9px] uppercase tracking-widest text-amber-400/80">
              fix ·{' '}
            </span>
            {verdict.recommendation}
          </p>

          {verdict.conflicts.length > 0 && (
            <ul className="mt-3 space-y-1">
              {verdict.conflicts.map((c) => (
                <li key={`${c.name}-${c.url}`} className="flex items-baseline gap-2 text-[12px]">
                  <span
                    className={`font-mono text-[9px] uppercase ${
                      c.severity === 'HIGH'
                        ? 'text-red-400'
                        : c.severity === 'MEDIUM'
                          ? 'text-amber-400'
                          : 'text-stone-500'
                    }`}
                  >
                    {c.severity}
                  </span>
                  <span className="text-stone-200">{c.name}</span>
                  <span className="text-stone-500">
                    {c.medium} · {c.year}
                  </span>
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-[9px] text-sky-500 underline decoration-dotted hover:text-sky-300"
                  >
                    source
                  </a>
                </li>
              ))}
            </ul>
          )}

          {alternates.length > 0 && (
            <div className="mt-4">
              <p className="mb-2 font-mono text-[9px] uppercase tracking-widest text-stone-500">
                cleared alternates — each screened live
              </p>
              <div className="flex flex-wrap gap-2">
                {alternates.map((a) => (
                  <span
                    key={a.title}
                    title={a.note}
                    className="card-in flex items-center gap-2 rounded border border-stone-700 bg-stone-950/60 px-3 py-1.5"
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${DOT[a.verdict]}`} />
                    <span className="font-display text-lg tracking-wide text-stone-100">
                      {a.title}
                    </span>
                    <span className="font-mono text-[9px] uppercase text-stone-500">
                      {a.verdict}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
