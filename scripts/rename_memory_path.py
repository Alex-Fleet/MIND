#!/usr/bin/env python3
"""数据修正：把 memory_registry 表里已改名文件的旧路径批量更新到新路径。

用法：
  python3 scripts/rename_memory_path.py <旧路径> <新路径>

幂等：重复跑不重复生效；新路径已有同 section 条目时跳过（不覆盖）。
本次执行：iron-rules.md → programming-standards.md。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import Store


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    old_path, new_path = sys.argv[1], sys.argv[2]
    store = Store()
    changed = store.rename_registry_path(old_path, new_path)
    print(f"registry: {old_path} → {new_path}（更新 {changed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
