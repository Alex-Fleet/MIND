#!/usr/bin/env python3
"""清理 2026-07-10 错误记忆（旧系统配对错乱产物），删后自动重摘要+重跑日报。"""
import os, sqlite3, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths, BASE_DIR
from store import Store


def main():
    paths = get_paths()
    store = Store()
    db = str(paths["db_path"])
    turns_dir = str(paths["turns_dir"])
    daily_dir = str(paths["daily_dir"])

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    # ── 1. 查 7-10 的 turn_summaries ──
    bad_ts = conn.execute("""
        SELECT ts.id, ts.file_path, ts.project
        FROM turn_summaries ts
        JOIN turns t ON t.session_id = ts.session_id
                     AND t.seq = ts.turn_seq AND t.role = 'user'
        WHERE date(t.timestamp) = '2026-07-10'
    """).fetchall()
    print(f"1. 7-10 错误 turn_summaries: {len(bad_ts)} 条")

    # ── 2. 删 archive 文件 ──
    deleted_files = 0
    for r in bad_ts:
        fp = os.path.join(BASE_DIR, "data", "archive", r["file_path"])
        if os.path.isfile(fp):
            os.remove(fp)
            deleted_files += 1
    print(f"2. 删 turn archive 文件: {deleted_files} 个")

    # ── 3. 删 DB 记录 ──
    ids = [r["id"] for r in bad_ts]
    if ids:
        ph = ",".join(["?"] * len(ids))
        n = conn.execute(f"DELETE FROM turn_summaries WHERE id IN ({ph})", ids).rowcount
        conn.commit()
        print(f"3. 删 DB turn_summaries: {n} 条")
    else:
        print("3. 无 DB 记录需删")

    # ── 4. 删 7-10 日报 ──
    bad_dr = conn.execute(
        "SELECT file_path FROM daily_reports WHERE date = '2026-07-10'"
    ).fetchall()
    for r in bad_dr:
        fp = os.path.join(BASE_DIR, "data", "archive", r["file_path"])
        if os.path.isfile(fp):
            os.remove(fp)
        # 同时删对应 index 文件
        idx_fp = fp.replace(".md", "-index.md")
        if os.path.isfile(idx_fp):
            os.remove(idx_fp)
    n_dr = conn.execute("DELETE FROM daily_reports WHERE date = '2026-07-10'").rowcount
    conn.commit()
    print(f"4. 删 7-10 日报: {n_dr} 篇 + 对应 index 文件")

    conn.close()

    # ── 5. 重摘要 7-10 的 turns ──
    print("\n5. 重摘要 7-10 turns（LLM 调用，需等待）...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    summarize_py = os.path.join(script_dir, "summarize.py")
    r = os.system(f"cd '{script_dir}' && python3 summarize.py 2>&1")
    if r != 0:
        print(f"   ⚠ summarize.py 退出码 {r}")

    # ── 6. 重跑日报 ──
    print("\n6. 重跑 7-10 日报...")
    from digest import generate_daily
    store2 = Store()
    ok = generate_daily("2026-07-10", "-Users-jane-windme-com-Documents-Agent-R-D-Memory-Plugin",
                         store2, paths, dry_run=False)
    print(f"   日报生成: {'✓' if ok else '✗（可能无摘要或已存在）'}")

    # ── 7. 最终验证 ──
    conn2 = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn2.row_factory = sqlite3.Row
    ts_count = conn2.execute(
        "SELECT COUNT(*) FROM turn_summaries ts "
        "JOIN turns t ON t.session_id=ts.session_id AND t.seq=ts.turn_seq AND t.role='user' "
        "WHERE date(t.timestamp)='2026-07-10'"
    ).fetchone()[0]
    dr_count = conn2.execute(
        "SELECT COUNT(*) FROM daily_reports WHERE date='2026-07-10'"
    ).fetchone()[0]
    conn2.close()
    print(f"\n7. 验证: turn_summaries={ts_count}, daily_reports={dr_count}")


if __name__ == "__main__":
    main()
