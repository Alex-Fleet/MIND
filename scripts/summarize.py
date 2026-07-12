#!/usr/bin/env python3
"""
Turn summarization — read unsummarized turns, call LLM to produce
one summary per user-assistant exchange, write archive files + DB.

Usage:
  python3 summarize.py                    # summarize all unsummarized
  python3 summarize.py --project <slug>   # single project
  python3 summarize.py --dry-run          # preview, don't write
  python3 summarize.py --limit N          # max turns to summarize
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths
from store import Store
from llm_utils import call_llm, LLMFatalError

# 当 --json 时，进度文本走 stderr（进 transcript），stdout 只留一条 JSON 供 hook 解析。
JSON_MODE = False


def out(msg: str) -> None:
    """进度输出：人类模式→stdout；--json 模式→stderr（保持 stdout 纯净）。"""
    print(msg, file=sys.stderr if JSON_MODE else sys.stdout)

TURN_SUMMARY_PROMPT = """你是"奶龙博士系统"的记忆整合器。阅读以下对话，生成能让下一个会话无缝衔接的摘要。

【项目】{project}
【会话】{session_id} / Turn #{turn_seq}

【对话】
{conversation}

【要求】
1. **做了什么**：描述用户的实际操作意图和结果（1-3句话，说具体而非"讨论了X"）
2. **关键决策**：如果对话中有技术选型、架构变化、策略调整，记录下来
3. **未完成/搁置**：明确提到但没做的事，或"回头再弄"的 TODO
4. **跨会话上下文**：值得下个会话记住的状态信息

输出严格JSON（不要markdown代码块）：
{{
  "title": "一句话标题",
  "summary": "2-4句话完整摘要",
  "key_decisions": ["决策1（或空数组）"],
  "unfinished": ["未完成1（或空数组）"],
  "retained_context": "跨会话上下文（没有则为空字符串）"
}}"""


def build_turn_pairs(turns: list[dict], summarized_keys: set) -> list[dict]:
    """把 turns 配成 user-assistant 对，严格按 session 边界，跳过已总结的 pair。

    每个 pair = 一条 user 消息 + 其后（同一 session、下一个 user 之前）的所有
    assistant 消息。修两个 bug：
      - 认 session 边界：session 变了就结束当前 pair，绝不跨会话粘内容。
      - 用 (session_id, user.seq) 判已总结，只产出未总结的 pair。

    Returns list of dicts: {session_id, turn_seq, project, conversation}
    """
    pairs = []
    cur_user = None
    parts: list[str] = []
    cur_session = None

    def commit():
        nonlocal cur_user, parts
        if cur_user is not None and \
                (cur_user["session_id"], cur_user["seq"]) not in summarized_keys:
            conv = f"👤 用户:\n{cur_user['content']}\n\n"
            if parts:
                conv += "🤖 Claude:\n" + "\n".join(parts)
            pairs.append({
                "session_id": cur_user["session_id"],
                "turn_seq": cur_user["seq"],
                "project": cur_user["project"],
                "conversation": conv,
            })
        cur_user = None
        parts = []

    for t in turns:
        if t["session_id"] != cur_session:
            commit()                      # 会话切换 → 结束上一个 pair，不跨会话
            cur_session = t["session_id"]
        if t["role"] == "user":
            commit()                      # 新 user → 结束上一个 pair
            cur_user = t
        elif t["role"] == "assistant":
            if cur_user is not None:
                c = t["content"]
                if len(c) > 3000:
                    c = c[:3000] + "\n... [截断]"
                parts.append(c)

    commit()
    return pairs


def summarize_turn(turn: dict, paths: dict, store: Store) -> str | None:
    """Summarize one turn pair. Returns the title on success, None on failure."""
    prompt = TURN_SUMMARY_PROMPT.format(
        project=turn["project"],
        session_id=turn["session_id"],
        turn_seq=turn["turn_seq"],
        conversation=turn["conversation"],
    )

    result = call_llm(prompt, system="你是项目记忆管理系统。严格按 JSON 格式输出。")

    if result is None or not result:
        return False

    title = result.get("title", f"Turn #{turn['turn_seq']}")
    summary = result.get("summary", "")
    key_decisions = result.get("key_decisions", [])
    unfinished = result.get("unfinished", [])
    retained_context = result.get("retained_context", "")

    if not summary.strip():
        return False

    # Build file path
    date_str = datetime.now().strftime("%Y-%m-%d")
    turn_dir = paths["turns_dir"] / date_str
    os.makedirs(turn_dir, exist_ok=True)

    safe_sid = turn["session_id"][:8]
    filename = f"{safe_sid}-turn-{turn['turn_seq']:04d}.md"
    file_path = turn_dir / filename

    # Relative path for DB (from archive/)
    rel_path = f"turns/{date_str}/{filename}"

    # Write archive file
    decisions_md = "\n".join(
        f"- {d}" for d in key_decisions
    ) if key_decisions else "（无）"
    unfinished_md = "\n".join(
        f"- {u}" for u in unfinished
    ) if unfinished else "（无）"

    content = f"""# {title}

**项目**: {turn["project"]}
**会话**: {turn["session_id"]}
**Turn**: #{turn["turn_seq"]}

## 做了什么
{summary}

## 关键决策
{decisions_md}

## 未完成
{unfinished_md}

## 跨会话上下文
{retained_context or "（无）"}

---
→ 原始日志: JSONL {turn["session_id"]}#L{turn["turn_seq"]}
"""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    # Write to DB
    ok = store.insert_turn_summary(
        session_id=turn["session_id"],
        turn_seq=turn["turn_seq"],
        project=turn["project"],
        file_path=rel_path,
        title=title,
        summary=summary,
        key_decisions=key_decisions,
        unfinished=unfinished,
        retained_context=retained_context,
    )

    if ok:
        out(f"  ✓ {title}")
        return title
    return None


def _emit_json(summarized: int, failed: int, total: int, titles: list) -> None:
    """--json 模式下往 stdout 吐唯一一条结果 JSON（供 Stop hook 解析）。"""
    if JSON_MODE:
        print(json.dumps({
            "summarized": summarized,
            "failed": failed,
            "total_pairs": total,
            "titles": titles,
        }, ensure_ascii=False))


def main():
    global JSON_MODE
    JSON_MODE = "--json" in sys.argv
    dry_run = "--dry-run" in sys.argv
    project_filter = None
    limit = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            project_filter = args[i + 1]
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        elif args[i] == "--dry-run":
            i += 1
        else:
            i += 1

    paths = get_paths()
    store = Store()

    # Get all turns (session-ordered) + already-summarized keys
    raw = store.get_turns_ordered(project=project_filter)
    summarized_keys = store.get_summarized_keys(project=project_filter)

    if not raw:
        out("  (无对话)")
        _emit_json(0, 0, 0, [])
        return

    # Build turn pairs (session-aware, 只留未总结的)
    pairs = build_turn_pairs(raw, summarized_keys)
    if not pairs:
        out("  (所有对话已总结)")
        _emit_json(0, 0, 0, [])
        return
    out(f"  未总结对话: {len(pairs)} 个")

    if limit:
        pairs = pairs[:limit]

    if dry_run:
        out(f"  [DRY RUN] 将总结 {len(pairs)} 个 turn:")
        for p in pairs[:5]:
            out(f"    [{p['project']}] {p['session_id'][:8]}... "
                f"turn #{p['turn_seq']}")
        _emit_json(0, 0, len(pairs), [])
        return

    # Summarize each turn（带熔断:致命错误立即中止,连续失败熔断退出）
    titles = []
    failed = 0
    consecutive_fail = 0
    aborted = None
    MAX_CONSECUTIVE_FAIL = 5
    for turn in pairs:
        try:
            title = summarize_turn(turn, paths, store)
        except LLMFatalError as e:
            aborted = f"致命错误({e})——余额/认证/权限问题,重试无意义"
            out(f"\n  ✖ 已中止:{aborted}")
            break
        if title:
            titles.append(title)
            consecutive_fail = 0
        else:
            failed += 1
            consecutive_fail += 1
            if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                aborted = f"连续 {consecutive_fail} 次失败(疑似网络/服务异常)"
                out(f"\n  ✖ 已熔断:{aborted}")
                break

    store.log("summarize", detail={
        "pairs_total": len(pairs),
        "success": len(titles),
        "project": project_filter,
        "aborted": aborted,
    })

    tail = f"（已中止:{aborted}，恢复后重跑自动续传）" if aborted else ""
    out(f"\n  完成: {len(titles)}/{len(pairs)} 个 turn 已总结{tail}")
    _emit_json(len(titles), failed, len(pairs), titles)


if __name__ == "__main__":
    main()
