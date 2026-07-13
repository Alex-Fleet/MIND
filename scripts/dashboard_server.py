#!/usr/bin/env python3
"""
奶龙博士看板 — 本地服务器（Python 标准库，零外部依赖，只绑 127.0.0.1）。

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
        "UNION SELECT DISTINCT project FROM monthly_reports").fetchall()]
    pretty = _make_pretty(projects)
    reg, _ = _load_registry_with_source()

    def plabel(slug):
        """有注册表就用真身显示名，否则退回剥前缀的短名。"""
        return reg.label_of(slug) if reg.is_registered(slug) else pretty(slug)

    def ptype(slug):
        return reg.type_of(slug) if reg.is_registered(slug) else ""

    items = []
    for r in conn.execute(
        "SELECT summarized_at, project, title, summary AS body, file_path, "
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
                      "user_input": r["user_input"], "file": r["file_path"]})
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
            if self.path.split("?")[0].rstrip("/") == "/api/projects":
                n = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(n).decode("utf-8") if n else "{}"
                ok, errs = save_projects(json.loads(body))
                self._send(200 if ok else 400,
                           json.dumps({"ok": ok, "errors": errs}, ensure_ascii=False),
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
    print(f"🐉 奶龙看板：http://127.0.0.1:{port}  (Ctrl-C 停)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
