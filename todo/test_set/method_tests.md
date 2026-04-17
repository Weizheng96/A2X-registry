# 方法级集成测试

每个对外方法的契约测试。**Mock 后端**（`respx`），验证：参数转换、HTTP 报文构造、响应解析、所有权副作用、异常映射。

**Fixture**：`client = A2XClient(base_url="http://test", ownership_file=tmp_path/"owned.json")`；每个 case 用 fresh `tmp_path`。

---

## 1. `__init__` / 生命周期 — `M-INIT-*`

### M-INIT-001 | P0 — 构造不发 HTTP
- **Steps**：`A2XClient(base_url="http://test")` → 检查 `respx` 路由未命中
- **Expect**：零请求

### M-INIT-002 | P0 — `ownership_file=None` → 默认路径
- **Steps**：monkeypatch `Path.home()` 指向 tmp；`A2XClient("http://test")`
- **Expect**：client 构造成功；没有文件被创建（load 不创建）；随后 `register_agent` 成功后文件出现在 `<HOME>/.a2x_client/owned.json`

### M-INIT-003 | P0 — `ownership_file=False` → 纯内存
- **Steps**：构造后 register 一次
- **Expect**：无任何文件写入（monkeypatch `open` 断言对 `owned.json` 无写）

### M-INIT-004 | P0 — `ownership_file=Path` → 自定义路径
- **Steps**：传自定义路径
- **Expect**：register 后该路径出现文件

### M-INIT-005 | P0 — `api_key` 注入所有请求
- **Steps**：`A2XClient(..., api_key="KEY")`；call 任一方法
- **Expect**：每次 request 都带 `Authorization: Bearer KEY`

### M-INIT-006 | P0 — `api_key=None` 不加 header
- **Expect**：请求头无 `Authorization`

### M-INIT-007 | P0 — 上下文管理器关闭连接
- **Steps**：`with A2XClient(...) as c: c.create_dataset(...)` 退出后
- **Expect**：底层 `httpx.Client.is_closed is True`；再调 method → ClosedError

### M-INIT-008 | P1 — `timeout` 传给 httpx
- **Steps**：`A2XClient(timeout=3.0)`
- **Expect**：检查底层 `httpx.Client.timeout` 为 3.0

### M-INIT-009 | P1 — 加载既存 `owned.json`
- **Pre**：tmp 文件已包含 `{"http://test":{"ds":["s1"]}}`
- **Steps**：构造 client；不发任何 HTTP；call `update_agent("ds","s1",{"description":"x"})`
- **Expect**：不抛 `NotOwnedError`（已从文件恢复）；后端收到 PUT

---

## 2. `create_dataset` — `M-CD-*`

Endpoint：`POST /api/datasets`

### M-CD-001 | P0 — 默认参数的报文
- **Steps**：`client.create_dataset("ds1")`
- **Expect**：请求 body `{"name":"ds1","embedding_model":"all-MiniLM-L6-v2","formats":{"a2a":"v0.0"}}`

### M-CD-002 | P0 — `formats=None` 省略字段
- **Steps**：`create_dataset("ds1", formats=None)`
- **Expect**：body 中**不出现** `formats` 键（交由后端默认）

### M-CD-003 | P0 — 自定义 `formats`
- **Steps**：`formats={"a2a":"v0.0","generic":"v0.0"}`
- **Expect**：body 字段原样透传

### M-CD-004 | P0 — 自定义 `embedding_model`
- **Steps**：`embedding_model="bge-m3"`
- **Expect**：body 中 `embedding_model=="bge-m3"`

### M-CD-005 | P0 — 响应解析为 `DatasetCreateResponse`
- **Pre**：mock → 200 `{"dataset":"ds1","embedding_model":"all-MiniLM-L6-v2","formats":{"a2a":"v0.0"},"status":"created"}`
- **Expect**：返回值类型正确；字段逐一对上

### M-CD-006 | P1 — 400（如 name 非法）→ `ValidationError`
- **Pre**：mock → 400 `{"detail":"invalid name"}`
- **Expect**：抛 `ValidationError`；`e.payload["detail"]=="invalid name"`

### M-CD-007 | P2 — 名称含 URL 保留字符
- **参数化**：`["my ds", "my/ds", "my#ds", "数据集"]`
- **Expect**：每种名称都能被 `httpx` URL-encode 到 path（由于 create_dataset 名字在 body，此 case 只测 body JSON 序列化对 Unicode 的处理）

### M-CD-008 | P1 — 不修改 `_owned`
- **Steps**：success 后 `client._owned._data`
- **Expect**：为空（数据集层面不追踪所有权）

---

## 3. `register_agent` — `M-REG-*`

Endpoint：`POST /api/datasets/{ds}/services/a2a`

### M-REG-001 | P0 — 最小 Card 注册成功路径
- **Pre**：mock → 200 `{"service_id":"agent_planner_xxx","dataset":"ds1","status":"registered"}`
- **Steps**：`client.register_agent("ds1", {"protocolVersion":"0.0","name":"N","description":"D"})`
- **Expect**：
  - 请求 path = `/api/datasets/ds1/services/a2a`
  - body = `{"agent_card":{...},"persistent":true}` （`service_id=None` 被剔除）
  - 返回 `RegisterResponse(service_id="agent_planner_xxx",...)`

### M-REG-002 | P0 — 成功后加入 `_owned` + 写盘
- **Steps**：接 M-REG-001
- **Expect**：
  - `client._owned.contains("ds1","agent_planner_xxx")` True
  - tmp 文件内容 `{"http://test":{"ds1":["agent_planner_xxx"]}}`

### M-REG-003 | P0 — 指定 `service_id` 透传
- **Steps**：`register_agent("ds1", card, service_id="my_sid")`
- **Expect**：body 含 `"service_id":"my_sid"`

### M-REG-004 | P0 — `persistent=False` 透传
- **Steps**：`register_agent("ds1", card, persistent=False)`
- **Expect**：body 含 `"persistent":false`

### M-REG-005 | P0 — agent_card 含嵌套 / 自定义字段**完整透传**
- **In**：
  ```python
  card = {
      "protocolVersion":"0.0","name":"N","description":"D",
      "skills":[{"name":"s","description":"d"}],
      "provider":{"organization":"O","url":"U"},
      "agentTeamCount": 0,
      "custom_x": 42,
  }
  ```
- **Expect**：body `agent_card` 与 input **字节级相同**（含驼峰命名、自定义字段、保留 None 规则不适用）

### M-REG-006 | P1 — status="updated" 仍加入 `_owned`
- **Pre**：mock → 200 `{..., "status":"updated"}`
- **Steps**：register 一个已存在的 sid
- **Expect**：`_owned` 仍 add；文件更新

### M-REG-007 | P0 — 400 → `ValidationError`；**不**加入 `_owned`
- **Pre**：mock → 400
- **Expect**：抛异常；`_owned` 不变；文件不变

### M-REG-008 | P1 — `persistent=False` 下的 `_owned` 策略（见 D4）
- **Steps**：`register_agent(..., persistent=False)` 成功
- **Expect**：**规范需明确**。当前设计倾向"不加入 `_owned`"（因为下次启动后端上已不存在）；测试应根据最终决策断言。建议断言：**不加入**。

### M-REG-009 | P1 — 数据集不存在 → 400/404
- **Pre**：mock → 400 `{"detail":"dataset not found"}`
- **Expect**：抛 `ValidationError`

### M-REG-010 | P0 — agent_card 字段不被 SDK 重写（驳斥 D1）
- **Steps**：提供 `inputSchema` 等驼峰字段
- **Expect**：请求 body `agent_card` 中仍是 `inputSchema`（不变 `input_schema`）

---

## 4. `update_agent` — `M-UPD-*`

Endpoint：`PUT /api/datasets/{ds}/services/{sid}`

**Fixture**：pre-load `_owned` with `{"ds1": {"s1"}}`。

### M-UPD-001 | P0 — 基本：只传要改的字段
- **Steps**：`client.update_agent("ds1","s1",{"description":"newd"})`
- **Expect**：
  - path `/api/datasets/ds1/services/s1`
  - method PUT
  - body **恰好** `{"description":"newd"}`（无包装）
  - 响应 `PatchResponse.changed_fields==["description"]`

### M-UPD-002 | P0 — 多字段 + 嵌套对象
- **In**：`{"description":"d","skills":[...],"provider":{"organization":"O"}}`
- **Expect**：body 与 input 相等

### M-UPD-003 | P0 — 不在 `_owned` → `NotOwnedError`（**不发请求**）
- **Steps**：`client.update_agent("ds1","not_owned_sid",{})`
- **Expect**：
  - 抛 `NotOwnedError`
  - `respx` 无请求命中
  - 错误信息含 `"ds1"` 和 `"not_owned_sid"`

### M-UPD-004 | P0 — 空 fields dict
- **Steps**：`update_agent("ds1","s1",{})`
- **Expect**：**规范需明确**。建议：本地抛 `ValueError("fields must not be empty")`，不发请求。

### M-UPD-005 | P1 — 后端 400 → `ValidationError`
- **Pre**：mock → 400 `{"detail":"conflict"}`
- **Expect**：抛 `ValidationError`；`_owned` 不变

### M-UPD-006 | P1 — 后端 404 → `NotFoundError` + 清 `_owned`（见 D3）
- **Pre**：mock → 404
- **Steps**：`update_agent("ds1","s1",{"description":"x"})`
- **Expect**：
  - 抛 `NotFoundError`
  - `_owned.contains("ds1","s1")` **变为** False
  - 文件更新
- **状态**：取决于 D3 是否采纳；若不采纳则此 case 断言 `_owned` 仍含 s1。

### M-UPD-007 | P2 — 改名（`name`）字段冲突 → 400
- **Pre**：mock → 400
- **Expect**：抛 `ValidationError`

### M-UPD-008 | P2 — `taxonomy_affected=true` 字段正确解析
- **Pre**：mock response `{"...","taxonomy_affected":true}`
- **Expect**：`resp.taxonomy_affected is True`

---

## 5. `set_team_count` — `M-STC-*`

Endpoint：同 §4（复用 PUT）。

### M-STC-001 | P0 — 基本
- **Pre**：`_owned["ds1"]={"s1"}`
- **Steps**：`set_team_count("ds1","s1",3)`
- **Expect**：
  - path `/api/datasets/ds1/services/s1` + PUT
  - body **恰好** `{"agentTeamCount":3}`

### M-STC-002 | P0 — `count=0` 合法
- **Expect**：body `{"agentTeamCount":0}`；**不**被当成 falsy 剔除

### M-STC-003 | P0 — 不在 `_owned` → `NotOwnedError`
- **Expect**：抛；无请求

### M-STC-004 | P0 — 负数 → 本地 `ValueError`
- **Steps**：`set_team_count("ds1","s1",-1)`
- **Expect**：抛 `ValueError`；无请求；不查 `_owned`（或查也可，视实现顺序）

### M-STC-005 | P1 — 非 int → 本地 TypeError / ValueError
- **参数化**：`[1.5, "3", None, True]`
- **Expect**：`True` 由 Python 视为 int，接受（Python 语义）；其他类型 → 抛

### M-STC-006 | P1 — 字段名由 SDK 固定（`agentTeamCount`）
- **Steps**：尝试任何参数都不能改成别名
- **Expect**：body 中字段名始终是 `"agentTeamCount"`

### M-STC-007 | P2 — 底层复用 `update_agent` 的实现
- **Steps**：monkeypatch `update_agent`；调 `set_team_count`
- **Expect**：`update_agent` 被调用，参数 `(ds, sid, {"agentTeamCount": n})`

---

## 6. `list_agents` — `M-LST-*`

Endpoint：`GET /api/datasets/{ds}/services?mode=browse`

### M-LST-001 | P0 — 正常列表
- **Pre**：mock → 200 `[{"id":"s1","name":"N1","description":"D1"},{"id":"s2","name":"N2","description":"D2"}]`
- **Steps**：`list_agents("ds1")`
- **Expect**：长度 2；`[0].id=="s1"`；每个都是 `AgentBrief`

### M-LST-002 | P0 — 空列表
- **Pre**：mock → `[]`
- **Expect**：返回 `[]`

### M-LST-003 | P0 — 数据集不存在返回空（非 404）
- **Pre**：mock → 200 `[]`
- **Expect**：返回 `[]`，不抛（设计明确）

### M-LST-004 | P0 — 不查 `_owned`
- **Pre**：`_owned` 为空
- **Expect**：`list_agents` 成功，不抛 `NotOwnedError`

### M-LST-005 | P1 — URL query string 正确
- **Expect**：实际 URL `...?mode=browse`

### M-LST-006 | P2 — 响应中多余字段容忍
- **Pre**：mock 返回项含 `{"id":"s1","name":"N","description":"D","extra":"x"}`
- **Expect**：成功，`extra` 被 `AgentBrief.from_dict` 丢弃

---

## 7. `get_agent` — `M-GET-*`

Endpoint：`GET /api/datasets/{ds}/services?mode=single&service_id=...`

### M-GET-001 | P0 — a2a 成功路径
- **Pre**：mock → 200 `application/json` `{"id":"s1","type":"a2a","name":"N","description":"D","metadata":{"protocolVersion":"0.0","name":"N","description":"D","agentTeamCount":3}}`
- **Steps**：`get_agent("ds1","s1")`
- **Expect**：`AgentDetail.metadata["agentTeamCount"]==3`

### M-GET-002 | P0 — 404 → `NotFoundError`
- **Pre**：mock → 404
- **Expect**：抛

### M-GET-003 | P0 — 不查 `_owned`
- **Expect**：能查询其他 client 注册的 sid

### M-GET-004 | P1 — `application/zip` → `UnexpectedServiceTypeError`
- **Pre**：mock → 200 `application/zip` `b"PK\x03\x04..."`
- **Expect**：抛

### M-GET-005 | P1 — Content-Type 带参数的容忍（见 D5）
- **Pre**：mock → 200 `application/zip; charset=binary`
- **Expect**：仍走 zip 分支 → `UnexpectedServiceTypeError`（而非 `.json()` 崩溃）

### M-GET-006 | P1 — query params 正确
- **Expect**：URL `...?mode=single&service_id=s1`

### M-GET-007 | P2 — `raw` 字段保留完整原始 dict
- **Pre**：mock 额外字段 `"_internal":"x"`
- **Expect**：`detail.raw["_internal"]=="x"`

### M-GET-008 | P2 — service_id 含特殊字符 URL encode
- **参数化**：`["s/1","s 1","s#1","服务_1"]`
- **Expect**：实际 URL 正确编码

---

## 8. `deregister_agent` — `M-DER-*`

Endpoint：`DELETE /api/datasets/{ds}/services/{sid}`

**Fixture**：pre-load `_owned={"ds1":{"s1"}}`。

### M-DER-001 | P0 — 成功 → 200 `{"status":"deregistered"}`
- **Steps**：`deregister_agent("ds1","s1")`
- **Expect**：
  - DELETE 请求命中
  - 返回 `DeregisterResponse.status=="deregistered"`
  - `_owned.contains("ds1","s1") is False`
  - 文件更新

### M-DER-002 | P0 — 后端 200 `{"status":"not_found"}` 也清 `_owned`
- **Pre**：mock → 200 `{"service_id":"s1","status":"not_found"}`
- **Expect**：不抛；返回 response；`_owned.contains("ds1","s1") is False`（设计明示）

### M-DER-003 | P0 — 不在 `_owned` → `NotOwnedError`，不发请求
- **Expect**：抛；`respx` 无请求

### M-DER-004 | P1 — 后端 404 → `NotFoundError`（异常场景）+ 清 `_owned`（见 D3）
- **Pre**：mock → 404（在设计中应为 200 `"not_found"`，此处测后端异常行为）
- **Expect**：抛；若采纳 D3 则 `_owned` 仍被清

### M-DER-005 | P1 — user_config 来源 → 400 → `UserConfigDeregisterForbiddenError`
- **Pre**：mock → 400 `{"detail":"Cannot deregister user_config entries"}`
- **Expect**：抛 `UserConfigDeregisterForbiddenError`（也能被 `except ValidationError` 捕获）；`_owned` 不清（因为 API 场景下不会命中此分支，保守起见保留）

### M-DER-006 | P2 — 成功后再次 deregister → `NotOwnedError`
- **Expect**：第二次直接 local fail-fast

---

## 9. `delete_dataset` — `M-DEL-*`

Endpoint：`DELETE /api/datasets/{name}`

**Fixture**：pre-load `_owned={"ds1":{"s1","s2"},"ds2":{"s3"}}`。

### M-DEL-001 | P0 — 成功清整个数据集段
- **Pre**：mock → 200 `{"dataset":"ds1","status":"deleted"}`
- **Steps**：`delete_dataset("ds1")`
- **Expect**：
  - 返回 `DatasetDeleteResponse`
  - `_owned._data` 中 `ds1` 键**消失**
  - `ds2` 不动
  - 文件更新

### M-DEL-002 | P0 — 数据集不存在 → 400 → `ValidationError`
- **Pre**：mock → 400
- **Expect**：抛

### M-DEL-003 | P1 — 400 后是否清 `_owned[name]`（见 D6）
- **Steps**：接 M-DEL-002
- **Expect**：**规范需明确**。若采纳 D6 → `_owned` 中 `ds1` 清理；否则仍保留。

### M-DEL-004 | P0 — 不检查数据集级所有权
- **Pre**：`_owned` 为空
- **Steps**：`delete_dataset("ds_x")`（mock → 200）
- **Expect**：成功；无 `NotOwnedError`

### M-DEL-005 | P2 — 名称 URL encode
- **参数化**：Unicode / 空格名称
- **Expect**：path 正确编码

---

## 10. 参数转换通用规则 — `M-CV-*`

### M-CV-001 | P0 — body dict 的 `None` 字段被剔除
- **Steps**：`register_agent(ds, card, service_id=None, persistent=True)`
- **Expect**：body 不含 `"service_id"` 键

### M-CV-002 | P0 — URL path 的 URL encode 由 httpx 负责
- **Steps**：`register_agent("my ds", card)`
- **Expect**：实际 URL `http://test/api/datasets/my%20ds/services/a2a`

### M-CV-003 | P0 — 所有方法抛 `A2XError` 子类，不泄露 httpx 原生异常
- **Steps**：每个方法 mock → `httpx.ConnectError`
- **Expect**：抛 `A2XConnectionError`，不是裸 httpx 异常
