# 工程经验目录说明（exps/）

> 模板：仿此结构建你自己的 `memory/global/exps/` 目录——按板块一个文件装一类工程经验。
> 本文件进 git 承载结构；`memory/global/exps/` 下实际内容被 gitignore（私密经验，不进分享包）。

## 板块划分

| 文件 | 装什么 |
|------|--------|
| `frontend.md` | 前端经验（本项目：dashboard TS+React+Vite） |
| `backend.md` | 后端经验（本项目：Python 服务与脚本） |
| `database.md` | 数据库经验（本项目：SQLite） |
| `cross-cutting.md` | 跨域经验（性能 / 安全 / 部署 / 成本） |

## 单文件骨架（frontend.md 示例，其余同构）

```
# 工程经验 — 前端

> 这里记前端工程经验（踩坑 / 模式 / 选型），**不是规则**——规则见 `../programming-standards.md`。

### 踩坑

（记录：问题现象 → 根因 → 解法）

### 模式

（记录：可复用的实现骨架 / 结构决策）

### 选型

（记录：比较过什么、为什么选这个）
```

> 注入：exps/ 下 *.md 由 `inject.py --section global --pack K` 运行时枚举打包注入（`config.inject.global_folders`），加文件自动生效，不用重跑 install。
