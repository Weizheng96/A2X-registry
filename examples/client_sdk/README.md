# Client SDK Examples

按 **A2X Python Client SDK** 的每个公开方法提供同步 / 异步示例。

每个文件覆盖三类路径：**正常使用**、**边界 / 输入校验**、**异常路径（NotFound / 网络失败 / 本地 fail-fast）**。可作为：

- 逐方法的使用示例
- 对照 [`docs/client_design.md`](../../docs/client_design.md) 的实现验证
- 本地起后端后的冒烟 / 回归测试

## 总入口

[`run_all_examples.py`](./run_all_examples.py) 顺序跑一遍所有示例，每个文件作为独立子进程，失败不互相影响。

```bash
# 起后端
python -m src.backend

# 另一个终端
python examples/client_sdk/run_all_examples.py
```

可用环境变量：

```bash
A2X_BASE_URL=http://127.0.0.1:8000   # 默认值
```

## 当前 SDK 公开方法

```
基础（dataset / 通用 agent CRUD）
  create_dataset / delete_dataset
  register_agent / update_agent / set_team_count
  list_agents / get_agent / deregister_agent

Agent Team 流程（团队组队原语）
  register_blank_agent       # teammate 入空闲池
  list_idle_blank_agents     # leader 取空闲队员（默认 n=1）
  replace_agent_card         # 整张覆盖（含 endpoint auto-fill）
  restore_to_blank           # 恢复为空白（L1→L2→L3 endpoint 解析）
```

## 文件索引

### 1. 数据集 CRUD

| 方法 | 同步 | 异步 |
|------|------|------|
| `create_dataset` | [create_dataset_sync.py](./create_dataset_sync.py) | [create_dataset_async.py](./create_dataset_async.py) |
| `delete_dataset` | [delete_dataset_sync.py](./delete_dataset_sync.py) | [delete_dataset_async.py](./delete_dataset_async.py) |

覆盖：默认参数 / 自定义 embedding / 自定义 formats / `formats=None` / 重复创建 / 删不存在的 dataset / 网络失败。

### 2. 通用 Agent CRUD

| 方法 | 同步 | 异步 |
|------|------|------|
| `register_agent` | [register_agent_sync.py](./register_agent_sync.py) | [register_agent_async.py](./register_agent_async.py) |
| `update_agent` | [update_agent_sync.py](./update_agent_sync.py) | [update_agent_async.py](./update_agent_async.py) |
| `set_team_count` | [set_team_count_sync.py](./set_team_count_sync.py) | [set_team_count_async.py](./set_team_count_async.py) |
| `deregister_agent` | [deregister_agent_sync.py](./deregister_agent_sync.py) | [deregister_agent_async.py](./deregister_agent_async.py) |

覆盖：persistent=True/False 的 ownership 差异 / 同 sid 重注册 → status="updated" / 校验失败 / `NotOwnedError` 本地 fail-fast / `NotFoundError` 后端 404 + 自动 ownership 清理 / `UserConfigServiceImmutableError` / 网络失败。

> **注**：deregister 不存在的 sid 会得到 **`NotFoundError`（HTTP 404）**，而不是 200 + `status="not_found"`。详见 [§3.4 NotFound 分层约定](../../docs/client_design.md#34-notfound-分层错误约定)。

### 3. 列表 / 查询

| 方法 | 同步 | 异步 |
|------|------|------|
| `list_agents` | [list_agents_sync.py](./list_agents_sync.py) | [list_agents_async.py](./list_agents_async.py) |
| `get_agent` | [get_agent_sync.py](./get_agent_sync.py) | [get_agent_async.py](./get_agent_async.py) |

`list_agents` 覆盖：

- 不传 filters → 返回全量
- 单字段过滤
- 复合 AND 过滤
- 扁平返回形状 `list[dict]`（`{id, type, name, description, ...card_fields}`）
- 保留键 / None 值 / 空 key 的本地 ValueError fail-fast
- 空数据集 / 不存在数据集都返回 `[]`

`get_agent` 覆盖：成功 / NotFoundError / UnexpectedServiceTypeError（skill ZIP）/ 网络失败。

### 4. Agent Team 流程

| 方法 | 同步 | 异步 |
|------|------|------|
| `register_blank_agent` | [register_blank_agent_sync.py](./register_blank_agent_sync.py) | [register_blank_agent_async.py](./register_blank_agent_async.py) |
| `list_idle_blank_agents` | [list_idle_blank_agents_sync.py](./list_idle_blank_agents_sync.py) | [list_idle_blank_agents_async.py](./list_idle_blank_agents_async.py) |
| `replace_agent_card` | [replace_agent_card_sync.py](./replace_agent_card_sync.py) | [replace_agent_card_async.py](./replace_agent_card_async.py) |
| `restore_to_blank` | [restore_to_blank_sync.py](./restore_to_blank_sync.py) | [restore_to_blank_async.py](./restore_to_blank_async.py) |

覆盖：

- **register_blank_agent**：blank 模板（`description="__BLANK__"`、`agentTeamCount=0`）/ L1 缓存写入 / 同 endpoint 重注册幂等 / bad endpoint fail-fast
- **list_idle_blank_agents**：默认 `n=1` / 显式 `n=N` / 后端 filter 严格 idle（`description=__BLANK__` AND `agentTeamCount=0`）/ 扁平返回形状 / `n=0` 短路无 HTTP / 非法 n 的本地 ValueError
- **replace_agent_card**：完整覆盖语义 / **endpoint auto-fill**（不传 endpoint 时从 L1 / L2 自动补）/ L1 缓存刷新 / NotOwnedError / 非 dict 的 ValueError / NotFoundError + 自动清理
- **restore_to_blank**：L1 命中（0 GET）/ L2 fallback（1 GET）/ L3 ValueError / NotOwnedError / NotFoundError

## 期望约定

每个示例：

- 自动选择临时 ownership 文件，避免污染主目录
- 用独立 dataset 名（`example_<method>_<sync|async>`），跑完会清理
- 读 `A2X_BASE_URL` 环境变量，默认 `http://127.0.0.1:8000`
- 异常都用 `try/except` 接住并打印类型 + 状态码（如有），方便看到完整错误链路
