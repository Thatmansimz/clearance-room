import { useState } from 'react'
import type { DossierEvent, DossierField } from '../types'
import { streamSSE } from '../lib/stream'
import { safeHostname } from '../lib/url'

const CONFIDENCE_STYLE: Record<string, string> = {
  high: 'border-emerald-500/50 text-emerald-400',
  medium: 'border-amber-400/50 text-amber-400',
  low: 'border-red-500/50 text-red-400',
  unknown: 'border-stone-600 text-stone-400',
}

export function Dossier({
  name,
  category,
  context,
}: {
  name: string
  category: string
  context: string
}) {
  const [open, setOpen] = useState(false)
  const [running, setRunning] = useState(false)
  const [tick, setTick] = useState<{ status: string; elapsed: number } | null>(null)
  const [fields, setFields] = useState<DossierField[] | null>(null)
  const [sourceCount, setSourceCount] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setOpen(true)
    setRunning(true)
    setError(null)
    setFields(null)
    setTick(null)
    try {
      await streamSSE<DossierEvent>('/api/dossier', { name, category, context }, (ev) => {
        switch (ev.type) {
          case 'dossier_tick':
            setTick({ status: ev.status, elapsed: ev.elapsed })
            break
          case 'dossier_result':
            setFields(ev.fields)
            setSourceCount(ev.source_count)
            break
          case 'error':
            setError(ev.message)
            break
        }
      })
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="mt-3 border-t border-stone-800 pt-3">
      {!open && (
        <button
          onClick={run}
          className="w-full rounded border border-sky-500/40 py-1.5 font-mono text-[10px] uppercase tracking-widest text-sky-400 transition hover:bg-sky-500/10"
        >
          🔬 deep dossier · parallel task api
        </button>
      )}

      {open && (
        <>
          <div className="mb-2 flex items-baseline justify-between">
            <span className="font-mono text-[9px] uppercase tracking-widest text-sky-400">
              🔬 deep dossier · parallel task api
            </span>
            {fields && (
              <span className="font-mono text-[9px] text-stone-400">
                {sourceCount} sources
              </span>
            )}
          </div>

          {running && (
            <p className="pulse-soft font-mono text-[10px] uppercase tracking-widest text-sky-400">
              multi-hop research running{tick ? ` · ${tick.elapsed}s` : '…'}
            </p>
          )}

          {error && (
            <p role="alert" className="font-mono text-[10px] text-red-300">
              {error}
            </p>
          )}

          {fields && (
            <dl className="space-y-2.5">
              {fields.map((f) => (
                <div key={f.field} className="card-in">
                  <dt className="flex items-baseline gap-2">
                    <span className="font-mono text-[9px] uppercase tracking-widest text-stone-400">
                      {f.label}
                    </span>
                    <span
                      className={`rounded border px-1.5 font-mono text-[8px] uppercase tracking-widest ${
                        CONFIDENCE_STYLE[f.confidence] ?? CONFIDENCE_STYLE.unknown
                      }`}
                      title={f.reasoning}
                    >
                      {f.confidence}
                    </span>
                  </dt>
                  <dd className="mt-0.5 text-[12px] leading-snug text-stone-300">
                    {f.value}
                    {f.citations.length > 0 && (
                      <span className="ml-1">
                        {f.citations.map((c) => (
                          <a
                            key={c.url}
                            href={c.url}
                            target="_blank"
                            rel="noreferrer"
                            title={c.title}
                            className="ml-1 font-mono text-[9px] text-sky-500 underline decoration-dotted hover:text-sky-300"
                          >
                            {safeHostname(c.url)}
                          </a>
                        ))}
                      </span>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </>
      )}
    </div>
  )
}
