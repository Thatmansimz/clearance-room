import type { Report } from '../types'

const TILE_STYLES: Record<string, string> = {
  CLEAR: 'border-emerald-500/40 text-emerald-400',
  CAUTION: 'border-amber-400/40 text-amber-400',
  BLOCKED: 'border-red-500/40 text-red-500',
}

export function ReportPanel({ report, title }: { report: Report; title: string }) {
  return (
    <section className="card-in mt-10 rounded-xl border border-amber-400/30 bg-stone-900/70 p-6">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="font-display text-3xl tracking-wide text-amber-400">
          FINAL CLEARANCE REPORT
        </h2>
        <span className="font-mono text-[10px] uppercase tracking-widest text-stone-500">
          {title}
        </span>
      </div>

      <div className="mb-5 grid grid-cols-3 gap-3">
        {(['CLEAR', 'CAUTION', 'BLOCKED'] as const).map((v) => (
          <div
            key={v}
            className={`rounded-lg border bg-stone-950/60 p-4 text-center ${TILE_STYLES[v]}`}
          >
            <div className="font-display text-4xl">{report.stats[v] ?? 0}</div>
            <div className="font-mono text-[10px] uppercase tracking-[0.25em]">{v}</div>
          </div>
        ))}
      </div>

      <p className="max-w-4xl text-[14px] leading-relaxed text-stone-200">{report.summary}</p>

      <p className="mt-4 font-mono text-[9px] uppercase tracking-widest text-stone-600">
        breakdown: gemini via google adk · evidence: parallel search api · assessment: gemini
        structured output · counsel review still required before principal photography
      </p>
    </section>
  )
}
