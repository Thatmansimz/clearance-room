import { useMemo, useState } from 'react'
import type { AiCheckEvent, AiResult, EoRow, TitleVerdict, Verdict } from '../types'
import { streamSSE } from '../lib/stream'

const AI_USAGES: { id: string; label: string }[] = [
  { id: 'ai_voice', label: 'AI voice / narration' },
  { id: 'ai_imagery', label: 'AI imagery / VFX' },
  { id: 'ai_music', label: 'AI music elements' },
  { id: 'ai_writing', label: 'AI-assisted writing' },
  { id: 'ai_likeness', label: 'Digital replica of a real person' },
]

const STATUS_STYLE: Record<string, { chip: string; label: string }> = {
  clear: { chip: 'border-emerald-500/50 text-emerald-400', label: 'CLEAR' },
  flagged: { chip: 'border-red-500/50 text-red-400', label: 'ACTION REQUIRED' },
  pending: { chip: 'border-stone-600 text-stone-400', label: 'PENDING' },
  out_of_scope: { chip: 'border-stone-700 text-stone-500', label: 'DOCUMENTS REQUIRED' },
}

const VERDICT_TEXT: Record<Verdict, string> = {
  CLEAR: 'text-emerald-400',
  CAUTION: 'text-amber-400',
  BLOCKED: 'text-red-400',
}

function worstVerdict(results: { verdict: Verdict }[]): Verdict | undefined {
  const order: Verdict[] = ['BLOCKED', 'CAUTION', 'CLEAR']
  return order.find((v) => results.some((r) => r.verdict === v))
}

export function EoBinder({
  rows,
  titleVerdict,
}: {
  rows: EoRow[]
  titleVerdict: TitleVerdict | null
}) {
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [aiResults, setAiResults] = useState<AiResult[]>([])
  const [aiRunning, setAiRunning] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)

  const effectiveRows = useMemo(() => {
    return rows.map((row) => {
      if (row.id === 'title_report' && titleVerdict) {
        const flagged = titleVerdict.verdict !== 'CLEAR'
        return {
          ...row,
          status: (flagged ? 'flagged' : 'clear') as EoRow['status'],
          worst: flagged ? titleVerdict.verdict : undefined,
          note: `TitleGuard: "${titleVerdict.title}" — ${titleVerdict.verdict}, confusion risk ${titleVerdict.risk_score}.`,
          findings: [],
        }
      }
      if (row.id === 'ai_usage_disclosure' && aiResults.length > 0) {
        const worst = worstVerdict(aiResults)
        const flagged = worst !== 'CLEAR'
        return {
          ...row,
          status: (flagged ? 'flagged' : 'clear') as EoRow['status'],
          worst: flagged ? worst : undefined,
          note: `${aiResults.length} AI usage${aiResults.length > 1 ? 's' : ''} researched against current carrier language.`,
          findings: [],
        }
      }
      return row
    })
  }, [rows, titleVerdict, aiResults])

  const covered = effectiveRows.filter((r) => r.status === 'clear').length
  const actionable = effectiveRows.filter((r) => r.status !== 'out_of_scope').length

  const runAiCheck = async () => {
    setAiRunning(true)
    setAiError(null)
    setAiResults([])
    try {
      await streamSSE<AiCheckEvent>('/api/eo/ai-check', { usages: [...checked] }, (ev) => {
        if (ev.type === 'ai_result') {
          const { type: _t, ...r } = ev
          setAiResults((prev) => [...prev, r as AiResult])
        } else if (ev.type === 'error') {
          setAiError(ev.message)
        }
      })
    } catch (e) {
      setAiError(String(e))
    } finally {
      setAiRunning(false)
    }
  }

  return (
    <div className="mt-6">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-display text-2xl tracking-wide text-stone-100">
          E&amp;O BINDER <span className="text-stone-500">· underwriter checklist</span>
        </h3>
        <span className="font-mono text-[10px] uppercase tracking-widest text-stone-500">
          {covered}/{actionable} procedures clear · no E&amp;O, no distribution{' '}
          <a
            href="https://www.frontrowinsurance.com/errors-omissions-insurance-101"
            target="_blank"
            rel="noreferrer"
            className="text-sky-500 underline decoration-dotted hover:text-sky-300"
          >
            source
          </a>
        </span>
      </div>

      <ol className="divide-y divide-stone-800 rounded-lg border border-stone-800 bg-stone-950/50">
        {effectiveRows.map((row, i) => {
          const style = STATUS_STYLE[row.status]
          return (
            <li key={row.id} className="flex items-start gap-3 px-4 py-2.5">
              <span className="mt-0.5 w-5 shrink-0 font-mono text-[10px] text-stone-600">
                {String(i + 1).padStart(2, '0')}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-[13px] font-medium text-stone-100">{row.title}</span>
                  <a
                    href={row.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-[9px] text-sky-600 underline decoration-dotted hover:text-sky-400"
                  >
                    req
                  </a>
                </div>
                <p className="text-[11px] leading-snug text-stone-500">{row.note}</p>
                {row.findings.length > 0 && (
                  <p className="mt-0.5 text-[11px] leading-snug">
                    {row.findings.slice(0, 4).map((f, j) => (
                      <span key={f.id}>
                        {j > 0 && <span className="text-stone-600"> · </span>}
                        <span className="text-stone-300">{f.name}</span>{' '}
                        <span className={`font-mono text-[9px] ${VERDICT_TEXT[f.verdict]}`}>
                          {f.verdict}
                        </span>
                      </span>
                    ))}
                    {row.findings.length > 4 && (
                      <span className="text-stone-600"> +{row.findings.length - 4} more</span>
                    )}
                  </p>
                )}
              </div>
              <span
                className={`mt-0.5 shrink-0 rounded border px-2 py-0.5 font-mono text-[9px] uppercase tracking-widest ${style.chip}`}
              >
                {row.status === 'flagged' && row.worst ? row.worst : style.label}
              </span>
            </li>
          )
        })}
      </ol>

      {/* AI-usage intake */}
      <div className="no-print mt-4 rounded-lg border border-stone-800 bg-stone-950/50 p-4">
        <div className="mb-2 flex items-center justify-between">
          <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-stone-400">
            🤖 ai-usage intake — 2026 policies carry ai exclusions
          </h4>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {AI_USAGES.map((u) => (
            <label
              key={u.id}
              className={`flex cursor-pointer items-center gap-1.5 rounded border px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-wide transition ${
                checked.has(u.id)
                  ? 'border-amber-400/60 bg-amber-400/10 text-amber-300'
                  : 'border-stone-700 text-stone-400 hover:border-stone-500'
              }`}
            >
              <input
                type="checkbox"
                className="hidden"
                checked={checked.has(u.id)}
                onChange={() =>
                  setChecked((prev) => {
                    const next = new Set(prev)
                    if (next.has(u.id)) next.delete(u.id)
                    else next.add(u.id)
                    return next
                  })
                }
              />
              {u.label}
            </label>
          ))}
          <button
            onClick={runAiCheck}
            disabled={aiRunning || checked.size === 0}
            className="ml-auto rounded border border-amber-400/60 px-3 py-1.5 font-display text-lg tracking-wider text-amber-400 transition hover:bg-amber-400/10 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {aiRunning ? 'RESEARCHING…' : 'CHECK INSURABILITY'}
          </button>
        </div>
        {aiError && (
          <p className="mt-3 rounded border border-red-500/40 bg-red-950/40 p-2 font-mono text-xs text-red-300">
            {aiError}
          </p>
        )}
        {aiResults.length > 0 && (
          <ul className="mt-3 space-y-2">
            {aiResults.map((r) => (
              <li key={r.usage} className="card-in flex items-start gap-3 text-[12px]">
                <span
                  className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase ${
                    r.verdict === 'CLEAR'
                      ? 'border-emerald-500/50 text-emerald-400'
                      : r.verdict === 'CAUTION'
                        ? 'border-amber-400/50 text-amber-400'
                        : 'border-red-500/50 text-red-400'
                  }`}
                >
                  {r.verdict}
                </span>
                <span className="min-w-0 leading-snug text-stone-300">
                  <span className="text-stone-100">{r.label}.</span> {r.guidance}{' '}
                  {r.sources.slice(0, 2).map((s) => (
                    <a
                      key={s.url}
                      href={s.url}
                      target="_blank"
                      rel="noreferrer"
                      className="mr-1 font-mono text-[9px] text-sky-500 underline decoration-dotted hover:text-sky-300"
                    >
                      {new URL(s.url).hostname.replace('www.', '')}
                    </a>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
