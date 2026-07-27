import { useEffect, useState, useCallback } from 'react'
import type { FeedItem, FeedResponse, Stats } from '../api/types'
import { fetchFeed } from '../api/client'
import { MODE_LABELS } from '../api/types'

// ── No heuristics ──
const NOISE_RE =
  /(用户中断|中断，无|无实际内容|无内容|空操作|空.?会话|空对话|compact|\/compact|对话压缩|会话压缩|记忆压缩|上下文压缩|内容压缩|历史压缩|压缩命令|压缩指令|压缩操作|压缩标记|压缩日志|压缩记录|压缩请求|压缩完成|压缩触发|压缩执行|纯粹回顾)/i

function isNoise(it: FeedItem): boolean {
  if (it.validity === 'invalid') return true
  if (
    it.validity === 'valid' ||
    it.validity === 'low_value' ||
    it.validity === 'merged'
  )
    return false
  return (
    NOISE_RE.test(it.title || '') ||
    NOISE_RE.test((it.body || '').slice(0, 40))
  )
}

function isMerged(it: FeedItem): boolean {
  return it.validity === 'merged'
}

const pad = (n: number) => String(n).padStart(2, '0')
function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

type TypeFilter = 'all' | 'turn' | 'daily' | 'monthly'

export default function Timeline() {
  const [data, setData] = useState<FeedResponse | null>(null)
  const [lastUpdate, setLastUpdate] = useState('')
  const [type, setType] = useState<TypeFilter>('all')
  const [projIdx, setProjIdx] = useState<number>(0)
  const [hideNonLong, setHideNonLong] = useState(true)
  const [hideNoise, setHideNoise] = useState(true)
  const [openKeys, setOpenKeys] = useState<Set<string>>(new Set())

  const load = useCallback(async () => {
    try {
      const d = await fetchFeed()
      setData(d)
      setLastUpdate(fmt(new Date().toISOString()))
    } catch {
      setLastUpdate('⚠ 连不上服务器')
    }
  }, [])

  useEffect(() => {
    load()
    const iv = setInterval(load, 12_000)
    return () => clearInterval(iv)
  }, [load])

  const toggle = (k: string) => {
    setOpenKeys((prev) => {
      const next = new Set(prev)
      next.has(k) ? next.delete(k) : next.add(k)
      return next
    })
  }

  if (!data) {
    return (
      <div className="timeline">
        <div className="empty">加载中…</div>
      </div>
    )
  }

  const { items, stats, projects } = data
  const project =
    projIdx === 0 ? null : projects[projIdx - 1]

  const filtered = items.filter((it) => {
    if (type !== 'all' && it.type !== type) return false
    if (project && it.project_name !== project.name) return false
    if (hideNonLong && it.project_type && it.project_type !== 'long_term')
      return false
    if (hideNoise && isNoise(it)) return false
    return true
  })

  const noiseCount = items.filter(isNoise).length

  return (
    <div className="timeline">
      {/* Stats */}
      <div className="stats">
        <StatsBar stats={stats} />
      </div>

      {/* Refresh */}
      <div
        style={{
          fontSize: 12,
          color: 'var(--muted)',
          marginBottom: 8,
        }}
      >
        更新于 {lastUpdate}
        <span style={{ marginLeft: 16, fontSize: 12, color: '#79c0ff' }}>
          {project ? `📌 ${project.name} (${filtered.length}条)` : `📌 全部项目 (${filtered.length}条)`}
        </span>
      </div>

      {/* Type filter */}

      <div className="chips" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {(
          ['all', 'turn', 'daily', 'monthly'] as TypeFilter[]
        ).map((t) => (
          <span
            key={t}
            className={`chip ${type === t ? 'on' : ''}`}
            onClick={() => setType(t)}
          >
            {t === 'all' ? '全部' : MODE_LABELS[t]}
          </span>
        ))}
      </div>

      {/* Project chips */}
      <div className="chips" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        <span
          className={`chip ${projIdx === 0 ? 'on' : ''}`}
          onClick={() => { setProjIdx(0); setType('all') }}
        >
          全部项目
        </span>
        {projects.map((p, i) => {
          if (hideNonLong && p.type !== 'long_term') return null
          return (
          <span
            key={i}
            className={`chip ${projIdx === i + 1 ? 'on' : ''}`}
            onClick={() => { setProjIdx(i + 1); setType('all') }}
          >
            {p.name}
          </span>
        )})}
      </div>

      {/* Toggles */}
      <div style={{ marginBottom: 12, display: 'flex', gap: 8 }}>
        <span
          className={`btn ${hideNoise ? 'on' : ''}`}
          onClick={() => setHideNoise(!hideNoise)}
        >
          {hideNoise ? `隐藏无效 (${noiseCount})` : `显示无效 (${noiseCount})`}
        </span>
        <span
          className={`btn ${hideNonLong ? 'on' : ''}`}
          onClick={() => setHideNonLong(!hideNonLong)}
        >
          {hideNonLong ? '隐藏非长期' : '显示非长期'}
        </span>
      </div>

      {/* Feed list */}
      {filtered.length === 0 ? (
        <div className="empty">没有匹配的记录</div>
      ) : (
        <div key={`list-${projIdx}-${type}-${hideNonLong}-${hideNoise}`}>
        {filtered.map((it) => {
          const key = (it.file || '') + it.ts + it.title
          const open = openKeys.has(key)
          const noise = isNoise(it)
          const merged = isMerged(it)

          return (
            <div
              key={key}
              className={`feed-row ${noise ? 'noise' : ''}`}
              onClick={() => toggle(key)}
            >
              <div className="row-head">
                <span className="row-time">{fmt(it.ts)}</span>
                <span className={`badge badge-${it.type}`}>
                  {MODE_LABELS[it.type]}
                </span>
                {noise && <span className="tag-noise">无效</span>}
                <span
                  className="row-proj"
                  title={it.project}
                >
                  {it.project_name}
                </span>
                {merged && (
                  <span className="tag-merged">已合并</span>
                )}
                <span className="row-title">
                  {merged
                      ? '(已合并到上一轮)'
                      : it.title}
                </span>
              </div>

              {open && (
                <>
                  <div className="row-body">
                    {it.user_input && (
                      <div className="row-user-input">
                        <b>👤 用户</b>
                        {it.user_input}
                      </div>
                    )}
                    {merged ? (
                      <div
                        style={{
                          color: 'var(--muted)',
                          fontStyle: 'italic',
                        }}
                      >
                        此轮为延续型输入（如"继续""好了吗"），已合并到上一轮对话。
                      </div>
                    ) : (
                      <>
                        {it.type === 'turn' && (
                          <b>📝 摘要</b>
                        )}
                        {it.body || ''}
                      </>
                    )}
                  </div>
                  {it.summarized_at && (
                    <div className="row-meta">
                      🕒 对话 {fmt(it.ts)}　·　总结于{' '}
                      {fmt(it.summarized_at)}
                    </div>
                  )}
                </>
              )}
            </div>
          )
        })}
        </div>
      )}
    </div>
  )
}

function StatsBar({ stats }: { stats: Stats }) {
  return (
    <>
      <div className="stat">
        <div className="n">{stats.projects}</div>
        <div className="l">项目</div>
      </div>
      <div className="stat">
        <div className="n">{stats.turn}</div>
        <div className="l">turn摘要</div>
      </div>
      <div className="stat">
        <div className="n">{stats.daily}</div>
        <div className="l">日报</div>
      </div>
      <div className="stat">
        <div className="n">{stats.monthly}</div>
        <div className="l">月报</div>
      </div>
      <div className="stat">
        <div className="n">{stats.backlog}</div>
        <div className="l">未摘要</div>
      </div>
      <div className="stat">
        <div
          className="n"
          style={{ fontSize: 13, paddingTop: 5 }}
        >
          {fmt(stats.last)}
        </div>
        <div className="l">最近活动</div>
      </div>
    </>
  )
}
