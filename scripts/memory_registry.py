#!/usr/bin/env python3
"""
Memory registry — tracks metadata for memory/*.md sections.

Weight model: Ebbinghaus forgetting curve
  w_effective = base_weight * exp(-days / (30 * base_weight))

Usage:
  python3 memory_registry.py --init            # first scan of memory/*.md
  python3 memory_registry.py --sync <path>      # re-scan one file
  python3 memory_registry.py --check            # decay check (dry-run report)
"""

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import BASE_DIR
from store import Store

MEMORY_DIR = BASE_DIR / "memory"


def effective_weight(base_weight: float, last_confirmed: str | None,
                     created_at: str | None = None) -> float:
    """Compute effective weight using Ebbinghaus decay.

    w_effective = base_weight * exp(-days / (30 * base_weight))

    Stronger memories (higher base_weight) decay slower.
    If base_weight <= 0, returns 0.
    If no timestamp available, returns base_weight (no decay).
    """
    if base_weight <= 0:
        return 0.0

    ts_str = last_confirmed or created_at
    if not ts_str:
        return base_weight

    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return base_weight

    now = datetime.now(timezone.utc)
    days = (now - ts).total_seconds() / 86400.0
    if days <= 0:
        return base_weight

    # Ebbinghaus: half-life scales with base_weight
    tau = 30.0 * base_weight
    return round(base_weight * math.exp(-days / tau), 4)


def init_registry(store: Store | None = None) -> dict:
    """First scan: parse all memory/*.md files into registry entries.

    Each `## ` heading within a file becomes a registry entry.
    If a file has no `## ` headings, the whole file is one entry.
    Files whose stem contains '.example' are skipped (templates).

    Returns {"created": N, "skipped": M}
    """
    if store is None:
        store = Store()

    created = 0
    skipped = 0

    for scope_dir in _find_scope_dirs():
        for md_file in sorted(scope_dir.glob("*.md")):
            if ".example" in md_file.stem:
                continue

            file_path = str(md_file.relative_to(BASE_DIR))
            scope = _scope_from_dir(scope_dir)

            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                skipped += 1
                continue

            sections = _parse_sections(content)
            if not sections:
                # Whole file as one entry
                heading = _extract_heading(content)
                store.upsert_registry_entry(
                    file_path=file_path,
                    section_heading=None,
                    scope=scope,
                    base_weight=0.40,
                )
                created += 1
            else:
                for heading, _body in sections:
                    store.upsert_registry_entry(
                        file_path=file_path,
                        section_heading=heading,
                        scope=scope,
                        base_weight=0.40,
                    )
                    created += 1

    store.log("init_registry", detail={"created": created, "skipped": skipped})
    return {"created": created, "skipped": skipped}


def confirm(entry_id: int, store: Store | None = None) -> float:
    """User confirmed a memory: boost base_weight by 0.20."""
    if store is None:
        store = Store()

    entry = _get_entry(store, entry_id)
    if not entry:
        return 0.0

    before = entry["base_weight"]
    after = min(1.0, before + 0.20)
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    store.update_registry_weight(
        entry_id, after, last_confirmed=now_ts, confirmed_delta=1
    )
    store.insert_weight_log(entry_id, "confirm", before, after)
    return round(after, 4)


def boost_by_reference(entry_id: int, store: Store | None = None) -> float:
    """Memory was referenced by a new proposal: boost by 0.10."""
    if store is None:
        store = Store()

    entry = _get_entry(store, entry_id)
    if not entry:
        return 0.0

    before = entry["base_weight"]
    after = min(1.0, before + 0.10)
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    store.update_registry_weight(entry_id, after, last_confirmed=now_ts)
    store.insert_weight_log(entry_id, "boost_reference", before, after)
    return round(after, 4)


def survive_cycle(entry_id: int, store: Store | None = None) -> float:
    """Memory survived a review cycle: boost by 0.05."""
    if store is None:
        store = Store()

    entry = _get_entry(store, entry_id)
    if not entry:
        return 0.0

    before = entry["base_weight"]
    after = min(1.0, before + 0.05)
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    store.update_registry_weight(entry_id, after, last_confirmed=now_ts)
    store.insert_weight_log(entry_id, "survive_cycle", before, after)
    return round(after, 4)


def reject_reset(entry_id: int, store: Store | None = None) -> float:
    """User rejected a delete proposal: reset base_weight to 0.25."""
    if store is None:
        store = Store()

    entry = _get_entry(store, entry_id)
    if not entry:
        return 0.0

    before = entry["base_weight"]
    after = 0.25

    store.update_registry_weight(entry_id, after)
    store.insert_weight_log(entry_id, "reject_reset", before, after)
    return after


def decay_check(store: Store | None = None) -> dict:
    """Check all active registry entries for decay.

    Returns:
      {"delete_candidates": [...],   # w < 0.15
       "low_weight_alerts": [...],    # 0.15 <= w < 0.30
       "healthy": N}
    Each item: {id, file_path, section_heading, scope,
                base_weight, effective_weight, days_since}
    """
    if store is None:
        store = Store()

    entries = store.get_registry_entries(status="active")
    result = {"delete_candidates": [], "low_weight_alerts": [], "healthy": 0}

    for e in entries:
        w = effective_weight(
            e["base_weight"],
            e.get("last_confirmed"),
            e.get("created_at"),
        )
        ts_str = e.get("last_confirmed") or e.get("created_at", "")
        days = _days_since(ts_str) if ts_str else 0

        item = {
            "id": e["id"],
            "file_path": e["file_path"],
            "section_heading": e.get("section_heading"),
            "scope": e["scope"],
            "base_weight": e["base_weight"],
            "effective_weight": round(w, 4),
            "days_since_confirmed": days,
        }

        if w < 0.15:
            result["delete_candidates"].append(item)
        elif w < 0.30:
            result["low_weight_alerts"].append(item)
        else:
            result["healthy"] += 1

    return result


def get_by_scope(scope: str,
                 store: Store | None = None) -> list[dict]:
    """Get registry entries for a scope with effective weights."""
    if store is None:
        store = Store()

    entries = store.get_registry_entries(scope=scope, status="active")
    result = []
    for e in entries:
        w = effective_weight(
            e["base_weight"],
            e.get("last_confirmed"),
            e.get("created_at"),
        )
        e["effective_weight"] = round(w, 4)
        result.append(e)
    return result


# ── Helpers ──────────────────────────────────────────────


def _find_scope_dirs() -> list[Path]:
    """Find global/ and projects/<id>/ dirs under memory/."""
    dirs = []
    global_dir = MEMORY_DIR / "global"
    if global_dir.is_dir():
        dirs.append(global_dir)

    projects_dir = MEMORY_DIR / "projects"
    if projects_dir.is_dir():
        for subdir in sorted(projects_dir.iterdir()):
            if subdir.is_dir():
                dirs.append(subdir)

    # Fallback: old flat structure
    if not dirs and MEMORY_DIR.is_dir():
        dirs.append(MEMORY_DIR)

    return dirs


def _scope_from_dir(dir_path: Path) -> str:
    """Derive scope string from directory path."""
    rel = dir_path.relative_to(MEMORY_DIR)
    if rel == Path("global") or rel == Path("."):
        return "global"
    # projects/<id>/ → project:<id>
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "projects":
        return f"project:{'/'.join(parts[1:])}"
    return str(rel)


def _parse_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown content into ## sections.

    Returns list of (heading, body) tuples.
    If no ## headings found, returns empty list.
    """
    lines = content.split("\n")
    sections = []
    current_heading = None
    current_lines = []

    for line in lines:
        if line.startswith("## ") and not line.startswith("### "):
            if current_heading is not None and current_lines:
                sections.append((
                    current_heading,
                    "\n".join(current_lines).strip()
                ))
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)

    if current_heading is not None and current_lines:
        sections.append((
            current_heading,
            "\n".join(current_lines).strip()
        ))

    return sections


def _extract_heading(content: str) -> str:
    """Extract first # heading from content."""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return ""


def _get_entry(store: Store, entry_id: int) -> dict | None:
    """Get a single registry entry by id."""
    with __import__("sqlite3").connect(store.db_path) as conn:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT * FROM memory_registry WHERE id = ?", (entry_id,)
        ).fetchone()
        return dict(row) if row else None


def _days_since(ts_str: str) -> int:
    """Compute days since an ISO timestamp string."""
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, int(
            (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
        ))
    except (ValueError, TypeError):
        return 0


# ── CLI ──────────────────────────────────────────────────


def main():
    if "--init" in sys.argv:
        result = init_registry()
        print(f"Registry initialized: {result['created']} created, "
              f"{result['skipped']} skipped")
    elif "--check" in sys.argv:
        result = decay_check()
        print(f"\nHealthy: {result['healthy']}")
        print(f"Low weight alerts ({len(result['low_weight_alerts'])}):")
        for item in result["low_weight_alerts"]:
            print(f"  ⚠ [{item['scope']}] {item['file_path']} "
                  f"w={item['effective_weight']:.3f} "
                  f"({item['days_since_confirmed']}d)")
        print(f"\nDelete candidates ({len(result['delete_candidates'])}):")
        for item in result["delete_candidates"]:
            print(f"  🔴 [{item['scope']}] {item['file_path']} "
                  f"w={item['effective_weight']:.3f} "
                  f"({item['days_since_confirmed']}d)")
    else:
        print("Usage: memory_registry.py --init | --check")


if __name__ == "__main__":
    main()
