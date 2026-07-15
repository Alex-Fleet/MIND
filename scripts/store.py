#!/usr/bin/env python3
"""
SQLite store for the Nailong Doctor System.
Schema initialization + all CRUD operations.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from config import get_paths

logger = logging.getLogger("nailong.store")

DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    project         TEXT NOT NULL,
    jsonl_path      TEXT NOT NULL,
    started_at      TEXT,
    ended_at        TEXT,
    turn_count      INTEGER DEFAULT 0,
    ingested_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    tool_calls      TEXT,
    raw_json        TEXT,
    timestamp       TEXT,
    ingested_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_turns_timestamp ON turns(timestamp);

CREATE TABLE IF NOT EXISTS turn_summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    turn_seq        INTEGER NOT NULL,
    project         TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL,
    key_decisions   TEXT,
    unfinished      TEXT,
    retained_context TEXT,
    summarized_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(session_id, turn_seq)
);

CREATE INDEX IF NOT EXISTS idx_ts_project ON turn_summaries(project);
CREATE INDEX IF NOT EXISTS idx_ts_date ON turn_summaries(summarized_at);

CREATE TABLE IF NOT EXISTS daily_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    project         TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    source_turns    TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(date, project)
);

CREATE INDEX IF NOT EXISTS idx_dr_date ON daily_reports(date);

CREATE TABLE IF NOT EXISTS monthly_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    month           TEXT NOT NULL,
    project         TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    source_dailies  TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(month, project)
);

CREATE TABLE IF NOT EXISTS preferences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    section         TEXT NOT NULL UNIQUE,
    content         TEXT NOT NULL,
    priority        INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS operation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operation       TEXT NOT NULL,
    project         TEXT,
    detail          TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""

# ── Public API ──────────────────────────────────────────────


class Store:
    """Manages the nailong.db SQLite database."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = str(get_paths()["db_path"])
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(DDL)
            # 幂等加列: v1.0→v1.1 有效性分类
            try:
                conn.execute(
                    "ALTER TABLE turn_summaries ADD COLUMN validity TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在
            conn.commit()

    # ── Sessions ────────────────────────────────────────

    def ensure_session(self, session_id: str, project: str,
                       jsonl_path: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, project, jsonl_path) "
                "VALUES (?, ?, ?)",
                (session_id, project, jsonl_path)
            )
            conn.commit()

    # ── Turns (ingestion) ────────────────────────────────

    def get_max_seq(self, session_id: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(seq) FROM turns WHERE session_id = ?",
                (session_id,)
            ).fetchone()
            return row[0] if row and row[0] is not None else -1

    def insert_turn(self, session_id: str, seq: int, role: str,
                    content: str, tool_calls: str | None,
                    raw_json: str, timestamp: str) -> bool:
        """Insert a turn. Returns True if newly inserted, False if duplicate."""
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO turns (session_id, seq, role, content, "
                    "tool_calls, raw_json, timestamp) VALUES (?,?,?,?,?,?,?)",
                    (session_id, seq, role, content, tool_calls,
                     raw_json, timestamp)
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def insert_turns_batch(self, rows: list[tuple]) -> int:
        """Batch insert turns. Returns count of newly inserted."""
        count = 0
        with sqlite3.connect(self.db_path) as conn:
            for row in rows:
                try:
                    conn.execute(
                        "INSERT INTO turns (session_id, seq, role, content, "
                        "tool_calls, raw_json, timestamp) VALUES "
                        "(?,?,?,?,?,?,?)", row
                    )
                    count += 1
                except sqlite3.IntegrityError:
                    pass
            conn.commit()
        return count

    # ── Turn Summaries ───────────────────────────────────

    def get_unsummarized_turns(self, project: str | None = None) -> list[dict]:
        """Find turns that exist in `turns` but not in `turn_summaries`."""
        query = """
            SELECT t.session_id, t.seq, t.role, t.content, t.timestamp,
                   s.project
            FROM turns t
            JOIN sessions s ON t.session_id = s.id
            WHERE t.role IN ('user', 'assistant')
              AND NOT EXISTS (
                  SELECT 1 FROM turn_summaries ts
                  WHERE ts.session_id = t.session_id AND ts.turn_seq = t.seq
              )
        """
        params = []
        if project:
            query += " AND s.project = ?"
            params.append(project)

        query += " ORDER BY t.session_id, t.seq"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_turns_ordered(self, project: str | None = None) -> list[dict]:
        """All user/assistant turns ordered by session then seq — 供配对用。
        不做'未总结'过滤（旧过滤把所有 assistant 都当未总结，是错配根源）。"""
        query = """
            SELECT t.session_id, t.seq, t.role, t.content, t.timestamp,
                   s.project
            FROM turns t
            JOIN sessions s ON t.session_id = s.id
            WHERE t.role IN ('user', 'assistant')
        """
        params = []
        if project:
            query += " AND s.project = ?"
            params.append(project)
        query += " ORDER BY t.session_id, t.seq"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def get_summarized_keys(self, project: str | None = None) -> set:
        """已总结的 (session_id, user 的 turn_seq) 集合。判断 pair 是否已做。"""
        q = "SELECT session_id, turn_seq FROM turn_summaries"
        params = []
        if project:
            q += " WHERE project = ?"
            params.append(project)
        with sqlite3.connect(self.db_path) as conn:
            return {(r[0], r[1]) for r in conn.execute(q, params).fetchall()}

    def count_unsummarized_pairs(self, project: str | None = None) -> int:
        """真实积压 = 没有对应摘要的 user turn 数（不含 assistant）。"""
        q = """
            SELECT COUNT(*) FROM turns t
            JOIN sessions s ON t.session_id = s.id
            WHERE t.role = 'user'
              AND NOT EXISTS (SELECT 1 FROM turn_summaries ts
                  WHERE ts.session_id = t.session_id AND ts.turn_seq = t.seq)
        """
        params = []
        if project:
            q += " AND s.project = ?"
            params.append(project)
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(q, params).fetchone()[0]

    def get_last_summarized_seq(self, session_id: str) -> int:
        """Get the last turn_seq that has been summarized for a session."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(turn_seq) FROM turn_summaries "
                "WHERE session_id = ?",
                (session_id,)
            ).fetchone()
            return row[0] if row and row[0] is not None else -1

    def insert_turn_summary(self, session_id: str, turn_seq: int,
                            project: str, file_path: str,
                            title: str, summary: str,
                            key_decisions: list | None = None,
                            unfinished: list | None = None,
                            retained_context: str = "",
                            validity: str | None = None) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO turn_summaries (session_id, turn_seq, project, "
                    "file_path, title, summary, key_decisions, unfinished, "
                    "retained_context, validity) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (session_id, turn_seq, project, file_path,
                     title, summary,
                     json.dumps(key_decisions or [], ensure_ascii=False),
                     json.dumps(unfinished or [], ensure_ascii=False),
                     retained_context, validity)
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_turn_summaries_in_window(self, days: int,
                                     project: str | list | None = None,
                                     limit: int | None = None
                                     ) -> list[dict]:
        """Get turn summaries whose REAL conversation time is within the
        last N days, newest first.  Uses turns.timestamp (not summarized_at)
        so the window reflects when the conversation actually happened.
        `project` can be a single slug or a list of slugs (for merged projects)."""
        query = """
            SELECT ts.* FROM turn_summaries ts
            JOIN turns t ON t.session_id = ts.session_id
                         AND t.seq = ts.turn_seq
                         AND t.role = 'user'
            WHERE t.timestamp > datetime('now', ?)
        """
        params = [f'-{days} days']
        if project:
            if isinstance(project, list) and len(project) == 1:
                query += " AND ts.project = ?"
                params.append(project[0])
            elif isinstance(project, list) and len(project) > 1:
                ph = ",".join(["?"] * len(project))
                query += f" AND ts.project IN ({ph})"
                params.extend(project)
            else:
                query += " AND ts.project = ?"
                params.append(project)
        query += " ORDER BY t.timestamp DESC"
        if limit:
            query += f" LIMIT {int(limit)}"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    # ── Daily Reports ────────────────────────────────────

    def get_turn_summaries_for_date(self, date_str: str,
                                    project: str) -> list[dict]:
        """Get turn summaries whose REAL conversation date matches date_str.
        Uses turns.timestamp (not summarized_at) so digest buckets reflect
        when conversations actually happened."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT DISTINCT ts.* FROM turn_summaries ts "
                "JOIN turns t ON t.session_id = ts.session_id "
                             "AND t.seq = ts.turn_seq "
                             "AND t.role = 'user' "
                "WHERE ts.project = ? AND date(t.timestamp) = ? "
                "ORDER BY t.timestamp",
                (project, date_str)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_daily_reports_in_window(self, days: int,
                                    project: str | list | None = None
                                    ) -> list[dict]:
        query = """
            SELECT * FROM daily_reports
            WHERE date > date('now', ?)
        """
        params = [f'-{days} days']
        if project:
            if isinstance(project, list) and len(project) == 1:
                query += " AND project = ?"
                params.append(project[0])
            elif isinstance(project, list) and len(project) > 1:
                ph = ",".join(["?"] * len(project))
                query += f" AND project IN ({ph})"
                params.extend(project)
            else:
                query += " AND project = ?"
                params.append(project)
        query += " ORDER BY date DESC"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def daily_report_exists(self, date_str: str, project: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM daily_reports WHERE date = ? AND project = ?",
                (date_str, project)
            ).fetchone()
            return row is not None

    def insert_daily_report(self, date_str: str, project: str,
                            file_path: str, title: str, content: str,
                            source_turns: list) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO daily_reports (date, project, file_path, "
                    "title, content, source_turns) VALUES (?,?,?,?,?,?)",
                    (date_str, project, file_path, title, content,
                     json.dumps(source_turns, ensure_ascii=False))
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_missing_daily_dates(self, project: str) -> list[str]:
        """Find dates that have turn summaries (by REAL conversation time)
        but no daily report yet."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT date(t.timestamp) as d "
                "FROM turn_summaries ts "
                "JOIN turns t ON t.session_id = ts.session_id "
                             "AND t.seq = ts.turn_seq "
                             "AND t.role = 'user' "
                "WHERE ts.project = ? "
                "AND d NOT IN (SELECT date FROM daily_reports WHERE project = ?) "
                "ORDER BY d",
                (project, project)
            ).fetchall()
            return [r[0] for r in rows]

    # ── Monthly Reports ─────────────────────────────────

    def get_daily_reports_for_month(self, month: str,
                                    project: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM daily_reports "
                "WHERE project = ? AND strftime('%Y-%m', date) = ? "
                "ORDER BY date",
                (project, month)
            ).fetchall()
            return [dict(r) for r in rows]

    def monthly_report_exists(self, month: str, project: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM monthly_reports "
                "WHERE month = ? AND project = ?",
                (month, project)
            ).fetchone()
            return row is not None

    def insert_monthly_report(self, month: str, project: str,
                              file_path: str, title: str, content: str,
                              source_dailies: list) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO monthly_reports (month, project, file_path, "
                    "title, content, source_dailies) VALUES (?,?,?,?,?,?)",
                    (month, project, file_path, title, content,
                     json.dumps(source_dailies, ensure_ascii=False))
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_all_monthly_reports(self, project: str | list | None = None
                                ) -> list[dict]:
        query = "SELECT * FROM monthly_reports"
        params = []
        if project:
            if isinstance(project, list) and len(project) == 1:
                query += " WHERE project = ?"
                params.append(project[0])
            elif isinstance(project, list) and len(project) > 1:
                ph = ",".join(["?"] * len(project))
                query += f" WHERE project IN ({ph})"
                params.extend(project)
            else:
                query += " WHERE project = ?"
                params.append(project)
        query += " ORDER BY month DESC"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_missing_monthly_months(self, project: str) -> list[str]:
        """Find months that have daily reports but no monthly report."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT strftime('%Y-%m', date) as m "
                "FROM daily_reports WHERE project = ? "
                "AND m NOT IN (SELECT month FROM monthly_reports WHERE project = ?) "
                "AND m < strftime('%Y-%m', 'now') "
                "ORDER BY m",
                (project, project)
            ).fetchall()
            return [r[0] for r in rows]

    # ── Preferences ──────────────────────────────────────

    def set_preference(self, section: str, content: str,
                       priority: int = 0) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO preferences (section, content, priority, "
                "updated_at) VALUES (?, ?, ?, datetime('now'))",
                (section, content, priority)
            )
            conn.commit()

    def get_preferences(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM preferences ORDER BY priority DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Operation Log ────────────────────────────────────

    def log(self, operation: str, project: str = "",
            detail: dict | None = None) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO operation_log (operation, project, detail) "
                "VALUES (?, ?, ?)",
                (operation, project,
                 json.dumps(detail or {}, ensure_ascii=False))
            )
            conn.commit()

    # ── Stats ─────────────────────────────────────────────

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            return {
                "sessions": conn.execute(
                    "SELECT COUNT(*) FROM sessions").fetchone()[0],
                "turns": conn.execute(
                    "SELECT COUNT(*) FROM turns").fetchone()[0],
                "turn_summaries": conn.execute(
                    "SELECT COUNT(*) FROM turn_summaries").fetchone()[0],
                "daily_reports": conn.execute(
                    "SELECT COUNT(*) FROM daily_reports").fetchone()[0],
                "monthly_reports": conn.execute(
                    "SELECT COUNT(*) FROM monthly_reports").fetchone()[0],
            }
