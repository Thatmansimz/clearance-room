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

export interface EntityResult {
  verdict: Verdict
  risk_score: number
  rationale: string
  recommendation: string
  sources: Source[]
}

export interface Report {
  summary: string
  stats: Record<Verdict, number>
  items: (Entity & EntityResult)[]
}

export type PipelineEvent =
  | { type: 'stage'; stage: StageKey; status: 'start' | 'done'; count?: number }
  | { type: 'entity_found'; entity: Entity }
  | { type: 'entity_status'; id: string; status: 'researching' | 'assessing' }
  | ({ type: 'entity_result'; id: string } & EntityResult)
  | ({ type: 'report' } & Report)
  | { type: 'warning'; id: string; message: string }
  | { type: 'done'; mock: boolean }
  | { type: 'error'; message: string }
