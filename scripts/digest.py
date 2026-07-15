#!/usr/bin/env python3
"""
Daily + Monthly report generation.

Usage:
  python3 digest.py --check              # check & generate if due
  python3 digest.py --daily --date YYYY-MM-DD --project <slug>
  python3 digest.py --monthly --month YYYY-MM --project <slug>
  python3 digest.py --dry-run
"""

import json
import os
import re
import sys
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_paths, load_config
from store import Store
from llm_utils import call_llm, call_llm_raw

# 当 --json 时，进度文本走 stderr，stdout 只留一条 JSON 供 hook 解析。
JSON_MODE = False


def _safe_name(s: str) -> str:
    """把项目名/slug 压成安全的文件名片段。"""
    return re.sub(r"[^0-9a-zA-Z一-鿿]+", "-", (s or "").strip()).strip("-")[:60]


def out(msg: str) -> None:
    """进度输出：人类模式→stdout；--json 模式→stderr（保持 stdout 纯净）。"""
    print(msg, file=sys.stderr if JSON_MODE else sys.stdout)

DAILY_PROMPT = """你是"MIND"的日报编辑。基于以下今天的 turn 摘要，生成一份日报。

【日期】{date}
【项目】{project}
{turn_summaries}

【要求】
1. **今日主线**（1段）：这些 turn 围绕什么主题展开？不同 turn 之间的关联？
2. **关键产出**（3-5条）：完成了什么？什么决策被做出了？
3. **遗留与风险**：今天没解决的问题，明天需要注意的事
4. **跨日趋势**（如果有）：连续处理多天？方向在变化？

输出严格JSON（不要markdown代码块）：
{{
  "title": "日报标题",
  "main_thread": "今日主线描述（1段）",
  "key_outputs": ["产出1", "产出2"],
  "legacy_risks": ["遗留问题1"],
  "cross_day_trend": "跨日趋势（没有则为空字符串）"
}}"""

MONTHLY_PROMPT = """你是"MIND"的月报编辑。基于以下日报，生成本月报告。

【月份】{month}
【项目】{project}
{daily_reports}

【要求】
1. **本月主线**（1-2段）：这个月在做什么？整体方向？有没有重大方向变化？
2. **里程碑与产出**：本月完成了什么重要的事？和上月比有什么进展？
3. **经验教训**：本月发现的值得长期记住的反模式/技巧/规则（永久保留级别）
4. **下月展望**：已知的下个月要做的事

输出严格JSON（不要markdown代码块）：
{{
  "title": "月报标题",
  "main_thread": "本月主线",
  "milestones": ["里程碑1", "里程碑2"],
  "lessons": [{{"lesson": "教训内容", "category": "architecture|prompt|workflow|bug"}}],
  "next_month": "下月展望"
}}"""


def generate_daily(date_str: str, project: str, store: Store,
                   paths: dict, dry_run: bool = False) -> bool:
    """Generate a daily report for one date + project.

    修后行为：
      - 按真实对话日期分桶（store 层已改）
      - 存档文件名含项目，避免同日不同项目互相覆盖
      - 来源 turn 清单剥离到独立索引 markdown，content 只留总结
      - 注入时 content 不截断（完整 1.5K 总结直注）
    """
    turns = store.get_turn_summaries_for_date(date_str, project)
    # 过滤明确噪音（Layer 1 拦截的无效 turn），避免污染日报质量
    valid_turns = [t for t in turns if t.get("validity") != "invalid"]
    if len(valid_turns) < load_config()["thresholds"]["min_summaries_for_daily"]:
        return False

    # Format turn summaries for LLM prompt
    turn_text = ""
    for t in valid_turns:
        if t.get("key_decisions"):
            kd = json.loads(t["key_decisions"]) if isinstance(
                t["key_decisions"], str) else t["key_decisions"]
            if kd:
                turn_text += f"决策: {', '.join(kd)}\n"
        turn_text += "\n"

    prompt = DAILY_PROMPT.format(
        date=date_str,
        project=project,
        turn_summaries=turn_text,
    )

    if dry_run:
        out(f"  [DRY RUN] 将生成日报: {date_str} / {project} "
            f"({len(valid_turns)} turns)")
        return False

    result = call_llm(prompt, system="你是项目记忆管理系统。严格按 JSON 格式输出。")
    if not result:
        return False

    # Build source list
    source_turns = [
        {"session_id": t["session_id"], "turn_seq": t["turn_seq"],
         "summary_id": t["id"]}
        for t in valid_turns
    ]

    # ── 存档文件名含项目，避免覆盖 ──
    safe_proj = _safe_name(project)
    daily_dir = paths["daily_dir"]
    os.makedirs(daily_dir, exist_ok=True)
    filename = f"{date_str}-{safe_proj}.md"
    file_path = daily_dir / filename
    rel_path = f"daily/{filename}"

    # ── 来源索引独立文件（渐进式披露：LLM 点开才看）──
    index_filename = f"{date_str}-{safe_proj}-index.md"
    index_path = daily_dir / index_filename
    index_rel = f"daily/{index_filename}"
    index_md = f"# 来源索引 · {date_str} · {project}\n\n"
    index_md += f"**覆盖**: {len(valid_turns)} 个 turn 摘要\n\n"
    for t in valid_turns:
        index_md += f"- [{t['title']}](../turns/{t['file_path'].split('turns/')[-1] if 'turns/' in t['file_path'] else t['file_path']})\n"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_md)

    # ── 日报正文：只有总结，不含来源清单 ──
    content = f"""# 日报: {date_str}
**项目**: {project}
**覆盖**: {len(valid_turns)} 个 turn 摘要

## 今日主线
{result.get('main_thread', '')}

## 关键产出
{chr(10).join('- ' + o for o in result.get('key_outputs', []))}

## 遗留与风险
{chr(10).join('- ' + r for r in result.get('legacy_risks', []))}

## 跨日趋势
{result.get('cross_day_trend', '（无）')}

---
→ [📋 来源索引]({index_rel})
"""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    store.insert_daily_report(
        date_str, project, rel_path,
        result.get("title", f"日报 {date_str}"),
        content, source_turns
    )
    out(f"  ✓ 日报: {date_str} / {project}  ({len(valid_turns)} turns)")
    return True


def generate_monthly(month: str, project: str, store: Store,
                     paths: dict, dry_run: bool = False) -> bool:
    """Generate a monthly report for one month + project."""
    dailies = store.get_daily_reports_for_month(month, project)
    if len(dailies) < load_config()["thresholds"]["min_dailies_for_monthly"]:
        return False

    daily_text = ""
    for d in dailies:
        daily_text += f"### {d['date']}: {d['title']}\n{d['content'][:500]}\n\n"

    prompt = MONTHLY_PROMPT.format(
        month=month,
        project=project,
        daily_reports=daily_text,
    )

    if dry_run:
        out(f"  [DRY RUN] 将生成月报: {month} / {project} "
            f"({len(dailies)} dailies)")
        return False

    result = call_llm(prompt, system="你是项目记忆管理系统。严格按 JSON 格式输出。")
    if not result:
        return False

    source_dailies = [d["id"] for d in dailies]

    safe_proj = _safe_name(project)
    monthly_dir = paths["monthly_dir"]
    os.makedirs(monthly_dir, exist_ok=True)
    filename = f"{month}-{safe_proj}.md"
    file_path = monthly_dir / filename
    rel_path = f"monthly/{filename}"

    lessons_md = ""
    for l in result.get("lessons", []):
        lessons_md += f"- **[{l.get('category', 'general')}]** {l.get('lesson', '')}\n"

    content = f"""# 月报: {month}
**项目**: {project}
**覆盖**: {len(dailies)} 个日报

## 本月主线
{result.get('main_thread', '')}

## 里程碑
{chr(10).join('- ' + m for m in result.get('milestones', []))}

## 经验教训
{lessons_md}

## 下月展望
{result.get('next_month', '（无）')}

---
来源日报:
{chr(10).join(f"- [{d['date']}]({d['file_path']})" for d in dailies)}
"""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    store.insert_monthly_report(
        month, project, rel_path,
        result.get("title", f"月报 {month}"),
        content, source_dailies
    )
    out(f"  ✓ 月报: {month} / {project}")
    return True


def check_and_generate(store: Store, paths: dict,
                       dry_run: bool = False) -> dict:
    """Check all projects for missing daily/monthly reports.

    Returns the dates/months actually generated:
      {"daily": ["2026-07-10", ...], "monthly": ["2026-07", ...]}
    """
    result = {"daily": [], "monthly": []}

    # Get unique projects from turn_summaries
    with __import__('sqlite3').connect(store.db_path) as conn:
        projects = [r[0] for r in conn.execute(
            "SELECT DISTINCT project FROM turn_summaries"
        ).fetchall()]

    today = date.today().isoformat()

    for project in projects:
        # Check missing daily reports
        missing_dates = store.get_missing_daily_dates(project)
        for d in missing_dates:
            # Don't generate today's report until tomorrow
            if d == today:
                continue
            if generate_daily(d, project, store, paths, dry_run):
                result["daily"].append(d)

        # Check missing monthly reports
        missing_months = store.get_missing_monthly_months(project)
        for m in missing_months:
            if generate_monthly(m, project, store, paths, dry_run):
                result["monthly"].append(m)

    return result


def main():
    global JSON_MODE
    JSON_MODE = "--json" in sys.argv
    dry_run = "--dry-run" in sys.argv
    check = "--check" in sys.argv

    paths = get_paths()
    store = Store()

    if check or len(sys.argv) == 1:
        result = check_and_generate(store, paths, dry_run)
        if result["daily"] or result["monthly"]:
            out(f"  日报: {len(result['daily'])} | 月报: {len(result['monthly'])}")
        else:
            out("  (无需生成新报告)")
        if JSON_MODE:
            print(json.dumps({
                "daily": result["daily"],
                "monthly": result["monthly"],
            }, ensure_ascii=False))
    else:
        # Parse --daily --date YYYY-MM-DD --project <slug>
        # or --monthly --month YYYY-MM --project <slug>
        args = sys.argv[1:]
        daily_mode = "--daily" in args
        monthly_mode = "--monthly" in args

        date_str = None
        month_str = None
        project = None
        i = 0
        while i < len(args):
            if args[i] == "--date" and i + 1 < len(args):
                date_str = args[i + 1]
                i += 2
            elif args[i] == "--month" and i + 1 < len(args):
                month_str = args[i + 1]
                i += 2
            elif args[i] == "--project" and i + 1 < len(args):
                project = args[i + 1]
                i += 2
            else:
                i += 1

        if daily_mode and date_str and project:
            generate_daily(date_str, project, store, paths, dry_run)
        elif monthly_mode and month_str and project:
            generate_monthly(month_str, project, store, paths, dry_run)
        else:
            print("Usage: digest.py --daily --date YYYY-MM-DD --project <slug>")
            print("       digest.py --monthly --month YYYY-MM --project <slug>")
            print("       digest.py --check")

    store.log("digest", detail={"dry_run": dry_run})


if __name__ == "__main__":
    main()
