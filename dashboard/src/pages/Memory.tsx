import { useEffect, useState, useCallback, useRef } from 'react'
import type {
  Proposal,
  RegistryEntry,
  ProposalsResponse,
  RegistryResponse,
} from '../api/types'
import {
  fetchProposals,
  fetchRegistry,
  applyProposal,
  confirmRegistry,
  requestDeleteRegistry,
} from '../api/client'
import { ACTION_LABELS } from '../api/types'

const pad = (n: number) => String(n).padStart(2, '0')
function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// Build set of registry IDs that have pending proposals
function buildPropRegIds(
  proposals: Proposal[],
  registry: Record<string, RegistryEntry[]>,
): Set<number> {
  const ids = new Set<number>()
  const byKey = new Map<string, number>()
  for (const scope of Object.values(registry)) {
    for (const e of scope) {
      byKey.set((e.file_path || '') + '|' + (e.section_heading || ''), e.id)
    }
  }
  for (const p of proposals) {
    if (Array.isArray(p.related_registry_ids)) {
      p.related_registry_ids.forEach((rid) => ids.add(rid))
    }
    if (p.action === 'delete' && p.target_path) {
      const key =
        (p.target_path || '') + '|' + (p.target_section || '')
      const rid = byKey.get(key)
      if (rid !== undefined) ids.add(rid)
    }
  }
  return ids
}

export default function Memory() {
  const [proposals, setProposals] =
    useState<ProposalsResponse | null>(null)
  const [registry, setRegistry] =
    useState<RegistryResponse | null>(null)
  const [lastUpdate, setLastUpdate] = useState('')
  const [memScope, setMemScope] = useState('all')
  const [expanded, setExpanded] = useState<Set<string>>(
    new Set(),
  )
  const [editId, setEditId] = useState<number | null>(null)
  const [editText, setEditText] = useState('')
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)

  const load = useCallback(async () => {
    try {
      const [pr, rr] = await Promise.all([
        fetchProposals('pending'),
        fetchRegistry(),
      ])
      setProposals(pr)
      setRegistry(rr)
      setLastUpdate(fmt(new Date().toISOString()))
      setError(false)
    } catch {
      setLastUpdate('⚠ 连不上服务器')
      setError(true)
    }
  }, [])

  useEffect(() => {
    load()
    const iv = setInterval(load, 30_000)
    return () => clearInterval(iv)
  }, [load])

  const pendCount = proposals?.pending_count ?? 0
  const regByScope = registry?.by_scope ?? {}
  const regList = registry?.registry ?? []
  const totalReg = regList.length
  const scopeCount = Object.keys(regByScope).length
  const propRegIds = buildPropRegIds(
    proposals?.proposals ?? [],
    regByScope,
  )

  const scopeLabels: Record<string, string> = {}
  for (const s of Object.keys(regByScope).sort()) {
    scopeLabels[s] = s.startsWith('project:')
      ? '📁 ' + s.replace('project:', '')
      : s === 'global'
        ? '🌐 用户全局'
        : s
  }

  // 防重复提交：任一请求在途时忽略后续点击（ref 同 tick 也拦得住），成功后刷新
  async function run(fn: () => Promise<void>) {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    try {
      await fn()
      await load()
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  async function doApprove(id: number) {
    await run(async () => { await applyProposal(id, 'approve') })
  }

  async function doReject(id: number) {
    await run(async () => { await applyProposal(id, 'reject') })
  }

  async function doApproveEdited(id: number) {
    await run(async () => {
      await applyProposal(id, 'approve', editText)
      setEditId(null)
    })
  }

  async function doConfirm(entryId: number) {
    await run(async () => { await confirmRegistry(entryId) })
  }

  async function doRequestDelete(
    path: string,
    section: string | null,
  ) {
    await run(async () => { await requestDeleteRegistry(path, section) })
  }

  function toggleScope(scope: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(scope) ? next.delete(scope) : next.add(scope)
      return next
    })
  }

  return (
    <div className="memory-page">
      {/* Stats */}
      <div className="stats">
        <div className="stat">
          <div className="n">{pendCount}</div>
          <div className="l">待批复提案</div>
        </div>
        <div className="stat">
          <div className="n">{totalReg}</div>
          <div className="l">记忆条目</div>
        </div>
        <div className="stat">
          <div className="n">{scopeCount}</div>
          <div className="l">范围</div>
        </div>
      </div>

      <div
        style={{
          fontSize: 12,
          color: 'var(--muted)',
          marginBottom: 8,
        }}
      >
        更新于 {lastUpdate}
      </div>

      {error && (
        <div className="error-banner">
          ⚠ 连不上服务器
          <span className="btn" style={{ marginLeft: 8 }} onClick={load}>
            重试
          </span>
        </div>
      )}

      {/* Proposals section */}
      <div className="section-title">
        📋 待批复提案{' '}
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>
          ({pendCount})
        </span>
      </div>

      {!proposals || proposals.proposals.length === 0 ? (
        <div className="empty">暂无待批复提案 🎉</div>
      ) : (
        proposals.proposals.map((p) => {
          const conflicts = Array.isArray(p.conflicts)
            ? p.conflicts
            : []
          return (
            <div
              key={p.id}
              className={`proposal-card ${conflicts.length ? 'conflict' : ''}`}
            >
              <div className="proposal-header">
                <span
                  className={`action-tag action-${p.action}`}
                >
                  {ACTION_LABELS[p.action] || p.action}
                </span>
                <span className="scope-tag">
                  {p.scope}
                </span>
                <span className="proposal-title">
                  {p.title}
                </span>
                <span
                  style={{
                    fontSize: 11,
                    color: 'var(--muted)',
                  }}
                >
                  {fmt(p.created_at)}
                </span>
              </div>

              <div className="proposal-meta">
                {p.target_path && (
                  <span
                    style={{
                      fontSize: 11,
                      color: 'var(--muted)',
                    }}
                  >
                    📁 {p.target_path}
                  </span>
                )}
                {p.target_section && (
                  <span
                    style={{
                      fontSize: 11,
                      color: 'var(--muted)',
                    }}
                  >
                    📍 {p.target_section}
                  </span>
                )}
              </div>

              {p.reason && (
                <div
                  style={{
                    fontSize: 12,
                    color: 'var(--muted)',
                    marginBottom: 6,
                  }}
                >
                  💡 {p.reason}
                </div>
              )}

              {conflicts.map((c, i) => (
                <div key={i} className="conflict-alert">
                  ⚠ 冲突: {c.summary || ''} (registry #
                  {c.registry_id})
                </div>
              ))}

              <div className="proposal-body">
                {(p.content || '').slice(0, 800)}
                {(p.content || '').length > 800 ? '…' : ''}
              </div>

              <div className={`proposal-actions${busy ? ' busy' : ''}`}>
                <span
                  className="btn success"
                  onClick={() => doApprove(p.id)}
                >
                  ✅ 批复
                </span>
                <span
                  className="btn danger"
                  onClick={() => doReject(p.id)}
                >
                  ❌ 驳回
                </span>
                <span
                  className="btn"
                  onClick={() => {
                    setEditId(
                      editId === p.id ? null : p.id,
                    )
                    setEditText(p.content || '')
                  }}
                >
                  ✏️ 编辑
                </span>
              </div>

              {editId === p.id && (
                <div style={{ marginTop: 8 }}>
                  <textarea
                    className="edit-area"
                    value={editText}
                    onChange={(e) =>
                      setEditText(e.target.value)
                    }
                  />
                  <span
                    className="btn success"
                    onClick={() => doApproveEdited(p.id)}
                  >
                    ✅ 批复修改后内容
                  </span>
                </div>
              )}
            </div>
          )
        })
      )}

      {/* Registry section */}
      <div className="section-title">
        📚 记忆清单{' '}
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>
          ({totalReg} 条)
        </span>
      </div>

      {/* Scope filter chips */}
      <div className="chips">
        <span
          className={`chip ${memScope === 'all' ? 'on' : ''}`}
          onClick={() => setMemScope('all')}
        >
          全部
        </span>
        {Object.keys(regByScope)
          .sort()
          .map((s) => (
            <span
              key={s}
              className={`chip ${memScope === s ? 'on' : ''}`}
              onClick={() => setMemScope(s)}
            >
              {scopeLabels[s]}
            </span>
          ))}
      </div>

      {Object.keys(regByScope)
        .sort()
        .map((scope) => {
          if (memScope !== 'all' && memScope !== scope)
            return null
          const items = regByScope[scope]
          if (!items || items.length === 0) return null

          const avgW = (
            items.reduce(
              (s, i) => s + (i.effective_weight || 0),
              0,
            ) / items.length
          ).toFixed(3)
          const isExpanded = expanded.has(scope)

          return (
            <div
              key={scope}
              style={{ marginBottom: 8 }}
            >
              <div
                className="scope-header"
                onClick={() => toggleScope(scope)}
              >
                <span
                  className={`arrow${isExpanded ? ' open' : ''}`}
                >
                  ▶
                </span>
                {scopeLabels[scope] || scope}
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 400,
                  }}
                >
                  · {items.length} 条 · 均权 {avgW}
                </span>
              </div>

              {isExpanded && (
                <div>
                  {items.map((e) => (
                    <RegistryRow
                      key={e.id}
                      entry={e}
                      hasProp={propRegIds.has(e.id)}
                      busy={busy}
                      onConfirm={doConfirm}
                      onDelete={doRequestDelete}
                    />
                  ))}
                </div>
              )}
            </div>
          )
        })}
    </div>
  )
}

function RegistryRow({
  entry,
  hasProp,
  busy,
  onConfirm,
  onDelete,
}: {
  entry: RegistryEntry
  hasProp: boolean
  busy?: boolean
  onConfirm: (id: number) => void
  onDelete: (path: string, section: string | null) => void
}) {
  const w = entry.effective_weight || 0
  const ws = entry.weight_status
  const wPct = Math.min(100, Math.round(w * 100))
  const wCls =
    ws === 'healthy'
      ? 'weight-healthy'
      : ws === 'warning'
        ? 'weight-warning'
        : 'weight-critical'
  const hdr =
    entry.section_heading ||
    entry.file_path.split('/').pop() ||
    '(整文件)'
  const atCap = (entry.base_weight || 0) >= 1.0

  return (
    <div className={`registry-item ${ws}`}>
      <div className="weight-bar">
        <div
          className={`weight-fill ${wCls}`}
          style={{ width: `${wPct}%` }}
        />
      </div>
      <span
        className="weight-num"
        style={{
          color:
            ws === 'healthy'
              ? 'var(--green)'
              : ws === 'warning'
                ? 'var(--yellow)'
                : 'var(--red)',
        }}
      >
        {w.toFixed(3)}
      </span>
      <div className="reg-info">
        <span>{hdr}</span>
        <div className="reg-path">
          {entry.file_path} · bw=
          {(entry.base_weight || 0).toFixed(3)} · 确认x
          {entry.confirmed_count || 0}
        </div>
      </div>
      <span className="reg-days">
        {entry.days_since_confirmed}天
      </span>
      {hasProp && (
        <span className={busy ? 'busy' : ''}>
          {atCap ? (
            <span
              className="btn"
              style={{
                fontSize: 11,
                padding: '3px 8px',
                opacity: 0.4,
                cursor: 'default',
              }}
              title={`已达上限 (bw=${(entry.base_weight ?? 0).toFixed(2)})`}
            >
              🔒
            </span>
          ) : (
            <span
              className="btn success"
              style={{
                fontSize: 11,
                padding: '3px 8px',
              }}
              onClick={() => onConfirm(entry.id)}
              title={`保留记忆：${(entry.base_weight ?? 0).toFixed(2)} → ${Math.min(1.0, (entry.base_weight ?? 0) + 0.2).toFixed(2)}（上限 1.00）`}
            >
              ✅
            </span>
          )}
          <span
            className="btn danger"
            style={{
              fontSize: 11,
              padding: '3px 8px',
            }}
            onClick={() =>
              onDelete(
                entry.file_path,
                entry.section_heading,
              )
            }
            title="标记删除"
          >
            🗑
          </span>
        </span>
      )}
    </div>
  )
}
