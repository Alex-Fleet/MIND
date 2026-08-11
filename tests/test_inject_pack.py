"""贪心打包 + 安全截断单测：_pack_greedy / _safe_truncate。

覆盖：多文件排序打包、超 1e4 封包、单文件 >1e4 截断单独成包、
空输入、包拼接 = 全量、排序稳定性、字符单位（非字节）。
"""

from inject import PACK_MAX_CHARS, _pack_greedy, _safe_truncate


def _md(name: str, n_chars: int) -> str:
    """构造一个 markdown 片段：标题 + 指定字符数的正文（中文 1 字符）。"""
    return f"## {name}\n\n" + "啊" * max(0, n_chars) + "\n"


# ── 打包边界 ──────────────────────────────────────────────

def test_sum_under_limit_single_pack():
    a, b = _md("a", 100), _md("b", 200)
    packs = _pack_greedy([a, b])
    assert len(packs) == 1
    # join 用 \n 分隔（每段自带尾部 \n），检查内容完整 + 顺序
    assert packs[0].startswith(a)
    assert "## b" in packs[0]


def test_sum_over_limit_splits_packs():
    a = _md("a", 6000)
    b = _md("b", 6000)
    packs = _pack_greedy([a, b])
    assert len(packs) == 2
    assert all(len(p) <= PACK_MAX_CHARS for p in packs)
    # 内容无丢失、顺序稳定：每段完整出现在拼接里
    joined = "".join(packs)
    assert joined.count("## a") == 1 and joined.count("## b") == 1
    assert joined.index("## a") < joined.index("## b")


def test_each_pack_within_limit_many_files():
    files = [_md(f"f{i}", 3000) for i in range(10)]  # 10 × 3000 = 30000
    packs = _pack_greedy(files)
    assert all(len(p) <= PACK_MAX_CHARS for p in packs)
    joined = "".join(packs)
    for i, f in enumerate(files):
        assert f"## f{i}" in joined


# ── 单文件超长 ────────────────────────────────────────────

def test_oversize_file_truncated_alone():
    big = _md("big", PACK_MAX_CHARS + 500)
    small = _md("s", 100)
    packs = _pack_greedy([big, small])
    # 大文件截断单独成包，小文件另一包
    assert len(packs) == 2
    assert all(len(p) <= PACK_MAX_CHARS for p in packs)


# ── 空输入 ───────────────────────────────────────────────

def test_empty_returns_single_empty():
    assert _pack_greedy([]) == [""]


def test_blank_entries_skipped():
    packs = _pack_greedy(["", "   ", "\n\n"])
    assert packs == [""]


# ── 稳定性 ───────────────────────────────────────────────

def test_stable_same_input_same_output():
    files = [_md(f"f{i}", 2500) for i in range(8)]
    first = _pack_greedy(files)
    second = _pack_greedy(files)
    assert first == second
    assert [len(p) for p in first] == [len(p) for p in second]


# ── _safe_truncate 字符单位 ──────────────────────────────

def test_truncate_char_units_not_bytes():
    """旧 bug：按字节 9500 会把中文 8504 字符砍到 ~4932（丢 42%）。
    现在按字符 1e4，8504 字符完整保留。"""
    text = "中文" * 4252  # 8504 字符（UTF-8 25512 字节）
    out = _safe_truncate(text, 10000)
    assert len(out) <= 10000
    assert len(out) > 8000  # 8504 字符不该被字节截断砍到 4932


def test_truncate_under_limit_untouched():
    text = "a" * 500
    assert _safe_truncate(text, 10000) == text


def test_truncate_notes_char_count():
    text = "啊" * 12000
    out = _safe_truncate(text, 10000)
    assert "字符" in out  # 提示信息标注是字符而非字节
