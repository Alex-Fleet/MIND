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
from projects import Registry


def _resolve_project_slugs(slug: str) -> list[str] | None:
    """Given a CC slug, return all sibling slugs if registered, else [slug].
    Returns None if no --project was provided (inject everything, backward compat)."""
    if not slug:
        return None
    reg = Registry.load()
    if reg.is_registered(slug):
        return reg.sibling_slugs(slug)
    return [slug]  # unregistered slug: inject only its own memories


def _heading_from_file(fpath: Path) -> str:
    """Extract heading from the first '# Title' line of a markdown file.
    Falls back to stem-to-title if no # line found."""
    try:
        first = fpath.read_text(encoding="utf-8").strip().split("\n")[0]
        if first.startswith("# "):
            return first[2:].strip()
    except Exception:
        pass
    # Fallback: filename stem → Title Case
    return fpath.stem.replace("-", " ").replace("_", " ").title()


def _read_memory_dir(memory_dir: Path, base_dir: Path | None = None
                     ) -> list[tuple[str, str, str]]:
    """Glob *.md in a directory, return [(heading, content, rel_path), ...]
    sorted by filename.  rel_path is relative to base_dir (or memory_dir).
    Returns empty list if directory doesn't exist."""
    if base_dir is None:
        base_dir = memory_dir
    if not memory_dir.is_dir():
        return []
    results = []
    for fpath in sorted(memory_dir.glob("*.md")):
        try:
            content = fpath.read_text(encoding="utf-8").strip()
            if content:
                heading = _heading_from_file(fpath)
                rel = str(fpath.relative_to(base_dir))
                results.append((heading, content, rel))
        except Exception:
            continue
    return results


def _get_deleted_sections(store: Store, file_path: str) -> set[str | None]:
    """Return set of section_headings marked 'deleted' for a given file.
    None in the set means the whole file is deleted."""
    entries = store.get_registry_entries(
        file_path=file_path, status="deleted"
    )
    return {e.get("section_heading") for e in entries}


def _strip_deleted_from_content(content: str,
                                 deleted_sections: set[str | None]) -> str:
    """Remove deleted sections from markdown content.
    If None is in deleted_sections, the whole file is deleted → return ''."""
    if None in deleted_sections:
        return ""
    if not deleted_sections:
        return content

    for section in deleted_sections:
        if not section:
            continue
        clean = section.strip().lstrip("#").strip()
        lines = content.split("\n")
        result = []
        skip = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## ") and not stripped.startswith("### "):
                if skip:
                    skip = False
                if stripped[3:].strip() == clean:
                    skip = True
                    continue
            if not skip:
                result.append(line)
        # Clean trailing blanks
        while result and result[-1].strip() == "":
            result.pop()
        if result:
            result.append("")
        content = "\n".join(result)

    return content


def _resolve_project_id(slug: str | None) -> str | None:
    """CC slug → stable project id (via Registry). Unregistered → slug itself."""
    if not slug:
        return None
    reg = Registry.load()
    return reg.resolve(slug) or slug


def build_prefs_md(store: Store | None = None) -> str:
    """Build prefs.md from memory/ markdown files (editable source of truth).
    Two-tier: memory/global/*.md (always) + memory/projects/<id>/*.md (per-project).
    Falls back to flat memory/*.md if global/ doesn't exist yet (upgrade compat)."""
    memory_dir = get_paths()["base_dir"] / "memory"
    global_dir = memory_dir / "global"
    lines = ["# MIND 记忆 — 用户偏好", ""]

    # ── Global memory ──
    if global_dir.is_dir():
        for heading, content, rel_path in _read_memory_dir(
            global_dir, memory_dir
        ):
            # Safety net: strip registry-deleted sections
            full_path = f"memory/{rel_path}"
            deleted = _get_deleted_sections(
                store, full_path
            ) if store else set()
            if deleted:
                content = _strip_deleted_from_content(content, deleted)
            if content.strip():
                lines.append(content)
                lines.append("")
    else:
        # Backward compat: flat files at memory/ root
        for heading, content, rel_path in _read_memory_dir(memory_dir):
            if ".example" in str(heading):
                continue
            full_path = f"memory/{rel_path}"
            deleted = _get_deleted_sections(
                store, full_path
            ) if store else set()
            if deleted:
                content = _strip_deleted_from_content(content, deleted)
            if content.strip():
                lines.append(content)
                lines.append("")

    return "\n".join(lines)


def build_brief_md(store: Store, project_slugs: list[str] | None = None) -> str:
    """Build brief.md with time-pyramid context.
    If `project_slugs` is provided, only inject memories from those slugs
    (project isolation). If None, inject everything (backward compat)."""
    cfg = load_config()
    turn_days = cfg["windows"]["turn_days"]
    daily_days = cfg["windows"]["daily_days"]

    lines = [
        f"# MIND 记忆简报 — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "> 这是最近的工作动态摘要。如需深挖某件事，请 Read 箭头指向的 archive 文件。",
        "",
    ]

    # ── Turn Summaries (recent N days, soft cap 150) ──
    lines.append(f"## 最近 {turn_days} 天动态")
    lines.append("")
    turns = store.get_turn_summaries_in_window(
        turn_days, project=project_slugs, limit=150)
    if turns:
        for t in turns:
            v = t.get("validity")
            if v in ("invalid", "merged"):
                continue  # 噪音或已合并，不注入
            lines.append(f"### {t['title']}")
            lines.append(t["summary"])
            lines.append(f"→ [查看完整摘要](../data/archive/{t['file_path']})")
            lines.append("")
    else:
        lines.append("（暂无）")
        lines.append("")

    # ── Daily Reports (recent N days, full content, no truncation) ──
    lines.append(f"## 最近 {daily_days} 天日报")
    lines.append("")
    dailies = store.get_daily_reports_in_window(
        daily_days, project=project_slugs)
    if dailies:
        for d in dailies:
            lines.append(f"### {d['date']}: {d['title']}")
            # 不截断——日报正文只含总结(~1.5K字)，来源清单已剥离到索引文件
            lines.append(d["content"])
            lines.append(f"→ [日报详情](../data/archive/{d['file_path']})")
            lines.append("")
    else:
        lines.append("（暂无）")
        lines.append("")

    # ── Monthly Reports (all, permanent) ──
    lines.append("## 历史月报")
    lines.append("")
    monthlies = store.get_all_monthly_reports(project=project_slugs)
    if monthlies:
        for m in monthlies:
            lines.append(f"### {m['month']}: {m['title']}")
            preview = m["content"][:300]
            if len(m["content"]) > 300:
                preview += "..."
            lines.append(preview)
            lines.append(f"→ [月报详情](../data/archive/{m['file_path']})")
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


def build_system_message(store: Store, project_slugs: list[str] | None = None) -> str:
    """Build the systemMessage markdown for Claude Code injection.
    Two-tier memory: memory/global/*.md (always) + memory/projects/<id>/*.md (per-project).
    Falls back to flat memory/*.md if global/ doesn't exist yet (upgrade compat)."""
    memory_dir = get_paths()["base_dir"] / "memory"
    global_dir = memory_dir / "global"
    lines = []

    # ── Global Memory ──
    if global_dir.is_dir():
        for heading, content, rel_path in _read_memory_dir(
            global_dir, memory_dir
        ):
            full_path = f"memory/{rel_path}"
            deleted = _get_deleted_sections(store, full_path)
            if deleted:
                content = _strip_deleted_from_content(content, deleted)
            if not content.strip():
                continue
            lines.append(f"## MIND 记忆 — {heading}")
            lines.append("")
            lines.append(content)
            lines.append("")
    else:
        # Backward compat: flat files at memory/ root
        for heading, content, rel_path in _read_memory_dir(memory_dir):
            if ".example" in str(heading):
                continue
            full_path = f"memory/{rel_path}"
            deleted = _get_deleted_sections(store, full_path)
            if deleted:
                content = _strip_deleted_from_content(content, deleted)
            if not content.strip():
                continue
            lines.append(f"## MIND 记忆 — {heading}")
            lines.append("")
            lines.append(content)
            lines.append("")

    # ── Project Memory ──
    if project_slugs:
        project_id = _resolve_project_id(project_slugs[0])
        project_dir = memory_dir / "projects" / (project_id or "")
        if project_dir.is_dir():
            entries = _read_memory_dir(project_dir, memory_dir)
            if entries:
                reg = Registry.load()
                label = reg.label_of(project_id) if project_id else project_id
                lines.append(f"## MIND 记忆 — 项目: {label}")
                lines.append("")
                for _heading, content, rel_path in entries:
                    full_path = f"memory/{rel_path}"
                    deleted = _get_deleted_sections(store, full_path)
                    if deleted:
                        content = _strip_deleted_from_content(content, deleted)
                    if content.strip():
                        lines.append(content)
                        lines.append("")

    # ── Brief (from DB, project-filtered) ──
    brief = build_brief_md(store, project_slugs=project_slugs)
    lines.append(brief)

    return "\n".join(lines)


def main():
    json_output = "--json-output" in sys.argv
    stdout = "--stdout" in sys.argv

    # ── 解析 --project <slug>（项目隔离）──
    project_slug = None
    try:
        pi = sys.argv.index("--project")
        project_slug = sys.argv[pi + 1]
    except (ValueError, IndexError):
        pass

    project_slugs = _resolve_project_slugs(project_slug)

    paths = get_paths()
    store = Store()

    # Rebuild injected/ files
    result = rebuild_injected(store, paths)

    # Build systemMessage
    sys_msg = build_system_message(store, project_slugs=project_slugs)

    if json_output:
        # 纯文本 stdout → VS Code 插件自动注入为上下文。
        # （过去包在 {"systemMessage": ...} JSON 里，插件不吃。）
        print(sys_msg)
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
        "project_slug": project_slug,
        "project_slugs": project_slugs,
    })


if __name__ == "__main__":
    main()
