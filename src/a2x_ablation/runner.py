"""Per-experiment orchestration: build taxonomy, run evaluation, record summary.

One function per phase, each stamped with timestamps in the returned
ExperimentResult so main.py can report elapsed times."""

from __future__ import annotations
import json
import logging
import shutil
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Result structures
# --------------------------------------------------------------------------


@dataclass
class ExperimentResult:
    experiment_id: str
    label: str
    status: str                     # "reused" | "built+evaluated" | "evaluated" | "skipped" | "failed"
    hit_rate: Optional[float]
    recall: Optional[float]
    precision: Optional[float]
    avg_tokens: Optional[float]
    avg_llm_calls: Optional[float]
    eval_output_dir: str
    build_elapsed_sec: Optional[float]
    eval_elapsed_sec: Optional[float]
    notes: str = ""


# --------------------------------------------------------------------------
# Reuse path
# --------------------------------------------------------------------------


def reuse_existing_result(experiment, dest_dir: str) -> ExperimentResult:
    """Copy an existing summary.json to the new directory — no LLM calls."""
    src = Path(experiment.reuse_eval_from)
    summary_path = src / "summary.json"
    if not summary_path.exists():
        return ExperimentResult(
            experiment_id=experiment.id, label=experiment.label, status="failed",
            hit_rate=None, recall=None, precision=None,
            avg_tokens=None, avg_llm_calls=None, eval_output_dir=dest_dir,
            build_elapsed_sec=None, eval_elapsed_sec=None,
            notes=f"source summary.json missing: {summary_path}",
        )

    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    dest_summary = Path(dest_dir) / "summary.json"
    shutil.copy2(summary_path, dest_summary)

    # Also copy config.json if present (for traceability)
    for aux in ("config.json", "evaluation_results.json"):
        aux_src = src / aux
        if aux_src.exists():
            shutil.copy2(aux_src, Path(dest_dir) / aux)

    with open(summary_path, 'r', encoding='utf-8') as f:
        summary = json.load(f)

    # Pick metric source: either top-level summary, or summary["metrics_at_k"][k]
    # (e.g. vector baseline's "5" sub-dict uses key "hit" instead of "hit_rate").
    if getattr(experiment, "reuse_metrics_at_k", None):
        k = experiment.reuse_metrics_at_k
        sub = summary.get("metrics_at_k", {}).get(k, {})
        hit_rate = sub.get("hit_rate", sub.get("hit"))
        recall = sub.get("recall")
        precision = sub.get("precision")
        avg_tokens = summary.get("avg_tokens")  # top-level if available
        avg_llm_calls = summary.get("avg_llm_calls")
        reuse_note = f"reused from {src.name} (top-{k})"
    else:
        hit_rate = summary.get("hit_rate")
        recall = summary.get("recall")
        precision = summary.get("precision")
        avg_tokens = summary.get("avg_tokens")
        avg_llm_calls = summary.get("avg_llm_calls")
        reuse_note = f"reused from {src.name}"

    # Write a small provenance file so future readers know this was reused.
    provenance = {
        "ablation_id": experiment.id,
        "ablation_label": experiment.label,
        "reused_from": str(src),
        "reuse_metrics_at_k": getattr(experiment, "reuse_metrics_at_k", None),
        "note": "This directory contains a copy of an earlier evaluation result; "
                "no new LLM calls were made for this ablation row.",
    }
    with open(Path(dest_dir) / "ablation_provenance.json", 'w', encoding='utf-8') as f:
        json.dump(provenance, f, indent=2, ensure_ascii=False)

    return ExperimentResult(
        experiment_id=experiment.id,
        label=experiment.label,
        status="reused",
        hit_rate=hit_rate,
        recall=recall,
        precision=precision,
        avg_tokens=avg_tokens,
        avg_llm_calls=avg_llm_calls,
        eval_output_dir=dest_dir,
        build_elapsed_sec=None,
        eval_elapsed_sec=None,
        notes=reuse_note,
    )


# --------------------------------------------------------------------------
# Build phase
# --------------------------------------------------------------------------


def build_taxonomy(experiment) -> float:
    """Build taxonomy for an ablation; returns elapsed seconds.

    Behavior:
    1. If taxonomy.json already exists in build_output_dir with matching config,
       reuse it (fast skip).
    2. If reuse_keywords_from is set, copy that keywords.json into build_output_dir
       before invoking the builder with resume="keyword" to skip B2 (extraction).
    3. Otherwise full rebuild (resume="no").
    """
    from src.a2x.build.config import AutoHierarchicalConfig
    from src.a2x.build.taxonomy_builder import TaxonomyBuilder
    from src.a2x_ablation import patches

    assert experiment.needs_build and experiment.build_output_dir

    out_dir = Path(experiment.build_output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build the config
    cfg_kwargs = {
        "service_path": "database/ToolRet_clean/service.json",
        "output_dir": str(out_dir),
    }
    cfg_kwargs.update(experiment.build_config_overrides or {})
    config = AutoHierarchicalConfig(**cfg_kwargs)

    # Decide resume mode based on existing state in out_dir:
    #   "yes"     — matching config + partial/complete taxonomy: smart-resume
    #   "keyword" — fresh build but seed keywords.json from baseline (skip B2)
    #   "no"      — full rebuild
    # The builder's own resume="yes" path will skip when build_status=="complete".
    existing_taxonomy = out_dir / "taxonomy.json"
    existing_config = out_dir / "build_config.json"

    can_smart_resume = (
        existing_taxonomy.exists()
        and existing_config.exists()
        and config.matches_saved_config(str(existing_config))
    )

    if can_smart_resume:
        try:
            with open(existing_taxonomy, 'r', encoding='utf-8') as f:
                tax = json.load(f)
            status = tax.get("build_status", "complete")
            if status == "complete":
                logger.info(
                    "[%s] Taxonomy already complete at %s — skipping build",
                    experiment.id, out_dir,
                )
                return 0.0
            logger.info(
                "[%s] Partial taxonomy (status=%s) — smart-resuming via resume='yes'",
                experiment.id, status,
            )
            resume_mode = "yes"
        except (json.JSONDecodeError, IOError):
            can_smart_resume = False

    if not can_smart_resume:
        # Fresh build. Seed keywords.json if allowed.
        resume_mode = "no"
        if experiment.reuse_keywords_from:
            src_kw = Path(experiment.reuse_keywords_from) / "keywords.json"
            if src_kw.exists():
                shutil.copy2(src_kw, out_dir / "keywords.json")
                resume_mode = "keyword"
                logger.info(
                    "[%s] Seeded keywords.json from %s (resume=keyword)",
                    experiment.id, src_kw,
                )
            else:
                logger.warning(
                    "[%s] reuse_keywords_from set but file missing: %s",
                    experiment.id, src_kw,
                )

    # Apply monkey-patch (if any) around the build call.
    patch_cm = experiment.build_patch() if experiment.build_patch else patches.noop_patch()

    start = time.time()
    with patch_cm:
        builder = TaxonomyBuilder(config)
        builder.build(resume=resume_mode)
    return time.time() - start


# --------------------------------------------------------------------------
# Evaluation phase
# --------------------------------------------------------------------------


def run_evaluation(experiment, workers: int = 20) -> tuple[float, Dict]:
    """Run A2X evaluation for one experiment.

    Returns (elapsed_seconds, summary_dict).
    """
    from src.a2x.evaluation.a2x_evaluator import A2XEvaluator
    from src.a2x_ablation import patches as patches_mod

    taxonomy_dir = Path(experiment.eval_taxonomy_dir)
    taxonomy_path = taxonomy_dir / "taxonomy.json"
    class_path = taxonomy_dir / "class.json"
    service_path = "database/ToolRet_clean/service.json"
    query_file = "database/ToolRet_clean/query/query.json"

    if not taxonomy_path.exists():
        raise FileNotFoundError(
            f"[{experiment.id}] Expected taxonomy at {taxonomy_path} (build failed?)"
        )

    out_dir = Path(experiment.eval_output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Skip if summary.json already exists and looks complete
    summary_path = out_dir / "summary.json"
    if summary_path.exists():
        try:
            with open(summary_path, 'r', encoding='utf-8') as f:
                s = json.load(f)
            if s.get("total_queries", 0) > 0:
                logger.info(
                    "[%s] Existing evaluation summary at %s — skipping eval",
                    experiment.id, summary_path,
                )
                return 0.0, s
        except (json.JSONDecodeError, IOError):
            pass

    patch_cm = (
        experiment.search_patch() if experiment.search_patch
        else patches_mod.noop_patch()
    )

    start = time.time()
    with patch_cm:
        evaluator = A2XEvaluator(
            max_workers=workers,
            taxonomy_path=str(taxonomy_path),
            class_path=str(class_path),
            service_path=service_path,
            mode="get_all",
        )
        evaluator.evaluate_batch(
            query_file=query_file,
            max_queries=None,
            output_dir=str(out_dir),
            experiment_id=experiment.id,
            notes=f"ablation={experiment.id}; {experiment.description}",
        )
    elapsed = time.time() - start

    with open(summary_path, 'r', encoding='utf-8') as f:
        summary = json.load(f)
    return elapsed, summary


# --------------------------------------------------------------------------
# Top-level orchestration for a single experiment
# --------------------------------------------------------------------------


def run_one_experiment(experiment, workers: int = 20) -> ExperimentResult:
    """Run a single ablation experiment end-to-end."""
    logger.info("\n" + "=" * 80)
    logger.info("[%s] %s", experiment.id, experiment.label)
    logger.info("    %s", experiment.description)
    logger.info("=" * 80)

    # Reuse path
    if experiment.reuse_eval_from:
        return reuse_existing_result(experiment, experiment.eval_output_dir)

    # Build taxonomy if needed
    build_elapsed = None
    try:
        if experiment.needs_build:
            build_elapsed = build_taxonomy(experiment)
        eval_elapsed, summary = run_evaluation(experiment, workers=workers)
    except Exception as e:
        logger.exception("[%s] failed: %s", experiment.id, e)
        return ExperimentResult(
            experiment_id=experiment.id, label=experiment.label, status="failed",
            hit_rate=None, recall=None, precision=None,
            avg_tokens=None, avg_llm_calls=None,
            eval_output_dir=experiment.eval_output_dir,
            build_elapsed_sec=build_elapsed,
            eval_elapsed_sec=None,
            notes=f"error: {e}",
        )

    return ExperimentResult(
        experiment_id=experiment.id,
        label=experiment.label,
        status="built+evaluated" if experiment.needs_build else "evaluated",
        hit_rate=summary.get("hit_rate"),
        recall=summary.get("recall"),
        precision=summary.get("precision"),
        avg_tokens=summary.get("avg_tokens"),
        avg_llm_calls=summary.get("avg_llm_calls"),
        eval_output_dir=experiment.eval_output_dir,
        build_elapsed_sec=build_elapsed,
        eval_elapsed_sec=eval_elapsed,
        notes="",
    )
