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

## 快速开始（也是"拷给别人"的步骤）

```bash
# 1. 把整个项目文件夹拷到任意位置（分享时用 git / 打包，data/ 已被 .gitignore 挡掉）

# 2. 注册 hook（自动写进 ~/.claude/settings.json，装前先备份）
python3 install.py

# 3. 填你自己的 API 凭证（若 settings.json 里还没有）
#    ~/.claude/settings.json 的 env:
#      "ANTHROPIC_AUTH_TOKEN": "sk-你自己的",
#      "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"

# 4. 重启 Claude Code → 生效
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

### v1.0.0 — 首个正式版：注入隔离 + 日报重构 + 看板保活

> 本轮为系统从"骨架"到"正式可用"的完整交付。所有已知 bug 已修复，注入链路已验证通过。

**项目隔离（Phase 2a）**：注入上下文按项目过滤。Hook 从 stdin 读取 `transcript_path` 提取 CC 项目 slug → 注册表查询兄弟 slug → store 层 `WHERE project IN (...)` 过滤。注入量从全局 69K 降至本项目 40K 字，验证通过：其他项目内容零泄漏。

**日报重构**：
- 时间分桶改用 `turns.timestamp`（真实对话时间），不再按 `summarized_at`（处理时间）
- 来源 turn 清单剥离到独立 `-index.md` 文件（渐进式披露），日报正文只存 ~1K 字总结
- 存档文件名加项目 slug（`{date}-{project}.md`），消除同日不同项目覆盖问题
- 注入层日报不再截断

**注入通道修复**：`inject.py` 输出从 JSON `{"systemMessage": "..."}` 改为纯文本。VS Code 扩展只吃 stdout 纯文本，JSON 包装会导致上下文不注入。经三轮金丝雀测试验证通过。

**7-10 错误记忆清理**：删除旧系统（配对错乱）产生的 17 条 turn 摘要 + 1 篇日报，LLM 全量重摘要并重建日报。

**时间线排序修复**：`dashboard_server.py` 和 `recall.py` 的排序键统一从 `summarized_at` 改为 `turns.timestamp`（真实对话时间）。

**看板保活**：SessionStart hook 自动检测 8765 端口，未监听则后台拉起 `dashboard_server.py`，开会话即看板在线。

**一次性脚本**：`scripts/rebuild_dailies.py`（日报全量重建）、`scripts/cleanup_0710.py`（7-10 错误记忆清理+重摘要）。

### 2026-07-12 — 看板上线 + 稳健性加固 + 数据重建
- **看板 (dashboard)**：新增 `dashboard_server.py`（Python 标准库，只读 DB，绑 127.0.0.1）
  + 暗色前端 `dashboard/index.html`。统一时间线（turn/日报/月报）、项目/类型筛选、
  无效记录标注与一键隐藏、手动刷新。补上 Claude Code 播报不渲染的缺口。
- **摘要错配修复（根因）**：`build_turn_pairs` 改为严格按**会话边界**配对，杜绝跨会话粘连；
  新增 `get_turns_ordered` / `get_summarized_keys`。修掉"用户输入与摘要张冠李戴"。
- **时间轴修复**：看板 turn 时间改用**真实对话时间**（`turns.timestamp`），不再显示"总结时刻"。
- **LLM 稳健性加固**：`llm_utils` 区分致命（401/402/403 余额/认证 → 抛 `LLMFatalError` 不重试）
  与瞬时（超时/断网/5xx → 退避重试）；`summarize.py` 加熔断（致命秒退、连续失败中止），
  全程幂等断点续。杜绝"余额不足空转一小时"。
- **数据重建**：清空并用 `deepseek-v4-pro` 全量重刷 turn 摘要（config 默认模型 → pro，timeout → 60）。
- **已知待办（下一步）**：注入尚未按项目隔离（跨项目污染）；日报月报按"总结时刻"分组、
  批量重建会塌，需改真实时间键；项目 slug 存在"分身"，计划做项目注册表
  （LLM 初步分类 + 用户拖拽调整 + 双向唯一校验）。

### 2026-07-11 — 迁移到独立工作区 + 可移植化
- **迁移**：系统从 `~/.claude/nailong_doctor_system/` 迁到独立工作区 `Memory Plugin/`。
- **路径解耦**：`config.py` 的 `BASE_DIR` 改为相对 `__file__`；新增可配 `DATA_DIR`；
  代码与数据分离，`data/` 走 `.gitignore`——**产品可分享，私密记忆留本地**。
- **可移植**：新增 `install.py`（一键注册 hook）、`config.example.json`（分享模板）、`.gitignore`。
- **修复 SessionStart 卡死**：去掉启动时同步 LLM 摘要（撞 60s 硬上限），改后台 detached 补漏，
  启动耗时从 >60s 降到 0.3s。
- **清理**：`on_stop.py` 去掉迁移期的诊断残留（心跳日志、临时测试通知）。
- **切换**：`settings.json` 的 Stop + SessionStart 两个 hook 指向新工作区；SessionStart 从
  旧系统 `~/.claude/memory/sync.py` 切成MIND自己的注入。
- **已知问题**：Claude Code 扩展 2.1.207 存在 Stop-hook `systemMessage` 不渲染的 bug
  （#50542，后端能解析、UI 不画）。对话内播报以此为准不可靠，可见播报改走看板(dashboard)。

### 2026-07-10 — 首次构建
- SQLite 引擎 + Markdown 存档；时间金字塔（turn→日报→月报）。
- Stop + SessionStart 双 hook；迁移旧系统 9616 对话 + 10 记忆 + 13 偏好。
