#!/usr/bin/env python3
"""
MIND 备份 — 数据库一致性快照。

用法：
  python3 scripts/backup.py            # 备份（今天已备过则跳过）
  python3 scripts/backup.py --force    # 今天已备过也强制重备
  python3 scripts/backup.py --check    # 只检查今天是否已备份（供脚本/定时器判断）

原理：VACUUM INTO 是 SQLite 原生的在线一致性快照——不锁库、不停服务。
备份存 data/backups/nailong-YYYYMMDD.db，保留最近 KEEP 份（默认 2），更旧的自动删除。
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths
from log_setup import setup_logger

KEEP = 2  # 保留最近 2 份每日备份（用户定：同目录备份只防「坏」不防「丢」，原始数据在 JSONL 可重建）


def backups_dir() -> Path:
    return get_paths()["data_dir"] / "backups"


def today_backup_path() -> Path:
    return backups_dir() / f"nailong-{time.strftime('%Y%m%d')}.db"


def cleanup(bdir: Path) -> None:
    """只保留最近 KEEP 份 nailong-*.db 备份。"""
    baks = sorted(bdir.glob("nailong-*.db"))
    for old in baks[:-KEEP]:
        try:
            old.unlink()
            print(f"· 清理旧备份 {old.name}")
        except OSError:
            pass


def do_backup(force: bool = False) -> bool:
    bdir = backups_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    target = today_backup_path()
    if target.exists():
        if not force:
            print(f"· 今天已备份（{target.name}），跳过。用 --force 强制重备")
            return True
        target.unlink()  # VACUUM INTO 目标已存在会报错，force 先删旧的

    db_path = str(get_paths()["db_path"])
    # VACUUM INTO 不支持参数占位，路径里的单引号需转义防注入/语法错
    escaped = str(target).replace("'", "''")
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute(f"VACUUM INTO '{escaped}'")
        conn.close()
    except sqlite3.Error as e:
        print(f"✗ 备份失败: {e}")
        return False

    cleanup(bdir)
    size = target.stat().st_size / 1024 / 1024
    print(f"✓ 备份完成 → {target} ({size:.1f}MB)")
    return True


def main() -> None:
    setup_logger()
    parser = argparse.ArgumentParser(description="MIND 数据库备份")
    parser.add_argument("--force", action="store_true",
                        help="今天已备过也强制重备")
    parser.add_argument("--check", action="store_true",
                        help="只检查今天是否已备份")
    args = parser.parse_args()

    if args.check:
        ok = today_backup_path().exists()
        print("已备份" if ok else "未备份")
        sys.exit(0 if ok else 1)
    sys.exit(0 if do_backup(args.force) else 1)


if __name__ == "__main__":
    main()
