#!/usr/bin/env python3
"""UserPromptSubmit hook — 每次用户按回车注入硬性约束铁律（当轮提醒）。

只输出行为规范文件的「# 硬性约束」章节（~1k 字符），不输出全文：
UPS 位于请求 token 流尾部，每轮注定 cache write、全价计费——输出越少越省。
完整规范（工作方式/工程规范）走 SessionStart 前缀区注入（注意力强 + 缓存命中）。

单一事实来源：直接读 behavior-standards.md，改文件自动生效，不硬编码。
"""

from pathlib import Path

BEHAVIOR = Path(__file__).resolve().parent.parent / "memory" / "global" / "behavior-standards.md"
SECTION = "# 硬性约束"


def extract_section(text: str, section: str) -> str:
    """从 markdown 提取指定 `# 章节`，截取到下一个 `# ` 标题前。"""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == section:
            start = i
            break
    if start is None:
        return ""
    out = [section, ""]
    for line in lines[start + 1:]:
        if line.startswith("# "):
            break
        out.append(line)
    return "\n".join(out).rstrip()


if __name__ == "__main__":
    if BEHAVIOR.exists():
        content = BEHAVIOR.read_text(encoding="utf-8")
        print(extract_section(content, SECTION))
