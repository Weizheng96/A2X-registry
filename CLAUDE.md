# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A2X Registry validates that **hierarchical taxonomy + LLM recursive retrieval** achieves more accurate service discovery than semantic vector retrieval.

**Core hypothesis**: Explicit category structure guides LLM through multi-step reasoning, decomposing complex "query→service" mapping into simpler "query→category" decisions, avoiding the semantic gap problem of vector retrieval.

## Datasets

> **Note**: The `database/` directory is kept empty in this repo (via `.gitignore`). Actual dataset contents are maintained in a separate repository: https://github.com/Weizheng96/A2X-registry-demo-data. After modifying local database files, sync changes to that repo.

| Dataset | Services | Queries | Location |
|---------|----------|---------|----------|
| **ToolRet_clean** | **1839** | **1714** | `database/ToolRet_clean/` |
| **ToolRet_clean_CN** | **1839** | **1714** | `database/ToolRet_clean_CN/` |
| **publicMCP** | **1387** | **50** | `database/publicMCP/` |
| **publicMCP_CN** | **1387** | **50** | `database/publicMCP_CN/` |
| **default** | test data | — | `database/default/` |

Per-dataset files: `service.json`, `user_config.json`, `vector_config.json`, `query/query.json`, `query/default_queries.json`, `taxonomy/taxonomy.json`, `taxonomy/class.json`, `taxonomy/build_config.json`

## Architecture

```
src/
├── common/          # Shared utilities
├── a2x/             # A2X taxonomy-based retrieval (build / search / evaluation / incremental)
├── vector/          # Vector baseline (ChromaDB index / search / evaluation)
├── traditional/     # MCP-style full-context baseline (search / evaluation)
├── register/        # Service registration business logic
├── backend/         # FastAPI backend (routers: dataset, build, search, provider)
├── frontend/        # React + Vite + Tailwind + D3.js
└── ui/              # Integrated launcher (python -m src.ui)
results/             # Evaluation results
docs/                # Design documents
```

## Commands

```bash
# Run UI (backend + frontend)
python -m src.ui

# Build taxonomy
python -m src.a2x.build --service-path database/ToolRet_clean/service.json
python -m src.a2x.build --service-path database/ToolRet_clean/service.json --resume yes

# Register services
python -m src.register --config src/register/user_config.example.json
python -m src.register --status

# Evaluate
python -m src.a2x.evaluation --data-dir database/ToolRet_clean --max-queries 50 --workers 20
python -m src.vector.evaluation --max-queries 50
python -m src.traditional.evaluation --max-queries 50
```

## Instructions

1. LLM API config: create `llm_apikey.json` locally (see `llm_apikey.example.json`)
2. Parallel LLM Calls: Always use `ThreadPoolExecutor` (default 20 workers)
3. **Test with 50 queries first**, then run full evaluation after confirming reasonable results
4. **Never manually edit taxonomy.json / class.json** — only modify build code
5. **Do not cite get_one / get_important evaluation metrics in docs**: These modes find mostly relevant services (manual sampling ~98% functional precision), but incomplete ground truth (not all equivalent/related services labeled) causes strict Precision/Recall to severely underestimate actual performance. These numbers are misleading until ground truth is improved.

## Git Workflow

- Version notes: `src/a2x/VERSION.md`
- Small changes: commit directly to main
- Experimental changes: `git checkout -b exp/v{X.Y}_{experiment_name}`, merge on success
- Branch naming: `exp/v{X.Y}_{name}` | `fix/{issue}` | `feat/{feature}`

## Documentation

When making changes that affect documentation, keep these files in sync:

- `README.md` — project overview, evaluation results, quick start
- `docs/` — design documents (a2x, build, search, backend API, register, incremental, frontend)
- `src/a2x/VERSION.md` — version history and changelog
- `src/a2x/EXPLORATION.md` — experiment records and version comparison

## Design Principles

- Hierarchical taxonomy with functional orientation
- Multi-parent support (services can belong to multiple categories)
- Generalization first (avoid overfitting to current dataset)
