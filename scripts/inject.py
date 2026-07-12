#!/usr/bin/env python3
"""
Build injection context: query active time windows from DB,
render injected/prefs.md + injected/brief.md,
output systemMessage JSON for Claude Code.

Usage:
  python3 inject.py                      # rebuild injected/ files
  python3 inject.py --json-output        # output {"systemMessage": "..."}
  python3 inject.py --stdout             # print brief.md to stdout
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths, load_config
from store import Store


def build_prefs_md(store: Store) -> str:
    """Build prefs.md from preferences table."""
    prefs = store.get_preferences()
    if not prefs:
        return "# 奶龙博士记忆 — 用户偏好\n\n（暂无偏好设置）\n"

    lines = ["# 奶龙博士记忆 — 用户偏好", ""]
    for p in prefs:
        section = p["section"]
        content = p["content"]
        if not content.strip():
            continue
        lines.append(f"## {section}")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


def build_brief_md(store: Store) -> str:
    """Build brief.md with time-pyramid context."""
    cfg = load_config()
    turn_days = cfg["windows"]["turn_days"]
    daily_days = cfg["windows"]["daily_days"]

    lines = [
        f"# 奶龙博士记忆简报 — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "> 这是最近的工作动态摘要。如需深挖某件事，请 Read 箭头指向的 archive 文件。",
        "",
    ]

    # ── Turn Summaries (recent N days) ──
    lines.append(f"## 最近 {turn_days} 天动态")
    lines.append("")
    turns = store.get_turn_summaries_in_window(turn_days)
    if turns:
        for t in turns:
            lines.append(f"### {t['title']}")
            lines.append(t["summary"])
            lines.append(f"→ [查看完整摘要](archive/{t['file_path']})")
            lines.append("")
    else:
        lines.append("（暂无）")
        lines.append("")

    # ── Daily Reports (recent N days) ──
    lines.append(f"## 最近 {daily_days} 天日报")
    lines.append("")
    dailies = store.get_daily_reports_in_window(daily_days)
    if dailies:
        for d in dailies:
            lines.append(f"### {d['date']}: {d['title']}")
            # Show first 300 chars of content as preview
            preview = d["content"][:300]
            if len(d["content"]) > 300:
                preview += "..."
            lines.append(preview)
            lines.append(f"→ [日报详情](archive/{d['file_path']})")
            lines.append("")
    else:
        lines.append("（暂无）")
        lines.append("")

    # ── Monthly Reports (all, permanent) ──
    lines.append("## 历史月报")
    lines.append("")
    monthlies = store.get_all_monthly_reports()
    if monthlies:
        for m in monthlies:
            lines.append(f"### {m['month']}: {m['title']}")
            preview = m["content"][:300]
            if len(m["content"]) > 300:
                preview += "..."
            lines.append(preview)
            lines.append(f"→ [月报详情](archive/{m['file_path']})")
            lines.append("")
    else:
        lines.append("（暂无）")
        lines.append("")

    return "\n".join(lines)


def rebuild_injected(store: Store, paths: dict) -> dict:
    """Rebuild all injected/ files. Returns {prefs_md, brief_md}."""
    os.makedirs(paths["injected_dir"], exist_ok=True)

    prefs_md = build_prefs_md(store)
    brief_md = build_brief_md(store)

    with open(paths["prefs_path"], "w", encoding="utf-8") as f:
        f.write(prefs_md)

    with open(paths["brief_path"], "w", encoding="utf-8") as f:
        f.write(brief_md)

    return {"prefs_md": prefs_md, "brief_md": brief_md}


def build_system_message(store: Store) -> str:
    """Build the systemMessage markdown for Claude Code injection."""
    prefs = store.get_preferences()

    # Only include the most critical preferences (priority >= 70)
    # to keep systemMessage concise. Full prefs always available
    # in injected/prefs.md + CLAUDE.md
    critical_prefs = [p for p in prefs if p.get("priority", 0) >= 70]

    lines = []

    # ── Critical Preferences (compact) ──
    if critical_prefs:
        lines.append("## 奶龙博士记忆 — 偏好")
        lines.append("")
        for p in critical_prefs:
            lines.append(p["content"])
        lines.append("")

    # ── Brief ──
    brief = build_brief_md(store)
    lines.append(brief)

    return "\n".join(lines)


def main():
    json_output = "--json-output" in sys.argv
    stdout = "--stdout" in sys.argv

    paths = get_paths()
    store = Store()

    # Rebuild injected/ files
    result = rebuild_injected(store, paths)

    # Build systemMessage
    sys_msg = build_system_message(store)

    if json_output:
        print(json.dumps({"systemMessage": sys_msg}, ensure_ascii=False))
    elif stdout:
        print(sys_msg)
    else:
        print(f"  ✓ injected/prefs.md  ({len(result['prefs_md'])} chars)")
        print(f"  ✓ injected/brief.md  ({len(result['brief_md'])} chars)")
        print(f"  ✓ systemMessage      ({len(sys_msg)} chars)")

    store.log("inject", detail={
        "prefs_chars": len(result["prefs_md"]),
        "brief_chars": len(result["brief_md"]),
        "sysmsg_chars": len(sys_msg),
    })


if __name__ == "__main__":
    main()
