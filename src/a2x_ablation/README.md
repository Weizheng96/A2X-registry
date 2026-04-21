# A2X Ablation Experiment Framework

Feeds the ablation table in `reference/patent/专利交底书.md §4.3`. Also provides the reproducible experimental substrate for the accompanying paper.

## What it runs

8 experiments (see `experiments.py` for the declarative list):

| 行 | Reuse / Rebuild | 说明 |
|----|----------------|------|
| 基线 A（向量基线，top-5） | 复用 `results/20260323_vector_toolretnew_1714` | 无新 LLM 调用 |
| 基线 B（无分类全量返回，n=50）| 复用 `results/20260323_traditional_toolretnew_50` | 无新 LLM 调用 |
| 完整方案 | 复用 `results/20260414_a2x-getall_toolretclean_1714` | 无新 LLM 调用 |
| 消融 5（去搜索去重 + LCA 合并）| 复用 baseline taxonomy，仅 search 改造 | 1 次 eval |
| 消融 2（无独立分类校验）| 新 taxonomy + eval，复用 baseline keywords | 1 次 build + 1 次 eval |
| 消融 3（无反馈迭代）| 新 taxonomy + eval，复用 baseline keywords | 1 次 build + 1 次 eval |
| 消融 4（去跨域多父归属）| 新 taxonomy + eval，复用 baseline keywords | 1 次 build + 1 次 eval |
| 消融 1（无关键词频率表）| 新 taxonomy + eval（关键词也重建）| 1 次 build + 1 次 eval |

## Run it

```bash
python -m src.a2x_ablation.main
```

可选参数：

```bash
# 只跑一个消融（按 id 子串匹配）
python -m src.a2x_ablation.main --only 消融3

# 调整并发度（默认 20）
python -m src.a2x_ablation.main --workers 30

# 自定义汇总输出位置
python -m src.a2x_ablation.main --summary-path results/ablation_v2.json
```

## Resume semantics

- 每个实验的 build / eval 输出都在各自目录中；若 `summary.json` 已存在且 `total_queries > 0`，该实验会被跳过。
- build 阶段若 `taxonomy.json` 的 `build_status == "complete"` 且 `build_config.json` 与当前 config 匹配，则整个 build 阶段被跳过。
- eval 阶段若因 API 中断而中止，下次运行会从 `partial_results.jsonl` 断点续传。

## Ablation implementation strategy

| 消融 | 实现手段 | 代码位置 |
|------|---------|---------|
| 1 无关键词频率表 | `keyword_threshold = 99999`（所有节点都走描述分支）| `experiments.py` config override |
| 2 无独立分类校验 | monkey-patch `NodeSplitter._should_terminate` 永远不提前退出 | `patches.patch_no_early_stop` |
| 3 无反馈迭代 | `max_refine_iterations = 1` | `experiments.py` config override |
| 4 去跨域多父 | `enable_cross_domain = False` | `experiments.py` config override |
| 5 去搜索去重+LCA | monkey-patch `ServiceSelector.deduplicate` 与 `merge_small_groups` | `patches.patch_no_dedup_and_merge` |

Monkey-patches 使用 `contextmanager`，进入时替换方法、退出时还原，确保不同消融之间不会互相污染。

## Wall-clock estimate

- 复用 3 条：秒级
- 消融 5：单轮 evaluation ~5–10 min
- 消融 2/3/4：每个 ~15–25 min build（复用 keywords）+ 5–10 min eval ≈ 60–90 min
- 消融 1：~20–30 min build + 5–10 min eval ≈ 30–40 min

总计 **~1.5–2.5 小时**（依 DeepSeek API 响应速度而定）。

## Output layout

```
results/
├── 20YYMMDD_a2x-getall_toolretclean_1714_基线A_向量基线/     # copy of vector baseline
├── 20YYMMDD_a2x-getall_toolretclean_1714_基线B_无分类全量返回/
├── 20YYMMDD_a2x-getall_toolretclean_1714_完整方案/
├── 20YYMMDD_a2x-getall_toolretclean_1714_消融5_去搜索去重及LCA合并/
├── 20YYMMDD_a2x-getall_toolretclean_1714_消融2_无独立分类校验/
├── 20YYMMDD_a2x-getall_toolretclean_1714_消融3_无反馈迭代/
├── 20YYMMDD_a2x-getall_toolretclean_1714_消融4_去跨域多父归属/
├── 20YYMMDD_a2x-getall_toolretclean_1714_消融1_无关键词频率表/
└── ablation_sweep_summary.json                               # consolidated summary

database/ToolRet_clean/
├── taxonomy/                  # baseline (unchanged)
├── taxonomy_abl1/             # 消融 1 taxonomy
├── taxonomy_abl2/             # 消融 2 taxonomy
├── taxonomy_abl3/             # 消融 3 taxonomy
└── taxonomy_abl4/             # 消融 4 taxonomy
```

每个消融目录内结构与 `results/20260414_a2x-getall_toolretclean_1714/` 一致：`summary.json` / `config.json` / `evaluation_results.json` / `partial_results.jsonl` / `error_analysis.json` / `error_analysis.md`。另附 `ablation_provenance.json` 标注该条实验数据的来源（新建 vs 复用）。
