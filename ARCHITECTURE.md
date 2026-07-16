# 架构 — MIND

## 定位
用户级全局记忆系统，让 Claude Code 每次新会话都能像旧会话无缝衔接。
**中心库模型**：一个大脑装所有项目的记忆，靠 `project` 字段区分。

## 两层记忆（别混淆）

| 层 | 存哪 | 粒度 | 谁读 |
|----|------|------|------|
| **Claude Code 原生记忆** | `~/.claude/projects/<项目slug>/memory/*.md` | 每项目一个文件夹 | Claude Code 内建，按项目加载 |
| **MIND** | 本项目 `data/`（中心库 + 存档） | 全项目集中，`project` 字段区分 | MIND hook 注入 |

- **全局记忆（可编辑）** → `memory/global/*.md`（用户铁律、技能、偏好）+ `memory/projects/<id>/*.md`（项目专属背景/经验），`inject.py` 两层 glob 注入。`CLAUDE.md` 做静态兜底。
- **项目摘要** → MIND中心库 `turn_summaries` / `daily_reports` / `monthly_reports`，`project` 字段区分。

### `memory/` 目录结构

```
memory/
├── global/                    # 全局注入（所有项目）
│   ├── user-profile.md        # 用户介绍 + 开发环境
│   ├── iron-rules.md          # 铁律（10 域 × 阶段）
│   ├── agenting-skills.md     # Agent 编排技能
│   └── coding-philosophy.md   # 代码风格 + Prompt 哲学
├── projects/                  # 按项目 ID 匹配注入
│   └── <project-id>/
│       └── context.md         # 项目背景 / 关键决策 / 经验
├── user-profile.example.md    # 模板（进 git）
├── iron-rules.example.md
└── project-context.example.md
```

- `global/` 下所有 `.md` 全局注入；`projects/<id>/` 仅当 Registry 解析到该 id 时注入。
- heading 从文件第一行 `# 标题` 提取，`inject.py` 不硬编码文件名。
- 向下兼容：若 `memory/global/` 不存在，回退到旧扁平结构。

## 数据流 / 依赖链

```
① 原始聊天 JSONL（Claude Code 写的，MIND只读不拥有）
   ~/.claude/projects/<slug>/*.jsonl
        │  ingest.py（文件I/O，无LLM）
        ▼
② turns 表（中心库，seq 级去重 UNIQUE(session_id,seq)）
   data/db/nailong.db
        │  summarize.py（LLM，一问一答→一条摘要）
        ▼
③ turn_summaries（DB行） ──同时落一份──▶ data/archive/turns/<日期>/<会话id>-turn-N.md
        │  digest.py --daily（按天聚合）
        ▼
④ daily_reports（DB行） ──▶ data/archive/daily/<日期>.md
        │  digest.py --monthly（按月聚合）
        ▼
⑤ monthly_reports（DB行） ──▶ data/archive/monthly/<月>.md
        │  inject.py（按时间窗口取：近7天turn + 近30天日报 + 全部月报 + 偏好）
        ▼
⑥ systemMessage（SessionStart 注入：memory/ 文件 + 时间金字塔简报）
```

存档按**日期**分文件夹（非按项目），项目身份靠文件名前缀 + DB `project` 字段。

## 两个 Hook

| Hook | 触发 | 干什么 | 约束 |
|------|------|--------|------|
| **Stop** | 每次 Claude 回复完 | ingest → summarize(LLM) → digest(check) → systemMessage/通知 | timeout 120s |
| **SessionStart** | 新会话启动 | ingest → inject（只读DB）→ systemMessage；后台 detached 补漏摘要 | **必须 <60s**，绝不同步调 LLM |

## 看板 (Dashboard)

只读旁路，**不参与注入链路**：`dashboard_server.py`（Python 标准库 `ThreadingHTTPServer`，
以 `mode=ro` 只读打开 DB，绑 `127.0.0.1`）提供 `/api/feed`（统一时间线 + 统计 + 项目列表），
`dashboard/index.html` 暗色前端每 12s 轮询渲染。用途：补上 Claude Code 2.1.207
Stop-hook `systemMessage` 不渲染的缺口——让总结进度看得见。
- 时间线按**真实对话时间**（`turns.timestamp`）排序，而非"总结时刻"。
- 无效记录（中断/空操作）自动标注，可一键隐藏；每条 turn 保留用户原话 + 摘要。

## 代码 / 数据解耦（可移植性命根子）

- `BASE_DIR = Path(__file__).resolve().parent.parent` —— 项目在哪就用哪，不认死路径。
- `DATA_DIR` —— 默认 `BASE_DIR/data`；可用 `config.json:data_dir` 或 env `NAILONG_DATA_DIR` 覆盖。
- `PROJECTS_DIR = ~/.claude/projects` —— `~` 自动对应当前用户，天然跨用户可移植（摄入来源，不迁）。
- 密钥只从 env 读，永不进文件；`data/` 走 `.gitignore`。分享的是"代码+文档+安装器"，非你的记忆。

## 关键设计决策

- **SQLite 做引擎**：支持时间窗口查询、去重、索引；Markdown 做输出面（人可读、可 Read、可拷贝）。
- **seq 级去重**（非 session 级）：`UNIQUE(session_id, seq)`，经得起断点续传。
- **中心库**（非每项目一库）：跨项目查询方便，`project` 字段区分。
- **SessionStart 必须快**：Claude Code 有 ~60s 启动硬上限，同步 LLM 会卡死；摘要移到 Stop + 后台。
- **不依赖 langchain**：直接 requests 调 DeepSeek，旧系统靠 langchain 是没跑起来的根因之一。
- **双通道注入**：systemMessage（动态简报 + memory/ 文件）+ CLAUDE.md（静态铁律兜底）。
- **全局记忆文件化**：铁律/偏好/技能从 DB `preferences` 表迁至 `memory/` 可编辑 markdown。两层 glob（global + projects/）自动注入，加文件不改代码。
- **LLM 错误分流**：致命（余额/认证/权限 401/402/403）抛 `LLMFatalError` 立即中止整批，
  瞬时（断网/超时/5xx）退避重试；`summarize.py` 批处理加连续失败熔断。全程幂等、断点续，杜绝空转。

## 已知问题 / 路线图

- Claude Code 扩展 2.1.207：Stop-hook `systemMessage` 后端解析成功但 UI 不渲染（GitHub #50542）。
  → 对话内每轮播报不可靠；可见播报改走**看板 dashboard（已建）**。
- **注入未按项目隔离** ✅ 已修复（v1.3.0）：`inject.py` 两层 glob，global/ 全量注入 + projects/<id>/ 按 Registry id 匹配。
- **时间金字塔键错**：`digest.py` 按 `date(summarized_at)`（总结时刻）分组聚合日报/月报；
  批量重刷会把历史全糊进当天。需改为按**真实对话时间**（`turns.timestamp`）分组后再重建。
- **项目身份不稳（slug 分身）** ✅ 已修复（v1.3.0）：Registry + 双向唯一（bijection）校验，`scripts/projects.py` 管理 slug→id 映射。项目记忆按稳定 id 而非 slug 匹配。
- 注入体积偏大（当前 ~50KB/会话），后续需精简（月报永久留、近 7 天 turn 收紧）。
