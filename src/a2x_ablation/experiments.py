"""Ablation experiment declarations.

Each Experiment records what to build, what to patch, and where to save results.
Ordering in EXPERIMENTS is deliberately tuned to maximize cache reuse:
  1. Reuse-only experiments (baselines + full_scheme) run first (near-instant).
  2. Ablations that share the baseline's keyword cache run next — keywords.json is
     copied from the baseline before build(resume="keyword") to skip the B2 stage.
  3. Ablation 1 (no keyword table) runs last since it regenerates keywords anyway.
  4. Ablation 5 (search-only) runs after baseline-taxonomy-reusable ablations —
     no build needed, only a patched evaluation against the existing baseline.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional


# Baseline taxonomy already built at this location.
BASELINE_TAXONOMY_DIR = "database/ToolRet_clean/taxonomy"
BASELINE_SERVICE_PATH = "database/ToolRet_clean/service.json"
BASELINE_QUERY_FILE = "database/ToolRet_clean/query/query.json"

# Pre-existing evaluation result directories reused as-is.
EXISTING_VECTOR_RESULT = "results/20260323_vector_toolretnew_1714"
EXISTING_TRADITIONAL_RESULT = "results/20260323_traditional_toolretnew_50"
EXISTING_FULL_SCHEME_RESULT = "results/20260414_a2x-getall_toolretclean_1714"


def _date_tag() -> str:
    return datetime.now().strftime("%Y%m%d")


@dataclass
class Experiment:
    """Single ablation / baseline experiment specification."""

    # Identification
    id: str                                # short id used for folder names
    label: str                             # Chinese label for display
    description: str                       # 1-line description

    # Build phase
    needs_build: bool = False              # True → rebuild taxonomy
    build_output_dir: Optional[str] = None # where taxonomy.json is written
    build_config_overrides: dict = field(default_factory=dict)
    build_patch: Optional[Callable] = None # monkey-patch applied during build
    reuse_keywords_from: Optional[str] = None  # copy keywords.json from this dir

    # Evaluation phase
    eval_output_dir: Optional[str] = None  # where evaluation summary is saved
    eval_taxonomy_dir: Optional[str] = None  # taxonomy directory to use for eval
    search_patch: Optional[Callable] = None  # monkey-patch applied during search

    # Reuse-only mode (no build, no eval — just copy summary from existing result)
    reuse_eval_from: Optional[str] = None
    # If set, extract metrics from summary["metrics_at_k"][reuse_metrics_at_k] instead
    # of the top-level summary (used for the vector baseline where top-level is
    # top-10 but the §4.3 table cites the top-5 column).
    reuse_metrics_at_k: Optional[str] = None

    def __post_init__(self):
        if self.eval_output_dir is None and self.id:
            self.eval_output_dir = (
                f"results/{_date_tag()}_a2x-getall_toolretclean_1714_{self.id}"
            )
        # If build is needed and eval_taxonomy_dir wasn't overridden,
        # default to the build output dir (the taxonomy we just built).
        if self.needs_build and self.eval_taxonomy_dir is None:
            self.eval_taxonomy_dir = self.build_output_dir


# --------------------------------------------------------------------------
# Declarative experiment table
# --------------------------------------------------------------------------
# Ordering rationale:
#   - baseline_vector / baseline_traditional / full_scheme: reuse only, O(seconds)
#   - ablation_5 (search-only, reuse baseline taxonomy): O(5-10 min) evaluation
#   - ablation_2 / 3 / 4 (keyword-reuse rebuilds): O(15-25 min) each
#   - ablation_1 (no keyword table, full regen): O(20-30 min) last
#
# The runner will import monkey-patch callables from patches.py at start time;
# strings here are placeholders to avoid import cycles.


def _build_experiment_list():
    """Build and return the full experiment list. Imports patches lazily."""
    from src.a2x_ablation import patches  # local import to avoid cycle

    return [
        # ------------------------------------------------------------------
        # Reuse-only: baselines + full scheme
        # ------------------------------------------------------------------
        Experiment(
            id="基线A_向量基线",
            label="基线 A（向量基线，top-5）",
            description="sentence-transformers/all-MiniLM-L6-v2 + ChromaDB top-5",
            reuse_eval_from=EXISTING_VECTOR_RESULT,
            reuse_metrics_at_k="5",  # summary.json top-level is top-10; §4.3 uses top-5
        ),
        Experiment(
            id="基线B_无分类全量返回",
            label="基线 B（无分类全量服务返回，n=50 查询子集）",
            description="把全部 1839 条服务描述注入 LLM 上下文一次推理",
            reuse_eval_from=EXISTING_TRADITIONAL_RESULT,
        ),
        Experiment(
            id="完整方案",
            label="完整方案（本发明）",
            description="全部特征均开启的 A2X（对应 20260414 基准实验）",
            reuse_eval_from=EXISTING_FULL_SCHEME_RESULT,
        ),

        # ------------------------------------------------------------------
        # 消融 5（仅搜索改造；复用 baseline 的 taxonomy）
        # ------------------------------------------------------------------
        Experiment(
            id="消融5_去搜索去重及LCA合并",
            label="消融 5（去搜索去重及 LCA 距离升序合并）",
            description="搜索阶段 R5 关闭去重与 LCA 合并，R6 在全量终端候选上一次性筛选",
            needs_build=False,
            eval_taxonomy_dir=BASELINE_TAXONOMY_DIR,
            search_patch=patches.patch_no_dedup_and_merge,
        ),

        # ------------------------------------------------------------------
        # 消融 2/3/4：构建阶段改造，复用 baseline 关键词缓存
        # ------------------------------------------------------------------
        Experiment(
            id="消融2_无独立分类校验",
            label="消融 2（无独立于分类生成步骤的分类校验环节）",
            description="强制跑满 max_refine_iterations，不因 stats 收敛提前退出",
            needs_build=True,
            build_output_dir="database/ToolRet_clean/taxonomy_abl2",
            build_config_overrides={},  # same config, behavior changed by patch
            build_patch=patches.patch_no_early_stop,
            reuse_keywords_from=BASELINE_TAXONOMY_DIR,
        ),
        Experiment(
            id="消融3_无反馈迭代",
            label="消融 3（无反馈迭代）",
            description="max_refine_iterations=1，只做一次分类即写入能力树",
            needs_build=True,
            build_output_dir="database/ToolRet_clean/taxonomy_abl3",
            build_config_overrides={"max_refine_iterations": 1},
            reuse_keywords_from=BASELINE_TAXONOMY_DIR,
        ),
        Experiment(
            id="消融4_去跨域多父归属",
            label="消融 4（去跨域多父归属）",
            description="enable_cross_domain=False，跳过 B6 跨域扫描",
            needs_build=True,
            build_output_dir="database/ToolRet_clean/taxonomy_abl4",
            build_config_overrides={"enable_cross_domain": False},
            reuse_keywords_from=BASELINE_TAXONOMY_DIR,
        ),

        # ------------------------------------------------------------------
        # 消融 1：关键词缓存也要重建（不可复用 baseline 的 keywords.json）
        # ------------------------------------------------------------------
        Experiment(
            id="消融1_无关键词频率表",
            label="消融 1（无关键词频率表）",
            description="keyword_threshold=99999，所有节点均直接基于服务描述设计分支",
            needs_build=True,
            build_output_dir="database/ToolRet_clean/taxonomy_abl1",
            build_config_overrides={"keyword_threshold": 99999},
            reuse_keywords_from=None,
        ),

        # ------------------------------------------------------------------
        # 增强消融 1+/2+/3+：针对每个模块的核心问题做更彻底的消融
        #   - 1+ 超长上下文：真正把 1839 条完整描述注入 prompt
        #   - 2+ 无分类方案校验：同时移除代码层根验证 + prompt 层维度约束
        #   - 3+ 允许泛用分类：移除反 catch-all 约束 + 禁用反馈迭代
        # 这些消融会让模块的"保护作用"真正显性化。
        # ------------------------------------------------------------------
        Experiment(
            id="增强消融1_超长上下文",
            label="增强消融 1+（无关键词频率表 · 超长上下文压力）",
            description="消融 1 基础上，关闭服务描述 150 字截断——完整 1839 条描述直接入 B3 prompt",
            needs_build=True,
            build_output_dir="database/ToolRet_clean/taxonomy_abl1plus",
            build_config_overrides={"keyword_threshold": 99999},
            build_patch=patches.patch_ultra_long_context,
            reuse_keywords_from=None,
        ),
        Experiment(
            id="增强消融2_无分类方案校验",
            label="增强消融 2+（无独立分类校验 · 移除维度一致性约束）",
            description="同时移除：代码层根节点 LLM 验证、早停、B3/B3-desc 的维度一致性 prompt",
            needs_build=True,
            build_output_dir="database/ToolRet_clean/taxonomy_abl2plus",
            build_config_overrides={},
            build_patch=patches.patch_no_dimension_validation,
            reuse_keywords_from=BASELINE_TAXONOMY_DIR,
        ),
        Experiment(
            id="增强消融3_允许泛用分类",
            label="增强消融 3+（无反馈迭代 · 允许泛用/catch-all 分类）",
            description="max_refine=1 + 移除反 catch-all prompt + 关闭根验证，允许'General/Other'桶产生",
            needs_build=True,
            build_output_dir="database/ToolRet_clean/taxonomy_abl3plus",
            build_config_overrides={"max_refine_iterations": 1},
            build_patch=patches.patch_allow_catchall_no_refine,
            reuse_keywords_from=BASELINE_TAXONOMY_DIR,
        ),
    ]


EXPERIMENTS = None  # populated on first call by get_experiments()


def get_experiments():
    """Return the experiment list (cached)."""
    global EXPERIMENTS
    if EXPERIMENTS is None:
        EXPERIMENTS = _build_experiment_list()
    return EXPERIMENTS
