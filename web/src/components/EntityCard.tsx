import type { Entity, EntityResult, EntityStatus } from '../types'

const CATEGORY_ICONS: Record<string, string> = {
  BRAND: '🏷',
  PERSON: '🎤',
  MUSIC: '🎵',
  ARTWORK: '🖼',
  LOCATION: '🏙',
  MEDIA: '📺',
  ORGANIZATION: '🏢',
  OTHER: '📎',
}

const VERDICT_STYLES: Record<string, { stamp: string; bar: string }> = {
  CLEAR: { stamp: 'border-emerald-400 text-emerald-400', bar: 'bg-emerald-400' },
  CAUTION: { stamp: 'border-amber-400 text-amber-400', bar: 'bg-amber-400' },
  BLOCKED: { stamp: 'border-red-500 text-red-500', bar: 'bg-red-500' },
}

export function EntityCard({
  entity,
  status,
  result,
}: {
  entity: Entity
  status: EntityStatus
  result?: EntityResult
}) {
  const v = result ? VERDICT_STYLES[result.verdict] : undefined
  return (
    <article className="card-in relative overflow-hidden rounded-lg border border-stone-800 bg-stone-900/60 p-4">
      {/* risk bar */}
      {result && v && (
        <div
          className={`absolute inset-x-0 top-0 h-0.5 ${v.bar}`}
          style={{ width: `${result.risk_score}%` }}
        />
      )}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm">{CATEGORY_ICONS[entity.category] ?? '📎'}</span>
            <h3 className="truncate font-semibold text-[15px] text-stone-100">{entity.name}</h3>
          </div>
          <p className="mt-0.5 font-mono text-[9px] uppercase tracking-widest text-stone-500">
            {entity.category} · {entity.scene}
          </p>
        </div>
        {result && v && (
          <span
            className={`stamp-in shrink-0 rounded border-2 px-2 py-0.5 font-display text-lg tracking-widest ${v.stamp}`}
          >
            {result.verdict}
          </span>
        )}
      </div>

      <p className="mt-2 text-[12px] italic leading-snug text-stone-400">{entity.context}</p>

      {status === 'queued' && (
        <p className="mt-3 font-mono text-[10px] uppercase tracking-widest text-stone-600">
          ⋯ queued
        </p>
      )}
      {status === 'researching' && (
        <p className="pulse-soft mt-3 font-mono text-[10px] uppercase tracking-widest text-sky-400">
          🔍 researching web evidence · parallel
        </p>
      )}
      {status === 'assessing' && (
        <p className="pulse-soft mt-3 font-mono text-[10px] uppercase tracking-widest text-violet-400">
          ⚖ gemini weighing the evidence
        </p>
      )}

      {result && (
        <div className="mt-3 space-y-2 border-t border-stone-800 pt-3">
          <p className="text-[12px] leading-snug text-stone-300">{result.rationale}</p>
          <p className="text-[12px] leading-snug text-stone-300">
            <span className="font-mono text-[9px] uppercase tracking-widest text-amber-400/80">
              fix ·{' '}
            </span>
            {result.recommendation}
          </p>
          {result.precedent && (
            <p className="rounded border border-red-500/20 bg-red-950/20 px-2 py-1.5 text-[11px] leading-snug text-stone-300">
              <span className="font-mono text-[9px] uppercase tracking-widest text-red-400">
                ⚖ precedent ·{' '}
              </span>
              {result.precedent.case}{' '}
              <a
                href={result.precedent.url}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-[9px] text-sky-500 underline decoration-dotted hover:text-sky-300"
              >
                source
              </a>
            </p>
          )}
          <div className="flex items-center justify-between">
            <div className="flex flex-wrap gap-2">
              {result.sources.slice(0, 3).map((s) => (
                <a
                  key={s.url}
                  href={s.url}
                  target="_blank"
                  rel="noreferrer"
                  title={s.title}
                  className="font-mono text-[9px] text-sky-500 underline decoration-dotted hover:text-sky-300"
                >
                  {new URL(s.url).hostname.replace('www.', '')}
                </a>
              ))}
            </div>
            <span className="font-mono text-[10px] text-stone-500">
              risk {result.risk_score}
            </span>
          </div>
        </div>
      )}
    </article>
  )
}
