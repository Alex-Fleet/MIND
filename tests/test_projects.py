#!/usr/bin/env python3
"""
对抗性测试：scripts/projects.py 的 Registry —— 核心是"双向唯一(bijection)"不变量。

纯 python assert，不依赖 pytest：
    python3 tests/test_projects.py

覆盖：合法注册表的查询、未登记 slug 的自我隔离、每一类违规是否被 validate 抓到、
save() 拒绝写坏文件、load() 容错、load→save→load 往返、unregistered、
slugify_id / basename_key 边界，以及 id/slug 命名空间重叠这一潜在坑（记录当前行为）。
"""

import json
import sys
import tempfile
from pathlib import Path

# ── 让 scripts/ 可导入（projects.py 内部 from config import BASE_DIR）──────────
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import projects  # noqa: E402
from projects import (  # noqa: E402
    Registry,
    slugify_id,
    basename_key,
    VALID_TYPES,
    DEFAULT_TYPE,
)

# ── 迷你测试框架：数清楚跑了多少条 ───────────────────────────────────────────
_CHECKS = 0


def check(cond, msg):
    global _CHECKS
    _CHECKS += 1
    if not cond:
        raise AssertionError(msg)


def has(errs, needle):
    """errs 里有任一条含 needle 子串。"""
    return any(needle in e for e in errs)


# ── 夹具：一份合法注册表（三种 type、多 slug、含中文与多段路径 slug）───────────
def valid_projects():
    return [
        {
            "id": "study-assistant",
            "label": "学习助手",
            "type": "long_term",
            "slugs": ["study-assistant-agent", "-Users-jane-study-assistant-agent"],
        },
        {
            "id": "trip-planner",
            "label": "Trip Planner",
            "type": "one_off",
            # slug 与 id 必须隔离：CC 文件夹 slug 天然带后缀/路径，不会等于短 id
            "slugs": ["trip-planner-agent"],
        },
        {
            "id": "old-thing",
            "label": "Old Thing",
            "type": "archived",
            "slugs": ["old-thing-v1", "old-thing-v2"],
        },
    ]


# ══════════════════════════════════════════════════════════════════════════
# 1. 合法注册表：validate 空 + 查询正确
# ══════════════════════════════════════════════════════════════════════════
def test_valid_registry_queries():
    r = Registry(valid_projects())

    check(r.validate() == [], f"合法注册表 validate 应为空，得到 {r.validate()}")
    check(r.is_valid() is True, "合法注册表 is_valid 应为 True")

    # resolve：两个 slug 都指向同一 id
    check(r.resolve("study-assistant-agent") == "study-assistant", "resolve slug1")
    check(
        r.resolve("-Users-jane-study-assistant-agent") == "study-assistant",
        "resolve 多段路径 slug",
    )
    check(r.resolve("trip-planner-agent") == "trip-planner", "resolve 单 slug 项目")

    # slugs_of：项目展开成全部 slug
    check(
        r.slugs_of("study-assistant")
        == ["study-assistant-agent", "-Users-jane-study-assistant-agent"],
        "slugs_of 顺序与内容",
    )
    check(r.slugs_of("no-such-id") == [], "slugs_of 未知 id → []")

    # sibling_slugs：同项目全部 slug（含自己）
    sib = r.sibling_slugs("study-assistant-agent")
    check(
        sib == ["study-assistant-agent", "-Users-jane-study-assistant-agent"],
        f"sibling_slugs 应含全部兄弟，得到 {sib}",
    )
    check(
        r.sibling_slugs("-Users-jane-study-assistant-agent") == sib,
        "任一 slug 得到相同兄弟集",
    )

    # label_of：id 或 slug 都能查
    check(r.label_of("study-assistant") == "学习助手", "label_of by id")
    check(r.label_of("study-assistant-agent") == "学习助手", "label_of by slug")
    check(r.label_of("trip-planner-agent") == "Trip Planner", "label_of 单 slug")

    # type_of：id 或 slug 都能查
    check(r.type_of("study-assistant") == "long_term", "type_of long_term")
    check(r.type_of("trip-planner") == "one_off", "type_of one_off by id")
    check(r.type_of("old-thing-v2") == "archived", "type_of archived by slug")

    check(r.is_registered("trip-planner-agent") is True, "is_registered 已登记")
    check(r.is_registered("trip-planner") is False, "id 不算 slug，未在 _by_slug")


# ══════════════════════════════════════════════════════════════════════════
# 2. 未登记 slug：自我隔离，绝不误并
# ══════════════════════════════════════════════════════════════════════════
def test_unregistered_slug_isolation():
    r = Registry(valid_projects())
    ghost = "ghost-project-xyz"

    check(r.resolve(ghost) is None, "未登记 resolve → None")
    check(r.sibling_slugs(ghost) == [ghost], "未登记 sibling_slugs → [slug] 自我隔离")
    check(r.label_of(ghost) == ghost, "未登记 label_of → 原样返回")
    check(r.type_of(ghost) == DEFAULT_TYPE, f"未登记 type_of → {DEFAULT_TYPE}")
    check(r.type_of(ghost) == "long_term", "默认 type 确为 long_term")
    check(r.is_registered(ghost) is False, "未登记 is_registered False")

    # 空注册表下，任何 slug 都自我隔离（下游不会串记忆）
    empty = Registry([])
    check(empty.resolve("anything") is None, "空表 resolve None")
    check(empty.sibling_slugs("anything") == ["anything"], "空表 sibling 自我隔离")


# ══════════════════════════════════════════════════════════════════════════
# 3. 每一类违规都必须被 validate 抓到
# ══════════════════════════════════════════════════════════════════════════
def test_violation_slug_double_claimed():
    """最严重：一个 slug 被两个项目认领 → 会串记忆。"""
    ps = valid_projects()
    ps[1]["slugs"] = ["trip-planner-agent", "study-assistant-agent"]  # 偷了项目0的 slug
    r = Registry(ps)
    errs = r.validate()
    check(errs != [], "slug 被两个项目认领必须报错")
    check(has(errs, "study-assistant-agent"), "错误里应点名冲突 slug")
    check(has(errs, "认领"), "错误应说明是认领冲突")
    check(r.is_valid() is False, "冲突表 is_valid False")


def test_violation_slug_double_claimed_same_project():
    """同一项目内重复 slug 也算破坏（同一 slug 出现两次）。"""
    ps = valid_projects()
    ps[1]["slugs"] = ["trip-planner-agent", "trip-planner-agent"]
    r = Registry(ps)
    check(r.validate() != [], "同项目内重复 slug 应被抓到")


def test_violation_duplicate_id():
    ps = valid_projects()
    ps[1]["id"] = "study-assistant"  # 与项目0 撞 id
    r = Registry(ps)
    errs = r.validate()
    check(has(errs, "id 重复"), f"id 重复必须报错，得到 {errs}")


def test_violation_duplicate_label():
    ps = valid_projects()
    ps[1]["label"] = "学习助手"  # 与项目0 撞 label
    r = Registry(ps)
    errs = r.validate()
    check(has(errs, "label 重复"), f"label 重复必须报错，得到 {errs}")


def test_violation_invalid_type():
    ps = valid_projects()
    ps[0]["type"] = "eternal"  # 不在 VALID_TYPES
    r = Registry(ps)
    errs = r.validate()
    check(has(errs, "type 非法"), f"非法 type 必须报错，得到 {errs}")
    # 边界：None / 空串 / 拼写大小写都算非法
    for bad in (None, "", "Long_Term", "longterm", "one-off"):
        ps2 = valid_projects()
        ps2[0]["type"] = bad
        check(
            Registry(ps2).validate() != [],
            f"type={bad!r} 应判非法",
        )
    # 确认三种合法 type 各自都能通过
    for good in VALID_TYPES:
        ps3 = valid_projects()
        ps3[0]["type"] = good
        check(Registry(ps3).validate() == [], f"type={good!r} 应合法")


def test_violation_empty_slugs():
    ps = valid_projects()
    ps[1]["slugs"] = []  # 空 slug 列表
    r = Registry(ps)
    check(has(r.validate(), "没有任何 slug"), "空 slug 列表必须报错")
    # 完全缺 slugs 键
    ps2 = valid_projects()
    del ps2[1]["slugs"]
    check(has(Registry(ps2).validate(), "没有任何 slug"), "缺 slugs 键必须报错")


def test_violation_missing_id():
    ps = valid_projects()
    del ps[0]["id"]
    check(has(Registry(ps).validate(), "缺 id"), "缺 id 必须报错")
    # id 为空串
    ps2 = valid_projects()
    ps2[0]["id"] = ""
    check(has(Registry(ps2).validate(), "缺 id"), "空串 id 必须报错")


def test_violation_missing_label():
    ps = valid_projects()
    del ps[0]["label"]
    check(has(Registry(ps).validate(), "缺 label"), "缺 label 必须报错")
    ps2 = valid_projects()
    ps2[0]["label"] = ""
    check(has(Registry(ps2).validate(), "缺 label"), "空串 label 必须报错")


def test_multiple_violations_at_once():
    """多个违规同时存在：validate 不短路，全部收集。"""
    ps = [
        {"id": "", "label": "", "type": "bogus", "slugs": []},  # 缺id/缺label/坏type/空slug
        {"id": "x", "label": "L", "type": "long_term", "slugs": ["s1"]},
        {"id": "x", "label": "L", "type": "long_term", "slugs": ["s1"]},  # 撞id/撞label/撞slug
    ]
    errs = Registry(ps).validate()
    check(has(errs, "缺 id"), "多违规：缺 id")
    check(has(errs, "缺 label"), "多违规：缺 label")
    check(has(errs, "type 非法"), "多违规：坏 type")
    check(has(errs, "没有任何 slug"), "多违规：空 slug")
    check(has(errs, "id 重复"), "多违规：撞 id")
    check(has(errs, "label 重复"), "多违规：撞 label")
    check(has(errs, "认领"), "多违规：撞 slug")
    check(len(errs) >= 6, f"多违规应收集到多条，得到 {len(errs)} 条：{errs}")


# ══════════════════════════════════════════════════════════════════════════
# 4. save()：面对非法注册表必须 raise，且绝不写坏文件
# ══════════════════════════════════════════════════════════════════════════
def test_save_rejects_invalid_and_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "projects.json"

        # (A) 文件原本不存在 → save 非法表抛错后，文件仍不存在
        ps = valid_projects()
        ps[1]["slugs"] = ["study-assistant-agent"]  # 偷 slug → 非法
        bad = Registry(ps, path=target)
        raised = False
        try:
            bad.save()
        except ValueError:
            raised = True
        check(raised, "save 非法表必须 raise ValueError")
        check(not target.exists(), "save 抛错后不得凭空创建坏文件")

        # (B) 文件已有旧的合法内容 → save 非法表抛错后，旧内容原封不动
        good = Registry(valid_projects(), path=target)
        good.save()
        check(target.exists(), "合法 save 应落盘")
        before = target.read_text(encoding="utf-8")

        bad.path = target
        raised = False
        try:
            bad.save()
        except ValueError:
            raised = True
        check(raised, "save 非法表必须 raise（覆盖已有文件的场景）")
        after = target.read_text(encoding="utf-8")
        check(after == before, "save 抛错绝不能污染/截断已有的合法文件")

        # 旧内容依然可被解析回合法注册表
        reloaded = Registry.load(target)
        check(reloaded.validate() == [], "抛错后旧文件仍是合法注册表")


def test_save_valid_writes_utf8_json():
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "projects.json"
        Registry(valid_projects(), path=target).save()
        raw = target.read_text(encoding="utf-8")
        check("学习助手" in raw, "中文应以 UTF-8 明文写出（ensure_ascii=False）")
        data = json.loads(raw)
        check("projects" in data and len(data["projects"]) == 3, "落盘结构正确")


# ══════════════════════════════════════════════════════════════════════════
# 5. load() 容错 + load→save→load 往返
# ══════════════════════════════════════════════════════════════════════════
def test_load_missing_file_is_empty():
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "does-not-exist.json"
        r = Registry.load(missing)
        check(r.projects == [], "读不存在的文件 → 空 projects")
        check(r.validate() == [], "空注册表本身合法")
        check(r.resolve("x") is None, "空表 resolve None（不崩）")


def test_roundtrip_load_save_load():
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "projects.json"
        original = valid_projects()

        Registry(original, path=target).save()
        r1 = Registry.load(target)
        check(r1.projects == original, "第一次 load 内容等于原始")

        r1.save(target)  # 原样再存
        r2 = Registry.load(target)
        check(r2.projects == original, "往返后数据不丢不变")
        check(r2.validate() == [], "往返后仍合法")

        # 查询在往返后依然正确
        check(r2.resolve("old-thing-v1") == "old-thing", "往返后 resolve 正确")
        check(r2.label_of("study-assistant") == "学习助手", "往返后 label 正确")


# ══════════════════════════════════════════════════════════════════════════
# 6. unregistered(all_slugs)
# ══════════════════════════════════════════════════════════════════════════
def test_unregistered_listing():
    r = Registry(valid_projects())
    all_slugs = [
        "study-assistant-agent",  # 已登记
        "brand-new-1",            # 未登记
        "trip-planner-agent",     # 已登记
        "brand-new-2",            # 未登记
        "old-thing-v2",           # 已登记
    ]
    check(
        r.unregistered(all_slugs) == ["brand-new-1", "brand-new-2"],
        f"unregistered 应只返回未认领者并保序，得到 {r.unregistered(all_slugs)}",
    )
    check(r.unregistered([]) == [], "空输入 → 空")
    check(
        r.unregistered(["study-assistant-agent", "trip-planner-agent"]) == [],
        "全部已登记 → []",
    )
    # 空注册表下所有 slug 都算未登记
    check(
        Registry([]).unregistered(["a", "b"]) == ["a", "b"],
        "空表下全部未登记",
    )


# ══════════════════════════════════════════════════════════════════════════
# 7. slugify_id 边界
# ══════════════════════════════════════════════════════════════════════════
def test_slugify_id_edges():
    cases = {
        "": "project",                       # 空串 → 兜底
        "   ": "project",                    # 纯空白
        "!!!": "project",                    # 纯符号
        "@#$ %^&": "project",                # 全是符号/空格
        "Study Assistant": "study-assistant",
        "  Hello--World!! ": "hello-world",  # 首尾空白 + 连续符号压成单 -
        "-Users-a-b-c": "users-a-b-c",       # 多段路径 slug
        "学习助手": "学习助手",              # 纯中文保留
        "中文 Mix 123": "中文-mix-123",      # 中英数字混合
        "UPPER_case": "upper-case",          # 下划线也算分隔
        "a---b___c": "a-b-c",                # 多种分隔压成单 -
    }
    for inp, expect in cases.items():
        got = slugify_id(inp)
        check(got == expect, f"slugify_id({inp!r}) 期望 {expect!r} 得到 {got!r}")
    # None 也不能崩
    check(slugify_id(None) == "project", "slugify_id(None) → project")
    # 输出应可直接当 id：不以 - 开头/结尾、非空
    for inp in cases:
        s = slugify_id(inp)
        check(s and not s.startswith("-") and not s.endswith("-"), f"{inp!r} 输出形态不佳")


# ══════════════════════════════════════════════════════════════════════════
# 8. basename_key 边界
# ══════════════════════════════════════════════════════════════════════════
def test_basename_key_edges():
    cases = {
        "": "",                               # 空串
        "---": "---",                         # 纯符号 → 退化为原串小写
        "a": "a",
        "FOO": "foo",                         # 小写化
        "-Users-a-b-c": "c",                  # 多段路径取末段
        "Study-Assistant-Agent": "agent",
        "学习助手": "学习助手",               # 单段中文
        "-Users-jane-study-assistant-agent": "agent",
        "trip-planner": "planner",
    }
    for inp, expect in cases.items():
        got = basename_key(inp)
        check(got == expect, f"basename_key({inp!r}) 期望 {expect!r} 得到 {got!r}")
    check(basename_key(None) == "", "basename_key(None) → ''（不崩）")


# ══════════════════════════════════════════════════════════════════════════
# 9. 新不变量：id / slug 命名空间不得交叉（从架构上封死跨命名空间串号）
#    —— 曾是"留档的潜在坑"，现已升级为硬规则：validate 报错、save 拒绝落盘。
# ══════════════════════════════════════════════════════════════════════════
def test_id_slug_namespace_crossing_rejected():
    """一个项目的 slug 恰好等于另一个项目的 id。
    这会让 label_of/type_of（按 id 优先）与 resolve（按 slug）指向不同项目，
    下游取到错项目的名字/类型 → 串记忆。新规则必须判非法并拒绝保存。"""
    ps = [
        {"id": "foo", "label": "A项目", "type": "long_term", "slugs": ["a1"]},
        {"id": "b", "label": "B项目", "type": "one_off", "slugs": ["foo"]},  # slug 'foo' == 项目0 的 id
    ]
    r = Registry(ps)
    errs = r.validate()
    check(errs != [], "id/slug 命名空间交叉必须被 validate 抓到")
    check(has(errs, "命名空间交叉"), f"错误应点明命名空间交叉，得到 {errs}")
    check(has(errs, "foo"), "错误应点名交叉的 key: foo")
    check(r.is_valid() is False, "交叉表 is_valid False")

    # save 必须 raise ValueError，且绝不写出坏文件
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "projects.json"
        r.path = target
        raised = False
        try:
            r.save()
        except ValueError:
            raised = True
        check(raised, "save 交叉表必须 raise ValueError")
        check(not target.exists(), "交叉表 save 不得写出坏文件")

    # 反向确认：id 与 slug 隔离时（合法夹具）不误报交叉
    clean = Registry(valid_projects())
    check(
        not has(clean.validate(), "命名空间交叉"),
        "id/slug 隔离的合法表不应报交叉",
    )


# ══════════════════════════════════════════════════════════════════════════
def main():
    tests = [
        test_valid_registry_queries,
        test_unregistered_slug_isolation,
        test_violation_slug_double_claimed,
        test_violation_slug_double_claimed_same_project,
        test_violation_duplicate_id,
        test_violation_duplicate_label,
        test_violation_invalid_type,
        test_violation_empty_slugs,
        test_violation_missing_id,
        test_violation_missing_label,
        test_multiple_violations_at_once,
        test_save_rejects_invalid_and_writes_nothing,
        test_save_valid_writes_utf8_json,
        test_load_missing_file_is_empty,
        test_roundtrip_load_save_load,
        test_unregistered_listing,
        test_slugify_id_edges,
        test_basename_key_edges,
        test_id_slug_namespace_crossing_rejected,
    ]
    failures = []
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            failures.append((t.__name__, str(e)))
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failures.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERR   {t.__name__}: {type(e).__name__}: {e}")

    print("-" * 60)
    print(f"用例函数 {len(tests)} 个，断言 {_CHECKS} 条，失败 {len(failures)} 个")
    if failures:
        for name, msg in failures:
            print(f"  ✗ {name}: {msg}")
        sys.exit(1)
    print("全绿：bijection 不变量未发现漏洞。")


if __name__ == "__main__":
    main()
