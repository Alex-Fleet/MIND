#!/usr/bin/env python3
"""一站重建所有日报：清旧 → 重跑 digest → 验证"""
import sqlite3, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths, BASE_DIR
from store import Store
from digest import generate_daily

def main():
    paths = get_paths()
    store = Store()
    db = str(paths["db_path"])
    daily_dir = str(paths["daily_dir"])

    # ── 1. 清旧日报 ──
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    n = conn.execute("DELETE FROM daily_reports").rowcount
    conn.commit()
    print(f"1. 清 DB 旧日报: {n} 篇")

    for f in os.listdir(daily_dir):
        fp = os.path.join(daily_dir, f)
        if os.path.isfile(fp):
            os.remove(fp)
    print(f"2. 清存档文件")

    # ── 2. 收集所有需要日报的(真实日期, project) ──
    pairs = conn.execute("""
        SELECT DISTINCT date(t.timestamp) AS d, ts.project
        FROM turn_summaries ts
        JOIN turns t ON t.session_id = ts.session_id
                     AND t.seq = ts.turn_seq AND t.role = 'user'
        WHERE d < date('now')  -- 跳过今天
        ORDER BY d, ts.project
    """).fetchall()
    conn.close()
    print(f"3. 待生成: {len(pairs)} 个 (日期, 项目) 对")

    # ── 3. 逐对生成 ──
    ok = fail = skipped = 0
    for r in pairs:
        date_str, project = r["d"], r["project"]
        try:
            if generate_daily(date_str, project, store, paths, dry_run=False):
                ok += 1
            else:
                skipped += 1
        except Exception as e:
            fail += 1
            print(f"   ✗ {date_str} / {project}: {e}")

    print(f"\n4. 结果: ✓{ok} 跳过{skipped} ✗{fail}")

    # ── 4. 验证 ──
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) FROM daily_reports").fetchone()[0]
    avg = conn.execute(
        "SELECT AVG(length(content)) FROM daily_reports").fetchone()[0] or 0
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_reports ORDER BY date").fetchall()]
    print(f"5. 验证: {total} 篇日报, 平均 {avg:,.0f} 字/篇, "
          f"日期范围 {dates[0] if dates else '?'} ~ {dates[-1] if dates else '?'}")
    conn.close()

    # ── 5. 展示存档文件(验证不再覆盖) ──
    files = sorted(os.listdir(daily_dir))
    print(f"6. 存档文件: {len(files)} 个")
    for f in files[:6]:
        print(f"     {f}")
    if len(files) > 6:
        print(f"     ... 共 {len(files)} 个")

if __name__ == "__main__":
    main()
