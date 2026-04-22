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


# =============================================================================
# Enhanced ablations (增强消融 1+/2+/3+)
#
# Target each module's CORE purpose rather than its code-level surface:
#   消融 1+: keyword freq table exists to prevent ultra-long context from
#            thousands of service descriptions. Enhanced ablation forces
#            full descriptions (no truncation) into the root design prompt.
#   消融 2+: independent classification validation exists to prevent MIXED
#            classification dimensions (functional / object / technical).
#            Enhanced ablation removes both the LLM-based root validation
#            AND the dimension-consistency rules from design prompts.
#   消融 3+: feedback iteration exists to prevent catch-all ("general",
#            "miscellaneous", "domain-agnostic") categories from absorbing
#            hard-to-classify services. Enhanced ablation removes all
#            anti-catch-all rules from design + refine prompts.
# =============================================================================


# -----------------------------------------------------------------------------
# 增强消融 1+: force ultra-long context (disable keyword table + no truncation)
# -----------------------------------------------------------------------------


@contextlib.contextmanager
def patch_ultra_long_context():
    """消融 1+: force full service descriptions into design prompt (no truncation).

    The keyword frequency table exists to compress 1839 services into ~200
    keyword frequencies, keeping the B3 prompt manageable. This patch:

    1. Keeps the `keyword_threshold` override (99999, applied via config),
       which forces the root node to use `DESIGN_FROM_DESCRIPTIONS_TEMPLATE`
       rather than the keyword-based path.
    2. Additionally patches `format_services_for_prompt` to emit FULL service
       descriptions (no 150-char truncation), simulating the real raw input
       the keyword table would have had to summarize.

    Combined with the existing description-based design path, this puts
    ~1839 × ~500–2000 char descriptions directly into B3's prompt — stress-
    testing the LLM's long-context attention ("Lost in the Middle").
    """
    from src.a2x.build import prompts as prompts_mod
    from src.a2x.build import category_designer as cd_mod

    original_format = prompts_mod.format_services_for_prompt
    # Also patch the reference that category_designer already imported:
    original_format_in_cd = cd_mod.format_services_for_prompt

    def patched_format(services, max_desc_len=None):
        # Ignore truncation parameter — emit full descriptions.
        lines = []
        for svc in services:
            desc = svc.get('description', 'No description')
            lines.append(f"- {svc['name']}: {desc}")
        return "\n".join(lines)

    prompts_mod.format_services_for_prompt = patched_format
    cd_mod.format_services_for_prompt = patched_format
    try:
        yield
    finally:
        prompts_mod.format_services_for_prompt = original_format
        cd_mod.format_services_for_prompt = original_format_in_cd


# -----------------------------------------------------------------------------
# 增强消融 2+: no dimension-consistency validation (prompts + root validator)
# -----------------------------------------------------------------------------


# Weakened design prompts — remove dimension-consistency rules so LLM may
# freely mix functional / operational / technical classification schemes.
# Format placeholders must remain identical to the originals.

_WEAK_CATEGORY_DESIGN_TEMPLATE = """Below are {n_keywords} keywords extracted from {n_services} API services,
with their frequency counts:

{keywords_text}
{node_context_section}
Group these keywords into up to {max_cats} categories.

For each category provide:
- id: "{parent_id}_sub1", "{parent_id}_sub2", etc.
- name: 2-4 word descriptive name
- description: short summary of what services belong here (under 200 chars)

Output ONLY valid JSON:
```json
{{
  "dimension": "grouping",
  "categories": [
    {{
      "id": "{parent_id}_sub1",
      "name": "...",
      "description": "...",
      "associated_keywords": ["keyword1", "keyword2"]
    }}
  ]
}}
```"""

_WEAK_DESIGN_FROM_DESCRIPTIONS_TEMPLATE = """You are designing categories for a group of {service_count} API services.
{node_context_section}
Design up to {max_cats} categories.

For each category provide:
- id: "{parent_id}_sub1", "{parent_id}_sub2", etc.
- name: 2-4 word descriptive name
- description: what services belong here (under 200 chars)

Services (name: description):
{services_text}

Output JSON:
```json
{{
  "dimension_used": "brief description",
  "categories": [
    {{
      "id": "{parent_id}_sub1",
      "name": "...",
      "description": "..."
    }}
  ]
}}
```"""

_WEAK_SYSTEM_CATEGORY_DESIGN = (
    "You are a taxonomy designer. You group services into categories."
)

_WEAK_SYSTEM_DESIGN_FROM_DESCRIPTIONS = (
    "You subdivide services into sub-categories."
)


@contextlib.contextmanager
def patch_no_dimension_validation():
    """消融 2+: remove dimension-consistency guards at BOTH prompt and code layers.

    Removes:
    1. `_validate_and_fix_root_categories` (root LLM-based dimension validator)
       — patched to become a no-op that returns categories unchanged.
    2. `CATEGORY_DESIGN_TEMPLATE` / `DESIGN_FROM_DESCRIPTIONS_TEMPLATE` —
       replaced with minimal templates that do NOT instruct LLM to keep a
       single classification dimension. LLM may freely mix functional /
       operational / technical schemes.
    3. The early-stop in `_should_terminate` (reused from original 消融 2).

    Expected effect: root category set becomes dimension-mixed (e.g. "Travel"
    + "Image Processing" + "Cloud APIs") → per-service classification
    becomes ambiguous → many泛分类/未分类 → Recall degrades.
    """
    from src.a2x.build import node_splitter
    from src.a2x.build import category_designer as cd_mod

    # (a) Also keep the original 消融 2 patch — no early stop
    cls = node_splitter.NodeSplitter
    original_should_terminate = cls.__dict__['_should_terminate']

    def patched_should_terminate(stats, iteration, max_refine):
        return iteration >= max_refine

    cls._should_terminate = staticmethod(patched_should_terminate)

    # (b) Neuter the LLM-based root validator
    original_validate = cd_mod.CategoryDesigner._validate_and_fix_root_categories

    def noop_validate(self, categories, keywords, max_retries=2):
        return categories

    cd_mod.CategoryDesigner._validate_and_fix_root_categories = noop_validate

    # (c) Weaken design prompts
    orig_cat_template = cd_mod.CATEGORY_DESIGN_TEMPLATE
    orig_desc_template = cd_mod.DESIGN_FROM_DESCRIPTIONS_TEMPLATE
    orig_sys_cat = cd_mod.SYSTEM_CATEGORY_DESIGN
    orig_sys_desc = cd_mod.SYSTEM_DESIGN_FROM_DESCRIPTIONS

    cd_mod.CATEGORY_DESIGN_TEMPLATE = _WEAK_CATEGORY_DESIGN_TEMPLATE
    cd_mod.DESIGN_FROM_DESCRIPTIONS_TEMPLATE = _WEAK_DESIGN_FROM_DESCRIPTIONS_TEMPLATE
    cd_mod.SYSTEM_CATEGORY_DESIGN = _WEAK_SYSTEM_CATEGORY_DESIGN
    cd_mod.SYSTEM_DESIGN_FROM_DESCRIPTIONS = _WEAK_SYSTEM_DESIGN_FROM_DESCRIPTIONS

    try:
        yield
    finally:
        cls._should_terminate = original_should_terminate
        cd_mod.CategoryDesigner._validate_and_fix_root_categories = original_validate
        cd_mod.CATEGORY_DESIGN_TEMPLATE = orig_cat_template
        cd_mod.DESIGN_FROM_DESCRIPTIONS_TEMPLATE = orig_desc_template
        cd_mod.SYSTEM_CATEGORY_DESIGN = orig_sys_cat
        cd_mod.SYSTEM_DESIGN_FROM_DESCRIPTIONS = orig_sys_desc


# -----------------------------------------------------------------------------
# 增强消融 3+: allow catch-all categories (no feedback iteration)
# -----------------------------------------------------------------------------


# Templates that explicitly ALLOW catch-all / general / miscellaneous categories.
# Preserves format placeholders identical to the originals, but strips the
# "NO CATCH-ALL" / "PROTECT SMALL DOMAINS" / "COMPLETE COVERAGE" rules that
# normally prevent dumping-ground categories.

_CATCHALL_OK_CATEGORY_DESIGN_TEMPLATE = """Below are {n_keywords} keywords extracted from {n_services} API services,
with their frequency counts:

{keywords_text}
{node_context_section}
Group these keywords into up to {max_cats} categories. You MAY create
general-purpose categories like "Other", "General Tools", "Miscellaneous",
or "Data & Utilities" if that makes the taxonomy simpler.

For each category provide:
- id: "{parent_id}_sub1", "{parent_id}_sub2", etc.
- name: 2-4 word descriptive name
- description: description (under 200 chars)

Output ONLY valid JSON:
```json
{{
  "dimension": "grouping",
  "categories": [
    {{
      "id": "{parent_id}_sub1",
      "name": "...",
      "description": "...",
      "associated_keywords": ["keyword1", "keyword2"]
    }}
  ]
}}
```"""

_CATCHALL_OK_DESIGN_FROM_DESCRIPTIONS_TEMPLATE = """You are designing categories for {service_count} API services.
{node_context_section}
Design up to {max_cats} categories. You MAY create general-purpose buckets
like "Other", "General", "Miscellaneous", "Data & Utilities" for services
that don't fit cleanly.

For each category provide:
- id: "{parent_id}_sub1", "{parent_id}_sub2", etc.
- name: 2-4 word descriptive name
- description: description (under 200 chars)

Services (name: description):
{services_text}

Output JSON:
```json
{{
  "dimension_used": "brief description",
  "categories": [
    {{
      "id": "{parent_id}_sub1",
      "name": "...",
      "description": "..."
    }}
  ]
}}
```"""


@contextlib.contextmanager
def patch_allow_catchall_no_refine():
    """消融 3+: allow catch-all categories AND disable feedback iteration.

    Removes all guards that prevent "dumping-ground" categories:
    1. CATEGORY_DESIGN_TEMPLATE / DESIGN_FROM_DESCRIPTIONS_TEMPLATE replaced
       with templates that explicitly permit catch-all categories.
    2. The LLM-based root validator's "catch_all" violation type becomes
       unenforced by neutering `_validate_and_fix_root_categories`.
    3. (max_refine_iterations=1 is already set via config override; no code
       patch needed for that — the config handles it.)

    Expected effect: first-pass design produces 1-2 catch-all buckets that
    absorb ~30-50% of services, and without refinement these catch-alls
    persist in the final taxonomy → search cannot discriminate between
    "genuinely clustered" services and dumped ones → both Recall and
    Precision degrade.
    """
    from src.a2x.build import category_designer as cd_mod

    # Neuter the root validator (which would normally flag "catch_all" violations)
    original_validate = cd_mod.CategoryDesigner._validate_and_fix_root_categories

    def noop_validate(self, categories, keywords, max_retries=2):
        return categories

    cd_mod.CategoryDesigner._validate_and_fix_root_categories = noop_validate

    # Replace design templates with catch-all-permissive versions
    orig_cat_template = cd_mod.CATEGORY_DESIGN_TEMPLATE
    orig_desc_template = cd_mod.DESIGN_FROM_DESCRIPTIONS_TEMPLATE

    cd_mod.CATEGORY_DESIGN_TEMPLATE = _CATCHALL_OK_CATEGORY_DESIGN_TEMPLATE
    cd_mod.DESIGN_FROM_DESCRIPTIONS_TEMPLATE = _CATCHALL_OK_DESIGN_FROM_DESCRIPTIONS_TEMPLATE

    try:
        yield
    finally:
        cd_mod.CategoryDesigner._validate_and_fix_root_categories = original_validate
        cd_mod.CATEGORY_DESIGN_TEMPLATE = orig_cat_template
        cd_mod.DESIGN_FROM_DESCRIPTIONS_TEMPLATE = orig_desc_template
