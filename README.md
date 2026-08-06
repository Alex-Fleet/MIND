# MIND: MIND Is Not Diary

用户级全局记忆系统，为 Claude Code 提供跨会话无缝记忆。
**自包含、可拷贝、路径相对化**——拷到任意位置跑一下 `install.py` 就能用。

## 目录结构

```
Memory Plugin/                 # 项目根（可放任意位置）
├── install.py                 # 安装器：一键注册 hook
├── requirements.txt           # Python 依赖（只有 requests）
├── config.example.json        # 配置模板（安装时复制为 config.json）
├── CLAUDE.example.md          # 辅助性程序设计规范模板（安装时复制为 CLAUDE.md）
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
- 创建 `CLAUDE.md`（辅助性程序设计规范兜底）
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

版本历史见独立文件 [CHANGELOG.md](CHANGELOG.md)（Keep a Changelog 标准，独立管理）。
