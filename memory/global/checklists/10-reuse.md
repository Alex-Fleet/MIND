# 代码复用检查单

> 核心原则：手写代码 = 引入 bug。成熟开源库 = 几万人替你踩过坑。
> 每个技术决策先问一句："这个轮子已经有人造过了吗？"

---

## 复用决策速查

```
这个功能是核心差异化的吗？
    ├── 是 → 自己写，但要充分测试
    └── 否 → 别人写过成熟方案吗？
              ├── 是 → 直接用
              └── 否 → 重新评估：是不是想的方向错了？
```

**手写代码前必须满足至少一个条件：**
- 这个功能是产品的核心竞争力（差异化）
- 现有开源方案都经过了充分评估，确实不满足需求
- 你在写的是胶水代码（组合现有库），不是从零造轮子

**永远不要手写的轮子（除非你在做安全研究）：**
- 加密算法
- 密码哈希
- 认证协议（OAuth/JWT）
- SQL 查询构建器
- HTTP 客户端
- 日期时间处理
- 日志框架

---

## 一、数据持久化（对应 01-data-concurrency）

| 你需要的 | 不要手写 | 应该用 | 为什么 |
|---------|---------|--------|--------|
| 嵌入式数据库 | 自己写文件存储格式 | **SQLite**（Python 标准库 `sqlite3`） | WAL、事务、并发控制都替你做了 |
| ORM / 查询构建 | 拼 SQL 字符串 | **SQLAlchemy**（重量）/**peewee**（轻量） | 防注入、连接池、Schema 迁移 |
| Schema 迁移 | 手动改表结构 | **Alembic**（配 SQLAlchemy） | 版本化迁移、可回滚 |
| 缓存层 | 自己写 dict + 过期逻辑 | **Redis** + **redis-py** | 持久化、分布式、过期策略、原子操作 |
| 全文搜索 | SQLite LIKE '%xxx%' | **SQLite FTS5**（内置）/ **Elasticsearch**（重量） | 分词、排名、模糊匹配 |
| 数据校验 | `if not x: raise` | **Pydantic** | 类型校验、嵌套模型、自动错误信息 |

**反例（来自 MIND）：** 自己拼 SQL 字符串 → SQL 注入风险。MIND 已经用 `?` 占位符避开了，但用 ORM 可以进一步减少手写 SQL。

---

## 二、异步与错误处理（对应 02-async-errors）

| 你需要的 | 不要手写 | 应该用 | 为什么 |
|---------|---------|--------|--------|
| 重试逻辑 | `for i in range(3): try...except` | **tenacity** | 指数退避、重试条件、callback、日志 |
| 熔断器 | 自己写计数器 | **pybreaker** | 状态机（关闭→打开→半开）、超时自动恢复 |
| 限流 | 自己写时间窗口计数 | **limiter** / **ratelimit** | 多种算法（令牌桶/滑动窗口）、装饰器 |
| 异步任务队列 | `subprocess.Popen` 裸调 | **Celery**（重量）/ **RQ**（轻量，Redis）/ **arq**（async） | 任务持久化、重试、监控、优先级 |
| 超时控制 | `signal.alarm` | **func-timeout** / `asyncio.wait_for` | 跨平台、线程安全 |
| 后台任务调度 | cron + 手写脚本 | **APScheduler** / **schedule** | 程序内调度、持久化、时区处理 |

**反例（来自 MIND）：** `spawn_background_catchup()` 裸 `subprocess.Popen` → stderr 丢 DEVNULL、失败无感知。用任务队列（RQ/arq）可以内置重试+日志+监控。

---

## 三、可观测性与运维（对应 03-observability-ops）

| 你需要的 | 不要手写 | 应该用 | 为什么 |
|---------|---------|--------|--------|
| 结构化日志 | `print(f"xxx={yyy}")` | **loguru** / **structlog** | 自动时间戳、级别、轮转、JSON 输出、彩色 |
| 日志聚合 | grep 日志文件 | **Loki** + **Promtail**（轻量） | 集中存储、标签搜索、可视化 |
| 指标暴露 | 自己写 HTTP endpoint | **prometheus_client** | 标准格式、Grafana 直接吃、自带 histogram/summary |
| 健康检查 | `curl http://localhost` | **healthchecks**（SaaS）/ **pinggy** | 定时 ping、告警通知、失败静默期 |
| 进程守护 | nohup & | **supervisord** / **systemd** | 自动重启、日志管理、启停控制 |
| 配置管理 | 手写 config.json parse | **pydantic-settings** / **python-dotenv** | env 注入、类型校验、默认值 |

**决策建议（MIND 规模）：** `loguru` 替代 `print` 日志 + `APScheduler` 替代裸 cron + `supervisord` 守护 dashboard。不急于上 Prometheus/Grafana，数据量还小。

---

## 四、资源管理与性能（对应 04-resources-performance）

| 你需要的 | 不要手写 | 应该用 | 为什么 |
|---------|---------|--------|--------|
| 连接池 | 每次新建连接 | **SQLAlchemy 连接池**（内置）/ **DBUtils** | 复用、上限控制、超时、健康检查 |
| 内存 profiling | `print(sys.getsizeof())` | **memory_profiler** / **objgraph** | 逐行内存、对象引用链、泄漏定位 |
| 性能 profiling | `time.time()` 打点 | **py-spy**（采样）/ **cProfile**（内置）/ **scalene** | 不侵入代码、火焰图、CPU+内存+GPU |
| 慢查询分析 | 手动跑 SQL 计时 | **SQLite `EXPLAIN QUERY PLAN`** + **sqlite3 `profile`** | 内置工具，不需要外部依赖 |
| 虚拟滚动（前端） | 渲染全部 DOM | **react-window** / **@tanstack/virtual** | 只渲染可见区域，支持 10 万+ 条 |
| 资源限制 | 信任代码不会超 | **resource** 模块（Unix）/ **docker --memory** | 硬限制内存/CPU，超了就杀 |

**反例（来自 MIND）：** 看板 5256 条 feed 全量渲染 → 数据再涨会卡。直接上 `react-window` 虚拟滚动。

---

## 五、安全（对应 05-security）

| 你需要的 | 不要手写 | 应该用 | 为什么 |
|---------|---------|--------|--------|
| 密码哈希 | 自己写 hash+salt | **bcrypt** / **passlib** / **argon2-cffi** | 抗彩虹表、抗暴力、自动加盐 |
| JWT | 自己拼 base64 | **PyJWT** / **python-jose** | 签名验证、过期、算法协商 |
| 加密 | 自己写 AES 封装 | **cryptography** / **PyNaCl** | 正确实现、侧信道防护、算法推荐 |
| CSRF 防护 | 手写 token | **WTForms**（表单）/ FastAPI/Flask 内置 | 框架已集成 |
| 输入清洗 | `str.replace()` | **bleach**（HTML）/ **marshmallow**（API） | 白名单过滤、防 XSS |
| 依赖漏洞扫描 | 手动关注 CVE | **pip-audit** / **safety** | 自动对比 CVE 数据库 |
| 密钥管理 | env 文件明文 | **python-dotenv** + `.gitignore`；生产用云厂商 Secret Manager | 不进 git、不硬编码 |

**永远不要自己实现加密。** 即使你是密码学博士。用 `cryptography` 或 `PyNaCl`。

---

## 六、兼容性（对应 06-compatibility）

| 你需要的 | 不要手写 | 应该用 | 为什么 |
|---------|---------|--------|--------|
| 跨平台路径 | 字符串拼 `/` | **pathlib**（标准库） | 自动适配 OS 分隔符 |
| 跨平台换行 | `\n` 写死 | Python `open()` 默认 `\n` 统一处理 | 写入时自动转换 |
| 编码处理 | `str.encode('utf-8')` 到处写 | **charset-normalizer** / **ftfy** | 自动检测、修复乱码 |
| 环境变量管理 | `os.environ.get()` 散落各处 | **pydantic-settings** / **python-dotenv** | 集中管理、类型校验、默认值 |
| 时区处理 | `datetime.now()` | **zoneinfo**（3.9+）/ **pytz** / **python-dateutil** | 时区转换、夏令时、ISO 8601 |

---

## 七、测试（对应 07-testing）

| 你需要的 | 不要手写 | 应该用 | 为什么 |
|---------|---------|--------|--------|
| 测试框架 | 自己写 assert + main | **pytest** | fixture、参数化、插件生态、并行 |
| Mock | 手写假对象 | **pytest-mock** / **unittest.mock** | 自动记录调用、断言调用次数 |
| HTTP Mock | 手写假 API 服务 | **responses** / **httpx** + **pytest-httpx** | 拦截请求、匹配 URL、返回假响应 |
| 覆盖率 | 自己数行 | **coverage.py** + **pytest-cov** | HTML 报告、分支覆盖、CI 集成 |
| 模糊测试 | 手动试边界 | **hypothesis** | 自动生成边界用例、收缩最小失败案例 |
| 快照测试 | 手动对比输出 | **syrupy** / **snapshottest** | 自动 diff、一键更新 |

**反例（来自 MIND）：** 项目无测试框架。最小成本起步：`pytest` + `coverage.py`。

---

## 八、API 设计（对应 08-api-design）

| 你需要的 | 不要手写 | 应该用 | 为什么 |
|---------|---------|--------|--------|
| HTTP Server | `http.server` 手写路由 | **FastAPI**（async）/ **Flask**（同步） | 路由、中间件、校验、OpenAPI 自动生成 |
| API 文档 | 手写 markdown | **FastAPI 内置 OpenAPI** + Swagger UI | 代码即文档，零维护 |
| 请求校验 | `if "field" not in data` | **Pydantic**（FastAPI 内置） | 类型+约束声明式校验 |
| 序列化 | 手写 `json.dumps()` 到处 | **Pydantic** `.model_dump()` / **marshmallow** | 排除 None、别名、嵌套序列化 |
| HTTP 客户端 | `urllib.request` | **httpx** / **requests** | 超时、重试、连接池、async 支持 |
| CLI 参数解析 | `sys.argv` 手动解析 | **argparse**（标准库）/ **click** / **typer** | 自动 help、类型转换、子命令 |

**反例（来自 MIND）：** `dashboard_server.py` 用 `http.server` 手写路由 → 无自动文档、无请求校验。如果重构后端，优先 FastAPI。

---

## 九、前端（对应 09-frontend）

| 你需要的 | 不要手写 | 应该用 | 为什么 |
|---------|---------|--------|--------|
| UI 框架 | 原生 JS DOM 操作 | **React** / **Vue** | 组件化、状态管理、生态 |
| 构建工具 | 手写 HTML + `<script>` | **Vite** | HMR、打包、Tree Shaking、TS 支持 |
| 拖拽 | 原生 drag/drop API | **@dnd-kit**（React）/ **SortableJS** | 跨浏览器、触摸支持、动画、无障碍 |
| 虚拟滚动 | 渲染全量 DOM | **react-window** / **@tanstack/virtual** | 10 万+ 条不卡 |
| 表单 | 手写 state + validation | **React Hook Form** + **zod** | 性能好（非受控）、声明式校验 |
| CSS | 手写 CSS / 内联样式 | **Tailwind CSS** / **CSS Modules** | 设计约束、响应式、暗色模式 |
| 图表 | Canvas 手绘 / SVG 手写 | **Recharts** / **ECharts** / **D3.js** | 响应式、动画、无障碍、导出 |
| 状态管理 | 多层 props 传递 | **Zustand**（轻量）/ **Jotai** | 按需订阅、无 boilerplate |
| 路由 | 手写 hash router | **React Router** / **TanStack Router** | 嵌套路由、懒加载、类型安全 |
| 日期处理 | 手写 `Date` 格式化 | **date-fns** / **dayjs** | 时区、国际化、tree-shaking |

**反例（来自 MIND）：** 看板最初是 480 行原生 JS 单文件 → v1.4.4 迁到 React+Vite。这是正确的复用决策。

---

## 通用：不论什么项目都可以用的

| 类别 | 库 | 一句话 |
|------|-----|--------|
| Python 环境管理 | **uv** / **pipenv** / **poetry** | 替代裸 pip，锁版本、虚拟环境 |
| 代码格式化 | **ruff** / **black** + **isort** | 自动格式化，省掉代码风格争论 |
| 代码质量 | **ruff**（linter）/ **mypy**（类型检查） | 提交前自动检查 |
| Git hooks | **pre-commit** | 提交前自动跑 lint + format + test |
| Pre-commit 检查 | **pre-commit** 框架 | 管理 git hooks，团队统一 |
| 文档生成 | **MkDocs** + **Material** 主题 | Markdown → 漂亮文档站 |
| 环境变量 | **python-dotenv** | `.env` → `os.environ` |
| 进度条 | **tqdm** / **rich** | 长循环给用户进度反馈 |

---

## 复用决策反模式

```
❌ "这个太简单了不需要依赖"      → 简单的轮子是 bug 密度最高的轮子
❌ "我看不上这个库的 API"         → 包装一层适配，而不是重写
❌ "担心依赖太多"                 → 依赖不是成本，手写代码的 bug 才是
❌ "我要学习所以自己写"           → 读源码学习，不要生产环境手写
❌ "这个库太重了"                 → 对于一次性脚本可以，对于长期维护的系统不要
❌ "先手写，后面再换"             → 你不会换的。一开始就选对
```

## 选库标准（按优先级）

1. **活跃度**：最近 6 个月有 commit？issue 有人回复？
2. **Stars/使用者**：足够多人用 = 边缘 case 有人踩过了
3. **Bus Factor**：核心维护者有几个？如果主要作者突然消失，项目会死吗？**1-2 个人的项目 = 高风险，3+ 活跃维护者 = 健康。不做的代价：你依赖的库突然停止维护，安全漏洞无人修。**
4. **依赖数量**：本身依赖少 > 依赖多（减少供应链风险）
5. **许可证**：MIT/Apache 2.0 > GPL（商业友好）
6. **API 稳定性**：1.0+ 版本号 > 0.x beta

---

## 对应 MIND 的具体建议

| MIND 现状 | 问题 | 建议替换 |
|----------|------|---------|
| `print()` 写日志 | 无级别、无轮转、无结构化 | **loguru** — 一行替换 |
| `subprocess.Popen` 裸调后台任务 | 失败静默、无重试 | **APScheduler** 或 **arq** |
| `http.server` 手写路由 | 无文档、无校验 | 下次重构后端用 **FastAPI** |
| 手写 `time.time()` 打点 | 不可维护 | 性能关键路径加 **cProfile** |
| 无测试框架 | 无自动化验证 | **pytest** + **coverage** |
| 手动 `wc -c` 验证输出 | 不可重复 | **pytest** 断言 + **syrupy** 快照 |
| JSON `json.loads` 裸调 | 错误信息不友好 | **Pydantic** 模型校验 |

---

## 底层原理：手写 = 引入 bug

### 依赖反转 DIP：依赖抽象，不依赖实现

**核心思路**：高层代码（业务逻辑）不直接依赖具体库，而是依赖一个**抽象接口**。具体库实现这个接口。

```
不好： 业务代码直接 import requests → 换库 = 改业务代码
好：   业务代码依赖 http_client 抽象 → requests/httpx 只是它的一个实现
```

**为什么重要**：依赖方向反了，业务逻辑就被某个库绑架了。库升级改了 API、库停止维护、想换更优的库——都得动业务代码。

**洞察**：检查单"包装一层适配，而不是重写"——底层就是 DIP。把第三方库包一层薄接口（见工程实践），换库只动包装层，业务逻辑纹丝不动。

### 抽象泄漏：库的内部细节为什么会漏出来

**核心问题**：用了一个库，却感觉在写库的内部实现——这是抽象泄漏（Leaky Abstraction）。

**例子**：
- 用了 ORM 还得手写 SQL 调优
- 用了任务队列还得懂它的底层存储格式
- 用了虚拟滚动还得操心它内部的计算逻辑

**为什么会泄漏**：没有完美的抽象——抽象底下总有些东西藏不住。性能、异常、底层协议的特殊行为，最终会渗出来。

**工程含义**：选库时不要只看"它声明能做什么"，要看**它的抽象有多薄**。抽象越薄（越接近底层语义），泄漏越少；抽象越厚（承诺越多魔法），踩坑时越难排查。

**洞察**：这解释了为什么"手写 SQL 用 `?` 占位符"比"上 ORM"在某些场景更可靠——SQLAlchemy 的抽象在某些边缘场景（复杂查询、性能）会泄漏，这时你反而要懂底层 SQL。

### 供应链安全：left-pad 事件教会我们什么

**事件回顾（2016）**：npm 上一个 11 行的库 `left-pad` 被作者移除，导致依赖它的**成千上万个包**构建失败，整个 npm 生态瘫痪。全世界成千上万的项目一夜之间装不上依赖。

**教训**：
1. **依赖越深，风险越被放大**：一个不起眼的小库可能被无数项目间接依赖。它出问题，你的项目跟着出问题，而你甚至不知道依赖了它
2. **锁版本**：不锁版本，上游删包/发坏版本 = 你下次构建就坏
3. **最小依赖**：依赖数量是供应链风险敞口。一个 500 行能解决的事，不要为它引入 50MB 的依赖树

**洞察**：检查单"依赖本身少 > 依赖多"——底层是供应链风险。你选的每个库，它的依赖的依赖……都成了你系统的攻击面。

### 稳定依赖原则：稳定的才配被依赖

**核心思路**：能被别人依赖的东西，必须是**稳定**的——API 少变、行为可预期。易变的东西不该被依赖（因为依赖者会随你起舞）。

**推论**：
- 为什么 1.0+ 的库比 0.x 值得用？——1.0 承诺了 API 稳定性，0.x 可以随意改
- 为什么大厂库比个人库值得用？——有更多人依赖 = 它被迫稳定
- 为什么你自己的通用工具函数也该稳定？——因为它被你的多个模块依赖

**洞察**：选库标准里的"API 稳定性、bus factor、活跃度"——全部指向同一个词：**稳定**。不稳定 = 依赖它 = 不断追赶它的变化。

### 复用决策树：什么时候该自己写

```
这个功能是核心差异化吗？
    ├── 是 → 自己写，但要充分测试
    └── 否 → 有成熟方案吗？
              ├── 有 → 用（评估后）
              └── 没有 → 重新想：是不是方向错了？
```

**三条准绳**（手写前必须满足至少一条）：
1. 核心差异化——这是你凭什么赢的东西
2. 成熟方案评估后确实不满足需求
3. 你写的是胶水代码（组合现有库），不是从零造轮子

**洞察**："手写学习 = 读源码学习，不要生产环境手写"——学习的成本应该花在理解成熟库怎么设计，而不是重复造一个更差的。

---

## 工程实践手段

| 原理 | 落地动作 |
|------|---------|
| DIP | 第三方库包一层薄接口（`from .http import client`），换库只动包装层 |
| 选库 | 活跃度 / stars / **bus factor** / 许可证 / API 稳定性 / 依赖数量，六项打分 |
| 供应链 | lockfile 锁版本（`uv`/`poetry`/`npm ci`）+ pip-audit / npm audit 定期扫 |
| 稳定依赖 | 优先 1.0+、活跃维护者多的库；0.x 和单维护者库高风险 |
| 复用决策 | 核心差异化自己写；其余用库；胶水代码用现有库组合 |
| 抽象评估 | 选库前试水：看它抽象厚不厚，边缘场景是否泄漏 |
| 依赖审计 | CI 集成依赖漏洞扫描，禁止引入已知高危漏洞的库 |

---

## 推荐开源项目（跨语言）

| 项目 | 语言 | 定位 | 优势 | 劣势 / 风险 | 适用场景 |
|------|------|------|------|------------|---------|
| **uv** | Rust | Python 包管理 | 超快（Rust）、锁版本、环境+依赖一体 | 较新、生态迁移中 | 新 Python 项目，**首选** |
| **poetry** | Python | Python 包管理 | 成熟、锁文件、发布支持 | 慢于 uv | 已有 poetry 的项目 |
| **pnpm** | JS | Node 包管理 | 省磁盘、严格、快 | 团队习惯迁移 | Node/TS 项目 |
| **cargo** | Rust | 包管理+构建 | 内置、锁文件、安全审计 | Rust only | Rust |
| **ruff** | Rust | Python lint+format | 超快、替代 black+isort+flake8 | Python only | Python 格式+质量 |
| **eslint** | JS | lint | 插件生态最大 | 配置复杂 | JS/TS |
| **golangci-lint** | Go | lint | 聚合多工具 | Go only | Go |
| **pre-commit** | Python | Git hooks | 统一管理、提交前自动跑 | 配置魔法多 | 所有项目 |
| **MkDocs + Material** | Python | 文档站 | Markdown → 文档、主题漂亮 | Python 生态 | 项目文档 |
| **VitePress** | Node | 文档站 | Vue 系、快、MDX | Node | JS/TS 项目文档 |
| **Docusaurus** | Node | 文档站 | 功能全、版本化 | 重 | 大型开源文档 |
| **Renovate** | TS | 依赖自动更新 | 自动 PR、group、可定制 | PR 噪音 | 依赖维护 |

**选型建议**：Python 用 uv（快）或 poetry（稳）+ ruff 一条龙 + pre-commit 管 hooks；依赖更新交给 Renovate，重点盯 breaking 变更。选库前过一遍 10-reuse 的"六项打分"。
