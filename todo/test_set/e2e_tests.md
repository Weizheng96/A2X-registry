# 端到端测试

**跑真后端**（`python -m src.backend`），验证 SDK → FastAPI → 文件系统全链路。每个 case 自己清理创建的数据集。

**Fixture**：
```python
@pytest.fixture(scope="session")
def backend_url():
    # 启动 uvicorn in subprocess 或使用 pytest-xprocess
    # 探测 /docs 可访问后 yield url
    yield "http://127.0.0.1:8765"
    # 结束后 terminate

@pytest.fixture
def unique_ds(request):
    name = f"test_e2e_{request.node.name}_{uuid.uuid4().hex[:8]}"
    yield name
    # 尽力清理
    try:
        A2XClient(backend_url).delete_dataset(name)
    except Exception:
        pass
```

---

## 1. Agent Team 主流程 — `E-FLOW-*`

### E-FLOW-001 | P0 — 完整 Agent Team 九步流程
**覆盖**：`client_design.md §1.3` 的示例代码。

- **Steps**：
  1. `client = A2XClient(base_url=backend_url, ownership_file=tmp_path/"owned.json")`
  2. `client.create_dataset(unique_ds, formats={"a2a":"v0.0"})`
  3. 注册 3 个 agent（planner / researcher / coder），每个最小 Card
  4. 对 3 个 agent 各 `set_team_count(0)`
  5. `update_agent(researcher, {"description":"新描述","skills":[...]})`
  6. 对 3 个 agent 各 `set_team_count(3)`
  7. `list_agents` 应返回 3 项
  8. `get_agent(planner)` 应含 `agentTeamCount==3`
  9. `deregister_agent(coder)` → `list_agents` 应返回 2 项
  10. `delete_dataset(unique_ds)`
- **Expect**：
  - 每步 HTTP 状态码正确
  - 步骤 7 中每个 brief 的 `description` 被 step 5 更新
  - 步骤 8 metadata 含自定义字段
  - 步骤 9 后本地 `_owned` 只剩 2 个
  - 步骤 10 后本地 `_owned[unique_ds]` 消失
  - 结束后通过另一个 `A2XClient` 查询 `list_agents(unique_ds)` → 抛或 `[]`（视设计）

### E-FLOW-002 | P0 — 异步版本的等价流程
**覆盖**：`client_design.md §1.4` 的异步示例。

- **Steps**：用 `AsyncA2XClient` + `asyncio.gather` 并发 register / set_team_count
- **Expect**：同 E-FLOW-001；**额外**验证并发 register 不串行（用 `time.monotonic()` 度量，预期 gather 时间 < 3 × 单次注册时间）

### E-FLOW-003 | P1 — `formats={"a2a":"v0.0"}` 锁定后拒绝 generic
- **Steps**：
  1. `create_dataset(ds, formats={"a2a":"v0.0"})`
  2. 直接用 httpx 对 `/api/datasets/{ds}/services/generic` POST（绕过 SDK）
- **Expect**：后端返回 400（格式不允许）。验证设计默认值的效果。

---

## 2. 跨进程所有权持久化 — `E-PERS-*`

### E-PERS-001 | P0 — 进程退出后 `_owned` 能恢复
- **Steps**：
  1. 进程 A：构造 client，`register_agent(ds, card)` → 拿到 sid
  2. 进程 A：**不**调 `deregister`；退出（`client.close()`）
  3. 进程 B：构造 client（同 `ownership_file` 和 `base_url`）
  4. 进程 B：`update_agent(ds, sid, {"description":"x"})` → 应成功
- **Expect**：进程 B 无需额外初始化，`_owned` 自动加载

### E-PERS-002 | P0 — 跨 base_url 隔离
- **Steps**：
  1. client_a `base_url=http://server_a`；注册 s_a
  2. client_b `base_url=http://server_b`（同 ownership_file）；注册 s_b
  3. 新进程构造 client `base_url=http://server_a`
- **Expect**：新进程只加载 `http://server_a` 段，只看到 s_a

### E-PERS-003 | P1 — 另一 client 实例（同机同后端）注册的 sid，不能被 update
- **Steps**：
  1. client_a `ownership_file=path_a`；register → sid
  2. client_b `ownership_file=path_b`（空文件）；
  3. `client_b.update_agent(ds, sid, {...})`
- **Expect**：抛 `NotOwnedError`（local fail-fast）

### E-PERS-004 | P1 — `ownership_file=False` 的 client 不写盘
- **Steps**：
  1. memory client 注册
  2. 进程退出
  3. 新进程默认 `ownership_file` 初始化
- **Expect**：新进程 `_owned` 不含之前注册的 sid

---

## 3. 真实 Agent Card 变体 — `E-CARD-*`

### E-CARD-001 | P1 — 只传必填字段
- **Card**：`{"protocolVersion":"0.0","name":"N","description":"D"}`
- **Expect**：注册成功；`get_agent` 返回的 metadata 含默认补全字段（后端补默认）

### E-CARD-002 | P1 — 完整 Card（所有 A2A spec 字段）
- **Card**：含 skills、provider、capabilities、defaultInputModes、documentationUrl
- **Expect**：所有字段透传并持久化；`get_agent` 后字段一一对齐

### E-CARD-003 | P1 — 自定义字段（`agentTeamCount`, `tags`, `custom_x`）
- **Expect**：`extra="allow"` 生效；查询时能读回

### E-CARD-004 | P2 — 改嵌套字段必须整段重传
- **Steps**：先 register 含 `provider={"organization":"O1","url":"U1"}`；然后 `update_agent(ds,sid,{"provider":{"organization":"O2"}})`
- **Expect**：`provider.url` 被清空（设计明确：顶层 upsert 不深度 merge）

---

## 4. 多客户端组合 — `E-MULTI-*`

### E-MULTI-001 | P1 — 两个 client 同一数据集，各管各的 agent
- **Steps**：
  - client_a register a1, a2
  - client_b register b1, b2
  - client_a `list_agents(ds)` 应返回 4 项
  - client_a `deregister_agent(b1)` → `NotOwnedError`（本地）
- **Expect**：读是全局的，写是所有权限定的

### E-MULTI-002 | P2 — client_a 删 dataset 影响 client_b 的 `_owned`
- **Steps**：
  - client_a 和 client_b 各 register 一个
  - client_a `delete_dataset(ds)`
  - client_b `update_agent(ds, client_b_sid, {...})`
- **Expect**：client_b 请求到后端返回 400/404；此时 client_b 的 `_owned` 仍有残留（未采纳 D3/D6 时）或已自动清理（采纳后）

---

## 5. 错误路径端到端 — `E-ERR-*`

### E-ERR-001 | P1 — 重复 `create_dataset` 同名
- **Steps**：第一次成功；第二次同名
- **Expect**：第二次返回 400 → `ValidationError`

### E-ERR-002 | P1 — 删除不存在的 dataset
- **Steps**：`delete_dataset("never_created")`
- **Expect**：抛 `ValidationError`

### E-ERR-003 | P1 — 后端未启动时连接错误
- **Pre**：关闭后端 fixture
- **Steps**：`client.list_agents(ds)`
- **Expect**：抛 `A2XConnectionError`（不是裸 `httpx.ConnectError`）

### E-ERR-004 | P2 — 超时
- **Pre**：构造 `timeout=0.001`
- **Steps**：`list_agents(ds)`
- **Expect**：`A2XConnectionError`（timeout 也归此类）

---

## 6. 后端返回结构稳定性 — `E-SCHEMA-*`

### E-SCHEMA-001 | P1 — `register_agent` 响应字段完整
- **Steps**：真实注册
- **Expect**：响应 JSON 至少含 `service_id, dataset, status`

### E-SCHEMA-002 | P1 — `update_agent` 响应含 `changed_fields` 和 `taxonomy_affected`
- **Steps**：真实 update
- **Expect**：两个字段存在且类型正确

### E-SCHEMA-003 | P1 — `list_agents` 每个 brief 含 `id,name,description`
- **Expect**：字段齐全；**不含** `metadata`（属于 single mode）

### E-SCHEMA-004 | P1 — `get_agent` metadata 保留原 Card
- **Steps**：register card X；get_agent；对比
- **Expect**：metadata 的每个字段与 X 相等（补全字段可多出，但原字段不丢失）

### E-SCHEMA-005 | P2 — 后端 schema 若增加新字段，SDK 不破坏
- **Mock/Mod**：让后端返回多一个未声明字段
- **Expect**：dataclass `from_dict` 丢弃；客户端继续工作

---

## 7. 性能回归 — `E-PERF-*`（nightly）

### E-PERF-001 | P2 — 并发 50 个 register 的吞吐
- **Steps**：异步 50 × register
- **Expect**：P95 < 1s 单次（本地后端）；无 race 导致的丢失

### E-PERF-002 | P2 — 1000 个 agent 数据集 `list_agents`
- **Pre**：预灌 1000 个
- **Expect**：< 2s 返回；无内存异常
