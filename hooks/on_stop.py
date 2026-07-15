#!/usr/bin/env python3
"""
Stop hook — 每次 Claude 回复后触发。
1. ingest 新 JSONL turns（快）
2. summarize 未摘要 turns（LLM，慢）→ 结构化
3. 检查日报/月报是否到生成条件 → 结构化
4. 输出 systemMessage（对话内一行播报）+ 日报/月报弹 macOS 通知

注意（2026-07-11 实测）：Claude Code 扩展 2.1.207 存在 Stop-hook systemMessage
不渲染的 bug（#50542）。后端能正确解析（日志已证），只是 UI 不画。可见播报以
看板(dashboard)为准；systemMessage 保留作未来兼容，无害。

Called by: settings.json Stop hook (timeout 120s)
"""

import json
import os
import sys
import subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, "scripts")
PYTHON = sys.executable


def log(msg: str) -> None:
    """诊断输出 → stderr（stdout 只留给 systemMessage 的 JSON）。"""
    print(msg, file=sys.stderr)


def run(script: str, *args, timeout: int = 30) -> tuple[bool, str]:
    """跑一个脚本；把它的 stderr 转发到我们的 stderr；返回 (是否成功, stdout)。"""
    try:
        result = subprocess.run(
            [PYTHON, os.path.join(SCRIPTS, script)] + list(args),
            capture_output=True, text=True, timeout=timeout
        )
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines():
                log(f"  {line}")
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        log(f"  ⚠ {script} 超时 ({timeout}s)")
        return False, ""
    except Exception as e:
        log(f"  ⚠ {script} 错误: {e}")
        return False, ""


def run_json(script: str, *args, timeout: int = 30):
    """跑带 --json 的脚本，解析 stdout 最后一行为 JSON。失败返回 None。"""
    ok, stdout = run(script, *args, timeout=timeout)
    if not ok or not stdout:
        return None
    try:
        last = [l for l in stdout.splitlines() if l.strip()][-1]
        return json.loads(last)
    except Exception as e:
        log(f"  ⚠ {script} JSON 解析失败: {e} | 原始: {stdout[:200]}")
        return None


def notify(title: str, message: str) -> None:
    """macOS 原生通知（仅日报/月报这类少见、重要事件）。失败静默。"""
    try:
        script = (f"display notification {json.dumps(message)} "
                  f"with title {json.dumps(title)}")
        subprocess.run(["osascript", "-e", script],
                       capture_output=True, timeout=5)
    except Exception as e:
        log(f"  ⚠ 通知失败: {e}")


def build_broadcast(summ: dict | None, dig: dict | None) -> str:
    """把结构化结果拼成一行紧凑播报。"""
    parts = []

    # turn 摘要段
    if summ is None:
        parts.append("⚠ 摘要脚本异常")
    else:
        n = summ.get("summarized", 0)
        failed = summ.get("failed", 0)
        if n > 0:
            seg = f"turn摘要 ×{n}"
            if failed:
                seg += f"（⚠{failed}失败）"
            parts.append(seg)
        elif failed > 0:
            parts.append(f"⚠ turn摘要失败 ×{failed}")
        else:
            parts.append("无新增 turn")

    # 日报/月报段（有才显示）
    dig = dig or {}
    daily = dig.get("daily", [])
    monthly = dig.get("monthly", [])
    if daily:
        parts.append("日报 " + "、".join(d[5:] for d in daily) + " ✓")   # 2026-07-10 → 07-10
    if monthly:
        parts.append("月报 " + "、".join(monthly) + " ✓")

    return "MIND · " + " ｜ ".join(parts)


def main():
    log("MIND Stop hook")

    # Step 1: Ingest (fast, no LLM)
    log("[1/3] 摄入新对话...")
    _, ingest_out = run("ingest.py", timeout=10)
    if ingest_out:
        for line in ingest_out.splitlines():
            log(f"  {line}")

    # Step 2: Summarize new turns (LLM, slow) — 结构化
    log("[2/3] 生成 turn 摘要...")
    summ = run_json("summarize.py", "--json", "--limit", "5", timeout=50)

    # Step 3: Check daily/monthly — 结构化
    log("[3/3] 检查日报/月报...")
    dig = run_json("digest.py", "--check", "--json", timeout=30)

    # 日报/月报（少见、重要）→ macOS 原生通知
    daily = (dig or {}).get("daily", [])
    monthly = (dig or {}).get("monthly", [])
    if daily or monthly:
        bits = []
        if daily:
            bits.append(f"日报 ×{len(daily)}（{'、'.join(d[5:] for d in daily)}）")
        if monthly:
            bits.append(f"月报 ×{len(monthly)}（{'、'.join(monthly)}）")
        notify("MIND · 报告已生成", " ｜ ".join(bits))

    log("✓ Stop hook 完成")

    # 对话内一行播报 → systemMessage（当前扩展不渲染，以看板为准；保留作未来兼容）
    print(json.dumps({
        "continue": True,
        "suppressOutput": False,
        "systemMessage": build_broadcast(summ, dig),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
