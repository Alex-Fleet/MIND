#!/usr/bin/env python3
"""
Manual memory recall CLI — full-text search across the archive.

Usage:
  python3 recall.py "query"                          # search all
  python3 recall.py --type daily "query"             # daily reports only
  python3 recall.py --type monthly "query"           # monthly reports only
  python3 recall.py --project <slug> "query"         # filter by project
  python3 recall.py --days 60 "query"                # time window
  python3 recall.py --list                           # list recent summaries
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths
from store import Store


def search_turn_summaries(store: Store, query: str,
                          project: str | None = None,
                          days: int | None = None) -> list[dict]:
    """Search turn_summaries by title/summary using LIKE."""
    sql = """
        SELECT title, summary, file_path, project, summarized_at
        FROM turn_summaries WHERE (title LIKE ? OR summary LIKE ?)
    """
    params = [f"%{query}%", f"%{query}%"]
    if project:
        sql += " AND project = ?"
        params.append(project)
    if days:
        sql += f" AND summarized_at > datetime('now', '-{days} days')"
    sql += " ORDER BY summarized_at DESC LIMIT 20"

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def search_daily_reports(store: Store, query: str,
                         project: str | None = None) -> list[dict]:
    sql = """
        SELECT title, content, file_path, date
        FROM daily_reports WHERE (title LIKE ? OR content LIKE ?)
    """
    params = [f"%{query}%", f"%{query}%"]
    if project:
        sql += " AND project = ?"
        params.append(project)
    sql += " ORDER BY date DESC LIMIT 20"

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def search_monthly_reports(store: Store, query: str,
                           project: str | None = None) -> list[dict]:
    sql = """
        SELECT title, content, file_path, month
        FROM monthly_reports WHERE (title LIKE ? OR content LIKE ?)
    """
    params = [f"%{query}%", f"%{query}%"]
    if project:
        sql += " AND project = ?"
        params.append(project)
    sql += " ORDER BY month DESC LIMIT 20"

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def list_recent(store: Store, days: int = 7):
    """List recent turn summaries."""
    turns = store.get_turn_summaries_in_window(days)
    if turns:
        print(f"\n最近 {days} 天的 turn 摘要:")
        for t in turns:
            print(f"  [{t['project']}] {t['title']}")
            print(f"    → archive/{t['file_path']}")
    else:
        print(f"\n最近 {days} 天没有 turn 摘要。")


def main():
    if "--list" in sys.argv:
        store = Store()
        list_recent(store)
        return

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    kwargs = {}
    i = 0
    all_args = sys.argv[1:]
    while i < len(all_args):
        if all_args[i] == "--type" and i + 1 < len(all_args):
            kwargs["search_type"] = all_args[i + 1]
            i += 2
        elif all_args[i] == "--project" and i + 1 < len(all_args):
            kwargs["project"] = all_args[i + 1]
            i += 2
        elif all_args[i] == "--days" and i + 1 < len(all_args):
            kwargs["days"] = int(all_args[i + 1])
            i += 2
        else:
            i += 1

    if not args:
        print("Usage: recall.py [--type daily|monthly] [--project <slug>] "
              "[--days N] [--list] \"query\"")
        return

    query = args[0]
    store = Store()
    search_type = kwargs.get("search_type", "all")
    project = kwargs.get("project")
    days = kwargs.get("days")

    results = []

    if search_type in ("all", "turns"):
        r = search_turn_summaries(store, query, project, days)
        if r:
            print(f"\n── Turn 摘要 ({len(r)} 条) ──")
            for item in r:
                print(f"  [{item['project']}] {item['title']}")
                print(f"    {item['summary'][:200]}")
                print(f"    → archive/{item['file_path']}")
            results.extend(r)

    if search_type in ("all", "daily"):
        r = search_daily_reports(store, query, project)
        if r:
            print(f"\n── 日报 ({len(r)} 条) ──")
            for item in r:
                print(f"  [{item['date']}] {item['title']}")
                snippet = item["content"][:200].replace("\n", " ")
                print(f"    {snippet}...")
                print(f"    → archive/{item['file_path']}")
            results.extend(r)

    if search_type in ("all", "monthly"):
        r = search_monthly_reports(store, query, project)
        if r:
            print(f"\n── 月报 ({len(r)} 条) ──")
            for item in r:
                print(f"  [{item['month']}] {item['title']}")
                snippet = item["content"][:200].replace("\n", " ")
                print(f"    {snippet}...")
                print(f"    → archive/{item['file_path']}")
            results.extend(r)

    if not results:
        print(f"  未找到匹配 \"{query}\" 的结果。")

    print(f"\n共 {len(results)} 条结果。")


if __name__ == "__main__":
    main()
