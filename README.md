# A2X Registry — 智能体服务发现注册中心

**v0.1.3**

## 概述

A2X 是一种 **Agent 原生**的服务发现注册中心，用于解决智能体互联网时代的核心问题：**如何让智能体从海量服务中高效、准确地找到所需能力。**

现有方案的局限：

| 方案 | 问题 |
|------|------|
| **MCP 全量上下文** | 上下文空间有限，服务规模增长后窗口溢出，"Lost in the Middle" 导致性能下降 |
| **关键词 / 向量检索** | 未利用 LLM 的理解能力，跨术语映射和复杂意图场景召回不稳定 |
| **查询改写** | 模型不了解服务库内部术语，改写方向不确定，容易引入偏差 |

A2X 通过自动构建 **层次化能力目录**（分类树）+ **LLM 递归语义导航**，实现 LLM 亲和且 Agent 自治的服务注册发现方案。分类树由 LLM 从服务描述中全自动构建。搜索时，LLM 沿着"领域 → 子领域 → 具体能力 → 服务"逐层逼近目标，查找成本接近 O(log N)。

## 评估结果

### [ToolRet_clean](database/ToolRet_clean/)（1839 服务 · 1714 查询）

数据来源：[tool-retrieval-benchmark](https://github.com/mangopy/tool-retrieval-benchmark)，经过数据清洗。

| 方法 | Hit Rate | Recall | Precision | Avg Tokens/q | Avg LLM Calls | 数据 |
|------|:--------:|:------:|:--------:|:------------:|:-------------:|:----:|
| **A2X** (v0.1.1) | **92.59%** | **89.19%** | 16.06% | 7,069 | 7.96 | [summary](results/20260323_a2x-getall_toolretnew_1714/summary.json) |
| Vector (top-5) | 69.08% | 61.81% | 15.24% | 0 | 0 | [summary](results/20260323_vector_toolretnew_1714/summary.json) |
| Traditional (MCP)* | 86.00% | 83.67% | 5.17% | 66,568 | 1.00 | [summary](results/20260323_traditional_toolretnew_50/summary.json) |

\* Traditional 方案仅使用 name + description。若加入完整 inputSchema，单次查询 Token 消耗将达到 ~200k，已超出大多数模型的上下文窗口限制。

### [publicMCP](database/publicMCP/)（1387 MCP 服务 · 50 查询）

数据来源：[MCP 官方服务器列表](https://github.com/modelcontextprotocol/servers)，共 1387 条 MCP 服务器描述。查询模拟真实用户请求（含个人偏好、多服务组合等）。

| 方法 | Hit Rate | Recall | Precision | Avg Tokens/q | Avg LLM Calls | 数据 |
|------|:--------:|:------:|:--------:|:------------:|:-------------:|:----:|
| **A2X** (v0.1.1) | **100%** | **94.87%** | 10.54% | 15,366 | 14.10 | [summary](results/20260323_a2x-getall_publicmcp_50/summary.json) |
| Vector (top-5) | 72.0% | 42.77% | 22.00% | 0 | 0 | [summary](results/20260323_vector_publicmcp_50/summary.json) |
| Vector (top-10) | 78.0% | 50.50% | 13.20% | 0 | 0 | [summary](results/20260323_vector_publicmcp_50/summary.json) |

> **关于 Precision**：在大规模服务库中，ground truth 难以标注所有与请求相关的服务。人工抽样检查发现超过 60% 的假阳选项实际上与请求功能相关，因此 Precision 指标显著低估了实际检索质量，本文不引用该指标。

## 快速开始

### 1. 环境准备

```bash
pip install -r requirements.txt
```

### 2. 下载演示数据集（可选）

项目提供预构建的演示数据集（含服务描述、分类树、评估查询），克隆到 `database/` 目录即可使用：

```bash
git clone https://github.com/Weizheng96/A2X-registry-demo-data.git database
```

包含以下数据集：

| 数据集 | 服务数 | 语言 | 说明 |
|--------|:-----:|:---:|------|
| `ToolRet_clean` | 1839 | EN | [tool-retrieval-benchmark](https://github.com/mangopy/tool-retrieval-benchmark) 清洗版 |
| `publicMCP` | 1387 | EN | [MCP 官方服务器列表](https://github.com/modelcontextprotocol/servers) |
| `ToolRet_clean_CN` | 1839 | ZH | ToolRet_clean 中文翻译版 |
| `publicMCP_CN` | 1387 | ZH | publicMCP 中文翻译版 |
| `default` | — | — | 21 个公开 A2A Agent（启动后自动拉取） |

> 不下载也可以正常使用，通过 UI 或 API 创建自己的数据集并注册服务。

### 3. 配置

#### LLM API（A2X 搜索和分类树构建必需）

在项目根目录创建 `llm_apikey.json`（参考 `llm_apikey.example.json`）：

```json
{
  "providers": [
    {
      "name": "deepseek",
      "base_url": "https://api.deepseek.com/chat/completions",
      "model": "deepseek-chat",
      "api_keys": ["sk-your-deepseek-api-key"]
    },
    {
      "name": "aliyun",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
      "model": "deepseek-v3.2",
      "api_keys": ["sk-your-aliyun-api-key"]
    }
  ]
}
```

支持多 provider 配置，按顺序轮询使用。兼容所有 OpenAI-compatible API。

> 仅使用向量检索时不需要此配置。

#### 基于配置文件的服务注册（可选）

通过 `user_config.json` 声明服务，放在 `database/{数据集名}/user_config.json`：

```json
{
  "services": [
    {
      "type": "generic",
      "name": "天气查询",
      "description": "根据城市名查询实时天气和未来预报",
      "url": "https://api.example.com/weather"
    },
    {
      "type": "a2a",
      "agent_card_url": "https://agent.example.com/.well-known/agent.json"
    },
    {
      "type": "a2a",
      "agent_card": {
        "name": "翻译助手",
        "description": "支持中英日韩多语言互译",
        "url": "https://translate.example.com/a2a",
        "skills": [
          {"name": "translate", "description": "将文本翻译为目标语言"}
        ]
      }
    }
  ]
}
```

支持三种注册方式：
- **generic** — 通用服务，提供 name + description
- **a2a (URL)** — 通过 `agent_card_url` 自动拉取 [A2A 协议](https://google.github.io/A2A/) Agent Card
- **a2a (内联)** — 通过 `agent_card` 直接提供 Agent Card 内容

### 4. 使用

#### 方式一：UI 界面

```bash
python -m src.ui
```

启动模式根据 `src/frontend/dist/` 是否存在自动判断：

| 情况 | 行为 | 访问地址 |
|------|------|----------|
| 有 `dist/` | 后端直接托管静态文件，无需 Node.js | http://localhost:8000 |
| 无 `dist/`（首次） | 自动启动 Vite 开发服务器（需 Node.js） | http://localhost:5173 |

构建前端生产版本：`python -m src.frontend`

UI 提供两个模式：
- **搜索模式** — 交互对比 A2X / 向量 / MCP 的检索效果，D3.js 实时动画展示分类树导航过程
- **管理员模式** — 数据集管理、服务注册/注销、分类树构建、Embedding 模型配置

**UI 演示视频：**

https://github.com/Weizheng96/A2X-registry-demo-data/raw/doc/ui_demo.mp4

> 注：演示中注销阶段灰色不可选的服务是通过 `user_config.json` 注册的，不支持单独注销。

#### 方式二：后端 API

```bash
python -m src.backend
# 服务: http://127.0.0.1:8000
# 文档: http://127.0.0.1:8000/docs
```

**数据集管理**：

```bash
# 列出数据集
curl http://localhost:8000/api/datasets

# 创建数据集（指定 Embedding 模型）
curl -X POST http://localhost:8000/api/datasets \
     -H "Content-Type: application/json" \
     -d '{"name": "my_dataset", "embedding_model": "all-MiniLM-L6-v2"}'

# 删除数据集
curl -X DELETE http://localhost:8000/api/datasets/my_dataset
```

**服务注册/注销**：

```bash
# 注册通用服务
curl -X POST http://localhost:8000/api/datasets/my_dataset/services/generic \
     -H "Content-Type: application/json" \
     -d '{"name": "天气查询", "description": "查询城市天气和预报"}'

# 注册 A2A Agent（通过 URL 自动拉取 Agent Card）
curl -X POST http://localhost:8000/api/datasets/my_dataset/services/a2a \
     -H "Content-Type: application/json" \
     -d '{"agent_card_url": "https://agent.example.com/.well-known/agent.json"}'

# 注册 A2A Agent（直接提供 Agent Card）
curl -X POST http://localhost:8000/api/datasets/my_dataset/services/a2a \
     -H "Content-Type: application/json" \
     -d '{"agent_card": {"name": "翻译助手", "description": "支持中英日韩多语言互译", "url": "https://translate.example.com/a2a"}}'

# 注销服务
curl -X DELETE http://localhost:8000/api/datasets/my_dataset/services/{service_id}

# 浏览服务
curl http://localhost:8000/api/datasets/my_dataset/services?mode=browse
```

**分类树构建**：

```bash
# 触发构建（后台运行）
curl -X POST http://localhost:8000/api/datasets/my_dataset/build \
     -H "Content-Type: application/json" -d '{}'

# 查看构建状态
curl http://localhost:8000/api/datasets/my_dataset/build/status

# 实时日志流（SSE）
curl http://localhost:8000/api/datasets/my_dataset/build/stream
```

**搜索**：

```bash
# 同步搜索
curl -X POST http://localhost:8000/api/search \
     -H "Content-Type: application/json" \
     -d '{"query": "帮我预订航班", "method": "a2x_get_all", "dataset": "my_dataset"}'
```

搜索方法：`a2x_get_all`（所有相关服务）、`a2x_get_one`（最相关的服务）、`a2x_get_important`（同类服务去重）、`vector`（向量检索）、`traditional`（MCP 全量）。

A2X 搜索支持 WebSocket 流式返回（`/api/search/ws`），实时推送分类导航步骤。

**Embedding 模型配置**：

```bash
# 查看支持的模型
curl http://localhost:8000/api/datasets/embedding-models

# 查看/切换数据集的 Embedding 模型
curl http://localhost:8000/api/datasets/my_dataset/vector-config
curl -X POST http://localhost:8000/api/datasets/my_dataset/vector-config \
     -H "Content-Type: application/json" \
     -d '{"embedding_model": "shibing624/text2vec-base-chinese"}'
```

支持 3 种 Embedding 模型：

| 模型 | 维度 | 适用语言 |
|------|:---:|:---:|
| `all-MiniLM-L6-v2` | 384 | 英文（默认） |
| `shibing624/text2vec-base-chinese` | 768 | 中文 |
| `paraphrase-multilingual-MiniLM-L12-v2` | 384 | 多语言 |

> 完整 API 文档见 [docs/backend_api.md](docs/backend_api.md)，各模块内部接口见对应设计文档。

## 文档

| 文档 | 内容 |
|------|------|
| [docs/backend_api.md](docs/backend_api.md) | 后端全量 API 接口说明（请求/响应格式、SSE 协议） |
| [docs/frontend_design.md](docs/frontend_design.md) | 前端架构与 API 调用说明（搜索流程、WebSocket、SSE） |
| [docs/a2x_design.md](docs/a2x_design.md) | A2X 搜索算法设计 |
| [docs/build_design.md](docs/build_design.md) | 分类树自动构建设计 |
| [docs/register_design.md](docs/register_design.md) | 服务注册模块设计 |
| [docs/search_design.md](docs/search_design.md) | 搜索流程详细设计 |
| [docs/incremental_design.md](docs/incremental_design.md) | 增量构建设计 |

## 适用场景

A2X 可索引任何带有 `description` 字段的智能体服务，包括：垂域智能体、MCP 工具、Agent Skill、以及可被智能体调用的社会服务与资源。

- **互联网级 Agent DNS**：作为海量智能体与服务之上的统一能力发现层
- **组织级网关**：在内部工具、部门服务、数据接口之间实现高效的能力发现与路由