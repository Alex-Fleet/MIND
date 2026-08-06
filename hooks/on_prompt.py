#!/usr/bin/env python3
"""UserPromptSubmit hook — 每次用户按回车注入辅助性程序设计规范。"""

import sys
from pathlib import Path

STANDARDS = Path(__file__).resolve().parent.parent / "memory" / "global" / "programming-standards.md"

if STANDARDS.exists():
    content = STANDARDS.read_text(encoding="utf-8").strip()
    print(content)
