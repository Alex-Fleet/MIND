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

### 前置要求

- **Python 3.9+**（系统自带或 `brew install python3`）
- **Claude Code**（VS Code 扩展或 CLI）
- **LLM API 凭证**（DeepSeek 或 Anthropic，用于摘要生成）

### 步骤

```bash
# 1. 克隆项目
git clone <你的仓库地址>
cd Memory-Plugin

# 2. 安装依赖（就一个 requests，其余全是标准库）
pip3 install -r requirements.txt

# 3. 配置 API 凭证
#    编辑 ~/.claude/settings.json，在 env 段填入：
#    "ANTHROPIC_AUTH_TOKEN": "sk-你自己的",
#    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"

# 4. 一键安装（自动创建 config.json + CLAUDE.md + 注册 hook）
python3 install.py

# 5. 重启 Claude Code → 生效
#    新会话启动时自动注入记忆，每次回复后自动摄入+摘要
```

### 验证

重启 Claude Code 后，新会话的系统消息应包含 "MIND 记忆简报"。也可以启动看板确认：

```bash
python3 scripts/dashboard_server.py
# 打开 http://127.0.0.1:8765
```

## 手动命令

```bash
python3 scripts/ingest.py                       # 摄入新对话
python3 scripts/summarize.py --limit 5          # 生成 turn 摘要
python3 scripts/summarize.py --json --limit 5   # 结构化输出（hook 用）
python3 scripts/inject.py --json-output         # 查看注入的 systemMessage
python3 scripts/digest.py --check --json        # 检查并生成日报/月报
python3 scripts/recall.py "关键词"               # 手动回忆
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
