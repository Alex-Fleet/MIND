import type {
  FeedResponse,
  ProposalsResponse,
  RegistryResponse,
  ProjectsResponse,
  SlugDetail,
  FsNode,
} from './types'

const BASE = ''

async function jfetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + url, {
    cache: 'no-store',
    ...init,
  })
  if (!res.ok) {
    throw new Error(`${url} → HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ── Timeline ──

export function fetchFeed(): Promise<FeedResponse> {
  return jfetch<FeedResponse>('/api/feed')
}

// ── Memory ──

export function fetchProposals(
  status?: string,
): Promise<ProposalsResponse> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : ''
  return jfetch<ProposalsResponse>('/api/memory-proposals' + qs)
}

export function fetchRegistry(): Promise<RegistryResponse> {
  return jfetch<RegistryResponse>('/api/memory-registry')
}

export function applyProposal(
  id: number,
  action: 'approve' | 'reject',
  editedContent?: string,
): Promise<{ ok: boolean; message: string }> {
  return jfetch('/api/memory-proposals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(
      editedContent
        ? { id, action, edited_content: editedContent }
        : { id, action },
    ),
  })
}

export function confirmRegistry(
  id: number,
): Promise<{ ok: boolean; new_base_weight: number }> {
  return jfetch(`/api/memory-registry/${id}/confirm`, { method: 'POST' })
}

export function requestDeleteRegistry(
  path: string,
  section: string | null,
): Promise<{ ok: boolean; message: string }> {
  return jfetch('/api/memory-proposals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      id: 0,
      action: 'create_delete_manual',
      target_path: path,
      target_section: section || null,
      title: '手动标记删除: ' + path,
      content: '用户从看板手动标记删除。',
      reason: '手动标记',
    }),
  })
}

// ── Projects ──

export function fetchProjects(): Promise<ProjectsResponse> {
  return jfetch<ProjectsResponse>('/api/projects')
}

export function saveProjects(projects: object): Promise<{ ok: boolean; errors?: string[] }> {
  return jfetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(projects),
  })
}

export function fetchSlugDetail(slug: string): Promise<SlugDetail> {
  return jfetch<SlugDetail>(
    '/api/slug?slug=' + encodeURIComponent(slug),
  )
}

export function fetchFs(path: string): Promise<FsNode> {
  return jfetch<FsNode>('/api/fs?path=' + encodeURIComponent(path))
}
