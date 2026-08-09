export type Verdict = 'CLEAR' | 'CAUTION' | 'BLOCKED'
export type EntityStatus = 'queued' | 'researching' | 'assessing' | 'done'
export type StageKey = 'breakdown' | 'research' | 'assess' | 'report'
export type StageStatus = 'idle' | 'running' | 'done'

export interface Entity {
  id: string
  name: string
  category: string
  scene: string
  context: string
}

export interface Source {
  url: string
  title: string
}

export interface Precedent {
  case: string
  url: string
}

export interface EntityResult {
  verdict: Verdict
  risk_score: number
  rationale: string
  recommendation: string
  sources: Source[]
  precedent?: Precedent | null
}

export interface EoFinding {
  id: string
  name: string
  verdict: Verdict
}

export type EoStatus = 'clear' | 'flagged' | 'pending' | 'out_of_scope'

export interface EoRow {
  id: string
  title: string
  requirement: string
  source_url: string
  status: EoStatus
  worst?: Verdict
  note: string
  findings: EoFinding[]
}

export interface Report {
  summary: string
  stats: Record<Verdict, number>
  items: (Entity & EntityResult)[]
  eo_checklist?: EoRow[]
  elapsed_seconds?: number
  searches?: number
  sources?: number
}

export interface AiResult {
  usage: string
  label: string
  verdict: Verdict
  guidance: string
  sources: Source[]
}

export type AiCheckEvent =
  | { type: 'ai_stage'; status: string }
  | ({ type: 'ai_result' } & AiResult)
  | { type: 'ai_done' }
  | { type: 'error'; message: string }

export type PipelineMode = 'clearance' | 'truestory'

export interface DossierField {
  field: string
  label: string
  value: string
  confidence: 'high' | 'medium' | 'low' | 'unknown'
  reasoning: string
  citations: Source[]
}

export type DossierEvent =
  | { type: 'dossier_stage'; status: string; processor?: string; run_id?: string; item?: string }
  | { type: 'dossier_tick'; status: string; elapsed: number }
  | { type: 'dossier_result'; item: string; fields: DossierField[]; source_count: number }
  | { type: 'error'; message: string }

export interface TitleConflict {
  name: string
  medium: string
  year: string
  url: string
  severity: string
}

export interface TitleVerdict {
  title: string
  verdict: Verdict
  risk_score: number
  conflicts: TitleConflict[]
  rationale: string
  recommendation: string
}

export interface TitleAlternate {
  title: string
  verdict: Verdict
  note: string
}

export type TitleGuardEvent =
  | { type: 'tg_stage'; stage: 'sweep' | 'assess' | 'alternates'; status: 'start' | 'done'; searches?: number; sources?: number }
  | ({ type: 'tg_verdict' } & TitleVerdict)
  | ({ type: 'tg_alternate' } & TitleAlternate)
  | { type: 'tg_done' }
  | { type: 'error'; message: string }

export type PipelineEvent =
  | { type: 'stage'; stage: StageKey; status: 'start' | 'done'; count?: number }
  | { type: 'entity_found'; entity: Entity }
  | { type: 'entity_status'; id: string; status: 'researching' | 'assessing' }
  | ({ type: 'entity_result'; id: string } & EntityResult)
  | ({ type: 'report' } & Report)
  | { type: 'ticker'; searches: number; sources: number }
  | { type: 'warning'; id: string; message: string }
  | { type: 'done'; mock: boolean }
  | { type: 'error'; message: string }
