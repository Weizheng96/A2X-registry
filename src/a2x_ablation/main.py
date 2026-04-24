"""CLI entry point for the ablation sweep.

Usage:
    python -m src.a2x_ablation.main

The sweep orchestrates 8 experiments (see src/a2x_ablation/experiments.py):
    - 3 reuse-only (baselines A / B / full scheme)
    - 1 search-only ablation (消融 5)
    - 3 keyword-cache-reusing rebuilds (消融 2 / 3 / 4)
    - 1 full rebuild (消融 1)

Total wall-clock estimate on DeepSeek-Chat with workers=20:
    - Reuse-only:        ~10 seconds total
    - 消融 5 evaluation:   ~5-10 min
    - 消融 2/3/4 each:    ~15-25 min build + 5-10 min eval  ≈ 60-90 min total
    - 消融 1:            ~20-30 min build + 5-10 min eval  ≈ 30-40 min
    Grand total: ~1.5-2.5 hours

Incremental / resumable:
    - Re-running main.py after an interruption is safe: each experiment skips
      work whose outputs already exist (matching build_config + complete
      summary.json). Partial evaluations resume from partial_results.jsonl.
"""

from __future__ import annotations
import argparse
import io
import json
import logging
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from tqdm import tqdm

from src.a2x_ablation.experiments import get_experiments
from src.a2x_ablation.runner import run_one_experiment, ExperimentResult, aggregate_runs

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Pretty printing
# --------------------------------------------------------------------------


def _format_pct(x):
    if x is None:
        return "   —   "
    return f"{x * 100:6.2f}%"


def _format_tokens(x):
    if x is None:
        return "   —   "
    return f"{x:>8.0f}"


def _format_calls(x):
    if x is None:
        return "  —  "
    return f"{x:5.2f}"


def _format_duration(sec):
    if sec is None:
        return "   —   "
    if sec < 60:
        return f"{sec:6.1f}s"
    minutes = int(sec // 60)
    seconds = sec - minutes * 60
    return f"{minutes:3d}m{seconds:04.1f}s"


def print_summary_table(results: list[ExperimentResult]):
    """Print the final results table to stdout + write consolidated summary file."""
    print()
    print("=" * 130)
    print("A2X ABLATION SWEEP — RESULTS")
    print("=" * 130)
    print(
        f"{'实验':<40} "
        f"{'HitRate':>9} {'Recall':>9} {'Precision':>10} "
        f"{'AvgTokens':>10} {'LLMCalls':>9} "
        f"{'Build':>9} {'Eval':>9}  Status"
    )
    print("-" * 130)

    for r in results:
        print(
            f"{r.label[:38]:<40} "
            f"{_format_pct(r.hit_rate):>9} {_format_pct(r.recall):>9} "
            f"{_format_pct(r.precision):>10} "
            f"{_format_tokens(r.avg_tokens):>10} {_format_calls(r.avg_llm_calls):>9} "
            f"{_format_duration(r.build_elapsed_sec):>9} "
            f"{_format_duration(r.eval_elapsed_sec):>9}  "
            f"{r.status}"
        )
        if r.notes:
            print(f"    └─ {r.notes}")

    print("=" * 130)
    print()


def write_consolidated_summary(
    results: list[ExperimentResult],
    output_path: Path,
    agg_by_experiment: list[tuple[str, str, dict]] = None,
):
    """Write a single JSON file with all experiment rows + aggregated stats."""
    data = {
        "description": "A2X ablation sweep results — populates reference/patent/专利交底书.md §4.3",
        "dataset": "ToolRet_clean",
        "n_services": 1839,
        "n_queries": 1714,
        "experiments": [
            {
                "id": r.experiment_id,
                "label": r.label,
                "status": r.status,
                "hit_rate": r.hit_rate,
                "recall": r.recall,
                "precision": r.precision,
                "avg_tokens": r.avg_tokens,
                "avg_llm_calls": r.avg_llm_calls,
                "eval_output_dir": r.eval_output_dir,
                "build_elapsed_sec": r.build_elapsed_sec,
                "eval_elapsed_sec": r.eval_elapsed_sec,
                "notes": r.notes,
            }
            for r in results
        ],
    }
    if agg_by_experiment:
        data["aggregated"] = [
            {"id": eid, "label": label, **agg}
            for eid, label, agg in agg_by_experiment
        ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"→ Consolidated summary written to: {output_path}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main():
    # Force UTF-8 on stdout/stderr so Unicode symbols don't crash on Windows GBK console.
    # Safe no-op if stdout is already UTF-8 (e.g. when logged to file).
    for _stream in ("stdout", "stderr"):
        s = getattr(sys, _stream)
        if hasattr(s, "buffer") and getattr(s, "encoding", "").lower() != "utf-8":
            try:
                setattr(sys, _stream, io.TextIOWrapper(s.buffer, encoding="utf-8", errors="replace"))
            except Exception:
                pass

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="A2X ablation experiment sweep (see reference/patent/专利交底书.md §4.3)",
    )
    parser.add_argument(
        "--workers", type=int, default=20,
        help="Parallel LLM workers during build + evaluation (default: 20)",
    )
    parser.add_argument(
        "--only", type=str, default=None,
        help="Run only experiments whose id matches ANY of the comma-separated "
             "substrings (e.g. --only 消融2 or --only 增强消融,完整方案)",
    )
    parser.add_argument(
        "--n-runs", type=int, default=1,
        help="Number of repeated runs per experiment for std-deviation estimation "
             "(default: 1). Build taxonomy is shared; each run gets its own eval "
             "output at {eval_output_dir}_run{N}. Reuse-only rows are never repeated.",
    )
    parser.add_argument(
        "--summary-path", type=str,
        default=f"results/ablation_sweep_summary.json",
        help="Consolidated JSON summary path (default: results/ablation_sweep_summary.json)",
    )
    args = parser.parse_args()

    experiments = get_experiments()
    if args.only:
        patterns = [p.strip() for p in args.only.split(",") if p.strip()]
        experiments = [e for e in experiments if any(p in e.id for p in patterns)]
        if not experiments:
            print(f"No experiments match --only {args.only!r}", file=sys.stderr)
            sys.exit(1)

    total = len(experiments)
    print(f"\nA2X ablation sweep: {total} experiments planned (n_runs={args.n_runs})")
    for e in experiments:
        tag = "REUSE" if e.reuse_eval_from else ("BUILD+EVAL" if e.needs_build else "EVAL")
        print(f"  [{tag:>10}] {e.id}: {e.label}")
    print()

    results: list[ExperimentResult] = []
    # Per-experiment aggregated view when n_runs > 1
    agg_by_experiment: list[tuple[str, str, dict]] = []

    overall_start = time.time()

    with tqdm(experiments, desc="Ablation sweep", unit="exp", position=0) as outer_bar:
        for exp in outer_bar:
            outer_bar.set_postfix_str(exp.id[:30])

            # Reuse-only: just one run regardless of --n-runs
            effective_runs = 1 if exp.reuse_eval_from else args.n_runs

            per_exp_results: list[ExperimentResult] = []
            for run_idx in range(effective_runs):
                res = run_one_experiment(
                    exp, workers=args.workers,
                    run_idx=run_idx, n_runs=effective_runs,
                )
                results.append(res)
                per_exp_results.append(res)

                if res.status == "failed":
                    outer_bar.write(f"✗ {exp.id} run {run_idx + 1}/{effective_runs} FAILED: {res.notes}")
                else:
                    metric = (
                        f"Recall={_format_pct(res.recall).strip()}, "
                        f"HitRate={_format_pct(res.hit_rate).strip()}"
                    )
                    outer_bar.write(
                        f"✓ {exp.id} run {run_idx + 1}/{effective_runs} "
                        f"[{res.status}] {metric}"
                    )

            # Aggregate if n_runs > 1
            if effective_runs > 1:
                agg = aggregate_runs(per_exp_results)
                agg_by_experiment.append((exp.id, exp.label, agg))
                outer_bar.write(
                    f"  ↳ {exp.id} aggregated: "
                    f"Recall={agg['recall_mean']*100:.2f}%±{agg['recall_std']*100:.2f}pp, "
                    f"HitRate={agg['hit_rate_mean']*100:.2f}%±{agg['hit_rate_std']*100:.2f}pp"
                )

    overall_elapsed = time.time() - overall_start
    print(f"\nAblation sweep finished in {_format_duration(overall_elapsed).strip()}")

    # Display + persist
    print_summary_table(results)
    write_consolidated_summary(results, Path(args.summary_path), agg_by_experiment)

    # Aggregated table (only if n_runs > 1 and we have aggregated data)
    if agg_by_experiment:
        print("\n" + "=" * 130)
        print(f"AGGREGATED (n_runs={args.n_runs}) — mean ± std")
        print("=" * 130)
        print(f"{'实验':<40} {'HitRate μ±σ':>17} {'Recall μ±σ':>17} {'Precision μ±σ':>18} {'Tokens μ±σ':>17} {'Calls μ±σ':>14}")
        print("-" * 130)
        for eid, label, agg in agg_by_experiment:
            def fmt_pct_ms(m, s):
                if m is None: return "       —        "
                return f"{m*100:6.2f}% ± {s*100:5.2f}pp"
            def fmt_num_ms(m, s):
                if m is None: return "       —        "
                return f"{m:7.0f} ± {s:6.0f}"
            def fmt_call_ms(m, s):
                if m is None: return "     —       "
                return f"{m:5.2f} ± {s:5.2f}"
            print(
                f"{label[:38]:<40} "
                f"{fmt_pct_ms(agg['hit_rate_mean'], agg['hit_rate_std']):>17} "
                f"{fmt_pct_ms(agg['recall_mean'], agg['recall_std']):>17} "
                f"{fmt_pct_ms(agg['precision_mean'], agg['precision_std']):>18} "
                f"{fmt_num_ms(agg['avg_tokens_mean'], agg['avg_tokens_std']):>17} "
                f"{fmt_call_ms(agg['avg_llm_calls_mean'], agg['avg_llm_calls_std']):>14}"
            )
        print("=" * 130)

    # Exit code: non-zero if any experiment failed
    n_failed = sum(1 for r in results if r.status == "failed")
    if n_failed:
        print(f"\n⚠ {n_failed} experiment(s) failed — re-run to retry.")
        sys.exit(1)


if __name__ == "__main__":
    main()
