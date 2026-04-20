# Client SDK Examples

本目录按 **A2X Python Client SDK 的公开方法** 提供同步 / 异步示例。

这些示例的目标不是只演示“最短 happy path”，而是尽量把**真实调用时最常见的成功路径、边界情况和异常路径**一起覆盖掉，方便你：

- 逐个理解 SDK 方法的用法
- 对照 `docs/client_design.md` 检查实现是否符合设计
- 在本地起后端后做 smoke test / 回归测试

此外，本目录还提供一个总的 smoke runner：

- [run_all_examples.py](/Users/Joanna/A2X-registry/examples/client_sdk/run_all_examples.py)

## 对应的 SDK 方法

当前 `src/client` 的公开 API 包括：

- `create_dataset`
- `delete_dataset`
- `register_agent`
- `update_agent`
- `set_team_count`
- `list_agents`
- `get_agent`
- `deregister_agent`

本目录已经为以上每个方法补齐了同步 / 异步示例。

## 文件索引

### 1. `create_dataset`

- [create_dataset_sync.py](/Users/Joanna/A2X-registry/examples/client_sdk/create_dataset_sync.py)
- [create_dataset_async.py](/Users/Joanna/A2X-registry/examples/client_sdk/create_dataset_async.py)

覆盖内容：

- 默认参数创建数据集
- `formats=None` 的行为
- 自定义 `embedding_model`
- 自定义 `formats`
- 非法 `formats` 导致的 `ValidationError`
- 重复创建数据集的后端错误
- 后端不可达时的网络 / HTTP 异常

### 2. `delete_dataset`

- [delete_dataset_sync.py](/Users/Joanna/A2X-registry/examples/client_sdk/delete_dataset_sync.py)
- [delete_dataset_async.py](/Users/Joanna/A2X-registry/examples/client_sdk/delete_dataset_async.py)

覆盖内容：

- 成功删除数据集
- 删除不存在的数据集时的错误路径
- 后端不可达时的网络 / HTTP 异常

### 3. `register_agent`

- [register_agent_sync.py](/Users/Joanna/A2X-registry/examples/client_sdk/register_agent_sync.py)
- [register_agent_async.py](/Users/Joanna/A2X-registry/examples/client_sdk/register_agent_async.py)

覆盖内容：

- `persistent=True` 时成功注册并记录 ownership
- 注册后继续调用 `update_agent()` 成功
- 使用相同显式 `service_id` 重注册，返回 `status="updated"`
- `persistent=False` 时不记录 ownership，后续 mutation 本地直接 `NotOwnedError`
- 非法 agent card 导致 `ValidationError`
- 后端不可达时的网络 / HTTP 异常

### 4. `update_agent`

- [update_agent_sync.py](/Users/Joanna/A2X-registry/examples/client_sdk/update_agent_sync.py)
- [update_agent_async.py](/Users/Joanna/A2X-registry/examples/client_sdk/update_agent_async.py)

覆盖内容：

- 成功更新字段
- `changed_fields` / `taxonomy_affected` 返回值
- 非 taxonomy 字段更新
- `NotOwnedError` 本地 fail-fast
- 远端已删除时的 `NotFoundError`
- 404 后本地 ownership 自动清理，第二次调用变成 `NotOwnedError`
- 非法字段类型导致 `ValidationError`
- 后端不可达时的网络 / HTTP 异常

### 5. `set_team_count`

- [set_team_count_sync.py](/Users/Joanna/A2X-registry/examples/client_sdk/set_team_count_sync.py)
- [set_team_count_async.py](/Users/Joanna/A2X-registry/examples/client_sdk/set_team_count_async.py)

覆盖内容：

- 成功设置 `agentTeamCount`
- `changed_fields == ["agentTeamCount"]`
- `taxonomy_affected == False`
- 通过 `get_agent()` 回读 `metadata.agentTeamCount`
- 非法 `count` 在本地直接抛 `ValueError`，不发 HTTP
- `NotOwnedError` 本地 fail-fast
- 远端已删除时的 `NotFoundError`
- 404 后 ownership 自动清理
- 后端不可达时的网络 / HTTP 异常

### 6. `list_agents`

- [list_agents_sync.py](/Users/Joanna/A2X-registry/examples/client_sdk/list_agents_sync.py)
- [list_agents_async.py](/Users/Joanna/A2X-registry/examples/client_sdk/list_agents_async.py)

覆盖内容：

- 成功列出多个 agent
- 空数据集返回 `[]`
- 不存在的数据集也返回 `[]`
- 后端不可达时的网络 / HTTP 异常

### 7. `get_agent`

- [get_agent_sync.py](/Users/Joanna/A2X-registry/examples/client_sdk/get_agent_sync.py)
- [get_agent_async.py](/Users/Joanna/A2X-registry/examples/client_sdk/get_agent_async.py)

覆盖内容：

- 成功读取单个 A2A agent 的详情
- 查看 `AgentDetail.metadata` / `AgentDetail.raw`
- 不存在的 service 返回 `NotFoundError`
- skill 类型返回 ZIP 时抛出 `UnexpectedServiceTypeError`
- 后端不可达时的网络 / HTTP 异常

说明：

- `get_agent_*` 示例里会通过原始 HTTP 调一次后端 `/skills` 上传接口，构造一个最小 skill，用来真实覆盖 `application/zip -> UnexpectedServiceTypeError` 这条设计分支。

### 8. `deregister_agent`

- [deregister_agent_sync.py](/Users/Joanna/A2X-registry/examples/client_sdk/deregister_agent_sync.py)
- [deregister_agent_async.py](/Users/Joanna/A2X-registry/examples/client_sdk/deregister_agent_async.py)

覆盖内容：

- 成功注销服务
- `NotOwnedError` 本地 fail-fast
- 远端已删除时的 `NotFoundError`
- 404 后本地 ownership 自动清理，第二次调用变成 `NotOwnedError`
- 后端不可达时的网络 / HTTP 异常

## 如何运行

先启动本地后端：

```bash
python -m src.backend --host 0.0.0.0 --port 8000
```

然后在另一个终端运行任意示例，例如：

```bash
python examples/client_sdk/create_dataset_sync.py
python examples/client_sdk/create_dataset_async.py
python examples/client_sdk/run_all_examples.py
```

如果后端不在默认地址 `http://127.0.0.1:8000`，可以通过环境变量指定：

```bash
A2X_BASE_URL=http://127.0.0.1:8016 python examples/client_sdk/set_team_count_sync.py
```

## 运行约定

这些示例大多遵循下面几条约定：

- 每个示例使用自己独立的 dataset 名称，尽量避免互相污染
- 需要 ownership 的示例会使用单独的临时 ownership 文件
- 开始前通常会先尝试清理同名 dataset
- 结束时会删除自己创建的数据集
- “后端不可达” 场景统一使用 `127.0.0.1:8999`

注意：

- 在某些环境里，坏地址 `127.0.0.1:8999` 可能表现为 `A2XConnectionError`
- 在另一些环境里，它可能表现为 `ServerError(502)`

所以大部分示例会同时捕获：

- `A2XConnectionError`
- `A2XHTTPError`

这是为了让示例在不同机器 / 代理环境下都能稳定工作。

## 设计文档对应关系

如果你要把示例和设计文档对照着看，推荐一起打开：

- [docs/client_design.md](/Users/Joanna/A2X-registry/docs/client_design.md)

尤其是下面这些部分：

- `create_dataset`
- `register_agent`
- `update_agent`
- `set_team_count`
- `list_agents`
- `get_agent`
- `deregister_agent`
- `NotFound` 分层约定
