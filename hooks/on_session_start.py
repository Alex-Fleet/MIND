#!/usr/bin/env python3
"""
SessionStart hook — 新会话启动时触发。

⚠️ 铁律：必须快。Claude Code 对 SessionStart 有约 60s 的"会话初始化"硬上限，
   超时会导致会话卡死（Error: Subprocess initialization did not complete
   within 60000ms）。这个上限跟 settings.json 里配的 timeout 无关。
   所以这里【绝不】同步调 LLM。摘要是 Stop hook 每轮的活；积压靠后台补漏。

流程（全部快操作）：
1. ingest 新 JSONL（文件 I/O，无 LLM）
2. inject 注入上下文（只读 DB，无 LLM）→ 输出 systemMessage
3. 后台补漏摘要（detached 进程，不阻塞启动）

Called by: settings.json SessionStart hook
Expected output: {"systemMessage": "..."}
"""

import json
import os
import sys
import subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, "scripts")
PYTHON = sys.executable


def run(script: str, *args, timeout: int = 15) -> tuple[bool, str]:
    """跑脚本，返回 (成功, stdout)。超时/异常都不抛，返回 (False, 原因)。"""
    try:
        result = subprocess.run(
            [PYTHON, os.path.join(SCRIPTS, script)] + list(args),
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, f"timeout ({timeout}s)"
    except Exception as e:
        return False, str(e)


def spawn_background_catchup() -> None:
    """后台补漏摘要：detached 进程，spawn 完立刻返回，不阻塞会话启动。
    与 Stop hook 的摘要可能并发——DB 的 UNIQUE(session_id,turn_seq) 去重兜底。"""
    try:
        subprocess.Popen(
            [PYTHON, os.path.join(SCRIPTS, "summarize.py"), "--limit", "20"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def main():
    # 1. 摄入新 JSONL（快，无 LLM）。超时也不阻塞后续。
    run("ingest.py", timeout=12)

    # 2. 注入上下文（快，只读 DB）→ systemMessage
    ok, out = run("inject.py", "--json-output", timeout=15)

    # 3. 后台补漏（不阻塞）
    spawn_background_catchup()

    # 输出 systemMessage（会话初始化就靠这条，必须在硬上限内返回）
    if ok and out:
        print(out.splitlines()[-1])
    else:
        print(json.dumps({"systemMessage": "🐉 奶龙博士记忆系统已就绪。"},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
