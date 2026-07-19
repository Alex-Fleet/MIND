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
import re
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

TURN_SUMMARY_PROMPT = """你是"MIND"的记忆整合器。阅读以下对话，生成能让下一个会话无缝衔接的摘要。

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

# ── Layer 1: 确定性噪音过滤器（0 假阳性）────────────────

_NOISE_PATTERNS = [
    # 系统通知 — Claude Code 后台任务通知，不是用户输入
    (re.compile(r'<task-notification', re.IGNORECASE), 'system-notification'),
    (re.compile(r'<system-reminder', re.IGNORECASE), 'system-reminder'),
    # 本地命令 — Claude Code 本地执行输出（/compact 等斜杠命令的产物）
    (re.compile(r'<local-command-caveat', re.IGNORECASE), 'local-command-caveat'),
    (re.compile(r'<local-command-stdout', re.IGNORECASE), 'local-command-stdout'),
    # 斜杠命令 — /compact 等，用户输入只有命令无实质内容
    (re.compile(r'<command-name', re.IGNORECASE), 'slash-command'),
    (re.compile(r'<command-message', re.IGNORECASE), 'slash-command'),
    # 用户中断 — 取消工具调用，无实质交互
    (re.compile(r'\[Request interrupted by user'), 'user-interrupt'),
    # 纯 compact 注入 — Claude Code 上下文续写摘要（新/旧两种格式）
    (re.compile(r'(?:This session is being continued from a '
                r'previous conversation|'
                r'Primary Request and Intent[\s\S]{50,}'
                r'(?:Files and Code Sections|Key Technical Concepts))',
                re.IGNORECASE), 'compact-injection'),
]


def _classify_noise(turn_pair: dict) -> str | None:
    """Layer 1 确定性过滤: 返回 'invalid' 或 None（放行给 Layer 2）。
    只匹配 100% 确定的噪音——宁可漏过，绝不误杀。

    特殊处理：如果噪音标记后有实际用户内容，提取真实内容并放行。
    例如 "[Request interrupted by user]\\n这个图为什么重叠？" → 保留后半。"""
    content = turn_pair.get("conversation", "")
    user_part = content.split("🤖 Claude:")[0] if "🤖 Claude:" in content else content

    for pattern, _label in _NOISE_PATTERNS:
        m = pattern.search(user_part)
        if m:
            # 检查噪音标记后是否有实际用户内容
            after = user_part[m.end():].strip()
            # 去掉开头的 </xxx> 闭合标签残留 + 去掉行尾残留（如 "for tool use]"）
            import re as _re
            after = _re.sub(r'^</?[^>]+>', '', after).strip()
            after = _re.sub(r'^[^\n]*?\]\s*', '', after).strip()
            after = _re.sub(r'^\S+?\s', '', after, count=1).strip()  # 去掉 "for tool use]" 等残留词
            if after and len(after) >= 8:
                # 有实质内容——提取真实部分，替换 conversation
                turn_pair["conversation"] = after
                return None  # 放行给 L2
            return "invalid"
    return None

# ── 延续型 turn 检测（拼入上一个 pair，不独立摘要）────

_CONTINUATION_RE = re.compile(
    r'^(好了吗|好了[。！]?$|继续你|继续$|嗯+$|好[。！]?$|ok$|接着$|'
    r'还在.*[跑做处理弄]|跑完了吗|完成了吗|可以了吗|行了吗|'
    r'确认一下|我再看看|等一下|等下|等等|稍等|'
    r'进行.*怎么样|进度.*如何)',
    re.IGNORECASE
)


def _is_continuation(content: str) -> bool:
    """检测是否为延续型 user turn：纯进度催促/确认，
    没有新信息，只是上一个话题的延续。"""
    text = content.strip()
    # 取第一行判断（排除 ide_selection 等附加信息）
    first_line = text.split("\n")[0].strip()
    # 极短（≤15 字）且匹配延续模式
    if len(first_line) <= 15 and _CONTINUATION_RE.match(first_line):
        return True
    # 极短纯标点/语气词
    if len(first_line) <= 5 and not any(
        kw in first_line for kw in ["错", "不", "改", "修", "加", "删", "做", "写", "跑"]
    ):
        return True
    return False


_SYSTEM_NOISE_RE = re.compile(
    r'<(?:task-notification|system-reminder|local-command-caveat|'
    r'local-command-stdout|command-name|command-message)'
    r'|This session is being continued from a previous conversation'
    r'|Primary Request and Intent:',
    re.IGNORECASE
)
# 注意：[Request interrupted by user] 不在此——它可能后面跟真实用户内容，
# 由 _classify_noise() 做内容提取+放行判断，不在此处一刀切跳过。


def _is_system_noise(content: str) -> bool:
    """快速检测是否为系统消息（非用户输入）。用于 build_turn_pairs
    中跳过噪音 turn，避免延续型内容被合并进噪音对。"""
    first_line = content.strip().split("\n")[0][:200]
    return bool(_SYSTEM_NOISE_RE.search(first_line))


def build_turn_pairs(turns: list[dict], summarized_keys: set) -> tuple[list[dict], set, set]:
    """把 turns 配成 user-assistant 对，严格按 session 边界。

    延续型 turn（"好了吗""继续"等）自动拼入上一个 pair，不单独成对。
    系统噪音 turn（task-notification/compact 等）跳过——不生成 pair，
    但后续延续型 turn 会合并到上一个真实 pair。

    返回 (pairs, merged_keys, noise_keys):
      - merged_keys: 延续型用户输入，标 validity="merged"
      - noise_keys: 系统消息（compact 注入/命令输出等），标 validity="invalid"

    每个 pair = 一条 user 消息 + 其后（同一 session、下一个 user 之前）的所有
    assistant 消息。修两个 bug：
      - 认 session 边界：session 变了就结束当前 pair，绝不跨会话粘内容。
      - 用 (session_id, user.seq) 判已总结，只产出未总结的 pair。
    """
    pairs = []
    cur_user = None
    parts: list[str] = []
    cur_session = None
    merged_keys: set = set()  # 延续型 (session_id, seq)
    noise_keys: set = set()   # 系统噪音 (session_id, seq)

    def commit():
        nonlocal cur_user, parts
        already = merged_keys | noise_keys
        if cur_user is not None and \
                (cur_user["session_id"], cur_user["seq"]) not in summarized_keys and \
                (cur_user["session_id"], cur_user["seq"]) not in already:
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
            # 系统噪音 → 跳过，不生成 pair，标记为噪音（不是合并）
            if _is_system_noise(t["content"]):
                noise_keys.add((t["session_id"], t["seq"]))
                continue

            if cur_user is not None and _is_continuation(t["content"]):
                # 延续型 turn → 拼入当前 pair，不提交
                merged_keys.add((t["session_id"], t["seq"]))
                parts.append(f"\n\n👤 用户(续):\n{t['content']}\n\n🤖 Claude(续):")
            else:
                commit()                  # 新话题 → 结束上一个 pair
                cur_user = t
        elif t["role"] == "assistant":
            if cur_user is not None:
                c = t["content"]
                if len(c) > 3000:
                    c = c[:3000] + "\n... [截断]"
                parts.append(c)

    commit()
    return pairs, merged_keys, noise_keys


def summarize_turn(turn: dict, paths: dict, store: Store) -> str | None:
    """Summarize one turn pair. Returns the title on success, None on failure.

    两步流水线:
      1. Layer 1 确定性过滤 → invalid 直接写噪音记录（不调 LLM）
      2. 摘要写作（temperature=0.3，仅 valid 执行）
    """
    # ── Layer 1: 确定性过滤 ──
    validity = _classify_noise(turn)
    if validity == "invalid":
        # 写一条极简噪音记录，阻止被重复拾取
        store.insert_turn_summary(
            session_id=turn["session_id"],
            turn_seq=turn["turn_seq"],
            project=turn["project"],
            file_path="",
            title=f"[噪音] {turn.get('conversation', '')[:40]}",
            summary="",
            validity="invalid",
        )
        out(f"  ✗ 噪音(规则): {turn.get('conversation', '')[:60]}")
        return None  # 不调 LLM，不写 archive 文件

    # L1 放行 → 一律 valid（已取消低价值分类）
    validity = "valid"

    # ── 摘要写作（温度 0.3，正常创作）──
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

**有效性**: {validity}
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
        validity=validity,
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
    pairs, merged_keys, noise_keys = build_turn_pairs(raw, summarized_keys)

    # 标记延续型 turn（真正用户输入，只是没新信息）
    if merged_keys:
        turn_project = {(t["session_id"], t["seq"]): t["project"] for t in raw}
        for sid, seq in merged_keys:
            proj = turn_project.get((sid, seq), "")
            store.insert_turn_summary(
                session_id=sid, turn_seq=seq, project=proj,
                file_path="", title="[已合并]", summary="",
                validity="merged",
            )
        store.log("summarize-merge", detail={
            "merged_count": len(merged_keys),
        })

    # 标记系统噪音（compact 注入/命令输出等——不是用户输入）
    if noise_keys:
        turn_project = {(t["session_id"], t["seq"]): t["project"] for t in raw}
        for sid, seq in noise_keys:
            proj = turn_project.get((sid, seq), "")
            store.insert_turn_summary(
                session_id=sid, turn_seq=seq, project=proj,
                file_path="", title="[噪音]", summary="",
                validity="invalid",
            )
        store.log("summarize-noise", detail={
            "noise_count": len(noise_keys),
        })

    if not pairs:
        out("  (所有对话已总结)")
        total_skipped = len(merged_keys) + len(noise_keys)
        if total_skipped:
            out(f"  (合并 {len(merged_keys)} 个延续 + {len(noise_keys)} 个噪音)")
        _emit_json(0, 0, 0, [])
        return
    out(f"  未总结对话: {len(pairs)} 个"
        + (f"（合并 {len(merged_keys)} 延续 + {len(noise_keys)} 噪音）"
           if merged_keys or noise_keys else ""))

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
