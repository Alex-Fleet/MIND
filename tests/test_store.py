"""store.py CRUD 测试：正常/空值/边界/重复写/写后读回。

覆盖：sessions、turns（幂等去重）、turn_summaries、日报、月报、
registry（幂等 upsert + 权重更新）、proposals（状态流转）。
用临时库 fixture（conftest.py），不碰真实 data/。
"""

import pytest

from store import Store


# ── Sessions ──────────────────────────────────────────────

def test_ensure_session_creates_and_is_idempotent(store):
    store.ensure_session("s1", "proj-a", "/tmp/a.jsonl")
    store.ensure_session("s1", "proj-a", "/tmp/a.jsonl")  # 重复调用不报错
    assert store.get_max_seq("s1") == -1  # 空会话无 turn


def test_ensure_session_empty_values(store):
    # 空值不崩溃（项目名/路径可为空串场景）
    store.ensure_session("s-empty", "", "")


# ── Turns：幂等是核心 ─────────────────────────────────────

def _seed_turn(store, sid="s1", seq=0):
    return store.insert_turn(
        sid, seq, "user", "hello", None,
        '{"role":"user","content":"hello"}', "2026-08-01T10:00:00Z",
    )


def test_insert_turn_new_then_duplicate(store):
    assert _seed_turn(store) is True      # 新插入
    assert _seed_turn(store) is False     # 同一 (session, seq) 幂等返回 False


def test_insert_turn_batch_counts_only_new(store):
    store.ensure_session("s1", "p", "/x.jsonl")
    rows = [
        ("s1", 0, "user", "a", None, "{}", "2026-08-01T10:00:00Z"),
        ("s1", 1, "assistant", "b", None, "{}", "2026-08-01T10:00:01Z"),
        ("s1", 0, "user", "a", None, "{}", "2026-08-01T10:00:00Z"),  # 重复
    ]
    assert store.insert_turns_batch(rows) == 2


def test_get_max_seq_empty_and_after_insert(store):
    assert store.get_max_seq("s1") == -1
    _seed_turn(store, seq=0)
    _seed_turn(store, seq=1)
    assert store.get_max_seq("s1") == 1


def test_insert_turn_without_session_row(store):
    # 无外键约束强制（SQLite 默认不开外键），插不同 session 不崩
    _seed_turn(store, sid="no-session")


# ── Turn Summaries：写后读回 ───────────────────────────────

def test_insert_summary_and_read_back(store):
    # 窗口查询按真实对话时间 JOIN turns（role='user'），必须先有对应 turn
    store.ensure_session("s1", "proj-a", "/x.jsonl")
    _seed_turn(store, seq=0)
    ok = store.insert_turn_summary(
        "s1", 0, "proj-a", "archive/turns/x.md", "标题", "摘要内容",
        key_decisions=["决策A"], unfinished=["待办"],
    )
    assert ok is True
    rows = store.get_turn_summaries_in_window(days=30)
    assert len(rows) == 1
    assert rows[0]["title"] == "标题"
    assert "决策A" in rows[0]["key_decisions"]


def test_insert_summary_duplicate_returns_false(store):
    args = ("s1", 0, "p", "f.md", "t", "s")
    assert store.insert_turn_summary(*args) is True
    assert store.insert_turn_summary(*args) is False


def test_insert_summary_empty_values(store):
    store.ensure_session("s1", "p", "/x.jsonl")
    _seed_turn(store, seq=0)
    store.insert_turn_summary("s1", 0, "p", "", "", "", None, None, "")
    rows = store.get_turn_summaries_in_window(days=30)
    assert rows[0]["summary"] == ""


# ── 日报 / 月报 ───────────────────────────────────────────

def test_daily_report_insert_exists_roundtrip(store):
    assert store.daily_report_exists("2026-08-01", "proj-a") is False
    assert store.insert_daily_report(
        "2026-08-01", "proj-a", "archive/daily/x.md", "日报", "正文", [1, 2]
    ) is True
    assert store.daily_report_exists("2026-08-01", "proj-a") is True
    # 重复写 → False
    assert store.insert_daily_report(
        "2026-08-01", "proj-a", "archive/daily/x.md", "日报", "正文", [1, 2]
    ) is False


def test_monthly_report_insert_exists(store):
    assert store.monthly_report_exists("2026-07", "proj-a") is False
    assert store.insert_monthly_report(
        "2026-07", "proj-a", "archive/monthly/x.md", "月报", "正文", [1]
    ) is True
    assert store.monthly_report_exists("2026-07", "proj-a") is True


def test_get_missing_daily_dates_empty(store):
    assert store.get_missing_daily_dates("proj-a") == []


# ── Registry：幂等 upsert + 权重 ──────────────────────────

def test_registry_upsert_and_get(store):
    rid = store.upsert_registry_entry(
        "memory/global/programming-standards.md", "架构", "global", 0.6)
    assert rid is not None
    entry = store.get_registry_entry("memory/global/programming-standards.md", "架构")
    assert entry is not None
    assert entry["scope"] == "global"
    assert entry["base_weight"] == 0.6


def test_rename_registry_path_updates_all_rows(store):
    """数据修正：旧路径 → 新路径，所有行都改，且幂等。"""
    for sec in ("架构", "Git", "测试"):
        store.upsert_registry_entry("memory/global/iron-rules.md", sec, "global", 0.6)
    changed = store.rename_registry_path(
        "memory/global/iron-rules.md", "memory/global/programming-standards.md")
    assert changed == 3
    # 旧路径已清空
    assert store.get_registry_entry("memory/global/iron-rules.md", "架构") is None
    # 新路径可查
    for sec in ("架构", "Git", "测试"):
        e = store.get_registry_entry("memory/global/programming-standards.md", sec)
        assert e is not None
    # 再跑一次幂等：0 条
    assert store.rename_registry_path(
        "memory/global/iron-rules.md", "memory/global/programming-standards.md") == 0


def test_rename_registry_path_no_clobber_existing(store):
    """新路径已有同 section 条目时，跳过不覆盖。"""
    store.upsert_registry_entry("memory/global/iron-rules.md", "架构", "global", 0.6)
    store.upsert_registry_entry("memory/global/programming-standards.md", "架构", "global", 0.9)
    changed = store.rename_registry_path(
        "memory/global/iron-rules.md", "memory/global/programming-standards.md")
    assert changed == 0  # 冲突，全部跳过
    e = store.get_registry_entry("memory/global/programming-standards.md", "架构")
    assert e["base_weight"] == 0.9  # 原有数据未被覆盖


def test_rename_registry_path_absent_old_is_noop(store):
    """旧路径本就不存在 → 0 条，不报错。"""
    assert store.rename_registry_path("memory/global/nope.md", "memory/global/x.md") == 0


def test_registry_upsert_same_key_updates_not_duplicates(store):
    store.upsert_registry_entry("f.md", "节", "global", 0.4)
    store.upsert_registry_entry("f.md", "节", "global", 0.8)
    entries = store.get_registry_entries(scope="global")
    same = [e for e in entries
            if e["file_path"] == "f.md" and e["section_heading"] == "节"]
    assert len(same) == 1  # 同一 key 不产生重复条目
    assert same[0]["base_weight"] == 0.8


def test_update_registry_weight(store):
    rid = store.upsert_registry_entry("f.md", "节", "global", 0.4)
    store.update_registry_weight(rid, 0.6, last_confirmed="2026-08-01T00:00:00Z",
                                 confirmed_delta=1)
    entry = store.get_registry_entry("f.md", "节")
    assert entry["base_weight"] == 0.6
    assert entry["confirmed_count"] == 1


def test_registry_entry_missing_returns_none(store):
    assert store.get_registry_entry("nope.md", "节") is None


# ── Proposals：插入 + 状态流转 ────────────────────────────

def test_proposal_insert_and_status_transitions(store):
    pid = store.insert_memory_proposal(
        "create", "global", "标题", "内容", target_path="memory/global/x.md")
    assert pid > 0

    pending = store.get_proposals(status="pending")
    assert any(p["id"] == pid for p in pending)

    store.update_proposal_status(pid, "approved")
    approved = store.get_proposals(status="approved")
    assert any(p["id"] == pid for p in approved)
    assert not any(p["id"] == pid for p in store.get_proposals(status="pending"))


# ── 空库边界 ─────────────────────────────────────────────

def test_empty_db_returns_empty_lists(store):
    assert store.get_unsummarized_turns() == []
    assert store.get_turn_summaries_in_window(days=30) == []
    assert store.get_daily_reports_in_window(days=30) == []
    assert store.get_registry_entries() == []


# ── Schema 版本化（批次4-项3）────────────────────────────

def test_schema_version_marked_on_new_db(store):
    """新建库自动打上当前 schema 版本号（user_version = SCHEMA_VERSION）。"""
    import sqlite3
    from store import SCHEMA_VERSION
    with sqlite3.connect(store.db_path) as conn:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
    assert ver == SCHEMA_VERSION


def test_legacy_db_zero_version_upgraded(store):
    """旧库（user_version=0）打开后自动升到当前版本，不丢数据。"""
    import sqlite3
    from store import SCHEMA_VERSION
    # 模拟旧库：把手动建的库 user_version 重置为 0 再重新实例化
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA user_version = 0")
    reopened = Store(db_path=store.db_path)
    with sqlite3.connect(reopened.db_path) as conn:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
    assert ver == SCHEMA_VERSION


def test_future_schema_version_raises(tmp_path):
    """库比代码新（旧版代码打开新版库）→ 拒绝打开，防降级写坏数据。"""
    import sqlite3
    from store import Store
    db = tmp_path / "future.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="比代码"):
        Store(db_path=str(db))
