#!/usr/bin/env python3
"""
One-time migration: old memory system → Nailong Doctor System.
Reads old data READ-ONLY, writes to new system.
Run once. Safe to re-run (idempotent).
"""

import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add scripts dir to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths
from store import Store

OLD_DB = get_paths()["old_memory_db"]
OLD_MEMORY_DIR = Path(os.path.expanduser("~/.claude/memory"))
OLD_PREFS = OLD_MEMORY_DIR / "preferences.md"
OLD_USER_PREFS = OLD_MEMORY_DIR / "user-preferences.md"


def copy_old_db(paths: dict) -> None:
    """Copy old memory.db to archive/old/ (read-only reference)."""
    dest = paths["old_db_dir"] / "memory.db"
    os.makedirs(paths["old_db_dir"], exist_ok=True)
    if not dest.exists() and OLD_DB.exists():
        shutil.copy2(OLD_DB, dest)
        print(f"  ✓ 复制旧 DB → {dest}")
    else:
        print(f"  - 旧 DB 副本已存在，跳过")


def migrate_conversations(store: Store) -> int:
    """Import old conversations into new turns table as legacy."""
    if not OLD_DB.exists():
        print("  - 旧 DB 不存在，跳过对话迁移")
        return 0

    old_conn = sqlite3.connect(str(OLD_DB))
    old_conn.row_factory = sqlite3.Row

    count = 0
    rows = old_conn.execute(
        "SELECT session_id, project, turn, role, content, tool_calls, timestamp "
        "FROM conversations ORDER BY session_id, turn"
    ).fetchall()

    batch = []
    for r in rows:
        legacy_sid = f"legacy_{r['session_id']}"
        store.ensure_session(
            legacy_sid, r["project"],
            "[legacy_archive]"
        )
        batch.append((
            legacy_sid, r["turn"], r["role"],
            r["content"], r["tool_calls"],
            json.dumps({"legacy": True, "source": "old_memory_db"},
                       ensure_ascii=False),
            r["timestamp"]
        ))
        if len(batch) >= 500:
            count += store.insert_turns_batch(batch)
            batch = []
    if batch:
        count += store.insert_turns_batch(batch)

    old_conn.close()
    print(f"  ✓ 迁移旧对话: {count} turns")
    return count


def migrate_memories(store: Store, paths: dict) -> int:
    """Import old memories (10 items) into archive/legacy-memories/."""
    if not OLD_DB.exists():
        print("  - 旧 DB 不存在，跳过记忆迁移")
        return 0

    old_conn = sqlite3.connect(str(OLD_DB))
    old_conn.row_factory = sqlite3.Row
    rows = old_conn.execute(
        "SELECT slug, title, content, type, tags, created_at "
        "FROM memories ORDER BY created_at"
    ).fetchall()
    old_conn.close()

    os.makedirs(paths["legacy_dir"], exist_ok=True)
    count = 0
    for r in rows:
        filepath = paths["legacy_dir"] / f"{r['slug']}.md"
        if filepath.exists():
            continue
        frontmatter = (
            f"---\n"
            f"title: {r['title']}\n"
            f"type: {r['type']}\n"
            f"tags: {r['tags']}\n"
            f"date: {r['created_at']}\n"
            f"source: old_memory_db\n"
            f"---\n\n"
            f"# {r['title']}\n\n"
            f"{r['content']}\n"
        )
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(frontmatter)
        count += 1

    print(f"  ✓ 迁移旧记忆: {count} 条")
    return count


def parse_preferences_md(text: str) -> dict[str, str]:
    """Parse a preferences.md file into sections.
    Returns {section_name: content}."""
    sections = {}
    current_section = "header"
    current_content = []

    for line in text.splitlines():
        # Match ## headers
        if line.startswith("## "):
            if current_content:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = line[3:].strip()
            current_content = []
        elif line.startswith("# "):
            if current_content:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = "header"
            current_content = [line]
        else:
            current_content.append(line)

    if current_content:
        sections[current_section] = "\n".join(current_content).strip()

    return sections


def migrate_preferences(store: Store) -> int:
    """Merge old preferences.md + user-preferences.md into preferences table."""
    priority_map = {
        "铁律": 100,
        "六条铁律": 100,
        "工作风格": 80,
        "干活流程": 80,
        "沟通": 70,
        "沟通风格": 70,
        "代码风格": 60,
        "Prompt工程哲学": 50,
        "会话行为规则": 40,
        "开发环境": 30,
    }

    all_sections = {}

    # Read user-preferences.md first (more structured, primary source)
    if OLD_USER_PREFS.exists():
        with open(OLD_USER_PREFS, encoding="utf-8") as f:
            sections = parse_preferences_md(f.read())
            all_sections.update(sections)

    # Read preferences.md (supplement, adds workflow + code style details)
    if OLD_PREFS.exists():
        with open(OLD_PREFS, encoding="utf-8") as f:
            sections = parse_preferences_md(f.read())
            # Only add sections not already present
            for k, v in sections.items():
                if k not in all_sections:
                    all_sections[k] = v

    count = 0
    for section, content in all_sections.items():
        if not content.strip() or section in ("header", "用户偏好",
                                               "用户偏好（本项目补充）"):
            continue
        # Normalize section name
        priority = 0
        for key, pri in priority_map.items():
            if key in section:
                priority = pri
                break
        store.set_preference(section, content, priority)
        count += 1
        print(f"  ✓ 偏好 [{section}] (priority={priority})")

    print(f"  ✓ 合并偏好: {count} 个 section")
    return count


def main():
    paths = get_paths()
    store = Store()

    print("🐉 奶龙博士系统 — 数据迁移")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    # 1. Copy old DB
    print("[1/4] 复制旧数据库...")
    copy_old_db(paths)

    # 2. Migrate conversations
    print("\n[2/4] 迁移旧对话...")
    n_turns = migrate_conversations(store)

    # 3. Migrate memories
    print("\n[3/4] 迁移旧记忆...")
    n_memories = migrate_memories(store, paths)

    # 4. Migrate preferences
    print("\n[4/4] 合并用户偏好...")
    n_prefs = migrate_preferences(store)

    # Summary
    stats = store.get_stats()
    print()
    print("=" * 50)
    print("迁移完成！")
    print(f"  旧对话: {n_turns} turns (legacy)")
    print(f"  旧记忆: {n_memories} 条")
    print(f"  偏好: {n_prefs} sections")
    print(f"  数据库总计:")
    print(f"    sessions:     {stats['sessions']}")
    print(f"    turns:        {stats['turns']}")
    print(f"    turn摘要:     {stats['turn_summaries']}")
    print(f"    日报:         {stats['daily_reports']}")
    print(f"    月报:         {stats['monthly_reports']}")
    print()
    print(f"  旧文件原封不动保留在: {OLD_MEMORY_DIR}")
    print(f"  旧 DB 副本: {paths['old_db_dir'] / 'memory.db'}")

    store.log("migration", detail={
        "legacy_turns": n_turns,
        "legacy_memories": n_memories,
        "preferences_sections": n_prefs,
    })


if __name__ == "__main__":
    main()
