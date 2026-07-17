#!/usr/bin/env python3
"""
Memory proposal agent — scans recent dailies for reusable knowledge patterns.

Triggered by on_stop.py after daily reports are generated (detached background).
Does NOT modify memory/ files directly — only writes to memory_proposals table.

Usage:
  python3 propose_memories.py --dry-run         # preview proposals (stdout JSON)
  python3 propose_memories.py --since 2026-07-15 # scan from specific date
  python3 propose_memories.py                    # normal run: scan → propose → write DB
"""

import json
import sys
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths, load_config
from store import Store
from llm_utils import call_llm
from memory_registry import decay_check, effective_weight, boost_by_reference

DRY_RUN = False

PROPOSAL_PROMPT = """你是"MIND"的记忆编辑。你的任务是扫描日报，从中提取**可复用的知识**，
起草全局记忆提案。你不是在做流水账——你只提炼可跨项目复用的模式、原则、边界。

【现有记忆索引】（不要重复这些已有的内容）
{registry_summary}

【近期日报】
{daily_texts}

【要求】
1. **Scan — 提取候选**：从日报中识别可复用的知识。门槛：
   - 必须是 ≥2 篇日报中出现的模式（单次出现 = 噪声，跳过）
   - 必须是模式/原则/边界/反模式，不是一次性操作步骤
   - 必须判断 scope：换个完全不同的项目也有用 → global；只在这个项目有用 → project:<id>
2. **Compare — 对比现有记忆**：每条候选与【现有记忆索引】比对：
   - 完全重复 → 跳过
   - 扩展/补充现有条目 → action="update", target_section 指向已有章节
   - 与现有条目矛盾 → 在 conflicts 字段标注
   - 全新内容 → action="create"
3. **Propose — 生成提案**：只输出 confidence ≥ 0.6 的候选。

输出严格JSON（不要markdown代码块）：
{{
  "candidates": [
    {{
      "action": "create",
      "scope": "global",
      "target_path": "memory/global/agenting-skills.md",
      "target_section": "## X、新发现的模式",
      "title": "简短标题（15字以内）",
      "content": "### 具体章节\\n\\n完整的 markdown 内容，包括原则、边界、做法",
      "reason": "为什么这条值得成为全局记忆",
      "source_dates": ["2026-07-15"],
      "related_registry_ids": [],
      "confidence": 0.8
    }}
  ],
  "decay_alerts": [
    {{"registry_id": 12, "reason": "有效权重 0.08, 45 天未确认", "suggest": "delete"}}
  ],
  "conflict_alerts": [
    {{"candidate_idx": 0, "registry_id": 7, "summary": "与现有条目部分重叠"}}
  ]
}}

注意：
- target_section 用 "## " 开头的 markdown 标题格式
- scope 用 "global" 或 "project:<id>" 格式
- content 是完整的 markdown，包含 ### 子标题
- 如果没有任何有效候选，返回 {{"candidates": [], "decay_alerts": [], "conflict_alerts": []}}
- 如果没有任何有效候选，返回 empty arrays"""


def _build_registry_summary(store: Store) -> str:
    """Build a compact summary of existing registry entries for the LLM."""
    entries = store.get_registry_entries(status="active")
    lines = []
    for e in entries:
        heading = e.get("section_heading") or Path(e["file_path"]).stem
        bw = e["base_weight"]
        w = effective_weight(bw, e.get("last_confirmed"), e.get("created_at"))
        lines.append(
            f"- [{e['scope']}] {e['file_path']} "
            f"| {heading[:50]} "
            f"| w={w:.2f}"
        )
    return "\n".join(lines)


def _read_recent_dailies(store: Store, since: str | None = None) -> list[dict]:
    """Read daily reports since last run (or since given date)."""
    if since is None:
        last = store.get_last_proposal_run()
        since = last or "2000-01-01"

    with __import__("sqlite3").connect(store.db_path) as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute(
            "SELECT DISTINCT project, date, title, content "
            "FROM daily_reports WHERE created_at > ? "
            "ORDER BY date DESC, project",
            (since,)
        ).fetchall()
        return [dict(r) for r in rows]


def _process_llm_response(response: dict, store: Store) -> dict:
    """Process LLM response: write proposals to DB, handle decay alerts."""
    result = {"proposals_created": 0, "decay_proposals": 0, "conflicts": 0}

    candidates = response.get("candidates", [])
    conflict_alerts = response.get("conflict_alerts", [])

    for i, candidate in enumerate(candidates):
        # Attach conflict info
        conflicts_json = None
        related_ids_json = None
        for ca in conflict_alerts:
            if ca.get("candidate_idx") == i:
                conflicts_json = json.dumps([ca], ensure_ascii=False)
                result["conflicts"] += 1

        # Collect related registry IDs
        related_ids = candidate.get("related_registry_ids", [])
        if related_ids:
            related_ids_json = json.dumps(related_ids)

        if DRY_RUN:
            print(json.dumps(candidate, ensure_ascii=False, indent=2))
            continue

        pid = store.insert_memory_proposal(
            action=candidate.get("action", "create"),
            scope=candidate.get("scope", "global"),
            title=candidate.get("title", "Untitled"),
            content=candidate.get("content", ""),
            target_path=candidate.get("target_path"),
            target_section=candidate.get("target_section"),
            reason=candidate.get("reason"),
            conflicts=conflicts_json,
            source_dates=json.dumps(
                candidate.get("source_dates", []), ensure_ascii=False
            ),
            related_registry_ids=related_ids_json,
            confidence=candidate.get("confidence", 0.5),
        )
        result["proposals_created"] += 1

        # Boost referenced memories
        for rid in related_ids:
            boost_by_reference(rid, store)

    # Generate decay/delete proposals
    decay = decay_check(store)
    for item in decay.get("delete_candidates", []):
        if DRY_RUN:
            print(f"  [DECAY] Delete candidate: {item['file_path']} "
                  f"w={item['effective_weight']}")
            continue
        store.insert_memory_proposal(
            action="delete",
            scope=item["scope"],
            title=f"淘汰: {item['file_path']}",
            content=(
                f"有效权重 {item['effective_weight']:.3f}，"
                f"距上次确认 {item['days_since_confirmed']} 天。"
                f"建议删除此记忆。"
            ),
            target_path=item["file_path"],
            target_section=item.get("section_heading"),
            reason=(
                f"艾宾浩斯衰减至 {item['effective_weight']:.3f} "
                f"（阈值 0.15），{item['days_since_confirmed']} 天未确认"
            ),
            confidence=0.9,
        )
        result["decay_proposals"] += 1

    return result


def main():
    global DRY_RUN
    DRY_RUN = "--dry-run" in sys.argv

    since = None
    for i, arg in enumerate(sys.argv):
        if arg == "--since" and i + 1 < len(sys.argv):
            since = sys.argv[i + 1]

    store = Store()
    dailies = _read_recent_dailies(store, since)

    if not dailies:
        if DRY_RUN:
            print(json.dumps(
                {"candidates": [], "decay_alerts": [], "reason": "no new dailies"},
                ensure_ascii=False
            ))
        return

    # Build daily text for LLM (truncate long content)
    daily_texts = ""
    for d in dailies:
        content = (d["content"] or "")[:800]
        daily_texts += (
            f"### [{d['date']}] {d['project']}: {d['title']}\n"
            f"{content}\n\n"
        )

    registry_summary = _build_registry_summary(store)

    if DRY_RUN:
        print(f"Dailies since {since or 'last run'}: {len(dailies)}")
        print(f"Registry entries: {len(registry_summary.splitlines())}")
        print("--- LLM prompt would be ---")
        print(f"Daily chars: {len(daily_texts)}")
        print(f"Registry chars: {len(registry_summary)}")
        print("---")

    prompt = PROPOSAL_PROMPT.format(
        registry_summary=registry_summary,
        daily_texts=daily_texts,
    )

    result = call_llm(
        prompt,
        system="你是项目记忆管理系统。严格按 JSON 格式输出，只提取可复用的知识模式。",
        max_tokens=4000,
    )

    if not result:
        print("LLM call failed or returned no valid JSON")
        sys.exit(1)

    processed = _process_llm_response(result, store)

    if not DRY_RUN:
        store.log(
            "propose_memories",
            detail={
                "dailies_scanned": len(dailies),
                "proposals_created": processed["proposals_created"],
                "decay_proposals": processed["decay_proposals"],
                "conflicts": processed["conflicts"],
            }
        )
        total = processed["proposals_created"] + processed["decay_proposals"]
        if total > 0:
            print(f"PROPOSALS:{total}")
    else:
        total = (len(result.get("candidates", [])) +
                 len(result.get("decay_alerts", [])))
        if total == 0:
            print("(no proposals — output is empty)")


if __name__ == "__main__":
    main()
