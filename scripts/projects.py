#!/usr/bin/env python3
"""
项目注册表 —— 把 Claude Code 易变的文件夹 slug 归一成"稳定项目身份"。

一个真实项目可能有多个 slug 分身（CC 换命名规则 / 用户改名 / 搬家产生）。注册表用
projects.json 记录 { id, label, type, slugs[] }，并强制"双向唯一(bijection)"：
  - id / label 全局唯一
  - 每个 slug 只能属于一个项目（不能被两个项目认领）
下游（注入、看板）都通过它把 slug 解析成项目、把项目展开成它的全部 slug。

projects.json 结构：
{
  "projects": [
    { "id": "study-assistant", "label": "学习助手", "type": "long_term",
      "slugs": ["study-assistant-agent", "-Users-…-study-assistant-agent"] }
  ]
}

type ∈ {long_term, one_off, archived}
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import BASE_DIR

REGISTRY_PATH = BASE_DIR / "projects.json"
VALID_TYPES = {"long_term", "one_off", "archived"}
DEFAULT_TYPE = "long_term"


def slugify_id(label: str) -> str:
    """把显示名转成稳定 id（小写、连续非字母数字/汉字压成单个 -）。"""
    s = re.sub(r"[^0-9a-zA-Z一-鿿]+", "-", (label or "").strip().lower())
    return s.strip("-") or "project"


def basename_key(slug: str) -> str:
    """slug 的归一化末段名，用作自动聚类键。
    slug 是把路径 / 空格都换成 - 的有损编码，取最后一段做近似 basename。"""
    parts = [p for p in (slug or "").strip("-").split("-") if p]
    return parts[-1].lower() if parts else (slug or "").lower()


class Registry:
    """项目注册表。核心不变量：双向唯一（见 validate）。"""

    def __init__(self, projects: list | None = None, path: Path | None = None):
        self.path = Path(path) if path else REGISTRY_PATH
        self.projects = list(projects or [])
        self._reindex()

    def _reindex(self):
        self._by_slug = {}
        self._by_id = {}
        for p in self.projects:
            if p.get("id"):
                self._by_id[p["id"]] = p
            for s in p.get("slugs", []):
                self._by_slug[s] = p

    # ── 加载 / 保存 ─────────────────────────────────
    @classmethod
    def load(cls, path: Path | None = None) -> "Registry":
        path = Path(path) if path else REGISTRY_PATH
        if not path.exists():
            return cls([], path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(data.get("projects", []), path)

    def save(self, path: Path | None = None) -> None:
        """写盘前强制校验；违反双向唯一直接拒绝，绝不落一份坏注册表。"""
        path = Path(path) if path else self.path
        errs = self.validate()
        if errs:
            raise ValueError("拒绝保存：注册表违反双向唯一：\n  " + "\n  ".join(errs))
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"projects": self.projects}, f,
                      ensure_ascii=False, indent=2)

    # ── 校验：双向唯一（bijection）────────────────────
    def validate(self) -> list:
        """返回错误列表（空 = 合法）。检查：
          1. id 全局唯一、非空
          2. label 全局唯一、非空
          3. type 合法
          4. 每个 slug 只属于一个项目
          5. 每个项目至少有一个 slug
        """
        errs = []
        seen_ids, seen_labels, seen_slugs = set(), set(), {}
        for p in self.projects:
            pid, label, typ = p.get("id"), p.get("label"), p.get("type")
            if not pid:
                errs.append(f"项目缺 id: {label or p}")
            elif pid in seen_ids:
                errs.append(f"id 重复: {pid}")
            else:
                seen_ids.add(pid)

            if not label:
                errs.append(f"[{pid}] 缺 label")
            elif label in seen_labels:
                errs.append(f"label 重复: {label}")
            else:
                seen_labels.add(label)

            if typ not in VALID_TYPES:
                errs.append(f"[{pid}] type 非法: {typ}")

            slugs = p.get("slugs", [])
            if not slugs:
                errs.append(f"[{pid}] 没有任何 slug")
            for s in slugs:
                if s in seen_slugs:
                    errs.append(f"slug 被多个项目认领: {s} "
                                f"（{seen_slugs[s]} 与 {pid}）")
                else:
                    seen_slugs[s] = pid
        # id / slug 命名空间不得交叉：否则 label_of/type_of 的 id-优先查找会与
        # resolve 的 slug 查找指向不同项目，下游会取到错项目的名字/类型。
        for k in sorted(seen_ids & set(seen_slugs)):
            errs.append(f"id 与 slug 命名空间交叉（必须隔离）: {k}")
        return errs

    def is_valid(self) -> bool:
        return not self.validate()

    # ── 查询 ──────────────────────────────────────
    def resolve(self, slug: str):
        """slug → 项目 id（未登记返回 None）。"""
        p = self._by_slug.get(slug)
        return p["id"] if p else None

    def slugs_of(self, pid: str) -> list:
        p = self._by_id.get(pid)
        return list(p.get("slugs", [])) if p else []

    def sibling_slugs(self, slug: str) -> list:
        """同项目的全部 slug（含自己）；未登记则退化为 [slug]（自我隔离，安全）。"""
        p = self._by_slug.get(slug)
        return list(p.get("slugs", [])) if p else [slug]

    def label_of(self, key: str) -> str:
        """key 可以是 id 或 slug；未登记返回 key 本身。"""
        p = self._by_id.get(key) or self._by_slug.get(key)
        return p["label"] if p else key

    def type_of(self, key: str) -> str:
        p = self._by_id.get(key) or self._by_slug.get(key)
        return p["type"] if p else DEFAULT_TYPE

    def is_registered(self, slug: str) -> bool:
        return slug in self._by_slug

    def unregistered(self, all_slugs) -> list:
        """给定全部 slug，返回还没被任何项目认领的（供扫描向导标黄）。"""
        return [s for s in all_slugs if s not in self._by_slug]


def load_registry(path=None) -> Registry:
    return Registry.load(path)
