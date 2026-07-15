#!/usr/bin/env python3
"""
SessionStart hook — 新会话启动时触发。

⚠️ 铁律：必须快。Claude Code 对 SessionStart 有约 60s 的"会话初始化"硬上限，
   超时会导致会话卡死（Error: Subprocess initialization did not complete
   within 60000ms）。这个上限跟 settings.json 里配的 timeout 无关。
   所以这里【绝不】同步调 LLM。摘要是 Stop hook 每轮的活；积压靠后台补漏。

流程（全部快操作）：
1. 看板保活：检测端口，没跑就后台拉起 dashboard server（非阻塞）
2. ingest 新 JSONL（文件 I/O，无 LLM）
3. inject 注入上下文（只读 DB，无 LLM）→ 纯文本 stdout
4. 后台补漏摘要（detached 进程，不阻塞启动）

Called by: settings.json SessionStart hook
Expected output: 纯文本（插件自动注入为上下文）
"""

import os
import socket
import sys
import subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, "scripts")
PYTHON = sys.executable
DASHBOARD_PORT = 8765


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


def _is_dashboard_running(port: int = DASHBOARD_PORT) -> bool:
    """检测 dashboard server 是否已在监听端口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def spawn_dashboard() -> None:
    """后台拉起 dashboard server（如未运行）。非阻塞。"""
    if _is_dashboard_running():
        return
    try:
        subprocess.Popen(
            [PYTHON, os.path.join(SCRIPTS, "dashboard_server.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _extract_project_slug() -> str | None:
    """从 stdin 的 SessionStart JSON 中提取 CC 项目 slug。

    transcript_path 形如：
      /Users/.../.claude/projects/-Users-jane-...-Memory-Plugin/xxx.jsonl
    倒数第二段就是 slug。
    """
    try:
        import json as _json
        raw = sys.stdin.read()
        if not raw.strip():
            return None
        data = _json.loads(raw)
        tp = data.get("transcript_path", "")
        # transcript_path: .../projects/<slug>/<session>.jsonl
        parts = tp.replace("\\", "/").rstrip("/").split("/")
        # 倒数第二段是项目 slug 文件夹
        if len(parts) >= 2 and parts[-2] and parts[-2] != "projects":
            return parts[-2]
        return None
    except Exception:
        return None


def main():
    # 0. 从 stdin 读当前项目 slug（用于注入隔离）
    project_slug = _extract_project_slug()

    # 0.5 看板保活：检测端口，没跑就后台拉起（非阻塞）
    spawn_dashboard()

    # 1. 摄入新 JSONL（快，无 LLM）。超时也不阻塞后续。
    run("ingest.py", timeout=12)

    # 2. 注入上下文（快，只读 DB）→ 纯文本 stdout
    inject_args = ["inject.py", "--json-output"]
    if project_slug:
        inject_args += ["--project", project_slug]
    ok, out = run(*inject_args, timeout=15)

    # 3. 后台补漏（不阻塞）
    spawn_background_catchup()

    # 输出注入内容（纯文本 stdout → 插件自动注入为上下文）
    if ok and out:
        print(out)   # 全量，不再只取末行
    else:
        # 纯文本兜底，不包 JSON（插件不吃 JSON 注入）
        print("MIND 记忆系统已就绪。")


if __name__ == "__main__":
    main()
