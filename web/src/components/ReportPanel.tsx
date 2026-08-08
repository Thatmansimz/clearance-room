import type { Report } from '../types'

const TILE_STYLES: Record<string, string> = {
  CLEAR: 'border-emerald-500/40 text-emerald-400',
  CAUTION: 'border-amber-400/40 text-amber-400',
  BLOCKED: 'border-red-500/40 text-red-500',
}

export function ReportPanel({
  report,
  title,
  heading = 'FINAL CLEARANCE REPORT',
}: {
  report: Report
  title: string
  heading?: string
}) {
  return (
    <section className="card-in mt-10 rounded-xl border border-amber-400/30 bg-stone-900/70 p-6">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="font-display text-3xl tracking-wide text-amber-400">{heading}</h2>
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

      {report.elapsed_seconds != null && (
        <div className="mb-5 flex flex-wrap items-baseline gap-x-6 gap-y-1 rounded-lg border border-stone-800 bg-stone-950/60 px-4 py-3">
          <span className="font-mono text-[10px] uppercase tracking-widest text-stone-500">
            human clearance report:{' '}
            <span className="text-stone-300">$1,000–$3,000 · 5–10 business days</span>{' '}
            <a
              href="https://www.coastalclearances.com/script-clearance-reports"
              target="_blank"
              rel="noreferrer"
              className="text-sky-500 underline decoration-dotted hover:text-sky-300"
            >
              source
            </a>
          </span>
          <span className="font-mono text-[10px] uppercase tracking-widest text-amber-400">
            this report: {Math.round(report.elapsed_seconds)}s · {report.searches} live searches ·{' '}
            {report.sources} sources
          </span>
        </div>
      )}
      <p className="max-w-4xl text-[14px] leading-relaxed text-stone-200">{report.summary}</p>

      <p className="mt-4 font-mono text-[9px] uppercase tracking-widest text-stone-600">
        breakdown: gemini via google adk · evidence: parallel search api · assessment: gemini
        structured output · counsel review still required before principal photography
      </p>
    </section>
  )
}
