# 异步客户端测试

针对 `AsyncA2XClient`。重点：**与同步版的对称性**、**并发正确性**、**事件循环不被阻塞**。

**基础约定**：
- 每个测试用 `@pytest.mark.asyncio`
- Mock 用 `respx.mock` + `httpx.AsyncClient`
- Fixture：`client = AsyncA2XClient(base_url="http://test", ownership_file=tmp_path/"owned.json")`
- 每个 case 结束 `await client.aclose()`

---

## 1. 签名对称性 — `A-PAR-*`

### A-PAR-001 | P0 — 方法集完全一致
- **Steps**：
  ```python
  sync_methods = {m for m in dir(A2XClient) if not m.startswith("_")}
  async_methods = {m for m in dir(AsyncA2XClient) if not m.startswith("_")}
  ```
- **Expect**：`sync_methods - {"close","__enter__","__exit__"} == async_methods - {"aclose","__aenter__","__aexit__"}`

### A-PAR-002 | P0 — 每个方法签名一致（参数名、默认值、annotations）
- **参数化**：对 8 个业务方法
- **Steps**：`inspect.signature(A2XClient.method).parameters == inspect.signature(AsyncA2XClient.method).parameters`
- **Expect**：参数完全一致（除 self）

### A-PAR-003 | P0 — 返回类型一致（同 dataclass）
- **Steps**：`get_type_hints(...).get("return")` 对比
- **Expect**：类型名一致（AsyncA2XClient 版允许 `Awaitable[T]` 外壳）

### A-PAR-004 | P0 — 每个 async 方法是 coroutine
- **参数化**：8 方法
- **Steps**：`inspect.iscoroutinefunction(AsyncA2XClient.method)`
- **Expect**：True

### A-PAR-005 | P0 — 异常层级共用
- **Steps**：触发每类异常（mock 对应状态码）
- **Expect**：sync 和 async 抛同一个类（`A2XError` 子类）

---

## 2. 功能镜像 — `A-MIR-*`

对 `method_tests.md` 的 P0 案例做异步镜像抽样：

### A-MIR-001 | P0 — create_dataset (异步版镜像 M-CD-001)
- **Steps**：`await client.create_dataset("ds1")`
- **Expect**：同 M-CD-001 的 body 和响应

### A-MIR-002 | P0 — register_agent + 写盘 (镜像 M-REG-001/002)
- **Steps**：`resp = await client.register_agent("ds1", card)`
- **Expect**：
  - `_owned.contains("ds1", resp.service_id)` True
  - 文件已更新
  - 请求 body 同 M-REG-001

### A-MIR-003 | P0 — update_agent NotOwnedError 本地 fail-fast (镜像 M-UPD-003)
- **Steps**：`await client.update_agent("ds1","not_owned",{})`
- **Expect**：`NotOwnedError`；`respx` 无请求；**不产生 await 任务悬挂**

### A-MIR-004 | P0 — deregister_agent 成功 + 清 _owned (镜像 M-DER-001)

### A-MIR-005 | P0 — delete_dataset 清数据集段 (镜像 M-DEL-001)

### A-MIR-006 | P0 — list_agents 空列表 (镜像 M-LST-002)

### A-MIR-007 | P0 — get_agent 404 (镜像 M-GET-002)

### A-MIR-008 | P0 — set_team_count 负数 ValueError (镜像 M-STC-004)

### A-MIR-009 | P1 — ConnectionError 映射 (镜像 U-TR-008 async)

---

## 3. 生命周期 — `A-LC-*`

### A-LC-001 | P0 — `async with` 上下文
- **Steps**：
  ```python
  async with AsyncA2XClient(...) as c:
      await c.list_agents("ds1")
  ```
- **Expect**：退出后 `c._transport._client.is_closed is True`

### A-LC-002 | P0 — `aclose()` 幂等
- **Steps**：`await c.aclose(); await c.aclose()`
- **Expect**：第二次不抛

### A-LC-003 | P1 — 未关闭泄露检测（pytest-asyncio）
- **Steps**：构造但不关闭
- **Expect**：pytest-asyncio 能识别（或显式断言 warning）

### A-LC-004 | P1 — 关闭后调方法 → 抛
- **Expect**：抛 `A2XError`（或包装过的 httpx 已关闭异常）

---

## 4. 并发 — `A-CC-*`

### A-CC-001 | P0 — `asyncio.gather` 并发 `register_agent`
- **Steps**：
  ```python
  cards = [card_i for i in range(10)]
  resps = await asyncio.gather(*[
      client.register_agent("ds1", c) for c in cards
  ])
  ```
- **Expect**：
  - 10 个都成功
  - `_owned._data["ds1"]` 含所有 10 个 sid
  - **文件最终状态包含所有 10 个**（这才是 D9 的回归锚点）

### A-CC-002 | P0 — 并发 `set_team_count`
- **Pre**：pre-register 10 个
- **Steps**：`gather(*[set_team_count(ds,sid,i) for ...])`
- **Expect**：全部 200；`_owned` 和文件不变；无 race

### A-CC-003 | P0 — 并发 register + deregister 最终一致
- **Steps**：交错 register 和 deregister
- **Expect**：`_owned` 和文件最终与逻辑一致（没有幽灵 sid）

### A-CC-004 | P1 — 并发写盘不阻塞事件循环
- **Steps**：
  - 在一个 task 里 `while True: await asyncio.sleep(0.01); tick_count += 1`
  - 同时开 50 条 `register_agent` 并发
- **Expect**：`tick_count` 增长不被明显拖慢（与无 register 的 baseline 对比，差距 < 50%）
- **验证**：证明 `to_thread(store._save)` 真的把 I/O 分离出主循环

### A-CC-005 | P1 — `contains` 同步调用不阻塞
- **Steps**：监测 `update_agent` 前置检查时的事件循环 tick
- **Expect**：`contains` 是纯内存操作 → 无 `to_thread` 调用开销（性能优化正确性）

---

## 5. 共享 OwnershipStore — `A-SH-*`

### A-SH-001 | P0 — 同一进程一个 sync + 一个 async 指向同一文件
- **Pre**：都用同一 `ownership_file`
- **Steps**：
  1. sync `register_agent("ds1", card_a)` → 成功
  2. 构造 async client（同文件）
  3. `await async.update_agent("ds1", card_a.service_id, {"description":"x"})`
- **Expect**：async client 加载到 sync 写入的记录；update 成功不抛 NotOwned

### A-SH-002 | P1 — async 写入后 sync 能读到
- **Steps**：async register → 关闭 async client → 构造 sync client → update
- **Expect**：sync 能操作 async 刚注册的 sid

---

## 6. 错误传播 — `A-ERR-*`

### A-ERR-001 | P0 — coroutine 中的异常从 `await` 正确抛出
- **Pre**：mock → 500
- **Steps**：
  ```python
  with pytest.raises(ServerError):
      await client.create_dataset("ds1")
  ```
- **Expect**：pytest 捕获到

### A-ERR-002 | P0 — `gather` 中一个失败不影响其他
- **Steps**：
  ```python
  results = await asyncio.gather(
      client.register_agent(...),   # ok
      client.register_agent(...),   # 400 mocked
      client.register_agent(...),   # ok
      return_exceptions=True,
  )
  ```
- **Expect**：3 个结果中第 2 个是 `ValidationError`；第 1、3 是正常 response；`_owned` 只加入成功的 2 个

### A-ERR-003 | P1 — cancellation 不破坏 `_owned`
- **Steps**：启动 register task → `task.cancel()` 在 HTTP 往返中
- **Expect**：`_owned` 不出现半完成状态（要么 add 成功要么没加入，没有"请求已发但记录没写"的局面）
- **验证方式**：先让 `respx` 延迟 0.5s 响应，100ms 后 cancel → 检查 `_owned` 和文件
- **备注**：可能需要 `shield` 或在 `register_agent` 里处理 CancelledError；若设计未定，此 case 先标记 expected: "record **not** added, since HTTP never completed"

---

## 7. 与 `pytest-asyncio` / `asyncio.run` 集成 — `A-INT-*`

### A-INT-001 | P1 — `asyncio.run(main())` 嵌套使用
- **Steps**：顶层 `asyncio.run(...)`；main 中用 `async with AsyncA2XClient`
- **Expect**：无 `RuntimeError: asyncio.run() cannot be called from a running event loop`

### A-INT-002 | P1 — 在 `uvloop` 下运行（可选）
- **Pre**：`asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())`
- **Expect**：所有 A-MIR-* 通过

### A-INT-003 | P2 — 兼容 FastAPI dependency 场景
- **Steps**：建一个最小 FastAPI 端点，其 dependency 用 AsyncA2XClient
- **Expect**：不阻塞 uvicorn worker
