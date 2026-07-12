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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths, load_config, BASE_DIR

PATHS = get_paths()
DB = str(PATHS["db_path"])
HTML = BASE_DIR / "dashboard" / "index.html"


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

    items = []
    for r in conn.execute(
        "SELECT summarized_at, project, title, summary AS body, file_path, "
        "(SELECT substr(content,1,600) FROM turns t "
        " WHERE t.session_id=turn_summaries.session_id "
        " AND t.seq=turn_summaries.turn_seq AND t.role='user' LIMIT 1) AS user_input, "
        "(SELECT timestamp FROM turns t "
        " WHERE t.session_id=turn_summaries.session_id "
        " AND t.seq=turn_summaries.turn_seq AND t.role='user' LIMIT 1) AS real_ts "
        "FROM turn_summaries ORDER BY summarized_at DESC LIMIT 1000"):
        items.append({"type": "turn",
                      "ts": _norm_ts(r["real_ts"]) or _norm_ts(r["summarized_at"]),
                      "summarized_at": _norm_ts(r["summarized_at"]),
                      "project": r["project"], "project_name": pretty(r["project"]),
                      "title": r["title"], "body": r["body"],
                      "user_input": r["user_input"], "file": r["file_path"]})
    for r in conn.execute(
        "SELECT date, created_at, project, title, content AS body, file_path "
        "FROM daily_reports ORDER BY date DESC"):
        items.append({"type": "daily", "ts": _norm_ts(r["date"]),
                      "summarized_at": _norm_ts(r["created_at"]),
                      "project": r["project"], "project_name": pretty(r["project"]),
                      "title": r["title"], "body": r["body"], "file": r["file_path"]})
    for r in conn.execute(
        "SELECT month, created_at, project, title, content AS body, file_path "
        "FROM monthly_reports ORDER BY month DESC"):
        items.append({"type": "monthly", "ts": _norm_ts(r["month"]),
                      "summarized_at": _norm_ts(r["created_at"]),
                      "project": r["project"], "project_name": pretty(r["project"]),
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
    proj_list = [{"slug": p, "name": pretty(p)} for p in sorted(projects)]
    conn.close()
    return {"items": items, "stats": stats, "projects": proj_list}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            if self.path.startswith("/api/feed"):
                self._send(200, json.dumps(build_feed(), ensure_ascii=False),
                           "application/json; charset=utf-8")
            elif self.path in ("/", "/index.html"):
                if HTML.exists():
                    self._send(200, HTML.read_text(encoding="utf-8"),
                               "text/html; charset=utf-8")
                else:
                    self._send(404, "dashboard/index.html 不存在", "text/plain; charset=utf-8")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}), "application/json; charset=utf-8")

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
