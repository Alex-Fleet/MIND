#!/usr/bin/env python3
"""
MIND 看板 — 本地服务器（Python 标准库，零外部依赖，只绑 127.0.0.1）。

路由：
  /            → 看板页面 dashboard/index.html
  /api/feed    → 实时查 DB：统一时间线(turn/日报/月报) + 统计 + 项目列表(JSON)

用法：
  python3 scripts/dashboard_server.py            # 用 config.json 里的端口(默认 8765)
  python3 scripts/dashboard_server.py --port 9000
"""

import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths, load_config, BASE_DIR
from projects import Registry, slugify_id, basename_key, VALID_TYPES
from memory_registry import effective_weight
from store import Store

PATHS = get_paths()
DB = str(PATHS["db_path"])
HTML = BASE_DIR / "dashboard" / "index.html"
PROJECTS_HTML = BASE_DIR / "dashboard" / "projects.html"
LIVE_REGISTRY = BASE_DIR / "projects.json"
DRAFT_REGISTRY = BASE_DIR / "projects.draft.json"
TYPE_ORDER = ["long_term", "one_off", "archived"]


def _connect():
    """只读连库，带 busy 超时，避免和 hook 写入抢锁时报错。"""
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)


def _make_pretty(slugs):
    """剥掉所有项目共有的路径前缀，给出短显示名。"""
    seglists = [s.strip("-").split("-") for s in slugs if s]
    n = 0
    if seglists:
        for segs in zip(*seglists):
            if len(set(segs)) == 1:
                n += 1
            else:
                break

    def pretty(slug):
        if not slug:
            return "(未知)"
        segs = slug.strip("-").split("-")
        tail = segs[n:] if n < len(segs) else segs[-1:]
        return "-".join(tail) if tail else slug
    return pretty


def _norm_ts(ts):
    """把各种存储形态归一成 ISO(带 Z)，前端转本地时区显示：
      - 'YYYY-MM-DDTHH:MM:SS.sssZ'(turns.timestamp 原始) → 原样
      - 'YYYY-MM-DD HH:MM:SS'(summarized_at/created_at, UTC) → 补 T 和 Z
      - 'YYYY-MM-DD'(daily.date) → 当天正午 Z，避免跨时区错位到前一天
      - 'YYYY-MM'(monthly.month) → 当月 1 号正午 Z
    """
    if not ts:
        return None
    ts = ts.strip()
    if "T" in ts:                      # 已是 ISO
        return ts if ts.endswith("Z") else ts + "Z"
    if len(ts) == 10:                  # YYYY-MM-DD
        return ts + "T12:00:00Z"
    if len(ts) == 7:                   # YYYY-MM
        return ts + "-01T12:00:00Z"
    return ts.replace(" ", "T") + "Z"  # YYYY-MM-DD HH:MM:SS


def build_feed():
    conn = _connect()
    conn.row_factory = sqlite3.Row

    projects = [r[0] for r in conn.execute(
        "SELECT DISTINCT project FROM turn_summaries "
        "UNION SELECT DISTINCT project FROM daily_reports "
        "UNION SELECT DISTINCT project FROM monthly_reports").fetchall()
        if r[0]]  # 过滤空字符串（合并型 turn 等）
    pretty = _make_pretty(projects)
    reg, _ = _load_registry_with_source()

    def plabel(slug):
        """有注册表就用真身显示名，否则退回剥前缀的短名。"""
        return reg.label_of(slug) if reg.is_registered(slug) else pretty(slug)

    def ptype(slug):
        return reg.type_of(slug) if reg.is_registered(slug) else ""

    items = []
    for r in conn.execute(
        "SELECT summarized_at, project, title, summary AS body, file_path, validity, "
        "(SELECT substr(content,1,600) FROM turns t "
        " WHERE t.session_id=turn_summaries.session_id "
        " AND t.seq=turn_summaries.turn_seq AND t.role='user' LIMIT 1) AS user_input, "
        "(SELECT timestamp FROM turns t "
        " WHERE t.session_id=turn_summaries.session_id "
        " AND t.seq=turn_summaries.turn_seq AND t.role='user' LIMIT 1) AS real_ts "
        "FROM turn_summaries ORDER BY real_ts IS NULL, real_ts DESC, summarized_at DESC LIMIT 1000"):
        items.append({"type": "turn",
                      "ts": _norm_ts(r["real_ts"]) or _norm_ts(r["summarized_at"]),
                      "summarized_at": _norm_ts(r["summarized_at"]),
                      "project": r["project"], "project_name": plabel(r["project"]), "project_type": ptype(r["project"]),
                      "title": r["title"], "body": r["body"],
                      "user_input": r["user_input"], "file": r["file_path"],
                      "validity": r["validity"]})
    for r in conn.execute(
        "SELECT date, created_at, project, title, content AS body, file_path "
        "FROM daily_reports ORDER BY date DESC"):
        items.append({"type": "daily", "ts": _norm_ts(r["date"]),
                      "summarized_at": _norm_ts(r["created_at"]),
                      "project": r["project"], "project_name": plabel(r["project"]), "project_type": ptype(r["project"]),
                      "title": r["title"], "body": r["body"], "file": r["file_path"]})
    for r in conn.execute(
        "SELECT month, created_at, project, title, content AS body, file_path "
        "FROM monthly_reports ORDER BY month DESC"):
        items.append({"type": "monthly", "ts": _norm_ts(r["month"]),
                      "summarized_at": _norm_ts(r["created_at"]),
                      "project": r["project"], "project_name": plabel(r["project"]), "project_type": ptype(r["project"]),
                      "title": r["title"], "body": r["body"], "file": r["file_path"]})

    items.sort(key=lambda x: x["ts"] or "", reverse=True)

    stats = {
        "projects": len(projects),
        "turn": conn.execute("SELECT COUNT(*) FROM turn_summaries").fetchone()[0],
        "daily": conn.execute("SELECT COUNT(*) FROM daily_reports").fetchone()[0],
        "monthly": conn.execute("SELECT COUNT(*) FROM monthly_reports").fetchone()[0],
        "backlog": conn.execute(
            "SELECT COUNT(*) FROM turns t WHERE t.role='user' AND NOT EXISTS "
            "(SELECT 1 FROM turn_summaries ts WHERE ts.session_id=t.session_id "
            "AND ts.turn_seq=t.seq)").fetchone()[0],
        "last": items[0]["ts"] if items else None,
    }
    seen_labels = {}
    for p in sorted(projects):
        lbl = plabel(p)
        seen_labels.setdefault(lbl, ptype(p))
    proj_list = [{"name": lbl, "type": t} for lbl, t in seen_labels.items()]
    conn.close()
    return {"items": items, "stats": stats, "projects": proj_list}


# ── 项目管理（Trello 看板）────────────────────────────────

def _all_slug_stats(conn):
    """每个 slug 的统计 + 最多 3 条用户原话样本（只读）。"""
    rows = conn.execute(
        "SELECT s.project AS slug, COUNT(DISTINCT s.id) AS sessions, "
        "COUNT(t.id) AS turns, MIN(t.timestamp) AS first_ts, "
        "MAX(t.timestamp) AS last_ts "
        "FROM sessions s LEFT JOIN turns t ON t.session_id=s.id "
        "GROUP BY s.project").fetchall()
    out = {}
    for r in rows:
        samples = [x[0].replace("\n", " ")[:60] for x in conn.execute(
            "SELECT t.content FROM turns t JOIN sessions s ON t.session_id=s.id "
            "WHERE s.project=? AND t.role='user' AND length(trim(t.content))>4 "
            "ORDER BY t.timestamp LIMIT 3", (r["slug"],)).fetchall()]
        out[r["slug"]] = {
            "slug": r["slug"], "sessions": r["sessions"], "turns": r["turns"],
            "first": _norm_ts(r["first_ts"]), "last": _norm_ts(r["last_ts"]),
            "basename": basename_key(r["slug"]), "samples": samples}
    return out


def _load_registry_with_source():
    """优先已生效 projects.json，其次草稿 projects.draft.json，否则空。"""
    if LIVE_REGISTRY.exists():
        return Registry.load(LIVE_REGISTRY), "projects.json"
    if DRAFT_REGISTRY.exists():
        return Registry.load(DRAFT_REGISTRY), "projects.draft.json"
    return Registry([]), "empty"


def build_projects():
    """项目管理看板数据：当前分组 + 每个 slug 的统计 + 未分配 slug。"""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    stats = _all_slug_stats(conn)
    conn.close()
    reg, source = _load_registry_with_source()
    return {
        "projects": reg.projects,
        "slug_stats": stats,
        "unassigned": reg.unregistered(list(stats.keys())),
        "source": source,
        "types": TYPE_ORDER,
    }


def save_projects(payload):
    """把前端拖好的分组落 projects.json：服务端规范 id、跑双向唯一校验，过了才写。
    返回 (ok, errors)。绝不写坏文件（Registry.save 内部再校验一次）。"""
    conn = _connect()
    known = {r[0] for r in conn.execute(
        "SELECT DISTINCT project FROM sessions").fetchall()}
    conn.close()

    used_ids = set()

    def uniq_id(label):
        base = slugify_id(label)
        i, n = base, 2
        while i in used_ids or i in known:   # 不撞已用 id，也不撞任何 slug
            i, n = f"{base}-{n}", n + 1
        used_ids.add(i)
        return i

    projects = []
    for p in payload.get("projects", []):
        label = (p.get("label") or "").strip()
        slugs = [s for s in p.get("slugs", []) if s in known]  # 丢弃未知 slug
        if not label or not slugs:
            continue  # 跳过空列 / 无名列
        typ = p.get("type") if p.get("type") in VALID_TYPES else "long_term"
        pid = (p.get("id") or "").strip()
        if not pid or pid in used_ids or pid in known:
            pid = uniq_id(label)
        else:
            used_ids.add(pid)
        proj = {"id": pid, "label": label, "type": typ, "slugs": slugs}
        path = (p.get("path") or "").strip()
        if path:
            proj["path"] = path
        projects.append(proj)

    reg = Registry(projects)
    errs = reg.validate()
    if errs:
        return False, errs
    reg.save(LIVE_REGISTRY)
    return True, []


def slug_detail(slug):
    """某个 slug 的展开详情：更多用户原话 + 近几条 turn 摘要（供前端点开卡片看内容手工分类）。"""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    samples = [{"ts": _norm_ts(r["ts"]), "text": r["text"]} for r in conn.execute(
        "SELECT t.timestamp AS ts, substr(t.content,1,240) AS text "
        "FROM turns t JOIN sessions s ON t.session_id=s.id "
        "WHERE s.project=? AND t.role='user' AND length(trim(t.content))>4 "
        "ORDER BY t.timestamp DESC LIMIT 8", (slug,)).fetchall()]
    summaries = [{"title": r["title"], "summary": r["summary"]} for r in conn.execute(
        "SELECT ts.title, ts.summary FROM turn_summaries ts "
        "JOIN turns t ON t.session_id = ts.session_id "
        "AND t.seq = ts.turn_seq AND t.role = 'user' "
        "WHERE ts.project=? "
        "ORDER BY t.timestamp DESC LIMIT 8", (slug,)).fetchall()]
    conn.close()
    return {"slug": slug, "samples": samples, "summaries": summaries}


def fs_list(path):
    """只读列出某目录下的直接子目录（供前端文件夹选择器）。只绑本机 127.0.0.1，读用户自己的盘。"""
    base = os.path.abspath(os.path.expanduser(path or "~"))
    if not os.path.isdir(base):
        base = os.path.expanduser("~")
    dirs = []
    try:
        for name in sorted(os.listdir(base)):
            if name.startswith("."):
                continue
            full = os.path.join(base, name)
            if os.path.isdir(full):
                dirs.append({"name": name, "path": full})
    except PermissionError:
        pass
    parent = os.path.dirname(base)
    return {"path": base, "parent": parent if parent != base else None, "dirs": dirs}


# ── Memory Proposals & Registry API (v1.4) ──────────────────


def _mproposal_to_dict(row, store) -> dict:
    """Convert a memory_proposals sqlite3.Row to a dict."""
    d = dict(row)
    # Parse JSON fields
    for field in ("conflicts", "source_dates", "related_registry_ids"):
        if d.get(field) and isinstance(d[field], str):
            try:
                d[field] = __import__("json").loads(d[field])
            except Exception:
                pass
    return d


def build_proposals(status_filter: str | None = None):
    """List memory proposals, optionally filtered by status."""
    with __import__("sqlite3").connect(
        f"file:{DB}?mode=ro", uri=True, timeout=5
    ) as conn:
        conn.row_factory = __import__("sqlite3").Row
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM memory_proposals WHERE status = ? "
                "ORDER BY created_at DESC",
                (status_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memory_proposals "
                "ORDER BY created_at DESC"
            ).fetchall()
        store = Store()
        proposals = [_mproposal_to_dict(r, store) for r in rows]
        # Count pending for badge
        pending_count = sum(
            1 for p in proposals if p.get("status") == "pending"
        )
        return {"proposals": proposals, "pending_count": pending_count,
                "total": len(proposals)}


def apply_proposal(payload: dict):
    """Approve or reject a memory proposal.

    approve: write to memory/ file, update registry, mark approved.
    reject: mark rejected.
    """
    store = Store()
    pid = payload.get("id")
    action = payload.get("action")

    if not pid or action not in ("approve", "reject"):
        return False, "Missing id or invalid action"

    if action == "reject":
        store.update_proposal_status(pid, "rejected")
        return True, "Rejected"

    # Approve
    proposal_rows = _get_proposal_by_id(pid)
    if not proposal_rows:
        return False, "Proposal not found"

    prop = dict(proposal_rows[0])

    # Handle delete proposals: remove from file + mark registry
    if prop.get("action") == "delete":
        target_path = prop.get("target_path", "")
        target_section = prop.get("target_section")

        # 1. Remove content from markdown file
        if target_path:
            full_path = BASE_DIR / target_path
            if full_path.exists():
                existing = full_path.read_text(encoding="utf-8")
                if target_section:
                    updated = _remove_section(existing, target_section)
                    if updated is not None:
                        full_path.write_text(updated, encoding="utf-8")
                else:
                    # Whole-file delete: clear file, leave a tombstone comment
                    full_path.write_text(
                        f"<!-- DELETED: {prop.get('title', 'untitled')} -->\n",
                        encoding="utf-8"
                    )

        # 2. Mark registry entry as deleted
        with __import__("sqlite3").connect(store.db_path) as conn:
            conn.execute(
                "UPDATE memory_registry SET status = 'deleted', "
                "updated_at = datetime('now') "
                "WHERE file_path = ? AND section_heading IS ?",
                (target_path, target_section)
            )
            conn.commit()
        store.update_proposal_status(pid, "approved")
        return True, "Deleted"

    # Create/update/upgrade/downgrade: write to memory/ file
    edited = payload.get("edited_content")
    content = edited or prop.get("content", "")
    target_path = prop.get("target_path", "")

    if target_path and content:
        full_path = BASE_DIR / target_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        if prop.get("action") in ("create", "upgrade", "downgrade"):
            # Append new section to file (or create file)
            if full_path.exists():
                existing = full_path.read_text(encoding="utf-8")
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                full_path.write_text(
                    existing + "\n" + content + "\n", encoding="utf-8"
                )
            else:
                title = prop.get("title", "Untitled")
                full_path.write_text(
                    f"# {title}\n\n{content}\n", encoding="utf-8"
                )

            # Add to registry
            from memory_registry import init_registry
            # Re-scan just this file
            scope = prop.get("scope", "global")
            store.upsert_registry_entry(
                file_path=target_path,
                section_heading=prop.get("target_section"),
                scope=scope,
                base_weight=0.40,
            )

        elif prop.get("action") == "update":
            # Replace existing section
            target_section = prop.get("target_section", "")
            if full_path.exists():
                existing = full_path.read_text(encoding="utf-8")
                updated = _replace_section(existing, target_section, content)
                full_path.write_text(updated, encoding="utf-8")

            # Confirm the registry entry (boosts weight)
            from memory_registry import confirm
            reg_entry = store.get_registry_entry(
                target_path, prop.get("target_section")
            )
            if reg_entry:
                confirm(reg_entry["id"], store)

    store.update_proposal_status(pid, "approved")
    return True, "Approved"


def build_registry():
    """List all memory registry entries with effective weights."""
    store = Store()
    entries = store.get_registry_entries(status="active")
    result = []
    for e in entries:
        w = effective_weight(
            e["base_weight"],
            e.get("last_confirmed"),
            e.get("created_at"),
        )
        ts_str = e.get("last_confirmed") or e.get("created_at", "")
        days = _days_since(ts_str) if ts_str else 0

        e["effective_weight"] = round(w, 4)
        e["days_since_confirmed"] = days
        e["weight_status"] = (
            "healthy" if w >= 0.30 else
            "warning" if w >= 0.15 else
            "critical"
        )
        result.append(e)

    # Group by scope
    grouped = {}
    for e in result:
        scope = e.get("scope", "unknown")
        grouped.setdefault(scope, []).append(e)

    return {"registry": result, "by_scope": grouped,
            "total": len(result)}


def build_weight_history(registry_id: int):
    """Return weight change history for a registry entry."""
    store = Store()
    history = store.get_weight_history(registry_id)
    # Also get the current entry info
    entry = None
    with __import__("sqlite3").connect(store.db_path) as conn:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT * FROM memory_registry WHERE id = ?",
            (registry_id,)
        ).fetchone()
        if row:
            entry = dict(row)

    # Compute effective weight at each log point
    enriched = []
    for h in history:
        bw = h["base_weight_after"]
        ts = h["created_at"]
        w = effective_weight(bw, ts)
        enriched.append({
            "date": ts,
            "base_weight": bw,
            "effective_weight": round(w, 4),
            "event": h["event"],
        })

    return {
        "registry_id": registry_id,
        "entry": entry,
        "history": enriched,
    }


def _get_proposal_by_id(pid: int):
    """Get a proposal row by id."""
    with __import__("sqlite3").connect(
        f"file:{DB}?mode=ro", uri=True, timeout=5
    ) as conn:
        conn.row_factory = __import__("sqlite3").Row
        return conn.execute(
            "SELECT * FROM memory_proposals WHERE id = ?", (pid,)
        ).fetchall()


def _replace_section(content: str, target_section: str,
                     new_content: str) -> str:
    """Replace a ## section within markdown content.

    Finds `target_section` and replaces its body up to the next ## heading.
    If the section isn't found, appends new_content at the end.
    """
    if not target_section:
        return content + "\n" + new_content + "\n"

    lines = content.split("\n")
    in_target = False
    result = []
    found = False

    for line in lines:
        if line.strip().startswith("## ") and not line.strip().startswith("### "):
            if in_target:
                # End of target section — insert new content
                result.append(new_content)
                result.append("")
                in_target = False
            if line.strip()[3:].strip() == target_section.strip().lstrip("#").strip():
                in_target = True
                result.append(line)
                found = True
                continue
        if not in_target:
            result.append(line)

    # Section was last in file
    if in_target:
        result.append(new_content)
        result.append("")

    if found:
        return "\n".join(result)
    # Not found: append
    return content.rstrip() + "\n\n" + new_content + "\n"


def _remove_section(content: str, target_section: str) -> str | None:
    """Remove a ## section (heading + body) from markdown.

    Returns updated content, or None if section not found.
    Strips the section heading line and all its body lines up to the
    next ## heading (or EOF). Trailing blank lines are cleaned up.
    """
    clean_heading = target_section.strip().lstrip("#").strip()
    lines = content.split("\n")
    result = []
    in_target = False
    found = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            if in_target:
                in_target = False
            if stripped[3:].strip() == clean_heading:
                in_target = True
                found = True
                continue  # skip this line (the heading)
        if not in_target:
            result.append(line)

    if not found:
        return None

    # Clean up trailing blank lines left by removal
    while result and result[-1].strip() == "":
        result.pop()
    if result:
        result.append("")  # single trailing newline

    return "\n".join(result)


def _days_since(ts_str: str) -> int:
    """Compute days since an ISO timestamp."""
    from datetime import datetime, timezone
    try:
        ts = __import__("datetime").datetime.fromisoformat(
            ts_str.replace("Z", "+00:00")
        )
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, int(
            (__import__("datetime").datetime.now(timezone.utc) - ts
             ).total_seconds() / 86400.0
        ))
    except Exception:
        return 0


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_html(self, path, missing):
        if path.exists():
            self._send(200, path.read_text(encoding="utf-8"),
                       "text/html; charset=utf-8")
        else:
            self._send(404, missing, "text/plain; charset=utf-8")

    def do_GET(self):
        try:
            p = self.path.split("?")[0].rstrip("/") or "/"
            if self.path.startswith("/api/feed"):
                self._send(200, json.dumps(build_feed(), ensure_ascii=False),
                           "application/json; charset=utf-8")
            elif self.path.startswith("/api/projects"):
                self._send(200, json.dumps(build_projects(), ensure_ascii=False),
                           "application/json; charset=utf-8")
            elif self.path.startswith("/api/slug"):
                q = parse_qs(urlparse(self.path).query)
                self._send(200, json.dumps(slug_detail((q.get("slug") or [""])[0]),
                           ensure_ascii=False), "application/json; charset=utf-8")
            elif self.path.startswith("/api/fs"):
                q = parse_qs(urlparse(self.path).query)
                self._send(200, json.dumps(fs_list((q.get("path") or [""])[0]),
                           ensure_ascii=False), "application/json; charset=utf-8")
            elif self.path.startswith("/api/memory-registry/") and \
                 "/history" in self.path:
                # /api/memory-registry/<id>/history
                parts = [x for x in self.path.split("/") if x]
                try:
                    rid = int(parts[-2]) if parts[-1] == "history" else None
                except (ValueError, IndexError):
                    rid = None
                if rid:
                    self._send(200, json.dumps(
                        build_weight_history(rid), ensure_ascii=False
                    ), "application/json; charset=utf-8")
                else:
                    self._send(400, '{"error":"invalid id"}',
                               "application/json; charset=utf-8")
            elif self.path.startswith("/api/memory-registry"):
                self._send(200, json.dumps(build_registry(), ensure_ascii=False),
                           "application/json; charset=utf-8")
            elif self.path.startswith("/api/memory-proposals"):
                q = parse_qs(urlparse(self.path).query)
                status = (q.get("status") or [None])[0]
                self._send(200, json.dumps(
                    build_proposals(status), ensure_ascii=False
                ), "application/json; charset=utf-8")
            elif p in ("/", "/index.html"):
                self._serve_html(HTML, "dashboard/index.html 不存在")
            elif p in ("/projects", "/projects.html"):
                self._serve_html(PROJECTS_HTML, "dashboard/projects.html 不存在")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}), "application/json; charset=utf-8")

    def do_POST(self):
        try:
            p = self.path.split("?")[0].rstrip("/")
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(n).decode("utf-8") if n else "{}"
            payload = json.loads(body)

            if p == "/api/projects":
                ok, errs = save_projects(payload)
                self._send(200 if ok else 400,
                           json.dumps({"ok": ok, "errors": errs}, ensure_ascii=False),
                           "application/json; charset=utf-8")
            elif p == "/api/memory-proposals":
                ok, msg = apply_proposal(payload)
                self._send(200 if ok else 400,
                           json.dumps({"ok": ok, "message": msg}, ensure_ascii=False),
                           "application/json; charset=utf-8")
            elif p.startswith("/api/memory-registry/") and p.endswith("/confirm"):
                # Manual confirm from dashboard: /api/memory-registry/<id>/confirm
                parts = [x for x in p.split("/") if x]
                try:
                    rid = int(parts[-2])
                except (ValueError, IndexError):
                    rid = None
                if rid:
                    from memory_registry import confirm
                    new_w = confirm(rid)
                    self._send(200,
                               json.dumps({"ok": True,
                                           "new_base_weight": new_w},
                                          ensure_ascii=False),
                               "application/json; charset=utf-8")
                else:
                    self._send(400, '{"error":"invalid id"}',
                               "application/json; charset=utf-8")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False),
                       "application/json; charset=utf-8")

    def log_message(self, *args):
        pass  # 静音访问日志


def main():
    cfg = load_config().get("dashboard", {})
    port = cfg.get("port", 8765)
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"MIND 看板：http://127.0.0.1:{port}  (Ctrl-C 停)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
