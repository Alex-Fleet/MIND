"""pytest 公共配置：让 tests/ 能导入 scripts/ 下的模块 + 共享 fixture。"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pytest  # noqa: E402


@pytest.fixture
def store(tmp_path):
    """临时库 Store 实例：正常/边界/重复写/写后读回都在它上面测。"""
    from store import Store
    return Store(db_path=str(tmp_path / "test.db"))
