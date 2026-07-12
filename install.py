#!/usr/bin/env python3
"""
奶龙博士记忆系统 — 安装器
把本文件夹拷到任意位置后运行：  python3 install.py

它会：
1. 把 Stop + SessionStart 两个 hook 注册进你的 ~/.claude/settings.json
   （指向本文件夹的 hooks/，绝对路径当场算出，自动处理空格）
2. 保留你 settings.json 里的其它设置，只替换奶龙自己的 hook 条目
3. 装前自动备份 settings.json
4. 检查 API 凭证并提示

数据会存在  <本项目>/data/（首次运行自动创建）。
"""

import json
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
SETTINGS = Path(os.path.expanduser("~/.claude/settings.json"))


def hook_entry(script: str, timeout: int = 120) -> dict:
    cmd = f'python3 "{PROJECT / "hooks" / script}"'
    return {
        "hooks": [{"command": cmd, "type": "command", "timeout": timeout}],
        "matcher": "",
    }


def _is_nailong(entry: dict, script: str) -> bool:
    return any(script in h.get("command", "") for h in entry.get("hooks", []))


def main():
    print(f"📦 奶龙博士项目位置：{PROJECT}")

    if SETTINGS.exists():
        try:
            settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"✗ 你的 settings.json 不是合法 JSON（{e}）。先修好再装。")
            sys.exit(1)
        bak = SETTINGS.with_name("settings.json.pre-nailong.bak")
        bak.write_text(SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"✓ 已备份原 settings.json → {bak.name}")
    else:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        settings = {}
        print("· 未发现 settings.json，将新建")

    # 注册 hook：保留非奶龙的条目，替换奶龙自己的
    hooks = settings.setdefault("hooks", {})
    for event, script in [("Stop", "on_stop.py"),
                          ("SessionStart", "on_session_start.py")]:
        kept = [e for e in hooks.get(event, []) if not _is_nailong(e, script)]
        hooks[event] = kept + [hook_entry(script)]
    print("✓ 已注册 Stop + SessionStart hook（保留了你其它的 hook）")

    SETTINGS.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    # 检查 API 凭证（奶龙的摘要要调 LLM）
    env = settings.get("env", {})
    has_key = bool(env.get("ANTHROPIC_AUTH_TOKEN")
                   or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    print()
    if has_key:
        print("✓ 检测到 API 凭证")
    else:
        print("⚠ 未检测到 API 凭证。请在 ~/.claude/settings.json 的 env 填你自己的：")
        print('    "env": {')
        print('      "ANTHROPIC_AUTH_TOKEN": "sk-你自己的",')
        print('      "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"')
        print('    }')

    print(f"\n下一步：重启 Claude Code 让 hook 生效。数据将存在 {PROJECT / 'data'}")
    print("完成 ✅")


if __name__ == "__main__":
    main()
