import type { PipelineMode, StageKey, StageStatus } from '../types'

const STAGES: { key: StageKey; label: string; engine: string }[] = [
  { key: 'breakdown', label: 'Breakdown', engine: 'Gemini · ADK' },
  { key: 'research', label: 'Research', engine: 'Parallel Search' },
  { key: 'assess', label: 'Assessment', engine: 'Gemini' },
  { key: 'report', label: 'Report', engine: 'Gemini · ADK' },
]

export function StageRail({
  stages,
  mode = 'clearance',
}: {
  stages: Record<StageKey, StageStatus>
  mode?: PipelineMode
}) {
  return (
    <ol className="grid grid-cols-2 items-stretch gap-2 md:flex">
      {STAGES.map((s, i) => {
        const st = stages[s.key]
        return (
          <li key={s.key} className="flex flex-1 items-center gap-2">
            <div
              className={[
                'flex-1 rounded-lg border px-3 py-2 transition-colors',
                st === 'running'
                  ? 'border-amber-400/70 bg-amber-400/10'
                  : st === 'done'
                    ? 'border-emerald-500/40 bg-emerald-500/5'
                    : 'border-stone-800 bg-stone-900/40',
              ].join(' ')}
            >
              <div className="flex items-center gap-2">
                <span
                  className={[
                    'font-mono text-[10px]',
                    st === 'running'
                      ? 'pulse-soft text-amber-400'
                      : st === 'done'
                        ? 'text-emerald-400'
                        : 'text-stone-600',
                  ].join(' ')}
                >
                  {st === 'done' ? '✔' : st === 'running' ? '●' : `0${i + 1}`}
                </span>
                <span
                  className={[
                    'font-display text-lg tracking-wide',
                    st === 'idle' ? 'text-stone-500' : 'text-stone-100',
                  ].join(' ')}
                >
                  {mode === 'truestory' && s.key === 'breakdown' ? 'Claims' : s.label}
                </span>
              </div>
              <div className="font-mono text-[10px] uppercase tracking-widest text-stone-400">
                {s.engine}
              </div>
            </div>
            {i < STAGES.length - 1 && <span className="hidden text-stone-700 md:inline">›</span>}
          </li>
        )
      })}
    </ol>
  )
}
