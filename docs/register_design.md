# 注册模块设计文档

## 概述

注册模块（`src/register/`）为 A2X Registry 提供服务注册能力，支持三类服务（通用服务 + A2A Agent + Skill），多数据集，多种注册方式（本地配置文件 / HTTP API / CLI / Skill 文件夹）。

参考 Nacos 设计思路：分离"注册存储"与"消费输出"，但针对单机文件驱动场景做了简化。

## 与 Nacos 的对比

| 维度 | Nacos | A2X Register |
|------|-------|-------------|
| 持久化 | MySQL / 嵌入式 DB | JSON 文件 |
| 每服务写入 | 3-4 个 Config 条目 | 1 个 entry |
| 缓存一致性 | 事件驱动 invalidation | 单锁保护内存 dict |
| 消费方式 | 实时查询 Config/Naming | 生成 service.json（纯输出） |
| 变更检测 | DB 版本号 | SHA256 content hash（独立 hash 文件） |

## 架构

```
user_config.json ──┐
  (用户编辑，只读)   │
                   ├─→ RegistryService ─→ service.json (纯输出)
api_config.json ───┤       │
  (系统写入)        │       │
skills/*/SKILL.md ─┘  RegistryStore
  (文件夹注册)         (文件 I/O)
```

### 文件职责

```
database/{dataset}/
    user_config.json    ← 用户手动编辑（系统只读）
    api_config.json     ← HTTP/CLI 注册的持久化服务（系统写入）
    skills/             ← Skill 文件夹（每个子目录含 SKILL.md）
    removed_skills/     ← 已注销的 Skill（移动而非删除）
    service.json        ← 输出（纯输出，service.json 从不作为输入读取）

database/.register_hashes.json  ← 变更检测用的 hash 存储
```

| 文件 | 谁写 | 谁读 | 用途 |
|------|------|------|------|
| `user_config.json` | 用户 | 系统（启动时） | 用户声明的服务 |
| `api_config.json` | 系统 | 系统（启动时 + 运行时） | HTTP 注册的持久化服务 |
| `skills/*/SKILL.md` | 用户/系统 | 系统（启动时 + 运行时） | Skill 文件夹注册源 |
| `removed_skills/` | 系统 | — | 已注销 Skill 的归档（同名覆盖） |
| `service.json` | 系统 | A2X | 合并后的输出，**从不被注册模块读取** |
| `.register_hashes.json` | 系统 | 系统（启动时） | 上次输出的 hash，用于变更检测 |

## 模块结构

```
src/register/
    __init__.py          导出 RegistryService, RegistryStore, validate_agent_card
    models.py            Pydantic 数据模型（AgentCard 支持 extra="allow"）
    store.py             RegistryStore: 单数据集文件 I/O（线程安全）
    service.py           RegistryService: 多数据集业务编排（单锁保护所有状态）
    agent_card.py        Agent Card URL 抓取 + description 聚合
    validation.py        A2A 格式检查（v0.0 / v1.0）
    __main__.py          CLI 入口

src/backend/routers/
    dataset.py           FastAPI 路由: 数据集 CRUD、服务注册/注销、taxonomy、embedding 配置
    build.py             FastAPI 路由: 分类树构建触发、状态查询、取消、SSE 日志流
```

## 三类服务

### 通用服务 (generic)
```json
{"type": "generic", "name": "Calculator", "description": "Basic arithmetic", "inputSchema": {...}, "url": "..."}
```
- `url` 可选，未提供时 service.json 输出中不包含 `url` 字段

### A2A Agent
**全量**：提供完整 AgentCard（所有原始字段完整保留，包括非标准字段）
```json
{"type": "a2a", "agent_card": {"name": "Weather Agent", "description": "...", "skills": [...]}}
```
**URL 引用**：提供 URL，系统抓取并缓存快照到 api_config.json
```json
{"type": "a2a", "agent_card_url": "https://.../.well-known/agent.json", "agent_card": {"...缓存..."}}
```

### Skill (skill)
Skill 以文件夹形式存储在 `database/{dataset}/skills/{name}/` 下，每个文件夹必须包含 `SKILL.md`，其 YAML frontmatter 必须含 `name` 和 `description` 字段：
```yaml
---
name: algorithmic-art
description: Creating algorithmic art using p5.js...
license: Complete terms in LICENSE.txt
---
```
- 注册方式：ZIP 上传（`POST /api/datasets/{dataset}/skills`）或直接放入 `skills/` 目录
- 注销时文件夹移至 `removed_skills/`（而非删除），同名覆盖
- 三来源合并优先级：`api_config` > `user_config` > `skill_folder`

## A2A 格式验证 (`validation.py`)

支持两个协议版本：

| 版本 | 必填字段 | 用途 |
|------|---------|------|
| **v0.0** | name, description | 最宽松，兼容非标准 Agent Card |
| **v1.0** | name, description, version, url, capabilities, defaultInputModes, defaultOutputModes, skills (含 id/name/description/tags) | 完整 A2A v1.0 规范 |

- 验证顺序：**先严后宽**（v1.0 → v0.0），返回最严格的匹配版本
- 默认允许 `{"v0.0", "v1.0"}`
- 可通过 `allowed_a2a_versions` 参数配置（如仅允许 `{"v1.0"}`）
- AgentCard 模型使用 `extra="allow"`，非标准字段完整保留

## 线程安全

```
RegistryService._lock
  └── 保护: _entries, _output_cache, _prev_hashes
  └── 所有读写操作都在锁内完成

RegistryStore._lock
  └── 保护: _api_entries dict + api_config.json 写入
```

设计原则：**锁内只操作内存，文件 I/O 在锁外执行**

- `_do_register`：锁内更新 `_entries` → 锁外写 `api_config.json` → 锁外 `_regenerate_output`
- `deregister`：锁内删除 `_entries` + 记录 source → 锁外删除 `api_config` 条目 → 锁外 `_regenerate_output`
- `_regenerate_output`：锁内生成 output + 更新 hash + 更新 cache → 锁外写文件
- `_fetch_agent_cards_parallel`：并行 fetch 后在锁内更新 `entry.agent_card`
- `register_batch`：锁内更新 `_entries` + 收集所有 `api_config` 条目 → 锁外批量写入
- 查询方法（`list_services` 等）返回副本，不暴露内部引用

## HTTP API

路由分布在两个文件中：`src/backend/routers/dataset.py`（数据集 + 服务 + 配置）和 `src/backend/routers/build.py`（构建相关）。

### 数据集管理

| Method | Path | 说明 | 错误码 |
|--------|------|------|--------|
| GET | `/api/datasets` | 数据集列表（含服务数、查询数） | — |
| POST | `/api/datasets` | 创建新数据集 | 400 |
| DELETE | `/api/datasets/{dataset}` | 删除数据集（含 ChromaDB 清理） | 400/500 |

### 服务注册/注销/查询

| Method | Path | 说明 | 错误码 |
|--------|------|------|--------|
| POST | `/api/datasets/{dataset}/services/generic` | 注册通用服务 | 400 (校验失败) |
| POST | `/api/datasets/{dataset}/services/a2a` | 注册 A2A Agent | 400 (校验/参数), 500 (fetch失败) |
| DELETE | `/api/datasets/{dataset}/services/{service_id}` | 注销（不含 skill） | 400 (user_config/skill 不可删) |
| GET | `/api/datasets/{dataset}/services` | 列出/查询服务（mode=browse/admin/full/single） | — |
| POST | `/api/datasets/{dataset}/skills` | 上传 Skill（ZIP） | 400 (校验失败) |
| DELETE | `/api/datasets/{dataset}/skills/{name}` | 注销 Skill（移至 removed_skills） | — |
| GET | `/api/datasets/{dataset}/skills/{name}/download` | 下载 Skill（ZIP） | 404 |

### 分类树

| Method | Path | 说明 | 错误码 |
|--------|------|------|--------|
| GET | `/api/datasets/{dataset}/taxonomy` | 分类树结构（D3.js 可视化） | 404 |

### 构建

| Method | Path | 说明 | 错误码 |
|--------|------|------|--------|
| POST | `/api/datasets/{dataset}/build` | 触发分类树后台构建 | 409 (已在构建) |
| GET | `/api/datasets/{dataset}/build/status` | 查询构建进度 | — |
| DELETE | `/api/datasets/{dataset}/build` | 取消正在进行的构建 | 409 (无运行中构建) |
| GET | `/api/datasets/{dataset}/build/stream` | SSE 实时构建日志流 | — |

### Embedding / 向量配置

| Method | Path | 说明 | 错误码 |
|--------|------|------|--------|
| GET | `/api/datasets/embedding-models` | 支持的 embedding 模型列表 | — |
| GET | `/api/datasets/{dataset}/vector-config` | 获取数据集向量配置 | — |
| POST | `/api/datasets/{dataset}/vector-config` | 设置 embedding 模型（触发向量索引重建） | 400 |

### 其他

| Method | Path | 说明 | 错误码 |
|--------|------|------|--------|
| GET | `/api/datasets/{dataset}/default-queries` | 随机示例查询（?count=5） | — |

错误映射：`ValueError` → 400，其他异常 → 500。服务未初始化 → 503。

## 流程

### 启动
```
Phase 1: 加载配置文件
  ├── 读 user_config.json (只读)
  ├── 读 api_config.json (缓存到内存)
  ├── 合并 + 验证 A2A 条目
  └── 扫描 skills/*/SKILL.md（最低优先级，不覆盖已有条目）

Phase 2: 并行抓取 agent_card_url
  └── ThreadPoolExecutor(10) → 锁内更新 entry.agent_card

Phase 3: 生成输出
  ├── 更新 api_config 缓存快照
  ├── 计算 hash，对比 .register_hashes.json
  └── 有变化才写 service.json
```

### 注册
```
register_generic / register_a2a
  → 校验输入（name/description 非空，A2A 格式验证）
  → 创建 RegistryEntry
  → 锁内更新 _entries
  → 锁外持久化写 api_config.json（如 persistent=True）
  → 锁外 _regenerate_output → 写 service.json（如 hash 变化）

register_skill(dataset, zip_bytes)
  → 解压 ZIP → 校验 SKILL.md 含 name + description
  → 存储至 skills/{name}/（已存在则覆盖）
  → 锁内更新 _entries
  → 锁外 _regenerate_output

register_batch
  → 锁内更新 _entries + 收集该 dataset 所有 api_config 条目
  → 锁外批量写 api_config.json（保留已有条目）
  → 锁外 _regenerate_output
```

### 注销
```
deregister(dataset, service_id)
  → 锁内检查 source + 删除 _entries
  → user_config: 拒绝（ValueError → 400）
  → skill_folder: 拒绝（需使用 DELETE /skills/{name}）
  → api_config: 锁外从文件删除
  → ephemeral: 仅内存删除（已在锁内完成）
  → 锁外 _regenerate_output

deregister_skill(dataset, name)
  → 锁内删除 _entries
  → 将 skills/{name}/ 移至 removed_skills/{name}/（同名覆盖）
  → 锁外 _regenerate_output
```

## service.json 输出格式

### Generic 服务
```json
{"id": "generic_xxx", "name": "...", "description": "...", "inputSchema": {...}}
```
- `url` 仅在有值时包含（不输出 `null`）

### A2A 服务
```json
{"id": "agent_xxx", "type": "a2a", "name": "...", "description": "聚合描述", "agent_card": {...}}
```
- `description` 由 `build_description()` 聚合：`"{card.description}. Skills: [name] desc; ..."`
- `url` 仅在有值时包含
- `agent_card` 保留完整原始数据（含非标准字段）

### Skill 服务
```json
{"id": "skill_xxx", "type": "skill", "name": "algorithmic-art", "description": "Creating algorithmic art...", "metadata": {"skill_path": "skills/algorithmic-art", "license": "...", "files": ["SKILL.md", "LICENSE.txt", ...]}}
```
- `name` 和 `description` 来自 SKILL.md frontmatter
- `metadata.skill_path` 为相对于数据集目录的路径
- `metadata.files` 列出文件夹内所有文件的相对路径

### 未解析的 A2A 服务（agent_card_url fetch 失败且无缓存）
```json
{"id": "agent_xxx", "type": "a2a", "name": "agent_xxx", "description": "Unresolved agent card: https://..."}
```

## 分类树状态 (TaxonomyState)

`RegistryService.check_taxonomy_state(dataset)` 返回当前数据集的分类树可用性：

| 状态 | 含义 |
|------|------|
| `available` | 分类树 hash 与 service.json 一致，A2X 搜索正常可用 |
| `unavailable` | service.json 变更后分类树未重建，A2X 搜索被阻断 |
| `stale` | 发生 CRUD 后尚未重新检查，下次调用时重新评估 |
| `nonexistent` | 尚未构建分类树（或 `build_config.json` 不存在） |
| `unmanaged` | 该数据集不受注册模块管理，不做限制 |

状态由 `RegistryService.check_taxonomy_state(dataset)` 计算，每次 CRUD 后置为 `stale`，下次查询时重新对比 hash。

## 分类树构建

```
POST /api/datasets/{dataset}/build
Body: {"resume": "no" | "yes" | "keyword"}
```

- `no`（默认）：完整重建
- `yes`：断点续建（跳过已完成步骤）
- `keyword`：复用已提取关键词，仅重建分类结构

构建在 FastAPI **后台任务**中执行（`src/backend/routers/build.py`），调用 `TaxonomyBuilder.build(resume=...)`；同一数据集同时只能有一个构建任务（重复请求 → 409）。支持取消（`DELETE /api/datasets/{dataset}/build`）和 SSE 实时日志流（`GET /api/datasets/{dataset}/build/stream`）。

进度通过轮询查询：

```
GET /api/datasets/{dataset}/build/status
→ {"dataset": "...", "status": "running|done|error|cancelled|idle", "message": "...", "started_at": ..., "finished_at": ...}
```

构建状态存储在模块级 `_build_jobs` dict（进程内，重启清零）。

## 向量数据库同步

注册模块与向量搜索引擎通过**回调**解耦：

```python
registry_svc.set_on_service_changed(callback)
# callback: (dataset: str) -> None
```

每次 `_regenerate_output` 检测到 service.json 实际变更后，触发回调。后端注册该回调为 `search_service.schedule_vector_sync(dataset)`，在独立线程中做**增量同步**：

- 对比 ChromaDB 已有文档与 service.json 当前内容
- 仅 upsert 新增/描述变化的条目，删除已注销的条目
- 同步完成后驱逐 `VectorSearch` 实例缓存，下次搜索时重建

后端启动时也会对所有 registry 管理数据集执行一次初始全量同步（`warmup` 阶段）。

## 变更检测

service.json 是**纯输出**，从不被注册模块读取。变更检测通过独立的 hash 文件：

```
database/.register_hashes.json = {"publicMCP": "sha256...", "ToolRet_clean": "sha256...", ...}
```

- 启动时：从 hash 文件读上次的 hash → 从源数据生成新 hash → 对比
- 运行时：每次 `_regenerate_output` 对比新旧 hash，仅变化时写盘
- `has_changed(dataset)` 供 A2X 查询，决定是否重建分类树

## 效率

| 场景 | 文件写入 | 说明 |
|------|---------|------|
| 启动无变更 | 0 次 | hash 相同，跳过写盘 |
| 启动有变更 | 1~2 次 | service.json + hash file |
| 单次持久注册 | 2~3 次 | api_config + service.json + hash |
| 单次临时注册 | 1~2 次 | service.json + hash |
| 批量 N 个 | 2~3 次 | 不是 N 倍 |
| 查询 | 0 次 | 直接返回内存缓存副本 |

## 测试覆盖

`test_register.py` 包含 35 个测试用例：

| 类别 | 测试 | 说明 |
|------|------|------|
| 启动 | T01-T02 | startup + 幂等性 |
| 注册 | T03-T06 | generic/A2A/persistent/v1.0 |
| 错误处理 | T07-T08, T21-T21b, T31 | bad URL / 缺参数 / 空名称 / 空描述 / 纯空格 |
| 注销 | T10-T13, T26 | api_config / user_config 拒绝 / 不存在 / ephemeral / 不存在数据集 |
| 查询 | T14-T18 | list / get_entry / status / datasets / has_changed |
| 新数据集 | T19 | 动态创建数据集 |
| 验证 | T20, T33 | v1.0-only 严格模式 / validation 模块直接测试 |
| 一致性 | T22-T23, T29 | service.json ↔ 内存 / api_config 完整性 / 无 null url |
| 并发 | T24, T30 | 10线程注册 / 混合注册+注销 |
| 批量 | T25, T28 | batch register / 保留已有条目 |
| A2A | T27, T34 | extra fields 保留 / build_description |
| 全局配置 | T32 | _distribute_global_config |

## Web 管理界面 (Admin Panel)

前端 `AdminPanel` 组件提供图形化操作界面，通过 Header 左上角的"管理员 / 用户模式"切换按钮进入。

**布局**：左侧操作面板 + 右上请求预览 + 右下响应展示。

### 操作类型

**注册 (register)**

- 服务类型：通用服务（generic） / A2A Agent / Skill
- 数据集：选择已有数据集，或输入新数据集名称（新建）
- A2A 模式：URL 模式（填写 `agent_card_url`）/ JSON 模式（粘贴完整 AgentCard JSON）
- Skill 模式：选择本地文件夹 → 预览 SKILL.md 信息 → JSZip 打包上传
- 各字段支持**数据集默认值**：字段为空时显示为较深占位文本（"使用默认值"提示），提交时自动使用
- `persistent` 开关：是否写入 `api_config.json` 持久化（仅 generic/A2A）

**注销 (deregister)**

- 内置**服务浏览器**：加载当前数据集所有已注册服务，支持按 ID / 名称 / 描述搜索
- 点击列表条目自动填入 `service_id`，也可手动输入
- Skill 类型条目标记 `skill` 标签，注销时自动调用 skill 专用删除接口（移至 `removed_skills/`）
- 注销成功后列表自动刷新

**查询 (query)**

- 双模式切换：列表（分页浏览全部服务） / 查找（按 ID 精确查询单个服务）
- 查找模式内置服务浏览器，点击即可选择 service_id
- Skill 类型服务查找时返回 ZIP 下载

**构建分类树 (build)**

- 构建模式：完整重建 / 智能续建 / 复用关键词
- 发起后后台异步执行，前端每 2 秒轮询一次构建状态
- 响应面板实时显示进度（`running` → `done` / `error`）

### 请求预览

操作面板变更时实时生成等效 HTTP 请求（含 Method、Path、JSON body），字段空值自动替换为对应默认值。

## CLI

```bash
python -m src.register --config src/register/user_config.example.json
python -m src.register --status
python -m src.register --status --dataset publicMCP
```
