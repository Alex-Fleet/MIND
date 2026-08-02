# MIND: MIND Is Not Diary

用户级全局记忆系统，为 Claude Code 提供跨会话无缝记忆。
**自包含、可拷贝、路径相对化**——拷到任意位置跑一下 `install.py` 就能用。

## 目录结构

```
Memory Plugin/                 # 项目根（可放任意位置）
├── install.py                 # 安装器：一键注册 hook
├── requirements.txt           # Python 依赖（只有 requests）
├── config.example.json        # 配置模板（安装时复制为 config.json）
├── CLAUDE.example.md          # 铁律模板（安装时复制为 CLAUDE.md）
├── ARCHITECTURE.md            # 架构与数据流
├── .gitignore                 # 分享边界（挡掉私密 data/ + config + CLAUDE.md）
├── scripts/                   # 核心脚本
│   ├── ingest.py              # JSONL → SQLite
│   ├── summarize.py           # turn 摘要（LLM）
│   ├── digest.py              # 日报+月报（LLM）
│   ├── inject.py              # 重建注入上下文
│   ├── recall.py              # 手动回忆 CLI
│   ├── migrate.py             # 旧数据迁移（只跑一次）
│   ├── store.py               # SQLite CRUD
│   ├── config.py              # 配置加载（BASE_DIR 相对化 + DATA_DIR 可配）
│   ├── llm_utils.py           # LLM 调用 + JSON 解析（致命/瞬时错误分流）
│   └── dashboard_server.py    # 看板本地服务器（只读 DB，绑 127.0.0.1）
├── hooks/
│   ├── on_stop.py             # Stop hook（每次回复完触发）
│   └── on_session_start.py    # SessionStart hook（必须快，不碰 LLM）
├── dashboard/
│   └── index.html             # 看板前端（暗色时间线，轮询 /api/feed）
└── data/                      # 🔒 私密运行时数据（.gitignore 挡掉，不进分享）
    ├── db/nailong.db          # SQLite 引擎
    ├── archive/               # 永久存档
    │   ├── turns/  daily/  monthly/
    │   ├── legacy-memories/   # 旧系统记忆
    │   └── old/               # 旧 memory.db 副本
    └── injected/              # 注入上下文（prefs.md / brief.md）
```

代码(`BASE_DIR`) 与 数据(`DATA_DIR`) 解耦：`DATA_DIR` 默认 `项目根/data`，可用 `config.json` 的
`data_dir` 或环境变量 `NAILONG_DATA_DIR` 覆盖。

## 安装

### 你需要的

- **Python 3.10+** — 代码用了 `str | None` 语法，3.9 会直接报错；mac 自带版本可能不足，建议 `brew install python3`
- **VS Code + Claude Code 扩展**
- **DeepSeek API 密钥**（或 Anthropic）

### 四步装完

```bash
# 1. 拿到代码
git clone https://github.com/Alex-Fleet/MIND.git
cd MIND

# 2. 装依赖（就一个 requests，其它全是 Python 自带）
pip3 install -r requirements.txt

# 3. 配 API 密钥
#    打开 ~/.claude/settings.json，在 env 段填你的密钥：
#    "ANTHROPIC_AUTH_TOKEN": "sk-xxxx",
#    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"

# 4. 一键安装
python3 install.py
```

`install.py` 会自动：

- 创建 `config.json`（从模板）
- 创建 `CLAUDE.md`（铁律兜底）
- 注册 3 个 hook（Stop / SessionStart / UserPromptSubmit）到 `~/.claude/settings.json`
- 备份你的旧 settings.json

装完**重启 Claude Code** 就生效。新会话启动时注入记忆，每次回复后自动记录。

### 验证

问 Claude："你知道日报吗？" 如果能看到最近工作动态，就成功了。

也可以开看板：

```bash
python3 scripts/dashboard_server.py
# 打开 http://127.0.0.1:8765
```

### 常见问题

**"装完没反应"** → 确认重启了 Claude Code（关掉 VS Code 重开）。hook 要新会话才触发。

**"日报是空的"** → 正常。记忆是慢慢积累的，用几轮后回来看看板。

**"hook 报错"** → 检查 `data/logs/` 下的日志；确认 API 密钥有效。

## 手动命令

```bash
python3 scripts/ingest.py                       # 摄入新对话
python3 scripts/summarize.py --limit 5          # 生成 turn 摘要
python3 scripts/summarize.py --json --limit 5   # 结构化输出（hook 用）
python3 scripts/inject.py --json-output         # 查看注入的 systemMessage
python3 scripts/digest.py --check --json        # 检查并生成日报/月报
python3 scripts/recall.py "关键词"               # 手动回忆
python3 scripts/backup.py                       # 数据库备份（在线快照，保留7份）
python3 -m pytest tests/                        # 跑全部测试（需 pip install -r requirements-dev.txt）
pip-audit -r requirements.txt                   # 依赖安全审计（需 pip install pip-audit）
python3 scripts/dashboard_server.py             # 启看板 → http://127.0.0.1:8765
```

## 工作原理

```
每 turn（Stop hook，回复完触发，timeout 120s）:
  ingest → summarize(turn, LLM) → digest(check) → systemMessage + 日报/月报通知

新会话（SessionStart hook，必须快，撞 60s 硬上限会卡死）:
  ingest(快) → inject(只读DB) → systemMessage
  ＋ 后台 detached 补漏摘要（不阻塞启动）
```

⚠️ **SessionStart 铁律**：绝不在会话启动时同步调 LLM。Claude Code 对 SessionStart 有约
60s 的初始化硬上限，同步跑摘要会导致 `Subprocess initialization did not complete` 卡死。
摘要是 Stop hook 每轮的活，积压靠后台补漏。

## Changelog

### v1.7.0 — schema 版本化 + 依赖锁定与安全审计

- **Schema 版本化**：`PRAGMA user_version` 记录表结构版本（`SCHEMA_VERSION` + 表驱动迁移表 `_MIGRATIONS`）；旧库自动升级、用旧版代码打开新版库会拒绝并提示升级
- **依赖安全**：requests 2.31.0 → **2.33.0**（修复 3 个已知漏洞，pip-audit 扫描 **0 漏洞**）；全部依赖精确锁版本；新增 `pip-audit -r requirements.txt` 审计命令（见"手动常用命令"）

### v1.6.0 — 检查单审计修复：WAL/日志/备份/测试/API 语义/幂等

- **数据安全**：SQLite 开 WAL + busy_timeout（崩溃不损坏、读写不互堵）；新增 `scripts/backup.py` 在线一致性备份（保留 7 份，`VACUUM INTO` 不锁库）
- **可观测性**：loguru 统一日志落盘 `data/logs/mind.log`（级别+时间戳、1MB 轮转、保留 5 份，不污染 hook 协议输出）；后台补漏摘要失败不再静默（落盘 summarize.log）
- **测试体系**：pytest 全量（原 test_projects + 新增 test_store / test_api）；`requirements-dev.txt` 声明测试依赖
- **API/安全**：未知 `/api/*` 返 404 JSON、坏 JSON 返 400、`/assets/` 路径穿越修复（403）、500 不回内部细节
- **幂等**：`apply_proposal` 已批过直接返回（不重复写记忆/加权重）、`confirm` 30s 防重；前端三页加失败态+重试按钮、写操作防重复提交、ErrorBoundary 兜底白屏
- **兼容**：README 声明 Python 3.9+ → **3.10+**（代码用 `str | None` 语法，3.9 会报错）

### v1.5.2 — 关记忆衰减 + 检查单索引注入 + update 合并修复

- **关闭记忆衰减**：`effective_weight` 不再按艾宾浩斯曲线衰减（恒等于 base_weight），`decay_check` 不再产出删除/降级提案；registry 全部 base_weight 统一 0.6
- **检查单索引注入**：新增 `checklists-index.md` 注入 SessionStart——每次会话告知 LLM 存在 11 份工程检查单（一行定位 + 路径 + 何时用/不用），内容按需 Read，demo/一次性项目可忽略
- **update 提案合并修复**：`apply_proposal` 的 update 从"整节替换"改为 `_merge_section` 按 `###` 子节粒度合并——保留原内容、更新同名子节、追加新子节，不再丢数据（原缺陷曾冲掉 iron-rules 架构节）

### v1.5.1 — checklists 私有化：加入 .gitignore 不进 git

- **checklists 标记私有**：`memory/global/checklists/` 加入 `.gitignore`，11 份工程检查单（含底层原理/工程实践/开源选型）从 git 跟踪移除，仅本地保存——内容含个人思考与经验，不与代码分享包混在一起

### v1.5.0 — 工程知识库：检查单补底层原理 + 工程实践 + 开源选型

- **checklists 知识库扩充**：11 份检查单（data-concurrency / async-errors / observability / resources / security / compatibility / testing / api-design / frontend / reuse / opensource）各补三大块
- **底层原理**：每板块揭示概念本质（WAL/ACID/MVCC、事件循环/状态机/背压、Little's Law、B+Tree/池化/GC、信任边界/STRIDE、SemVer/Postel、属性测试收缩、幂等数学、虚拟 DOM/渲染管线、DIP/供应链、Redis/SQLite 内部机制）
- **工程实践手段**：每个原理配落地动作（参数化、tenacity/pybreaker、EXPLAIN、keyset 分页、CSP、feature flag、hypothesis、Idempotency-Key、Lighthouse 等）
- **推荐开源项目**：每板块列跨语言候选 + 多维优劣（成熟度/性能/学习成本/生态/风险/适用场景），含同类替代正面对比表

### v1.4.6 — install.py 跟上拆分 + 安装教程重写

- **install.py 修复**：旧版只注册 2 个单条 hook，现在注册 Stop + SessionStart(9条拆分) + UserPromptSubmit，与最新 hook 配置一致
- **安装教程重写**：三步傻瓜式（pip → 配密钥 → install.py → 重启），加常见问题解答
- 新版 `_build_session_start_entry()` 自动生成含 8 条 `inject --section` 的完整 SessionStart 配置

### v1.4.5 — SessionStart hook 拆分：绕开 10KB 输出上限

- **hook 拆分**：SessionStart 从单条 48KB 命令拆为 9 条（on_session_start + 8 条 inject --section），每条独立 10KB 预算，合计 ~37KB 全部注入上下文，不再被截断
- **inject.py --section**：新增 `--section`（global/project/turns/dailies/monthlies）、`--file`（单文件输出）、`--limit`（条数控制）；重构为独立 section builder，字节级 `_safe_truncate` 在 `##` 段落边界截断
- **项目隔离**：section 命令从 stdin SessionStart JSON 自动提取项目 slug，无需 `--project` 传参
- `on_session_start.py` 不再输出注入内容，只做 ingest + dashboard + 后台补漏

### v1.4.4 — 看板 TS+React+Vite 重构 + 项目管理 UX 改进

- **看板重构**：dashboard 从原生 JS 重写为 TypeScript + React 19 + Vite 6，三页面（时间线/记忆审核/项目管理）统一 SPA 导航
- **时间线修复**：React reconciliation 导致筛选切换后列表不更新——加动态 key 强制 remount；API `build_feed()` 移除 LIMIT 1000（数据从 1084 → 5256 条恢复）
- **项目管理改进**：未分配窗口与长期项目卡片 CSS columns 瀑布流叠放，默认收起为紧凑卡片，拖入 slug 自动展开；一次性/归档默认折叠
- **后端修复**：`propose_memories` 日志不再丢 DEVNULL，输出到 `logs/propose.log`

### v1.4.3 — DeepSeek API 瞬时异常容错加固

- **call_llm 空响应重试**：HTTP 200 但 body 为空不再直接放弃，改为走重试循环（最多 3 次指数退避）。7/25 DeepSeek 抽风返回空 body 导致 11 对全失败—此改修复根因。
- **summarize 失败退避**：连续失败 ≥2 次后 `sleep(2)`，给 API 恢复窗口，降低熔断概率。

### v1.4.2 — 日报日切点改为北京时间 04:00

- **日切偏移**：`date(t.timestamp)` → `date(t.timestamp, '+4 hours')`，日切点从 UTC 00:00 改为北京时间 04:00。凌晨 4 点前的对话归前一天，4 点后归当天。
- 影响范围：`get_turn_summaries_for_date`、`get_daily_reports_in_window`、`get_missing_daily_dates`、`today` 判定。

### v1.4.1 — 铁律扩充：拒绝 Demo 思维

- **新增铁律"拒绝 Demo 思维"**：不接受"现在能用，遇到情况再说"。设计时考虑边界，实现时处理异常。用户要求 quick hack 时主动停下提示风险。
- 同步更新 `CLAUDE.md`、`iron-rules.md`、`user-iron-rules.md` 三处。

### v1.4.0 — 每轮铁律注入：UserPromptSubmit hook + 全局记忆扩充

- **每轮铁律注入**：新增 `hooks/on_prompt.py`，通过 `UserPromptSubmit` hook 每次用户按回车注入 `iron-rules.md`（~6KB），compact 后同样生效，解决 system-reminder 被冲淡后规则丢失的根因
- **全局记忆三项扩充**：`memory/global/iron-rules.md` 新增——调研/搜索优先（GitHub 不可达立即停下）、Git 分支策略（feature/重构开 branch，合并 main 用户主导）、用户违背记忆时停下确认
- **注入链条梳理**：明确 Claude Code 原生层（CLAUDE.md/MEMORY.md 永久可见）与 MIND 注入层（system-reminder 瞬态）的边界，UserPromptSubmit 填补了每轮规则刷新缺口

### v1.3.3 — 系统噪音与延续型分流

- **噪音/延续分流**：`build_turn_pairs` 返回值从 `(pairs, merged_keys)` 拆为 `(pairs, merged_keys, noise_keys)`
- **系统噪音独立标记**：compact 注入、`<local-command-stdout>` 等系统消息走 `validity="invalid"`、`title="[噪音]"`，不再混入 merged
- **中断内容智能保留**：`[Request interrupted by user]` 从 `_SYSTEM_NOISE_RE` 移除，交给 `_classify_noise()` 提取后续真实内容（≥8 字符放行）
- **前端 merged 说明修正**：删除"或系统消息"误导文字

### v1.3.2 — 有效性分类修复：中断内容保留 + compact 漏网 + 子代理合并

- **用户中断不再丢内容**：`_classify_noise` 检测到 `[Request interrupted by user]` 后提取标记后的真实用户输入（≥8 字符），放行给 L2 摘要，不再整体标 invalid
- **compact 摘要不再漏网**：`_is_system_noise` 新增 "This session is being continued..." 和 "Primary Request and Intent:" 两种 compact 格式匹配
- **子代理反馈后内容不丢失**：`build_turn_pairs` 中系统噪音 turn 直接跳过（不生成 pair），后续延续型输入合并到前一个真实 pair，而非被噪音对吞噬
- **merged turn 看板说明**：前端 merged turn 标题显示"(已合并到上一轮)"，展开后显示合并原因说明

### v1.3.1 — 记忆生命周期管理 + 看板审核面板 + 删除流程修复

- **记忆自动提案**：`propose_memories.py` 从日报扫描可复用知识，LLM 三步分析（Scan→Compare→Propose），≥2 次出现的跨项目模式才生成提案，写入 `memory_proposals` 表待人工批复
- **艾宾浩斯权重模型**：`memory_registry` 表追踪每条记忆，`w_effective = base_weight × e^(-days / (30 × base_weight))`，强记忆慢衰减；权重只用于淘汰建议，不影响注入
- **看板记忆审核面板**：待批复提案卡片（批复/驳回/编辑）+ 记忆清单（scope 筛选、分组折叠、权重进度条、确认/删除）；确认上限 1.0 防重复刷权重
- **衰减淘汰**：w<0.15 自动提案删除 → 人工批复后才执行，**无静默删除**
- **删除流程修复**：批复 delete 提案后从 `memory/` 文件真实移除被删章节 + `inject.py` registry 兜底校验
- **精度统一**：`effective_weight` 后端 4 位、前端显示 3 位小数
- **新增表**：`memory_registry`、`memory_proposals`、`weight_log`；新增脚本：`memory_registry.py`、`propose_memories.py`

### v1.3.0 — 全局记忆文件化 + 项目隔离注入

- DB `preferences` 表迁至可编辑 `memory/global/*.md`（用户铁律/技能/偏好）
- 新增 `memory/projects/<id>/*.md` 项目专属记忆，Registry id 匹配注入
- `inject.py` 两层 glob（global 全量 + project 按 id），加文件不改代码
- 新增 `agenting-skills.md`（Agent 编排/RAG/LLM 边界/容错），泛化跨项目经验
- ARCHITECTURE.md 同步更新；`.gitignore` 挡掉记忆内容，`.example.md` 模板进 git

### v1.2.1 — README 清理：删冗余段落 + 整合早期 changelog

删除了与"安装"章节重复的"快速开始（拷给别人）"段落。将 v1.0.0 之前三个日期条目（2026-07-10/11/12）整合进 v1.0.0，形成统一的首版说明。

### v1.2.0 — 开源准备：依赖声明 + 安装器完善 + 脱敏

- 新增 `requirements.txt`（唯一外部依赖 `requests`）
- 新增 `CLAUDE.example.md` 模板
- `.gitignore` 新增 `config.json`、`CLAUDE.md`、`.claude/`、`*.pyc`
- `config.json` 和 `CLAUDE.md` 停止 git 追踪（脱敏），`install.py` 自动从模板创建
- `install.py` 新增依赖检查 + 自动创建用户配置文件
- README 新增"安装"章节（前置要求 → pip → 配置 → 验证）

### v1.1.0 — 有效性分类 + 延续合并 + 噪声覆盖 + 项目更名

**项目更名**：奶龙博士 → **MIND (MIND Is Not Diary)**。

**有效性三层分类**：Layer 1 确定性正则（`<task-notification>`、`<local-command-*>`、`<command-name>`、
`[Request interrupted` 等 7 类）+ Layer 2 LLM 判断（后取消低价值，简化为 valid/invalid/merged 三类）。
注入分级：invalid/merged 跳过，valid 完整注入。

**延续合并**：`summarize.py` 新增 `_is_continuation()` 正则检测（"好了吗""继续""嗯"等），延续型 turn 自动拼入
前序 pair 而非独立摘要，从源头消除碎片。merged 记录在看板项目名后标注 `已合并` 标识。

**噪声覆盖扩展**：L1 从 4 个模式扩至 7 个（补 `<local-command-caveat>`、`<local-command-stdout>`、
`<command-name>`、`<command-message>`），修复 `/compact` 斜杠命令和本地命令输出被误判为有效对话。
回填 176 条历史误判记录。

**看板升级**：`/api/feed` 返回 validity 字段，前端 `isNoise()` 读 DB 值，无效记录自动隐藏，
已合并记录可见+标识。项目列表过滤空 slug 消除"（未知）"。

**一次性脚本**：`scripts/classify_0714.py`、`scripts/classify_sample.py`。

### v1.0.0 — 首个正式版：从零构建到生产可用

**核心架构**：SQLite 引擎 + Markdown 存档；时间金字塔（turn→日报→月报）；Stop + SessionStart 双 hook。

**路径解耦与可移植**：系统从 `~/.claude/` 迁到独立工作区。`config.py` 的 `BASE_DIR` 相对化，新增可配 `DATA_DIR`；代码与数据分离。`install.py` 一键注册 hook，`config.example.json` 分享模板，`.gitignore` 挡掉私密数据。启动耗时从 >60s 降 0.3s（后台 detached 补漏替代同步 LLM 摘要）。

**看板 (dashboard)**：`dashboard_server.py`（Python 标准库，只读 DB，绑 `127.0.0.1`）+ 暗色前端。统一时间线（turn/日报/月报）、项目/类型筛选、噪音标注与一键隐藏。SessionStart 自动保活。

**摘要引擎修复**：`build_turn_pairs` 严格按会话边界配对，杜绝跨会话粘连。时间排序统一用真实对话时间 `turns.timestamp`。

**LLM 稳健性加固**：`llm_utils` 区分致命错误（余额/认证→秒退）与瞬时错误（超时→退避重试）；`summarize.py` 加熔断。全程幂等可续传。

**注入通道**：`inject.py` 输出纯文本（VS Code 扩展只吃 stdout）；项目隔离（`WHERE project IN (...)` 过滤，注入量从 69K 降至 40K 字）。

**日报重构**：时间分桶按真实对话时间；来源清单剥离独立 `-index.md`，正文只存 ~1K 字总结；文件名加项目 slug 防覆盖。

**迁移**：从旧系统吞入 9616 对话 + 10 记忆 + 13 偏好；清理 7-10 错误记忆（17 条错配摘要+1篇日报，LLM 全量重摘要）。
