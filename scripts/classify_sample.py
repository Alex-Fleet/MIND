#!/usr/bin/env python3
"""随机抽取 100 条历史摘要做有效性分类，供人工审查校准。"""
import os, sys, sqlite3, random
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

    # 随机抽 100 条（排除 7-14 已分类和已有 validity 的）
    all_rows = conn.execute("""
        SELECT ts.id, ts.title, ts.summary, ts.session_id, ts.turn_seq, ts.project,
               (SELECT substr(t.content, 1, 300) FROM turns t
                WHERE t.session_id = ts.session_id
                  AND t.seq = ts.turn_seq AND t.role = 'user'
                LIMIT 1) AS user_input
        FROM turn_summaries ts
        WHERE ts.validity IS NULL
        ORDER BY RANDOM()
        LIMIT 100
    """).fetchall()

    print(f"随机抽取: {len(all_rows)} 条\n")

    results = []
    for i, r in enumerate(all_rows):
        title = r["title"] or ""
        summary = (r["summary"] or "")[:500]
        user_input = (r["user_input"] or "")[:300]

        # Layer 1
        fake_pair = {"conversation": f"👤 用户:\n{user_input}\n\n🤖 Claude:\n{summary}"}
        l1 = _classify_noise(fake_pair)

        if l1 == "invalid":
            validity = "invalid"
            method = "L1"
        else:
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
            method = "L2"

        conn.execute("UPDATE turn_summaries SET validity = ? WHERE id = ?",
                     (validity, r["id"]))

        results.append({
            "title": title,
            "user_input": user_input[:120],
            "summary": summary[:200],
            "validity": validity,
            "method": method,
            "project": r["project"],
        })

        tag = {"valid": ".", "low_value": "L", "invalid": "X"}.get(validity, "?")
        print(f"{tag}", end="", flush=True)
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/100")

    conn.commit()
    conn.close()

    vcounts = {}
    for r in results:
        vcounts[r["validity"]] = vcounts.get(r["validity"], 0) + 1

    print(f"\n=== 分类结果 ===")
    for k, v in sorted(vcounts.items()):
        print(f"  {k}: {v} 条")

    # 输出 low_value 和 invalid 列表供审查
    flagged = [r for r in results if r["validity"] != "valid"]
    print(f"\n=== 非 valid ({len(flagged)} 条) ===")
    for r in flagged:
        print(f"\n[{r['validity']}] [{r['project'][:30]}] {r['title']}")
        print(f"  用户: {r['user_input'][:150]}")
        print(f"  摘要: {r['summary'][:150]}")

    # 保存到文件供深度审查
    import json as _json
    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "classify_sample_100.json")
    with open(out_path, "w", encoding="utf-8") as f:
        _json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n完整结果: {out_path}")


if __name__ == "__main__":
    main()
