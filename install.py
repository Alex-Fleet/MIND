#!/usr/bin/env python3
"""
MIND 记忆系统 — 安装器
把本文件夹拷到任意位置后运行：  python3 install.py

它会：
1. 检查依赖（requests）
2. 自动创建 config.json（从 config.example.json）
3. 自动创建 CLAUDE.md（从 CLAUDE.example.md）
4. 把 Stop + SessionStart 两个 hook 注册进你的 ~/.claude/settings.json
   （指向本文件夹的 hooks/，绝对路径当场算出，自动处理空格）
5. 保留你 settings.json 里的其它设置，只替换MIND自己的 hook 条目
6. 装前自动备份 settings.json
7. 检查 API 凭证并提示

数据会存在  <本项目>/data/（首次运行自动创建）。
"""

import json
import os
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
SETTINGS = Path(os.path.expanduser("~/.claude/settings.json"))


def _check_deps() -> bool:
    """检查外部依赖是否已安装。"""
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False


def _copy_if_missing(src: str, dst: str, label: str) -> None:
    """如果 dst 不存在，从 src 复制模板。"""
    dst_path = PROJECT / dst
    if dst_path.exists():
        print(f"· {label} 已存在，跳过")
        return
    src_path = PROJECT / src
    if src_path.exists():
        shutil.copy(src_path, dst_path)
        print(f"✓ 已创建 {label}（从 {src}）")
    else:
        print(f"⚠ 模板 {src} 不存在，跳过 {label}")


def hook_entry(script: str, timeout: int = 120,
               extra_hooks: list[dict] | None = None) -> dict:
    """Build a hook entry. extra_hooks are command dicts appended after the main one."""
    cmd = f'python3 "{PROJECT / "hooks" / script}"'
    main = {"command": cmd, "type": "command", "timeout": timeout}
    hooks = [main] + (extra_hooks or [])
    return {"hooks": hooks, "matcher": ""}


def _cmd(script_name: str, *args, timeout: int = 15) -> dict:
    """Build a single inject.py --section command."""
    script_path = PROJECT / "scripts" / script_name
    arg_str = " ".join(args)
    return {
        "command": f'python3 "{script_path}" {arg_str}'.strip(),
        "type": "command",
        "timeout": timeout,
    }


def _is_nailong(entry: dict, script: str) -> bool:
    return any(script in h.get("command", "") for h in entry.get("hooks", []))


# SessionStart 拆分：on_session_start.py + (N+4) 条 inject 命令
# 每条独立 1e4 字符预算，绕开 Claude Code persistHookOutput 硬限制。
# global 用 --pack K 贪心打包（运行时枚举注入源），N 从 config.inject.shards 读，
# 新增/删除 global 文件不用重跑 install——命令数固定，运行时自动分包。
PROJECT_SECTIONS = [
    ("project", ""),
    ("turns", "--limit 28"),
    ("dailies", "--limit 5"),
    ("monthlies", ""),
]


def _load_shards() -> int:
    """读 config.inject.shards（分片预算），缺失时默认 24。"""
    try:
        cfg_path = PROJECT / "config.json"
        if cfg_path.exists():
            import json
            inject = json.loads(cfg_path.read_text(encoding="utf-8")).get("inject", {})
            return int(inject.get("shards", 24))
    except Exception:
        pass
    return 24


def _build_session_start_entry() -> dict:
    """构建完整的 SessionStart hook entry（on_session_start + N+4 命令）。"""
    extra = [
        _cmd("inject.py", f"--section global --pack {k}".strip(), timeout=15)
        for k in range(1, _load_shards() + 1)
    ]
    extra += [
        _cmd("inject.py", f"--section {sec} {args}".strip(), timeout=15)
        for sec, args in PROJECT_SECTIONS
    ]
    return hook_entry("on_session_start.py", timeout=120, extra_hooks=extra)


def main():
    print(f"📦 MIND 项目位置：{PROJECT}")

    # ── 1. 检查依赖 ──
    if not _check_deps():
        print("✗ 缺少依赖。请先安装：")
        print(f"  pip3 install -r \"{PROJECT / 'requirements.txt'}\"")
        sys.exit(1)
    print("✓ 依赖检查通过")

    # ── 2. 创建配置文件 ──
    print()
    _copy_if_missing("config.example.json", "config.json", "config.json")
    _copy_if_missing("CLAUDE.example.md", "CLAUDE.md", "CLAUDE.md")

    # ── 3. 注册 hook ──
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

    # 注册 hook：保留非MIND的条目，替换MIND自己的
    hooks = settings.setdefault("hooks", {})

    # Stop hook
    kept_stop = [e for e in hooks.get("Stop", [])
                 if not _is_nailong(e, "on_stop.py")]
    hooks["Stop"] = kept_stop + [hook_entry("on_stop.py", timeout=120)]

    # SessionStart hook（主入口 + N+4 条 section 拆分命令）
    kept_ss = [e for e in hooks.get("SessionStart", [])
               if not _is_nailong(e, "on_session_start.py")]
    hooks["SessionStart"] = kept_ss + [_build_session_start_entry()]

    # UserPromptSubmit hook（每轮注入硬性约束铁律，见 on_prompt.py）
    kept_ups = [e for e in hooks.get("UserPromptSubmit", [])
                if not _is_nailong(e, "on_prompt.py")]
    hooks["UserPromptSubmit"] = kept_ups + [
        hook_entry("on_prompt.py", timeout=10)]

    n_ss = _load_shards() + len(PROJECT_SECTIONS)
    print(f"✓ 已注册 Stop + SessionStart({n_ss}条) + UserPromptSubmit hook")
    print("  （保留了你其它的 hook 条目）")

    SETTINGS.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    # 检查 API 凭证（MIND的摘要要调 LLM）
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
