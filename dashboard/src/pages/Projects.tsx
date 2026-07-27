import { useEffect, useState, useCallback } from 'react'
import type {
  ProjectDef,
  ProjectsResponse,
  SlugStats,
  SlugDetail,
} from '../api/types'
import {
  fetchProjects,
  saveProjects as apiSave,
  fetchSlugDetail,
  fetchFs,
} from '../api/client'
import { TYPE_LABELS } from '../api/types'

const pad = (n: number) => String(n).padStart(2, '0')
function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
const MODES = [
  ['all', '全部'],
  ['long_term', '长期'],
  ['one_off', '一次性'],
  ['archived', '归档'],
] as const

export default function Projects() {
  const [data, setData] = useState<ProjectsResponse | null>(null)
  const [mode, setMode] = useState('all')
  const [status, setStatus] = useState('')
  const [statusCls, setStatusCls] = useState('')
  const [detailCache, setDetailCache] = useState<
    Record<string, SlugDetail>
  >({})
  const [showOneOff, setShowOneOff] = useState(false)
  const [openSlugs, setOpenSlugs] = useState<Set<string>>(
    new Set(),
  )
  const [unassignedOpen, setUnassignedOpen] = useState(false)

  // Local mutable project state (drag target)
  const [projects, setProjects] = useState<ProjectDef[]>([])
  const [unassigned, setUnassigned] = useState<string[]>([])
  const [drag, setDrag] = useState<string | null>(null)

  // Folder picker
  const [picker, setPicker] = useState<{
    idx: number
    path: string
    dirs: { name: string; path: string }[]
    parent: string | null
  } | null>(null)

  const load = useCallback(async () => {
    try {
      const d = await fetchProjects()
      setData(d)
      setProjects(d.projects.map((p) => ({ ...p, slugs: [...p.slugs] })))
      setUnassigned([...d.unassigned])
      const src =
        d.source === 'projects.json'
          ? '已生效'
          : d.source === 'projects.draft.json'
            ? 'LLM 草稿（未生效）'
            : '空'
      setStatus(
        `来源：${src}　·　${d.projects.length} 个项目　·　未分配 ${d.unassigned.length}`,
      )
      setStatusCls('')
    } catch {
      setStatus('⚠ 连不上服务器')
      setStatusCls('err')
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const slugStats: Record<string, SlugStats> =
    data?.slug_stats ?? {}

  // ── DnD handlers ──
  function moveSlug(slug: string, toIdx: number) {
    setUnassigned((prev) => prev.filter((s) => s !== slug))
    setProjects((prev) =>
      prev.map((p) => ({
        ...p,
        slugs: p.slugs.filter((s) => s !== slug),
      })),
    )
    if (toIdx === -1) {
      setUnassigned((prev) => [...prev, slug])
    } else {
      setProjects((prev) =>
        prev.map((p, i) =>
          i === toIdx ? { ...p, slugs: [...p.slugs, slug] } : p,
        ),
      )
    }
  }

  function onDragStart(slug: string) {
    setDrag(slug)
  }

  function onDrop(colIdx: number) {
    if (drag !== null) {
      moveSlug(drag, colIdx)
    }
    if (colIdx === -1) {
      setUnassignedOpen(true)
    }
    setDrag(null)
  }

  // ── Card expand ──
  async function toggleSlug(slug: string) {
    setOpenSlugs((prev) => {
      const next = new Set(prev)
      if (next.has(slug)) {
        next.delete(slug)
        return next
      }
      next.add(slug)
      if (!detailCache[slug]) {
        fetchSlugDetail(slug)
          .then((d) =>
            setDetailCache((prev) => ({
              ...prev,
              [slug]: d,
            })),
          )
          .catch(() =>
            setDetailCache((prev) => ({
              ...prev,
              [slug]: {
                slug,
                samples: [],
                summaries: [],
              },
            })),
          )
      }
      return next
    })
  }

  // ── Folder picker ──
  async function browse(path: string, idx: number) {
    try {
      const d = await fetchFs(path)
      setPicker({
        idx,
        path: d.path,
        dirs: d.dirs,
        parent: d.parent,
      })
    } catch {
      setPicker((prev) =>
        prev
          ? { ...prev, dirs: [], parent: null }
          : null,
      )
    }
  }

  function openPicker(idx: number) {
    const p = projects[idx]
    const init = p.path || '~'
    browse(init, idx)
  }

  function closePicker() {
    setPicker(null)
  }

  function choosePath() {
    if (!picker || picker.idx < 0) return
    setProjects((prev) =>
      prev.map((p, i) =>
        i === picker.idx ? { ...p, path: picker.path } : p,
      ),
    )
    closePicker()
  }

  // ── Save ──
  async function save() {
    setStatus('保存中…')
    setStatusCls('')
    const payload = {
      projects: projects
        .filter((p) => p.label.trim() && p.slugs.length > 0)
        .map((p) => ({
          id: p.id,
          label: p.label.trim(),
          type: p.type,
          slugs: p.slugs,
          path: p.path || '',
        })),
    }
    try {
      const r = await apiSave(payload)
      if (r.ok) {
        setStatus('已保存 ✓ 已生效')
        setStatusCls('ok')
        load()
      } else {
        setStatus(
          '保存被拒（双向唯一校验）：\n' +
            (r.errors || []).join('\n'),
        )
        setStatusCls('err')
      }
    } catch (e) {
      setStatus('⚠ 保存失败：' + String(e))
      setStatusCls('err')
    }
  }

  if (!data) {
    return (
      <div className="projects-page">
        <div className="empty">加载中…</div>
      </div>
    )
  }

  // Partition projects by type for layout
  const longs: [number, ProjectDef][] = []
  const oneoffs: [number, ProjectDef][] = []
  const archs: [number, ProjectDef][] = []
  projects.forEach((p, i) => {
    if (p.type === 'long_term') longs.push([i, p])
    else if (p.type === 'archived') archs.push([i, p])
    else oneoffs.push([i, p])
  })

  function cardHTML(slug: string) {
    const st = slugStats[slug]
    const open = openSlugs.has(slug)
    const sample =
      !open && st?.samples?.[0]
        ? st.samples[0]
        : ''
    return (
      <div
        key={slug}
        className={`card ${drag === slug ? 'dragging' : ''}`}
        draggable
        data-slug={slug}
        onDragStart={() => onDragStart(slug)}
        onClick={(e) => {
          e.stopPropagation()
          toggleSlug(slug)
        }}
        title={slug}
      >
        <div className="bn">
          {st?.basename || slug}
        </div>
        <div className="st">
          {st?.sessions ?? 0} 会话 · {st?.turns ?? 0} turn ·{' '}
          {fmt(st?.first ?? null)}~{fmt(st?.last ?? null)}{' '}
          {open ? '▲' : '▾'}
        </div>
        {sample && (
          <div className="sm">{sample}</div>
        )}
        {open && <DetailSection slug={slug} cache={detailCache} />}
      </div>
    )
  }

  function colHTML(idx: number, p: ProjectDef, compact: boolean) {
    const cards = p.slugs.length
      ? p.slugs.map(cardHTML)
      : [<div key="empty" className="empty">（空，拖卡片到这里）</div>]
    return (
      <div
        key={p.id || idx}
        className={`project-col${compact ? ' compact' : ''}${drag ? ' drag-over' : ''}`}
        data-idx={idx}
        onDragOver={(e) => e.preventDefault()}
        onDrop={() => onDrop(idx)}
      >
        <div className="col-head">
          <input
            className="label"
            value={p.label}
            onChange={(e) => {
              const v = e.target.value
              setProjects((prev) =>
                prev.map((pp, i) =>
                  i === idx ? { ...pp, label: v } : pp,
                ),
              )
            }}
          />
          <button
            className="btn"
            style={{
              color: 'var(--muted)',
              border: 'none',
              background: 'none',
              fontSize: 13,
              padding: '2px 4px',
            }}
            disabled={p.slugs.length > 0}
            onClick={() =>
              setProjects((prev) =>
                prev.filter((_, i) => i !== idx),
              )
            }
            title={
              p.slugs.length ? '列非空，先移走卡片' : '删除项目'
            }
          >
            ✕
          </button>
        </div>
        <div className="col-meta">
          <select
            value={p.type}
            onChange={(e) =>
              setProjects((prev) =>
                prev.map((pp, i) =>
                  i === idx
                    ? {
                        ...pp,
                        type: e.target
                          .value as ProjectDef['type'],
                      }
                    : pp,
                ),
              )
            }
          >
            {Object.entries(TYPE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <span
            style={{
              fontSize: 11,
              color: 'var(--muted)',
              background: '#ffffff0a',
              padding: '1px 7px',
              borderRadius: 6,
            }}
          >
            {p.slugs.length} 个分身
          </span>
        </div>
        <div className="pathrow">
          <span
            className={`pathtxt ${p.path ? '' : 'unset'}`}
            title={p.path}
          >
            {p.path || '未设置文件夹'}
          </span>
          <button
            className="pathbtn"
            style={{
              border: '1px solid var(--border)',
              background: 'var(--bg)',
              color: 'var(--text)',
              borderRadius: 6,
              padding: '2px 7px',
              cursor: 'pointer',
              fontSize: 11,
              whiteSpace: 'nowrap',
            }}
            onClick={() => openPicker(idx)}
          >
            📁 选择
          </button>
        </div>
        <div className="cards">{cards}</div>
      </div>
    )
  }

  return (
    <div className="projects-page">
      <p className="tips">
        拖 slug 卡片进项目列合并（同项目的分身拖到一起）；点卡片展开看记忆内容判断归属；每个项目可设文件夹路径。改完点保存，做双向唯一校验。
      </p>

      {/* Mode chips */}
      <div className="chips">
        {MODES.map(([k, l]) => (
          <span
            key={k}
            className={`chip ${mode === k ? 'on' : ''}`}
            onClick={() => setMode(k)}
          >
            {l}
          </span>
        ))}
      </div>

      {/* Status */}
      <div className={`status ${statusCls}`}>{status}</div>

      {/* Unified grid: add button + unassigned + long-term */}
      {(mode === 'all' || mode === 'long_term') && (
        <div className="gridzone" style={{ marginBottom: 16 }}>
          {/* Add project button — compact tile */}
          <div className="compact-tile">
            <button
              className="btn"
              onClick={() =>
                setProjects((prev) => [
                  {
                    id: '',
                    label: '新项目',
                    type: 'long_term',
                    slugs: [],
                    path: '',
                  },
                  ...prev,
                ])
              }
            >
              ＋ 新建项目
            </button>
          </div>

          {/* Unassigned — collapsible tile */}
          <div
            className={
              unassignedOpen
                ? 'project-col unassigned'
                : 'compact-tile unassigned-tile'
            }
            data-idx={-1}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => onDrop(-1)}
          >
            {unassignedOpen ? (
              <>
                <div className="col-head">
                  <div
                    style={{
                      fontSize: 14,
                      fontWeight: 600,
                      flex: 1,
                    }}
                  >
                    未分配
                  </div>
                  <button
                    className="btn"
                    style={{
                      fontSize: 11,
                      padding: '2px 8px',
                    }}
                    onClick={() => setUnassignedOpen(false)}
                  >
                    收起
                  </button>
                </div>
                <div className="col-meta">
                  <span
                    style={{
                      fontSize: 11,
                      color: 'var(--muted)',
                      background: '#ffffff0a',
                      padding: '1px 7px',
                      borderRadius: 6,
                    }}
                  >
                    {unassigned.length}
                  </span>{' '}
                  个待归类
                </div>
                <div className="cards">
                  {unassigned.length === 0 ? (
                    <div className="empty">（空）</div>
                  ) : (
                    unassigned.map(cardHTML)
                  )}
                </div>
              </>
            ) : (
              <div
                className="tile-inner"
                onClick={() => setUnassignedOpen(true)}
              >
                <div className="tile-label">未分配</div>
                <div className="tile-count">
                  {unassigned.length} 个待归类
                </div>
              </div>
            )}
          </div>

          {/* Long-term project columns */}
          {longs.map(([i, p]) => colHTML(i, p, false))}
        </div>
      )}

      {/* One-off + Archived (collapsible) */}
      {((mode === 'all' || mode === 'one_off') && oneoffs.length > 0) ||
       ((mode === 'all' || mode === 'archived') && archs.length > 0) ? (
        <>
          <div
            className="zone-title"
            style={{ cursor: 'pointer', userSelect: 'none' }}
            onClick={() => setShowOneOff(!showOneOff)}
          >
            <span style={{ display: 'inline-block', transition: 'transform .2s', transform: showOneOff ? 'rotate(90deg)' : '' }}>▶</span>
            {' '}一次性 / 归档（{oneoffs.length + archs.length} 个项目）
          </div>
          {showOneOff && (
            <div className="gridzone">
              {(mode === 'all' || mode === 'one_off') &&
                oneoffs.map(([i, p]) =>
                  colHTML(i, p, true),
                )}
              {(mode === 'all' || mode === 'archived') &&
                archs.map(([i, p]) =>
                  colHTML(i, p, true),
                )}
            </div>
          )}
        </>
      ) : null}

      {/* Save button */}
      <div style={{ marginTop: 20 }}>
        <button className="btn primary" onClick={save}>
          💾 保存
        </button>
      </div>

      {/* Folder picker modal */}
      {picker && (
        <div className="overlay" onClick={closePicker}>
          <div
            className="modal"
            onClick={(e) => e.stopPropagation()}
          >
            <h4>📁 选择项目文件夹</h4>
            <div className="curpath">
              {picker.path}
            </div>
            <div className="dirlist">
              {picker.parent && (
                <div
                  className="diritem"
                  onClick={() => browse(picker.parent!, picker.idx)}
                >
                  📁 .. 上级目录
                </div>
              )}
              {picker.dirs.map((d) => (
                <div
                  key={d.path}
                  className="diritem"
                  onClick={() => browse(d.path, picker.idx)}
                >
                  📁 {d.name}
                </div>
              ))}
              {picker.dirs.length === 0 && (
                <div
                  className="diritem"
                  style={{
                    color: 'var(--muted)',
                    cursor: 'default',
                  }}
                >
                  （无子文件夹）
                </div>
              )}
            </div>
            <div className="modal-actions">
              <button
                className="btn"
                onClick={closePicker}
              >
                取消
              </button>
              <button
                className="btn primary"
                onClick={choosePath}
              >
                选定此文件夹
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function DetailSection({
  slug,
  cache,
}: {
  slug: string
  cache: Record<string, SlugDetail>
}) {
  const d = cache[slug]
  if (!d)
    return (
      <div className="detail-section">加载中…</div>
    )
  return (
    <div className="detail-section">
      {d.summaries && d.summaries.length > 0 && (
        <>
          <h5>📝 近期摘要</h5>
          {d.summaries.map((s, i) => (
            <div className="s" key={i}>
              <b>{s.title}</b>
              {s.summary || ''}
            </div>
          ))}
        </>
      )}
      {d.samples && d.samples.length > 0 && (
        <>
          <h5>👤 用户原话</h5>
          {d.samples.map((s, i) => (
            <div className="u" key={i}>
              {s.text}
            </div>
          ))}
        </>
      )}
      {(!d.summaries || d.summaries.length === 0) &&
        (!d.samples || d.samples.length === 0) && (
          <div className="empty">（无内容）</div>
        )}
    </div>
  )
}
