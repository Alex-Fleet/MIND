#!/usr/bin/env python3
"""
项目初步分类 —— 扫描所有 slug，喂给 LLM 归并成"真身项目"，产出 projects.json 草稿。

流程：
  1. 从 DB 收集每个 slug 的统计（会话数/turn数/真实时间跨度）+ 几条用户原话样本
  2. 交给 LLM：把同一项目的 slug 归并、起中文名、猜类型(long_term/one_off/archived)
  3. 对 LLM 输出做双向唯一兜底（漏掉的 slug 补成独立项目、重复的只留一次）
  4. 过 Registry.validate 校验后写 projects.draft.json（不覆盖已有 projects.json）

用法：
  python3 classify.py --scan              # 扫描 + LLM 分类 → 写草稿
  python3 classify.py --scan --dry-run    # 只打印，不写文件

安全：只写 *.draft.json，不生效、不碰注入。过目无误后改名成 projects.json 才生效。
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths, BASE_DIR
from projects import Registry, slugify_id, basename_key, VALID_TYPES
from llm_utils import call_llm

DRAFT_PATH = BASE_DIR / "projects.draft.json"


def collect_slug_stats(db_path: str) -> list:
    """每个 slug 一行统计 + 最多 3 条用户原话样本。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.project AS slug,
               COUNT(DISTINCT s.id) AS sessions,
               COUNT(t.id)          AS turns,
               MIN(t.timestamp)     AS first_ts,
               MAX(t.timestamp)     AS last_ts
        FROM sessions s LEFT JOIN turns t ON t.session_id = s.id
        GROUP BY s.project
        ORDER BY last_ts DESC
    """).fetchall()

    stats = []
    for r in rows:
        samples = [x[0].replace("\n", " ")[:60] for x in conn.execute("""
            SELECT t.content FROM turns t JOIN sessions s ON t.session_id = s.id
            WHERE s.project = ? AND t.role = 'user'
              AND length(trim(t.content)) > 4
            ORDER BY t.timestamp LIMIT 3
        """, (r["slug"],)).fetchall()]
        stats.append({
            "slug": r["slug"],
            "sessions": r["sessions"],
            "turns": r["turns"],
            "first": (r["first_ts"] or "")[:10],
            "last": (r["last_ts"] or "")[:10],
            "basename": basename_key(r["slug"]),
            "samples": samples,
        })
    conn.close()
    return stats


CLASSIFY_PROMPT = """你是项目归类器。下面是某用户在 Claude Code 里所有"项目文件夹 slug"及其统计。
同一个真实项目可能有多个 slug 分身（Claude Code 换过命名规则、或用户改名/搬家导致）。

【任务】
1. 把指向**同一真实项目**的 slug 归并到一组。依据：末段名相同、或从样本内容明显看出是同一项目（改名/搬家）。
2. 给每组起一个简短**中文显示名** label。
3. 判类型 type：
   - long_term：多会话、跨多天、明显长期在做的项目
   - one_off：单次找 Claude 干一件事（外包、临时脚本、一次性任务）
   - archived：很久没动、明显烂尾
4. 【保守铁律】拿不准是否同一项目时，**宁可拆成两个也不要错误合并**——错误合并会串记忆，是最脏的污染。

【slug 列表】
{slugs_block}

【输出：严格 JSON，不要 markdown 代码块】
{{
  "projects": [
    {{"label": "中文名", "type": "long_term", "slugs": ["slug1", "slug2"], "reason": "归并/定类型的依据"}}
  ]
}}
每个输入 slug 必须**恰好出现一次**——不能漏、不能重复。"""


def build_slugs_block(stats: list) -> str:
    lines = []
    for s in stats:
        sm = " / ".join(s["samples"]) if s["samples"] else "(无样本)"
        lines.append(
            f'- slug: {s["slug"]}\n'
            f'  末段名={s["basename"]}  会话={s["sessions"]}  turn={s["turns"]}  '
            f'时间={s["first"]}~{s["last"]}\n'
            f'  样本: {sm}'
        )
    return "\n".join(lines)


def reconcile(llm_projects: list, all_slugs: list):
    """对 LLM 输出做双向唯一兜底，返回 (projects, notes)：
      - 未知 slug 丢弃
      - 重复分配的 slug 只留第一次
      - LLM 漏掉的 slug 补成独立项目（type=one_off，待人确认）
      - id / label 去重
    """
    notes = []
    valid = set(all_slugs)
    assigned = set()
    out = []
    used_ids, used_labels = set(), set()

    def uniq_id(label):
        base = slugify_id(label)
        i, n = base, 2
        while i in used_ids or i in valid:   # id 不得撞已用 id，也不得撞任何 slug
            i, n = f"{base}-{n}", n + 1
        used_ids.add(i)
        return i

    def uniq_label(label):
        i, n = label, 2
        while i in used_labels:
            i, n = f"{label}-{n}", n + 1
        used_labels.add(i)
        return i

    for p in llm_projects or []:
        slugs = []
        for s in p.get("slugs", []):
            if s not in valid:
                notes.append(f"LLM 提到未知 slug，忽略：{s}")
            elif s in assigned:
                notes.append(f"LLM 把 slug 重复分配，只留第一次：{s}")
            else:
                assigned.add(s)
                slugs.append(s)
        if not slugs:
            continue
        label = uniq_label((p.get("label") or basename_key(slugs[0])).strip())
        typ = p.get("type") if p.get("type") in VALID_TYPES else "long_term"
        out.append({"id": uniq_id(label), "label": label, "type": typ,
                    "slugs": slugs, "reason": p.get("reason", "")})

    for s in all_slugs:
        if s not in assigned:
            label = uniq_label(basename_key(s))
            notes.append(f"LLM 漏了 slug，补成独立项目待确认：{s}")
            out.append({"id": uniq_id(label), "label": label, "type": "one_off",
                        "slugs": [s], "reason": "LLM 未归类，自动补齐待确认"})
    return out, notes


def main():
    dry = "--dry-run" in sys.argv
    paths = get_paths()
    stats = collect_slug_stats(str(paths["db_path"]))
    all_slugs = [s["slug"] for s in stats]
    print(f"  扫描到 {len(all_slugs)} 个 slug")

    prompt = CLASSIFY_PROMPT.format(slugs_block=build_slugs_block(stats))
    result = call_llm(prompt, system="你是项目归类器，严格按 JSON 输出。")
    llm_projects = (result or {}).get("projects", [])
    if not llm_projects:
        print("  ⚠ LLM 没返回有效分组，全部走兜底（每个 slug 独立）")

    projects, notes = reconcile(llm_projects, all_slugs)
    reg = Registry(projects)
    errs = reg.validate()
    if errs:
        print("  ⚠ 兜底后仍未过双向唯一校验（不应发生）：\n   " + "\n   ".join(errs))
        return

    print(f"\n  归并结果：{len(all_slugs)} 个 slug → {len(projects)} 个项目")
    for p in projects:
        print(f"  ● [{p['type']}] {p['label']}  （{len(p['slugs'])} 个分身）")
        for s in p["slugs"]:
            print(f"      - {s}")
        if p.get("reason"):
            print(f"      理由：{p['reason']}")
    if notes:
        print("\n  兜底提示：")
        for n in notes:
            print("   - " + n)

    if dry:
        print("\n  [DRY RUN] 未写文件")
        return
    reg.save(DRAFT_PATH)
    print(f"\n  ✓ 草稿写入 {DRAFT_PATH}")
    print("    过目无误后改名成 projects.json 即生效（Phase 2 接入注入/看板）")


if __name__ == "__main__":
    main()
