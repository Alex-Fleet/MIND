# Git 历史改造：旧工作流 → "版本号在 merge 点"的漂亮形态

> 记录 2026-08-11 对 MIND 仓库的 Git 历史清理/重写全过程，供其他项目复用。
> 起因：用户不满 git graph 乱——版本号散落三处、tag 噪音、commit 挂一堆 branch 标。

## 旧工作流的问题

1. **版本号三个宿主**：同一版本号既写进分支 commit 名（`v1.6.0: ...`）、又写进 merge message、又在主线上补一个版本 commit——一个版本占 2~4 个点。
2. **打 tag**：`git log --decorate` 里一堆 `(tag: v1.x.y)` 噪音。
3. **merge 后分支不删**：`(chore/add-ci)`、`(feat/...)` 等 ref 挂在历史 commit 上。
4. **主线补版本 commit**：merge 完再在 main 上补 `v1.10.0: ...` commit，和 merge 内容重复（实际只是 CHANGELOG 更新）。

## 目标形态

```
* 功能 commit（feature/xxx）                    ← 分支 commit：纯功能描述，无版本号
* v1.10.0: skills 统一管理（...）               ← merge commit：即版本点，名字是 v...
|\
| * 功能 commit ×N                              ← 分支轨迹保留（--no-ff）
|/
* v1.9.0: ...                                   ← 主线发布 commit（正式版 / 后续小改）
```

**原则**：
- 版本号唯一宿主 = main 的 **merge/发布 commit message**
- 分支 commit 不带版本号（简短中文功能描述）
- **不打 git tag**（可回溯性靠 commit message + CHANGELOG）
- `--no-ff` 保留 branch 轨迹（main 有 merge 分叉是设计，不是乱）

## 一次性清理（ref 噪音，不需重写历史）

```bash
# 删 tag（版本号已在 commit message，删 tag 不丢信息）
git tag -l | xargs git tag -d

# 删已 merge 进 main 的本地分支
git branch --merged main | grep -v '^*\|main' | xargs git branch -d

# 删本地 origin/HEAD 符号引用（减少 main commit 上的标）
git remote set-head origin -d

# 删远程分支 + 清理本地跟踪（走代理 push）
git push origin --delete <feature-branch>
git remote prune origin
```

效果：`(tag: v1.10.0, origin/main, origin/HEAD, main)` 4 标 → 只剩 `(origin/main, main)`。

## 历史重写（把旧 merge/分支 commit 改成目标形态）

### 1. 安全网

```bash
git branch backup-main main   # 重写前留一个可回滚指针
```

### 2. msg-filter 脚本（精确替换每个 commit message）

`filter-branch --msg-filter` 只改 message 不改内容，保留 merge 结构。映射规则：
- merge commit：`Merge branch 'xxx'（vX.Y.Z ...）` → `vX.Y.Z: 一句话`
- 分支 commit：`vX.Y.Z: ...` → `...`（去版本号，保留功能描述）

脚本骨架（逐行匹配，`*)` 兜底原样输出）：

```bash
#!/bin/bash
while IFS= read -r line; do
  case "$line" in
    "Merge branch 'fix/checklist-audit'（v1.6.0 检查单审计修复）")
      echo "v1.6.0: 检查单审计修复——WAL/日志/备份/测试/API语义/幂等" ;;
    "v1.6.0: 检查单审计修复——WAL/日志/备份/测试/API语义/幂等")
      echo "检查单审计修复——WAL/日志/备份/测试/API语义/幂等" ;;
    *) echo "$line" ;;
  esac
done
```

> 注意：映射表要覆盖"merge 改名"和"分支 commit 去版本号"两方向。v1.10.0 这类"主线补的版本 commit"（纯 CHANGELOG 更新）去版本号后变普通 commit，版本点让给 merge commit。

### 3. 重写 main（指定坏历史起点）

```bash
# 先 stash 未提交改动，filter-branch 要求干净工作区
git stash -u
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f \
  --msg-filter 'bash /tmp/msg_filter.sh' -- <good-commit>..main
```

`<good-commit>` = 第一个坏点之前的 commit（如 `v1.5.2`，v1.5.2 及更早是线性没病）。

### 4. 分支跟到新历史 + 恢复 stash

```bash
git rebase main            # 重写后所有 commit hash 都变了，分支要 rebase
git stash pop              # 恢复未提交改动（rebase 后 base 内容不变，无冲突）
```

### 5. 清理

```bash
git for-each-ref --format='%(refname)' refs/original | xargs -n1 git update-ref -d
```

### 6. force push（覆盖远程）

```bash
HTTPS_PROXY=http://127.0.0.1:31181 git push --force origin main
```

> ⚠️ 重写会改所有涉及 commit 的 hash，远程历史被覆盖，多人协作慎用（单人项目可接受）。回滚：`git branch -f main backup-main && git push --force origin main`。

## 日常新工作流（防复发）

| 动作 | 命令 / message |
|---|---|
| 分支 commit | `git commit -m "修复 WAL 并发竞态"`（功能描述，无版本号） |
| merge 回 main | `git merge --no-ff <branch> -m "v1.11.0-alpha.1: 一句话"`（merge 即版本点） |
| 正式版/后续小改 | main 上直接 `git commit -m "v1.11.0: 一句话"`（version commit） |
| tag | **不打** |
