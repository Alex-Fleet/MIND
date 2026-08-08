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
from log_setup import setup_logger
from projects import Registry
from store import Store


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


# ═══════════════════════════════════════════════════════════════
# Section builders — 每个输出一个独立章节，自然段落边界切断。
# 拆段是为了绕开 Claude Code 的 10K hook 输出上限：
#   每个 command 独立 10K 预算，5 段 × 10K = 50K 总预算。
# ═══════════════════════════════════════════════════════════════

def _build_global_memory(store: Store,
                         _project_slugs: list[str] | None = None) -> str:
    """全局记忆: memory/global/*.md 全部文件（向后兼容）。"""
    memory_dir = get_paths()["base_dir"] / "memory"
    global_dir = memory_dir / "global"
    lines: list[str] = []

    if global_dir.is_dir():
        sources = _read_memory_dir(global_dir, memory_dir)
    else:
        sources = [(h, c, r) for h, c, r in _read_memory_dir(memory_dir)
                   if ".example" not in str(h)]

    for heading, content, rel_path in sources:
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

    if not lines:
        lines.append("（暂无全局记忆）")
        lines.append("")
    return "\n".join(lines)


def _safe_truncate(text: str, max_bytes: int = 9500) -> str:
    """在 max_bytes 以内的最后一个自然段落边界截断。
    优先级: ## heading > 空行 > 句号/问号/感叹号 > 硬截断。
    所有长度按 UTF-8 字节计算（Claude Code hook 阈值是字节）。"""
    text_bytes = len(text.encode("utf-8"))
    if text_bytes <= max_bytes:
        return text

    # 截取 max_bytes 内的字符（处理多字节边界）
    raw = text.encode("utf-8")[:max_bytes]
    chunk = raw.decode("utf-8", errors="ignore")  # 丢弃不完整的多字节字符

    # 在 chunk 中找最后一个 ## heading
    heading_pos = chunk.rfind("\n## ")
    if heading_pos > len(chunk) * 0.6:
        truncated = text[:heading_pos].rstrip()
        return (truncated +
                f"\n\n（完整内容见 memory/ 对应文件，已截断约 {text_bytes - len(truncated.encode('utf-8'))} 字节）")

    # 找最后一个段落空行
    para_pos = chunk.rfind("\n\n")
    if para_pos > len(chunk) * 0.7:
        return text[:para_pos].rstrip() + \
               "\n\n（完整内容见 memory/ 对应文件）"

    # 找最后一个句子边界
    for punct in ["。\n", "！\n", "？\n", ".\n", "!\n", "?\n"]:
        punct_pos = chunk.rfind(punct)
        if punct_pos > len(chunk) * 0.7:
            return text[:punct_pos + 1] + \
                   "\n\n（完整内容见 memory/ 对应文件）"

    # 硬截断（按空白字符）
    last_space = chunk.rfind(" ")
    if last_space > len(chunk) * 0.8:
        return text[:last_space] + "\n\n（完整内容见 memory/ 对应文件）"

    return chunk + "\n\n（完整内容见 memory/ 对应文件）"


def _build_global_file(store: Store, filename: str) -> str:
    """输出 memory/global/<filename> 单个文件，超长安全截断。"""
    memory_dir = get_paths()["base_dir"] / "memory"
    global_dir = memory_dir / "global"
    fpath = global_dir / filename if global_dir.is_dir() else memory_dir / filename

    if not fpath.is_file():
        return f"（全局记忆文件不存在: {filename}）\n"

    try:
        content = fpath.read_text(encoding="utf-8").strip()
    except Exception:
        return f"（无法读取: {filename}）\n"

    if not content:
        return f"（空文件: {filename}）\n"

    rel_path = str(fpath.relative_to(memory_dir))
    full_path = f"memory/{rel_path}"
    deleted = _get_deleted_sections(store, full_path)
    if deleted:
        content = _strip_deleted_from_content(content, deleted)
    if not content.strip():
        return ""

    heading = _heading_from_file(fpath)
    output = f"## MIND 记忆 — {heading}\n\n{content}\n"

    # 安全截断（文件级自然边界，不在句中切断）
    return _safe_truncate(output)


def _build_project_memory(store: Store,
                          project_slugs: list[str] | None) -> str:
    """项目专属记忆: memory/projects/<id>/*.md 文件内容。"""
    if not project_slugs:
        return ""

    memory_dir = get_paths()["base_dir"] / "memory"
    project_id = _resolve_project_id(project_slugs[0])
    project_dir = memory_dir / "projects" / (project_id or "")
    if not project_dir.is_dir():
        return ""

    entries = _read_memory_dir(project_dir, memory_dir)
    if not entries:
        return ""

    lines: list[str] = []
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

    return "\n".join(lines) if len(lines) > 2 else ""


def _build_turns_section(store: Store,
                         project_slugs: list[str] | None,
                         limit: int = 150) -> str:
    """最近 7 天 turn 摘要。"""
    cfg = load_config()
    turn_days = cfg["windows"]["turn_days"]
    lines = [f"## 最近 {turn_days} 天动态", ""]
    turns = store.get_turn_summaries_in_window(
        turn_days, project=project_slugs, limit=limit)
    if turns:
        for t in turns:
            v = t.get("validity")
            if v in ("invalid", "merged"):
                continue
            lines.append(f"### {t['title']}")
            lines.append(t["summary"])
            lines.append(f"→ [查看完整摘要](../data/archive/{t['file_path']})")
            lines.append("")
    else:
        lines.append("（暂无）")
        lines.append("")
    return "\n".join(lines)


def _build_dailies_section(store: Store,
                           project_slugs: list[str] | None,
                           limit: int | None = None) -> str:
    """最近 30 天日报。"""
    cfg = load_config()
    daily_days = cfg["windows"]["daily_days"]
    lines = [f"## 最近 {daily_days} 天日报", ""]
    dailies = store.get_daily_reports_in_window(
        daily_days, project=project_slugs)
    if dailies:
        for d in (dailies[:limit] if limit else dailies):
            lines.append(f"### {d['date']}: {d['title']}")
            lines.append(d["content"])
            lines.append(f"→ [日报详情](../data/archive/{d['file_path']})")
            lines.append("")
    else:
        lines.append("（暂无）")
        lines.append("")
    return "\n".join(lines)


def _build_monthlies_section(store: Store,
                             project_slugs: list[str] | None) -> str:
    """历史月报（永久保留）。"""
    lines = ["## 历史月报", ""]
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


SECTION_BUILDERS = {
    "global":     _build_global_memory,
    "project":    _build_project_memory,
    "turns":      _build_turns_section,
    "dailies":    _build_dailies_section,
    "monthlies":  _build_monthlies_section,
}


def build_section(store: Store, section: str,
                  project_slugs: list[str] | None = None) -> str:
    """Build a single named section for hook output."""
    builder = SECTION_BUILDERS.get(section)
    if builder is None:
        raise ValueError(
            f"Unknown section: {section!r}. "
            f"Valid: {', '.join(SECTION_BUILDERS)}")
    return builder(store, project_slugs)


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
    """Build the full systemMessage markdown (backward compat — all sections).
    For hook splitting, use build_section() instead."""
    parts: list[str] = []

    global_mem = _build_global_memory(store)
    if global_mem.strip():
        parts.append(global_mem)

    project_mem = _build_project_memory(store, project_slugs)
    if project_mem.strip():
        parts.append(project_mem)

    # ── Brief: turns + dailies + monthlies ──
    brief = build_brief_md(store, project_slugs=project_slugs)
    parts.append(brief)

    return "\n".join(parts)


def _extract_slug_from_stdin() -> str | None:
    """从 stdin JSON 中提取 CC 项目 slug。section 命令各自独立读 stdin。"""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return None
        data = json.loads(raw)
        tp = data.get("transcript_path", "")
        parts = tp.replace("\\", "/").rstrip("/").split("/")
        if len(parts) >= 2 and parts[-2] and parts[-2] != "projects":
            return parts[-2]
        return None
    except Exception:
        return None


def main():
    setup_logger()
    section = None
    try:
        si = sys.argv.index("--section")
        section = sys.argv[si + 1]
    except (ValueError, IndexError):
        pass

    global_file = None
    try:
        fi = sys.argv.index("--file")
        global_file = sys.argv[fi + 1]
    except (ValueError, IndexError):
        pass

    limit = None
    try:
        li = sys.argv.index("--limit")
        limit = int(sys.argv[li + 1])
    except (ValueError, IndexError):
        pass

    json_output = "--json-output" in sys.argv
    stdout = "--stdout" in sys.argv

    # ── 解析 --project <slug>（项目隔离）──
    project_slug = None
    try:
        pi = sys.argv.index("--project")
        project_slug = sys.argv[pi + 1]
    except (ValueError, IndexError):
        pass

    # Fallback: 从 stdin 提取 slug（section 模式无 --project 时）
    if not project_slug:
        project_slug = _extract_slug_from_stdin()

    project_slugs = _resolve_project_slugs(project_slug)

    paths = get_paths()
    store = Store()

    if section:
        # ── Section mode: 只输出一个章节 ──
        if section == "global" and global_file:
            output = _build_global_file(store, global_file)
        elif section == "global":
            output = _build_global_memory(store, project_slugs)
        elif section == "project":
            output = _build_project_memory(store, project_slugs)
        elif section == "turns":
            output = _build_turns_section(store, project_slugs,
                                          limit=limit or 30)
        elif section == "dailies":
            output = _build_dailies_section(store, project_slugs,
                                            limit=limit or 5)
        elif section == "monthlies":
            output = _build_monthlies_section(store, project_slugs)
        else:
            print(f"Unknown section: {section}", file=sys.stderr)
            sys.exit(1)

        print(output)
        store.log("inject_section", detail={
            "section": section,
            "file": global_file,
            "limit": limit,
            "chars": len(output),
            "project_slug": project_slug,
            "project_slugs": project_slugs,
        })
        return

    # ── Legacy: 完整重建 + 输出 ──
    result = rebuild_injected(store, paths)

    # Build systemMessage
    sys_msg = build_system_message(store, project_slugs=project_slugs)

    if json_output:
        # docstring 约定输出 {"systemMessage": "..."}（hook 用 --section，此处仅调试）
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
        "project_slug": project_slug,
        "project_slugs": project_slugs,
    })


if __name__ == "__main__":
    main()
