"""改名完整性测试：iron-rules.md → programming-standards.md（辅助性程序设计规范）。

改名后验证：
1. 新规则文件存在，标题为「辅助性程序设计规范」，旧文件消失
2. 示例文件同步改名
3. 代码/文档引用（on_prompt / install / test_store / CLAUDE / ARCHITECTURE）不再指向旧文件名
4. 全仓库 `iron-rules` 字符串只允许出现在豁免文件（WHITELIST）

豁免原因：
- CHANGELOG.md          历史记录，改名不改历史
- tests/test_store.py   rename 迁移测试断言旧路径字面量（验证迁移必须引用旧名）
- scripts/rename_memory_path.py  迁移脚本要写旧名才能迁移
- tests/test_rename.py  测试自身（扫描时单独排除）

不改动：口语「铁律」（classify*.py / on_session_start.py）、migrate.py 旧数据映射（中文 key）、
CHANGELOG.md 历史条目——这些不属于本文件改名范围。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLOBAL_DIR = ROOT / "memory" / "global"
NEW_FILE = GLOBAL_DIR / "programming-standards.md"
OLD_FILE = GLOBAL_DIR / "iron-rules.md"
NEW_EXAMPLE = ROOT / "memory" / "programming-standards.example.md"
OLD_EXAMPLE = ROOT / "memory" / "iron-rules.example.md"

# 允许继续保留 `iron-rules` 字符串的文件（历史 / 测试断言 / 迁移脚本）
WHITELIST = {
    ROOT / "CHANGELOG.md",
    ROOT / "tests" / "test_store.py",
    ROOT / "scripts" / "rename_memory_path.py",
}

# 排除目录：私密数据 / VCS / 缓存 / 依赖
EXCLUDED_PARTS = {".git", "data", "__pycache__", "node_modules",
                  ".claude", "dist", ".venv", "venv"}


# ── 文件存在性 ──────────────────────────────────────────────

def test_new_rules_file_exists():
    assert NEW_FILE.exists(), f"新规则文件应存在: {NEW_FILE}"


def test_old_rules_file_gone():
    assert not OLD_FILE.exists(), "旧规则文件 iron-rules.md 应已改名"


def test_new_file_title_is_aux_programming_standard():
    first_line = NEW_FILE.read_text(encoding="utf-8").strip().splitlines()[0]
    assert first_line == "# 辅助性程序设计规范", f"标题应为 # 辅助性程序设计规范，实际: {first_line}"


# ── 示例文件 ────────────────────────────────────────────────

def test_example_files_renamed():
    assert NEW_EXAMPLE.exists(), "新示例文件应存在"
    assert not OLD_EXAMPLE.exists(), "旧示例文件 iron-rules.example.md 应已改名"


# ── 代码 / 文档引用 ─────────────────────────────────────────

def test_hook_points_to_new_name():
    text = (ROOT / "hooks" / "on_prompt.py").read_text(encoding="utf-8")
    assert "programming-standards.md" in text
    assert "iron-rules.md" not in text


def test_install_uses_new_name():
    text = (ROOT / "install.py").read_text(encoding="utf-8")
    assert "--file programming-standards.md" in text
    assert "iron-rules.md" not in text


def test_test_store_uses_new_path():
    """test_store 里旧路径只允许出现在 rename 迁移测试中。

    普通测试（registry upsert）应改用新路径；但 3 个
    rename_registry_path 测试必须引用旧路径才能验证迁移行为。
    """
    lines = (ROOT / "tests" / "test_store.py").read_text(encoding="utf-8").splitlines()
    assert any("programming-standards.md" in ln for ln in lines)
    # 追踪当前所在测试函数名，旧路径不得出现在迁移测试之外
    in_mig_test = False
    offenders = []
    for i, ln in enumerate(lines, 1):
        if ln.startswith("def test_"):
            in_mig_test = "test_rename_registry_path" in ln
        if "iron-rules" in ln and not in_mig_test:
            offenders.append(i)
    assert not offenders, f"test_store.py 非迁移测试处仍有旧路径: {offenders}"


def test_claude_md_uses_new_name():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "programming-standards.md" in text
    assert "iron-rules.md" not in text


def test_architecture_md_uses_new_name():
    text = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "programming-standards.md" in text
    assert "programming-standards.example.md" in text
    assert "iron-rules" not in text


# ── 全仓库残留扫描 ──────────────────────────────────────────

def test_no_stray_iron_rules_outside_whitelist():
    """除白名单外，仓库内不得再出现 `iron-rules` 字符串。"""
    offenders = []
    for f in ROOT.rglob("*"):
        if not f.is_file():
            continue
        if any(part in EXCLUDED_PARTS for part in f.relative_to(ROOT).parts):
            continue
        # 排除测试文件自身：它含 iron-rules 字面量用于断言（不是残留引用）
        if f.resolve() == (ROOT / "tests" / "test_rename.py").resolve():
            continue
        if f.resolve() in {w.resolve() for w in WHITELIST}:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "iron-rules" in text:
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, f"仓库内残留 iron-rules 引用: {offenders}"
