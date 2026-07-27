// ── API Response Types ──

export interface Stats {
  projects: number
  turn: number
  daily: number
  monthly: number
  backlog: number
  last: string | null
}

export interface ProjectInfo {
  name: string
  type: string
}

export interface FeedItem {
  type: 'turn' | 'daily' | 'monthly'
  ts: string | null
  summarized_at: string | null
  project: string
  project_name: string
  project_type: string
  title: string
  body: string
  user_input: string | null
  file: string
  validity: 'valid' | 'invalid' | 'merged' | 'low_value' | null
}

export interface FeedResponse {
  items: FeedItem[]
  stats: Stats
  projects: ProjectInfo[]
}

// ── Memory Types ──

export interface Conflict {
  registry_id: number
  summary: string
}

export interface Proposal {
  id: number
  action: 'create' | 'update' | 'delete' | 'upgrade' | 'downgrade'
  scope: string
  target_path: string
  target_section: string | null
  title: string
  content: string
  reason: string
  conflicts: Conflict[] | null
  source_dates: string[] | null
  related_registry_ids: number[] | null
  confidence: number
  status: string
  created_at: string
}

export interface ProposalsResponse {
  proposals: Proposal[]
  pending_count: number
  total: number
}

export interface RegistryEntry {
  id: number
  file_path: string
  section_heading: string | null
  scope: string
  base_weight: number
  effective_weight: number
  days_since_confirmed: number
  weight_status: 'healthy' | 'warning' | 'critical'
  confirmed_count?: number
  last_confirmed?: string
  created_at?: string
}

export interface RegistryResponse {
  registry: RegistryEntry[]
  by_scope: Record<string, RegistryEntry[]>
  total: number
}

// ── Projects Types ──

export interface SlugStats {
  slug: string
  sessions: number
  turns: number
  first: string | null
  last: string | null
  basename: string
  samples: string[]
}

export interface ProjectDef {
  id: string
  label: string
  type: 'long_term' | 'one_off' | 'archived'
  slugs: string[]
  path: string
}

export interface ProjectsResponse {
  projects: ProjectDef[]
  slug_stats: Record<string, SlugStats>
  unassigned: string[]
  source: string
  types: string[]
}

export interface SlugDetail {
  slug: string
  samples: { ts: string; text: string }[]
  summaries: { title: string; summary: string }[]
}

export interface FsNode {
  path: string
  parent: string | null
  dirs: { name: string; path: string }[]
}

// ── Action Labels ──

export const ACTION_LABELS: Record<string, string> = {
  create: '新建',
  update: '更新',
  delete: '删除',
  upgrade: '升级为全局',
  downgrade: '降级为项目',
}

export const TYPE_LABELS: Record<string, string> = {
  long_term: '长期',
  one_off: '一次性',
  archived: '归档',
}

export const MODE_LABELS: Record<string, string> = {
  turn: 'turn',
  daily: '日报',
  monthly: '月报',
}
