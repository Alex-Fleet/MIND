#!/usr/bin/env python3
"""
MIND skills 双向同步 — MIND 内容源与用户级 skills 保持一致。

用法：
  python3 scripts/skills_sync.py            # 双向同步（默认，MIND ⇄ ~/.claude/skills）
  python3 scripts/skills_sync.py --push     # 只 MIND → ~/.claude/skills
  python3 scripts/skills_sync.py --pull     # 只 ~/.claude/skills → MIND
  python3 scripts/skills_sync.py --dry-run  # 只预览将做什么，不写文件
  python3 scripts/skills_sync.py --prune    # 清理用户侧 MIND 没有的真实 skill（symlink 永不碰）

两侧：
  MIND 内容源: memory/global/skills/   唯一真相（inject 用非递归 glob，天然不扫此子目录）
  用户侧:      ~/.claude/skills/       Claude Code 原生扫描的读取位

规则：
  - 某侧独有 → 复制到另一侧（增改双向一致）
  - 两侧内容不同 → mtime 新的一侧覆盖旧的；mtime 相同但内容不同 → ⚠ 冲突跳过不覆盖
  - symlink（cc-switch 等外部工具管理的 skill）→ 一律不碰，两套独立系统
  - 删除不自动传播；--prune 才清理用户侧 MIND 没有的真实 skill
"""

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths


def skills_dirs():
    """(MIND 侧, 用户侧) 两个 skills 目录。"""
    mind = get_paths()["base_dir"] / "memory" / "global" / "skills"
    user = Path.home() / ".claude" / "skills"
    return mind, user


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def dir_snapshot(d: Path) -> dict:
    """目录内所有常规文件的 相对路径→sha256；符号链接一律忽略。"""
    snap = {}
    if not d.is_dir():
        return snap
    for p in sorted(d.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        snap[p.relative_to(d).as_posix()] = _sha256(p)
    return snap


def latest_mtime(d: Path) -> float:
    """目录内常规文件的最新 mtime；目录不存在/为空返回 0.0。"""
    if not d.is_dir():
        return 0.0
    return max(
        (p.stat().st_mtime for p in d.rglob("*") if p.is_file() and not p.is_symlink()),
        default=0.0,
    )


def skill_names(d: Path) -> list:
    """一级真实 skill 目录名（排除符号链接——外部工具管理的）。"""
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.is_symlink())


def _overwrite_dir(mind: Path, user: Path, name: str):
    """两侧都有且内容不同时的方向：'push'/'pull'/'conflict'；内容一致返回 None。"""
    if dir_snapshot(mind / name) == dir_snapshot(user / name):
        return None
    m_t = latest_mtime(mind / name)
    u_t = latest_mtime(user / name)
    if m_t > u_t:
        return "push"
    if u_t > m_t:
        return "pull"
    return "conflict"  # mtime 相同但内容不同 → 真冲突


def plan_actions(mind: Path, user: Path, mode: str = "sync", prune: bool = False) -> list:
    """计算同步动作列表（纯逻辑，便于单测）。mode: sync/push/pull。"""
    actions = []
    m_names = set(skill_names(mind))
    u_names = set(skill_names(user))
    u_symlinks = (
        sorted(p.name for p in user.iterdir() if p.is_symlink()) if user.is_dir() else []
    )

    for name in u_symlinks:
        actions.append({"action": "skip_symlink", "skill": name})

    for name in sorted(m_names | u_names):
        in_m, in_u = name in m_names, name in u_names
        if in_m and not in_u:
            if mode in ("sync", "push"):
                actions.append({"action": "push", "skill": name})
        elif in_u and not in_m:
            if prune:
                actions.append({"action": "prune", "skill": name})
            elif mode in ("sync", "pull"):
                actions.append({"action": "pull", "skill": name})
        else:  # 两侧都有
            d = _overwrite_dir(mind, user, name)
            if d == "push" and mode in ("sync", "push"):
                actions.append({"action": "overwrite_push", "skill": name})
            elif d == "pull" and mode in ("sync", "pull"):
                actions.append({"action": "overwrite_pull", "skill": name})
            elif d == "conflict":
                actions.append({"action": "conflict", "skill": name})
    return actions


def apply_action(action: dict, mind: Path, user: Path, dry_run: bool = False) -> bool:
    """执行一个动作。返回 False 表示执行失败（不影响正确性）。"""
    a, name = action["action"], action["skill"]
    if a == "skip_symlink":
        print(f"· 跳过外部管理 skill（symlink）: {name}")
        return True
    if a == "conflict":
        print(f"⚠ 冲突跳过不覆盖（两侧 mtime 相同但内容不同，需手动处理）: {name}")
        return True
    if a == "prune":
        if dry_run:
            print(f"· [预览] 将清理用户侧多出 skill: {name}")
        else:
            shutil.rmtree(user / name)
            print(f"· 已清理用户侧多出 skill: {name}")
        return True

    src, dst = (mind, user) if a in ("push", "overwrite_push") else (user, mind)
    verb = "推送" if src == mind else "拉取"
    if a.startswith("overwrite"):
        if dry_run:
            print(f"· [预览] 将{verb}覆盖（mtime 新）: {name}")
            return True
        shutil.rmtree(dst / name)
        try:
            shutil.copytree(src / name, dst / name)
        except OSError as e:
            print(f"✗ {verb}覆盖失败 {name}: {e}")
            return False
        print(f"✓ {verb}覆盖: {name}")
    else:
        if dry_run:
            print(f"· [预览] 将{verb}: {name}")
            return True
        try:
            shutil.copytree(src / name, dst / name)
        except OSError as e:
            print(f"✗ {verb}失败 {name}: {e}")
            return False
        print(f"✓ {verb}: {name}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="MIND skills 双向同步器")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--sync", action="store_true", help="双向同步（默认）")
    g.add_argument("--push", action="store_true", help="只 MIND → ~/.claude/skills")
    g.add_argument("--pull", action="store_true", help="只 ~/.claude/skills → MIND")
    parser.add_argument("--dry-run", action="store_true", help="只预览将做什么，不写文件")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="清理用户侧 MIND 没有的真实 skill（symlink 永不碰）",
    )
    args = parser.parse_args()

    mode = "push" if args.push else ("pull" if args.pull else "sync")
    mind, user = skills_dirs()
    mind.mkdir(parents=True, exist_ok=True)
    user.mkdir(parents=True, exist_ok=True)

    actions = plan_actions(mind, user, mode, args.prune)
    # symlink 报告是说明性信息，不是同步动作——分开处理，
    # 否则用户侧有 symlink 时"无待同步项"永远不成立、计数虚高。
    skips = [a for a in actions if a["action"] == "skip_symlink"]
    real = [a for a in actions if a["action"] != "skip_symlink"]
    for s in skips:
        apply_action(s, mind, user, args.dry_run)

    if not real:
        print("· 两侧已一致，无待同步项")
        return 0

    failed = 0
    for act in real:
        if not apply_action(act, mind, user, args.dry_run):
            failed += 1
    if args.dry_run:
        print(f"· [预览] 共 {len(real)} 项，未写任何文件")
    else:
        print(f"✓ 同步完成（{len(real) - failed} 项成功，{failed} 项失败）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
