#!/usr/bin/env python3
"""
Central configuration for the Nailong Doctor System.
Reads config.json + environment variables.
"""

import json
import os
from pathlib import Path

# 代码根 = 本文件上两级 (scripts/config.py → 项目根)。相对定位，跟机器/用户解耦。
BASE_DIR = Path(__file__).resolve().parent.parent

# Claude Code 会话数据源（摄入来源）。~ 自动对应当前用户，天然可移植。
PROJECTS_DIR = Path(os.path.expanduser("~/.claude/projects"))
# 旧记忆库（仅一次性迁移用）
OLD_MEMORY_DB = Path(os.path.expanduser("~/.claude/memory/memory.db"))


def _resolve_data_dir() -> Path:
    """数据目录：env NAILONG_DATA_DIR > config.json 的 data_dir > 默认 BASE_DIR/data。
    代码(BASE_DIR) 与 数据(DATA_DIR) 解耦——产品可分享，私密数据留本地。"""
    env = os.environ.get("NAILONG_DATA_DIR")
    if env:
        return Path(os.path.expanduser(env)).resolve()
    cfg_path = BASE_DIR / "config.json"
    if cfg_path.exists():
        try:
            with open(cfg_path) as f:
                dd = json.load(f).get("data_dir")
            if dd:
                return Path(os.path.expanduser(dd)).resolve()
        except Exception:
            pass
    return BASE_DIR / "data"


DATA_DIR = _resolve_data_dir()


def load_config() -> dict:
    """Load config.json, return dict with defaults filled."""
    cfg_path = BASE_DIR / "config.json"
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = json.load(f)
    else:
        cfg = {}

    # Fill defaults
    cfg.setdefault("windows", {"turn_days": 7, "daily_days": 30})
    cfg.setdefault("thresholds", {
        "min_turns_for_summary": 1,
        "min_summaries_for_daily": 2,
        "min_dailies_for_monthly": 3,
    })
    cfg.setdefault("llm", {
        "model": "deepseek-v4-flash",
        "temperature": 0.3,
        "max_tokens": 4000,
        "timeout": 30,
        "max_retries": 3,
    })

    # API credentials from env (set in Claude Code settings.json)
    cfg["api"] = {
        "token": os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
        "base_url": os.environ.get("ANTHROPIC_BASE_URL",
                                    "https://api.deepseek.com/anthropic"),
    }
    return cfg


def get_paths() -> dict:
    """Return all system-relative paths as a dict."""
    return {
        "base_dir": BASE_DIR,
        "data_dir": DATA_DIR,
        "db_path": DATA_DIR / "db" / "nailong.db",
        "archive_dir": DATA_DIR / "archive",
        "turns_dir": DATA_DIR / "archive" / "turns",
        "daily_dir": DATA_DIR / "archive" / "daily",
        "monthly_dir": DATA_DIR / "archive" / "monthly",
        "legacy_dir": DATA_DIR / "archive" / "legacy-memories",
        "old_db_dir": DATA_DIR / "archive" / "old",
        "injected_dir": DATA_DIR / "injected",
        "prefs_path": DATA_DIR / "injected" / "prefs.md",
        "brief_path": DATA_DIR / "injected" / "brief.md",
        "claude_md": BASE_DIR / "CLAUDE.md",
        "projects_dir": PROJECTS_DIR,
        "old_memory_db": OLD_MEMORY_DB,
    }
