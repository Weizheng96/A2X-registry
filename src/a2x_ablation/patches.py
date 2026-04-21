"""Monkey-patches that realize individual ablations.

Each patch returns a context manager `with patch_xxx(): ...` that applies the
patch on entry and restores the original on exit. Use `apply_patch(fn)` helper
when you need to apply a patch by reference from experiments.py.
"""

from __future__ import annotations
import contextlib
from typing import List


# --------------------------------------------------------------------------
# Build-phase patches
# --------------------------------------------------------------------------


@contextlib.contextmanager
def patch_no_early_stop():
    """消融 2：取消独立分类校验早停。

    原 `_should_terminate(stats, iteration, max_refine)` 在 stats 显示无泛分类/
    无未分类时会提前停止；消融 2 要求"强制跑满 max_refine_iterations 才停"。

    实现细节：原方法是 `@staticmethod`。通过 `__dict__` 捕获原始描述符对象，
    以便恢复时完整还原 staticmethod 语义（通过类属性访问获得的是解包后的函数，
    直接赋值会变成普通实例方法，导致 self 被误传为 stats 实参）。
    """
    from src.a2x.build import node_splitter

    cls = node_splitter.NodeSplitter
    original_descriptor = cls.__dict__['_should_terminate']  # staticmethod descriptor

    def patched(stats, iteration, max_refine):
        # Only stop when iteration budget is exhausted (never early-stop from stats).
        return iteration >= max_refine

    cls._should_terminate = staticmethod(patched)
    try:
        yield
    finally:
        cls._should_terminate = original_descriptor


# --------------------------------------------------------------------------
# Search-phase patches
# --------------------------------------------------------------------------


@contextlib.contextmanager
def patch_no_dedup_and_merge():
    """消融 5：禁用搜索末端 R5 的去重 + 最近公共祖先合并。

    - `deduplicate(terminal_nodes)` → 直接透传（不去重）
    - `merge_small_groups(terminal_nodes)` → 每个 terminal 形成独立 group，不合并

    这两项操作仅在 search 阶段发生，因此无需重建 taxonomy，直接复用 baseline
    的 taxonomy.json 即可测得消融后的搜索行为。
    """
    from src.a2x.search import selector as selector_mod
    from src.a2x.search.models import ServiceGroup, TerminalNode

    original_dedup = selector_mod.ServiceSelector.deduplicate
    original_merge = selector_mod.ServiceSelector.merge_small_groups

    def patched_deduplicate(self, terminal_nodes: List[TerminalNode]):
        # Passthrough: keep duplicate services across terminals.
        return [
            TerminalNode(category_id=n.category_id, service_ids=list(n.service_ids))
            for n in terminal_nodes
        ]

    def patched_merge(self, terminal_nodes: List[TerminalNode]):
        # One group per terminal, no LCA-based merging.
        return [
            ServiceGroup(
                leaf_ids={n.category_id},
                service_ids=list(n.service_ids),
            )
            for n in terminal_nodes
        ]

    selector_mod.ServiceSelector.deduplicate = patched_deduplicate
    selector_mod.ServiceSelector.merge_small_groups = patched_merge
    try:
        yield
    finally:
        selector_mod.ServiceSelector.deduplicate = original_dedup
        selector_mod.ServiceSelector.merge_small_groups = original_merge


# --------------------------------------------------------------------------
# Helper
# --------------------------------------------------------------------------


@contextlib.contextmanager
def noop_patch():
    """Null context for experiments that declare no patch."""
    yield
