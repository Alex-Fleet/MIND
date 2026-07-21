#!/usr/bin/env python3
"""UserPromptSubmit hook — 每次用户按回车注入铁律。"""

import sys
from pathlib import Path

IRON_RULES = Path(__file__).resolve().parent.parent / "memory" / "global" / "iron-rules.md"

if IRON_RULES.exists():
    content = IRON_RULES.read_text(encoding="utf-8").strip()
    print(content)
