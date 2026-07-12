#!/usr/bin/env python3
"""
Ingest JSONL transcripts into SQLite — incremental, turn-level dedup.

Usage:
  python3 ingest.py                    # scan all projects
  python3 ingest.py --project <slug>   # single project
  python3 ingest.py --session <id>     # single session
"""

import json
import os
import sys
from pathlib import Path

from config import get_paths
from store import Store


def extract_text(content_blocks) -> str:
    """Extract plain text from Claude Code message content blocks."""
    if isinstance(content_blocks, str):
        return content_blocks
    texts = []
    for block in content_blocks:
        if isinstance(block, dict):
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                texts.append(f"[Tool: {block.get('name', '?')}]")
            elif block.get("type") == "thinking":
                t = block.get("thinking", "")
                if t:
                    texts.append(f"[thinking: {t[:100]}]")
    return "\n".join(texts)


def extract_tool_calls(content_blocks) -> str | None:
    """Extract tool_use block names as comma-separated string."""
    if isinstance(content_blocks, str):
        return None
    tools = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tools.append(block.get("name", "?"))
    return ",".join(tools) if tools else None


def ingest_jsonl(jsonl_path: str, store: Store,
                 project_slug: str, session_id: str) -> int:
    """Ingest one JSONL file. Returns number of new turns inserted."""
    if not os.path.exists(jsonl_path):
        return 0

    store.ensure_session(session_id, project_slug, jsonl_path)

    # Get last ingested seq for this session
    max_seq = store.get_max_seq(session_id)

    new_count = 0
    batch = []
    with open(jsonl_path) as f:
        for seq, line in enumerate(f):
            if seq <= max_seq:
                continue  # already ingested

            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = d.get("type", "")
            if msg_type not in ("user", "assistant"):
                continue

            message = d.get("message", {})
            content_blocks = message.get("content", [])
            role = message.get("role", msg_type)
            text = extract_text(content_blocks)
            if not text.strip():
                continue

            tools = extract_tool_calls(content_blocks)
            ts = d.get("timestamp", "")

            batch.append((
                session_id, seq, role, text,
                tools,
                json.dumps(d, ensure_ascii=False),
                ts
            ))

            if len(batch) >= 200:
                new_count += store.insert_turns_batch(batch)
                batch = []

    if batch:
        new_count += store.insert_turns_batch(batch)

    return new_count


def ingest_all(store: Store, project_filter: str | None = None,
               session_filter: str | None = None) -> dict:
    """Scan all projects and ingest new JSONL data.

    Returns dict with counts per project.
    """
    projects_dir = str(get_paths()["projects_dir"])
    if not os.path.isdir(projects_dir):
        return {}

    results = {}
    slugs = [project_filter] if project_filter else sorted(
        os.listdir(projects_dir)
    )

    for slug in slugs:
        proj_dir = os.path.join(projects_dir, slug)
        if not os.path.isdir(proj_dir):
            continue

        # Find JSONL files
        jsonl_files = []
        for fname in sorted(os.listdir(proj_dir)):
            if fname.endswith(".jsonl") and not fname.startswith("."):
                if session_filter and fname != f"{session_filter}.jsonl":
                    continue
                jsonl_files.append(fname)

        total = 0
        for fname in jsonl_files:
            sid = fname.replace(".jsonl", "")
            jsonl_path = os.path.join(proj_dir, fname)
            n = ingest_jsonl(jsonl_path, store, slug, sid)
            if n > 0:
                total += n

        if total > 0:
            results[slug] = total

    return results


def main():
    project_filter = None
    session_filter = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            project_filter = args[i + 1]
            i += 2
        elif args[i] == "--session" and i + 1 < len(args):
            session_filter = args[i + 1]
            i += 2
        else:
            i += 1

    store = Store()
    results = ingest_all(store, project_filter, session_filter)

    total = sum(results.values())
    if total > 0:
        for slug, n in results.items():
            print(f"  {slug}: {n} new turns")
        print(f"  Total: {total} new turns")
    else:
        print("  (no new turns)")

    store.log("ingest", detail={
        "total_turns": total,
        "projects": list(results.keys()),
    })


if __name__ == "__main__":
    main()
