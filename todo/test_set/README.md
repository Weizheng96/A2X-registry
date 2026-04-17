# A2X Client SDK 测试用例集

本目录面向 [todo/client_design.md](../client_design.md) 描述的 `src/client/` SDK，提供一套完整的测试用例蓝图。实现阶段可据此生成 pytest 脚本。

## 目录结构

| 文件 | 范围 |
|------|------|
| [design_review.md](design_review.md) | 设计审查：发现的问题、缺口、风险 |
| [unit_tests.md](unit_tests.md) | 单元测试：`ownership` / `transport` / `models` / `errors` 四个内部模块 |
| [method_tests.md](method_tests.md) | 方法级集成测试：9 个对外方法（含 `__init__`），用 `respx` mock 后端 |
| [async_tests.md](async_tests.md) | `AsyncA2XClient` 专项：同步/异步对称性、并发、事件循环不被阻塞 |
| [e2e_tests.md](e2e_tests.md) | 端到端：Agent Team 完整流程、跨进程所有权、多后端分段 |
| [edge_cases.md](edge_cases.md) | 边界与异常：URL encoding、文件 I/O 错误、Unicode、并发竞争、大负载 |

## 测试工具约定

| 层次 | 推荐工具 |
|------|---------|
| 单元（纯 Python） | `pytest` + `pytest-asyncio` + `freezegun`（如需） |
| HTTP mock | `respx`（httpx 原生支持）或 `pytest-httpx` |
| 真后端 e2e | `pytest` + 本地 `python -m src.backend` fixture |
| 文件系统 | `tmp_path` fixture（每个 case 独立的 `~/.a2x_client/owned.json`） |
| 并发 | `threading` / `asyncio.gather` / `pytest-xdist`（多 worker 场景） |

## 优先级约定

- **P0** — 阻塞发布：任一失败都不可交付
- **P1** — 重要：常规 CI 必跑
- **P2** — 完备性：nightly / pre-release 跑一次即可

## 覆盖率目标

| 模块 | 行覆盖 | 分支覆盖 |
|------|:------:|:--------:|
| `ownership.py` | ≥ 95% | ≥ 90% |
| `transport.py` | ≥ 95% | ≥ 90% |
| `models.py` | 100% | 100% |
| `errors.py` | 100% | — |
| `client.py` / `async_client.py` | ≥ 90% | ≥ 85% |

## 测试用例 ID 规则

`<层>-<模块>-<序号>`，例如：
- `U-OWN-001` — 单元测试 · OwnershipStore · #1
- `M-REG-004` — 方法测试 · register_agent · #4
- `A-PAR-002` — 异步测试 · 对称性 · #2
- `E-FLOW-001` — 端到端 · 主流程 · #1
- `X-URL-003` — 边界 · URL 编码 · #3
