#!/usr/bin/env python3
"""对 7-14 已有摘要做有效性分类：Layer 1 正则 + Layer 2 LLM 判断，更新 validity 列。"""
import os, sys, sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths
from store import Store
from llm_utils import call_llm
from summarize import _classify_noise

VALIDITY_JUDGE_PROMPT = """你是记忆系统的过滤守卫。判断以下对话摘要是否有跨会话记忆价值。

【铁律】"不确定"一律判 "valid"。只有 100% 确定低价值时才判 "low_value"。

"valid"（默认，大多数情况）：
- 用户提出需求、决策、纠正方向
- 助手产出代码、解释、方案、文件修改
- 含可复用知识或项目状态变化

"low_value"（仅在完全确定时）：
- 用户仅说"好了吗""继续""嗯""好"等纯进度催促
- 且助手回复无新内容（如"还在跑""等一下"）
- 互动结束后无任何新信息残留

【摘要信息】
标题: {title}
摘要: {summary}
用户说了什么: {user_input}

输出严格JSON（不要markdown代码块）：
{{"validity": "valid"|"low_value"}}"""


def main():
    paths = get_paths()
    store = Store()

    conn = sqlite3.connect(str(paths["db_path"]))
    conn.row_factory = sqlite3.Row

    # 查 7-14 的所有 turn_summaries
    rows = conn.execute("""
        SELECT ts.id, ts.title, ts.summary, ts.session_id, ts.turn_seq,
               (SELECT substr(t.content, 1, 200) FROM turns t
                WHERE t.session_id = ts.session_id
                  AND t.seq = ts.turn_seq AND t.role = 'user'
                LIMIT 1) AS user_input
        FROM turn_summaries ts
        JOIN turns t2 ON t2.session_id = ts.session_id
                      AND t2.seq = ts.turn_seq AND t2.role = 'user'
        WHERE date(t2.timestamp) = '2026-07-14'
        ORDER BY t2.timestamp
    """).fetchall()

    print(f"7-14 turn_summaries: {len(rows)} 条\n")

    results = {"valid": 0, "low_value": 0, "invalid": 0}
    for i, r in enumerate(rows):
        title = r["title"] or ""
        summary = (r["summary"] or "")[:500]
        user_input = (r["user_input"] or "")[:200]

        # ── Layer 1: 确定性规则（基于摘要标题+正文）──
        # 构造伪 turn_pair 供 _classify_noise 检查
        fake_pair = {"conversation": f"👤 用户:\n{user_input}\n\n🤖 Claude:\n{summary}"}
        l1_result = _classify_noise(fake_pair)

        if l1_result == "invalid":
            validity = "invalid"
            method = "L1规则"
        else:
            # ── Layer 2: LLM 判断 ──
            prompt = VALIDITY_JUDGE_PROMPT.format(
                title=title, summary=summary[:300], user_input=user_input,
            )
            result = call_llm(
                prompt,
                system="你是记忆过滤守卫。只输出 JSON。",
                temperature=0.0,
                max_tokens=50,
            )
            validity = (result or {}).get("validity", "valid")
            if validity not in ("valid", "low_value", "invalid"):
                validity = "valid"
            method = "L2 LLM"

        # 写 DB
        conn.execute(
            "UPDATE turn_summaries SET validity = ? WHERE id = ?",
            (validity, r["id"])
        )
        results[validity] = results.get(validity, 0) + 1

        tag = {"valid": "✓", "low_value": "⚠", "invalid": "✗"}.get(validity, "?")
        print(f"  {tag} [{validity:9s}] {method}: {title[:70]}")

    conn.commit()
    conn.close()

    print(f"\n=== 结果 ===")
    total = sum(results.values())
    for k, v in sorted(results.items()):
        pct = v / total * 100 if total else 0
        print(f"  {k:10s}: {v:2d} 条 ({pct:.0f}%)")
    print(f"  总计: {total} 条")


if __name__ == "__main__":
    main()
