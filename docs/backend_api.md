# 后端 API 文档

后端基于 FastAPI，启动命令：

```bash
python -m src.backend
# 服务地址: http://127.0.0.1:8000
# 交互文档: http://127.0.0.1:8000/docs
```

## 路由总览

API 由 4 个路由模块 + 1 个应用级端点组成：

| 模块 | 前缀 | 源文件 | 说明 |
|------|------|--------|------|
| 数据集 | `/api/datasets` | `src/backend/routers/dataset.py` | 数据集 CRUD、服务注册/注销、分类树、嵌入配置 |
| 构建 | `/api/datasets/{dataset}/build` | `src/backend/routers/build.py` | 分类树构建触发、状态、取消、SSE 日志流 |
| 搜索 | `/api/search` | `src/backend/routers/search.py` | 同步搜索、WebSocket 流式搜索、LLM 相关性判断 |
| 提供商 | `/api/providers` | `src/backend/routers/provider.py` | LLM 提供商列表与切换 |
| 应用级 | `/api/warmup-status` | `src/backend/app.py` | 启动预热进度 |

---

## 1. 数据集路由 `/api/datasets`

### 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/datasets` | 列出所有数据集 |
| `POST` | `/api/datasets` | 创建新数据集 |
| `DELETE` | `/api/datasets/{dataset}` | 删除数据集 |
| `GET` | `/api/datasets/{dataset}/services` | 列出/查询服务（4 种模式） |
| `POST` | `/api/datasets/{dataset}/services/generic` | 注册通用服务 |
| `POST` | `/api/datasets/{dataset}/services/a2a` | 注册 A2A Agent |
| `DELETE` | `/api/datasets/{dataset}/services/{service_id}` | 注销服务 |
| `POST` | `/api/datasets/{dataset}/skills` | 上传 Skill（ZIP） |
| `DELETE` | `/api/datasets/{dataset}/skills/{name}` | 删除 Skill |
| `GET` | `/api/datasets/{dataset}/skills/{name}/download` | 下载 Skill（ZIP） |
| `GET` | `/api/datasets/{dataset}/taxonomy` | 获取分类树 |
| `GET` | `/api/datasets/{dataset}/default-queries` | 示例查询 |
| `GET` | `/api/datasets/embedding-models` | 支持的嵌入模型列表 |
| `GET` | `/api/datasets/{dataset}/vector-config` | 获取嵌入配置 |
| `POST` | `/api/datasets/{dataset}/vector-config` | 设置嵌入模型 |

---

### GET `/api/datasets`

列出所有数据集及其服务数量和查询数量。

**响应：**
```json
[
  { "name": "ToolRet_clean", "service_count": 1839, "query_count": 1714 },
  { "name": "publicMCP",   "service_count": 1387, "query_count": 50 }
]
```

---

### POST `/api/datasets`

创建一个新的空数据集目录并配置嵌入模型。

**请求体：**
```json
{
  "name": "my_dataset",
  "embedding_model": "all-MiniLM-L6-v2"
}
```

`embedding_model` 默认值为 `"all-MiniLM-L6-v2"`。

**响应：**
```json
{ "dataset": "my_dataset", "embedding_model": "all-MiniLM-L6-v2", "status": "created" }
```

---

### DELETE `/api/datasets/{dataset}`

删除数据集目录及其全部数据，同时清理 ChromaDB 集合并释放缓存的搜索实例。

**响应：**
```json
{ "dataset": "my_dataset", "status": "deleted" }
```

---

### GET `/api/datasets/{dataset}/services`

列出数据集中的服务。通过 `mode` 参数控制返回粒度。

**查询参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `mode` | string | `"browse"` | `browse` \| `admin` \| `full` \| `single` |
| `service_id` | string | — | 服务 ID，仅 `single` 模式必填 |
| `size` | int | `-1` | 分页大小，`-1` 返回全部 |
| `page` | int | `1` | 页码（从 1 开始），仅 `full` 模式 + `size>0` 时生效 |

**mode 说明：**

| 模式 | 返回字段 | 用途 |
|------|----------|------|
| `browse` | `id, name, description` | 前端服务浏览器（轻量，直接读取 `service.json`） |
| `admin` | `id, name, description, type, source` | 管理面板条目列表 |
| `full` | 完整元数据（分页） | 管理面板详细查看 |
| `single` | 单个服务完整信息（skill 类型返回 ZIP） | 按 ID 精确查询 |

**响应 — browse 模式：**
```json
[
  { "id": "flight_booking", "name": "航班预订", "description": "支持国内外航班预订..." }
]
```

**响应 — admin 模式：**
```json
[
  { "id": "flight_booking", "type": "generic", "name": "航班预订", "description": "...", "source": "user_config" }
]
```

**响应 — full 模式：**
```json
{
  "servers": [ { "id": "...", "name": "...", "description": "...", ... } ],
  "metadata": { "count": 20, "total": 100, "page": 1, "total_pages": 5, "size": 20 }
}
```

---

### POST `/api/datasets/{dataset}/services/generic`

注册一个通用服务到指定数据集。

**请求体：**
```json
{
  "name": "航班预订",
  "description": "支持国内外航班预订与查询",
  "service_id": "flight_booking",
  "url": "https://example.com/api/flight",
  "inputSchema": { "type": "object", "properties": {} },
  "persistent": true
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 服务名称 |
| `description` | 是 | 服务描述 |
| `service_id` | 否 | 省略则自动生成 |
| `url` | 否 | 服务 URL |
| `inputSchema` | 否 | JSON Schema |
| `persistent` | 否 | `true`（默认）写入 `api_config.json` 持久化；`false` 为会话级 |

**响应：**
```json
{ "service_id": "flight_booking", "dataset": "my_dataset", "status": "registered" }
```

`status` 取值：`"registered"`（新注册） | `"updated"`（更新已有）

---

### POST `/api/datasets/{dataset}/services/a2a`

注册 A2A Agent。支持 URL 抓取或直接传入 Agent Card JSON。

**请求体（URL 模式）：**
```json
{
  "agent_card_url": "https://example.com/.well-known/agent.json",
  "persistent": true
}
```

**请求体（JSON 模式）：**
```json
{
  "agent_card": {
    "name": "翻译助手",
    "description": "多语种翻译 Agent",
    "url": "https://example.com/agent",
    "skills": [{ "name": "translate", "description": "文本翻译" }]
  },
  "persistent": true
}
```

**响应：**
```json
{ "service_id": "translate_agent", "dataset": "my_dataset", "status": "registered" }
```

---

### DELETE `/api/datasets/{dataset}/services/{service_id}`

注销指定服务。

**响应：**
```json
{ "service_id": "flight_booking", "status": "deregistered" }
```

`status` 取值：`"deregistered"` | `"not_found"`

> **注意**：`skill_folder` 来源的服务不能通过此端点注销，需使用 `DELETE /skills/{name}` 端点。

---

### POST `/api/datasets/{dataset}/skills`

上传 Skill 文件夹（ZIP 格式）。ZIP 必须包含 `SKILL.md`（根目录或单层子目录内），frontmatter 需含 `name` 和 `description` 字段。

**请求：** `Content-Type: multipart/form-data`，字段名 `file`。

**响应：**
```json
{ "name": "algorithmic-art", "dataset": "default", "service_id": "skill_xxxx", "status": "registered" }
```

`status` 取值：`"registered"` | `"updated"`（同名覆盖）

---

### DELETE `/api/datasets/{dataset}/skills/{name}`

删除 Skill 文件夹及其注册条目。

**响应：**
```json
{ "name": "algorithmic-art", "dataset": "default", "service_id": "skill_xxxx", "status": "deleted" }
```

`status` 取值：`"deleted"` | `"not_found"`

---

### GET `/api/datasets/{dataset}/skills/{name}/download`

将 Skill 文件夹打包为 ZIP 下载。

**响应：** `Content-Type: application/zip`，附件名 `{name}.zip`。

---

### GET `/api/datasets/{dataset}/taxonomy`

获取分类树结构，用于前端 D3.js 可视化。

**响应：** 完整的分类树 JSON（`taxonomy.json` 格式），包含 `root`、`categories`、`build_status` 字段。

若分类树尚未构建，返回 404。

---

### GET `/api/datasets/{dataset}/default-queries`

获取示例查询（随机子集），用于前端输入框建议。

**查询参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `count` | int | `5` | 返回条数 |

**响应：**
```json
[
  { "query": "帮我预订下周五的航班", "query_en": "Book a flight for next Friday" }
]
```

---

### GET `/api/datasets/embedding-models`

返回系统支持的嵌入模型列表。

**响应：**
```json
{
  "models": {
    "all-MiniLM-L6-v2": { "dim": 384, "description": "..." },
    "bge-small-zh-v1.5": { "dim": 512, "description": "..." },
    "text-embedding-3-small": { "dim": 1536, "description": "..." }
  }
}
```

---

### GET `/api/datasets/{dataset}/vector-config`

获取数据集当前的嵌入（向量）配置。

**响应：**
```json
{ "dataset": "ToolRet_clean", "embedding_model": "all-MiniLM-L6-v2", "embedding_dim": 384 }
```

---

### POST `/api/datasets/{dataset}/vector-config`

设置数据集的嵌入模型，保存后自动触发向量索引后台重建。

**请求体：**
```json
{ "embedding_model": "bge-small-zh-v1.5" }
```

**响应：**
```json
{
  "dataset": "ToolRet_clean",
  "embedding_model": "bge-small-zh-v1.5",
  "embedding_dim": 512,
  "message": "配置已保存，向量索引将在后台重建"
}
```

若模型名未知且未提供 `embedding_dim`，返回 400。

---

## 2. 构建路由 `/api/datasets/{dataset}/build`

### 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/datasets/{dataset}/build` | 触发分类树构建 |
| `GET` | `/api/datasets/{dataset}/build/status` | 查询构建状态 |
| `DELETE` | `/api/datasets/{dataset}/build` | 取消构建 |
| `GET` | `/api/datasets/{dataset}/build/stream` | SSE 实时日志流 |

---

### POST `/api/datasets/{dataset}/build`

触发后台分类树构建任务。

**请求体（`BuildRequest`，所有字段均可选）：**
```json
{
  "resume": "yes",
  "workers": 20,
  "generic_ratio": 0.333,
  "delete_threshold": 2,
  "max_depth": 3
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `resume` | string | `"no"` | `"no"` 完整重建 / `"yes"` 断点续传 / `"keyword"` 复用关键词重建 |
| `workers` | int | — | 并发 LLM 线程数 |
| `generic_ratio` | float | — | 通用类别比例阈值 |
| `delete_threshold` | int | — | 类别最小服务数（低于则删除） |
| `max_depth` | int | — | 最大分类树深度 |
| `max_service_size` | int | — | 叶节点最大服务数 |
| `max_categories_size` | int | — | 同级最大类别数 |
| `min_leaf_size` | int | — | 叶节点最小服务数 |
| `enable_cross_domain` | bool | — | 是否启用跨域分配 |
| `log_level` | string | — | 构建日志捕获级别（`"DEBUG"` / `"INFO"` / `"WARNING"` / `"ERROR"`），未指定时跟随系统默认级别 |

**响应：**
```json
{ "dataset": "ToolRet_clean", "status": "started", "message": "构建已启动" }
```

409 若该数据集已有构建在运行。

---

### GET `/api/datasets/{dataset}/build/status`

查询当前构建状态（一次性 HTTP 请求，非流式）。

**响应：**
```json
{
  "dataset": "ToolRet_clean",
  "status": "running",
  "message": "构建中，请稍候...",
  "started_at": 1711430400.0,
  "finished_at": null,
  "logs": ["10:00:01  ███░░ 12.5% [23/184] keywords", "..."]
}
```

`status` 取值：`"idle"` | `"running"` | `"done"` | `"cancelled"` | `"error"`

---

### DELETE `/api/datasets/{dataset}/build`

取消正在运行的构建任务。

向构建线程发送取消信号（`threading.Event`），构建在下一个阶段边界处停止。立即向 SSE 订阅者推送 `status: cancelled` 事件。

**响应：**
```json
{ "dataset": "ToolRet_clean", "status": "cancelled", "message": "构建已取消" }
```

409 若无运行中的构建。

---

### GET `/api/datasets/{dataset}/build/stream`

**Server-Sent Events 流**，实时推送构建日志。

`Content-Type: text/event-stream`，每个事件为一行 `data: <JSON>\n\n`。

**连接行为：**
1. 连接时先回放 `logs` 中已有的历史日志
2. 若构建未在运行，发送当前状态后关闭
3. 若构建正在运行，订阅后续实时日志，直到构建结束或客户端断开

**事件格式：**
```
data: {"type": "log",    "message": "10:00:05  ████░░ 23.1% [425/1839] assigned"}

data: {"type": "status", "status": "done",      "message": "分类树构建完成"}
data: {"type": "status", "status": "error",     "message": "service.json not found"}
data: {"type": "status", "status": "cancelled", "message": "构建已取消"}
```

收到 `type: "status"` 事件后流关闭。构建运行中每秒无新事件时发送 `: keepalive`（SSE 注释，浏览器忽略），防止连接超时。

---

## 3. 搜索路由 `/api/search`

### 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/search` | 同步搜索 |
| WebSocket | `/api/search/ws` | A2X 流式搜索 |
| `POST` | `/api/search/judge` | LLM 相关性判断 |

---

### POST `/api/search`

同步搜索，结果一次性返回。适用于所有搜索方法。

**请求体：**
```json
{
  "query": "预订北京到东京的航班",
  "method": "vector_5",
  "dataset": "ToolRet_clean",
  "top_k": 5
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | string | — | 搜索查询（必填） |
| `method` | string | — | 搜索方法（必填） |
| `dataset` | string | `"ToolRet_clean"` | 数据集名称 |
| `top_k` | int | `10` | 返回数量（主要用于 vector 方法） |

`method` 取值：`vector_5` | `vector_10` | `traditional` | `a2x_get_all` | `a2x_get_important` | `a2x_get_one`

**响应：**
```json
{
  "results": [
    { "id": "flight_booking", "name": "航班预订", "description": "..." }
  ],
  "stats": { "llm_calls": 3, "total_tokens": 1520 },
  "elapsed_time": 0.32
}
```

---

### WebSocket `/api/search/ws`

A2X 流式搜索，实时返回每一步分类导航过程，驱动前端树动画。非 A2X 方法也支持，直接返回最终结果。

**客户端发送（连接建立后）：**
```json
{ "query": "预订航班", "method": "a2x_get_all", "dataset": "ToolRet_clean", "top_k": 10 }
```

**服务端推送消息序列：**
```json
// 每个导航步骤（重复多次，仅 A2X 方法）
{ "type": "step", "data": { "parent_id": "交通出行", "selected": ["航班预订", "机票查询"], "pruned": ["酒店预订"] } }

// 最终结果（一次）
{ "type": "result", "data": { "results": [...], "stats": {...}, "elapsed_time": 2.14 } }

// 错误（可选）
{ "type": "error", "message": "LLM call failed: ..." }
```

搜索结束后服务端主动关闭连接。

---

### POST `/api/search/judge`

LLM 相关性判断，用于对比图中验证检索结果质量。

**请求体：**
```json
{
  "query": "预订北京到东京的航班",
  "services": [
    { "id": "flight_booking", "name": "航班预订", "description": "..." }
  ]
}
```

**响应：**
```json
{
  "results": [
    { "service_id": "flight_booking", "relevant": true }
  ]
}
```

---

## 4. 提供商路由 `/api/providers`

### 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/providers` | 列出 LLM 提供商 |
| `POST` | `/api/providers/{name}` | 切换提供商 |

---

### GET `/api/providers`

列出所有可用 LLM 提供商及当前激活的提供商。提供商配置来源于 `llm_apikey.json`。

**响应：**
```json
{
  "providers": [
    { "name": "deepseek", "model": "deepseek-chat" },
    { "name": "openai",   "model": "gpt-4o" }
  ],
  "current": "deepseek"
}
```

---

### POST `/api/providers/{name}`

切换 LLM 提供商。修改 `llm_apikey.json` 中的排序（将目标提供商置顶），同时重置所有缓存的 A2X 搜索实例。

**路径参数：** `name` — 提供商名称（须与 `llm_apikey.json` 中的 `name` 一致）

**响应（成功）：**
```json
{ "status": "ok", "current": "openai" }
```

**响应（失败 — 提供商不存在）：**
```json
{ "error": "Unknown provider: xxx", "valid": ["deepseek", "openai"] }
```

---

## 5. 应用级端点

### GET `/api/warmup-status`

预热状态查询，前端加载屏幕轮询此端点直到 `ready: true`。

**响应：**
```json
{ "stage": "taxonomy", "progress": 0.6, "ready": false }
```
```json
{ "stage": "done", "progress": 1.0, "ready": true }
```

---

## 并发构建隔离

多个数据集可同时构建，互不干扰：

- `_build_jobs` / `_log_subs` / `_cancel_flags` 均以 `dataset` 为 key 独立存储
- `_LogCapture` handler 按**线程 ID** 过滤日志记录，防止两个构建线程共享同一 `src.a2x` logger 时日志串流；日志捕获级别由请求参数 `log_level` 控制，未指定时跟随 `src.a2x` logger 的系统默认级别
- `_push_to_subs(dataset, event)` 只向该数据集的订阅者队列推送
