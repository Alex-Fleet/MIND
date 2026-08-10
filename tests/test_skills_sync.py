"""skills_sync 双向同步器单测：方向判定 / 冲突 / symlink / 删除 / 预览。

全部用 tmp_path 构造两侧目录，不碰真实 memory/ 或 ~/.claude/skills。
"""

import os
from pathlib import Path

from skills_sync import apply_action, plan_actions


def _root(tmp_path: Path, side: str) -> Path:
    """构造一侧 skills 根目录并返回。"""
    d = tmp_path / side
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_skill(root: Path, name: str, content: str = "SKILL", mtime: float | None = None) -> Path:
    """在 root/<name>/SKILL.md 造一个 skill，可选固定 mtime。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "SKILL.md"
    f.write_text(content)
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return d


def _read_skill(root: Path, name: str) -> str:
    return (root / name / "SKILL.md").read_text()


def _actions(mind: Path, user: Path, **kw) -> list:
    """plan_actions 简写，返回 (action, skill) 列表便于断言。"""
    return [(a["action"], a["skill"]) for a in plan_actions(mind, user, **kw)]


# ── 方向判定 ──


def test_push_only_in_mind(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    _make_skill(mind, "alpha")
    assert ("push", "alpha") in _actions(mind, user)

    action = plan_actions(mind, user)[0]
    assert apply_action(action, mind, user)
    assert _read_skill(user, "alpha") == "SKILL"


def test_pull_only_in_user(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    _make_skill(user, "beta", content="downloaded")
    assert ("pull", "beta") in _actions(mind, user)

    action = plan_actions(mind, user)[0]
    assert apply_action(action, mind, user)
    assert _read_skill(mind, "beta") == "downloaded"


def test_identical_skip(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    _make_skill(mind, "gamma", content="same")
    _make_skill(user, "gamma", content="same")
    assert _actions(mind, user) == []


def test_overwrite_newer_mtime_wins(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    _make_skill(mind, "delta", content="newer", mtime=2000.0)
    _make_skill(user, "delta", content="older", mtime=1000.0)
    actions = _actions(mind, user)
    assert ("overwrite_push", "delta") in actions

    for act in plan_actions(mind, user):
        assert apply_action(act, mind, user)
    assert _read_skill(user, "delta") == "newer"  # MIND 侧被推送覆盖


def test_overwrite_pull_when_user_newer(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    _make_skill(mind, "eps", content="older", mtime=1000.0)
    _make_skill(user, "eps", content="newer", mtime=2000.0)
    assert ("overwrite_pull", "eps") in _actions(mind, user)

    for act in plan_actions(mind, user):
        assert apply_action(act, mind, user)
    assert _read_skill(mind, "eps") == "newer"  # 用户侧新改动被拉回 MIND


def test_conflict_skipped_when_mtime_equal(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    _make_skill(mind, "zeta", content="mind-side", mtime=1000.0)
    _make_skill(user, "zeta", content="user-side", mtime=1000.0)
    actions = _actions(mind, user)
    assert ("conflict", "zeta") in actions
    # 不覆盖：两侧内容保持原样
    assert _read_skill(mind, "zeta") == "mind-side"
    assert _read_skill(user, "zeta") == "user-side"


# ── symlink（外部工具管理）──


def test_symlink_ignored(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    external = tmp_path / "external" / "pptx"
    external.mkdir(parents=True)
    (external / "SKILL.md").write_text("external")
    os.symlink(external, user / "pptx")

    actions = _actions(mind, user)
    assert ("skip_symlink", "pptx") in actions
    assert all(a != "push" or s != "pptx" for a, s in actions)

    for act in plan_actions(mind, user):
        assert apply_action(act, mind, user)
    # symlink 不被删除、不被拉取进 MIND
    assert (user / "pptx").is_symlink()
    assert not (mind / "pptx").exists()


def test_symlink_never_pruned(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    external = tmp_path / "external" / "ext"
    external.mkdir(parents=True)
    (external / "SKILL.md").write_text("external")
    os.symlink(external, user / "ext")

    for act in plan_actions(mind, user, prune=True):
        assert apply_action(act, mind, user)
    assert (user / "ext").is_symlink()  # --prune 也不碰外部管理的


# ── 删除（--prune）──


def test_prune_removes_extra_real_skill(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    _make_skill(mind, "keep")
    _make_skill(user, "keep", content="same")
    _make_skill(user, "extra")  # MIND 侧没有

    actions = _actions(mind, user, prune=True)
    assert ("prune", "extra") in actions
    for act in plan_actions(mind, user, prune=True):
        assert apply_action(act, mind, user)
    assert not (user / "extra").exists()  # 多出的真实 skill 被清
    assert (user / "keep").exists()  # 两边都有的保留


def test_no_prune_by_default(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    _make_skill(user, "extra")
    actions = _actions(mind, user)
    assert ("pull", "extra") in actions  # 默认拉取，不删
    assert all(a != "prune" for a, _ in actions)


# ── 方向限制模式 ──


def test_push_mode_does_not_pull(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    _make_skill(mind, "a")
    _make_skill(user, "b")
    actions = _actions(mind, user, mode="push")
    assert ("push", "a") in actions
    assert all(a != "pull" for a, _ in actions)


def test_pull_mode_does_not_push(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    _make_skill(mind, "a")
    _make_skill(user, "b")
    actions = _actions(mind, user, mode="pull")
    assert ("pull", "b") in actions
    assert all(a != "push" for a, _ in actions)


# ── 预览（--dry-run）──


def test_dry_run_writes_nothing(tmp_path):
    mind, user = _root(tmp_path, "mind"), _root(tmp_path, "user")
    _make_skill(mind, "alpha")
    action = plan_actions(mind, user)[0]
    assert apply_action(action, mind, user, dry_run=True)
    assert not (user / "alpha").exists()  # 预览不写文件
