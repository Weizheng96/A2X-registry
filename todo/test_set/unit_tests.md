# 单元测试

针对 SDK 4 个内部模块：`ownership.py` / `transport.py` / `models.py` / `errors.py`。不依赖真实后端，**无网络**、**无真实磁盘**（用 `tmp_path`）。

---

## 1. `OwnershipStore` — `U-OWN-*`

Fixture：`tmp_path` 给每个 case 一个独立 JSON 文件路径；`base_url` 默认 `"http://test"`。

### U-OWN-001 | P0 — 首次加载：文件不存在
- **Pre**：`file_path` 指向不存在的文件
- **Steps**：`store = OwnershipStore(file_path, "http://test")`
- **Expect**：`store._data == {}`；构造不抛异常；文件仍不存在（load 不创建文件）

### U-OWN-002 | P0 — 加载：JSON 结构正常
- **Pre**：文件内容 `{"http://test": {"ds1": ["s1","s2"]}, "http://other": {"ds2": ["s3"]}}`
- **Steps**：用 `base_url="http://test"` 构造
- **Expect**：`store._data == {"ds1": {"s1","s2"}}`；不读入 `http://other` 段

### U-OWN-003 | P0 — 加载：JSON 损坏容忍
- **Pre**：文件内容 `"{not json"`
- **Steps**：构造 store
- **Expect**：不抛异常，`_data == {}`

### U-OWN-004 | P1 — 加载：空文件
- **Pre**：文件为 0 字节
- **Steps**：构造 store
- **Expect**：不抛，`_data == {}`

### U-OWN-005 | P1 — 加载：`base_url` 段不存在
- **Pre**：文件 `{"http://other": {...}}`
- **Steps**：构造 `base_url="http://test"`
- **Expect**：`_data == {}`，但文件内容不变（测 U-OWN-011 时验证）

### U-OWN-006 | P0 — `add` 写盘 + 内存一致
- **Steps**：`store.add("ds1","s1")` → 读回文件
- **Expect**：内存 `_data["ds1"] == {"s1"}`；文件内容反序列化为 `{"http://test":{"ds1":["s1"]}}`

### U-OWN-007 | P0 — `add` 幂等
- **Steps**：连续 `store.add("ds1","s1")` 三次
- **Expect**：`_data["ds1"] == {"s1"}`（集合去重）；文件最终只有一条

### U-OWN-008 | P0 — `contains` 命中/未命中
- **Pre**：`store.add("ds1","s1")` 后
- **Steps**：`contains("ds1","s1")` / `contains("ds1","s2")` / `contains("ds2","s1")`
- **Expect**：`True / False / False`

### U-OWN-009 | P0 — `remove` + 持久化
- **Pre**：`ds1` 下有 `s1,s2`
- **Steps**：`store.remove("ds1","s1")`
- **Expect**：`_data["ds1"] == {"s2"}`；文件同步更新

### U-OWN-010 | P1 — `remove` 不存在的 sid 静默
- **Steps**：`store.remove("ds_no","s_no")`（从未 add）
- **Expect**：不抛；`_data` 不变；**仍调用 `_save`**（可容忍冗余写盘）或**不调用**（优化）— 需与设计确认

### U-OWN-011 | P0 — 多 `base_url` 共存不互相破坏
- **Pre**：文件已有 `{"http://A":{"ds":["a"]}, "http://B":{"ds":["b"]}}`
- **Steps**：用 `base_url="http://A"` 构造 store，`store.add("ds","a2")`
- **Expect**：文件最终 `{"http://A":{"ds":["a","a2"]}, "http://B":{"ds":["b"]}}`，B 段完整保留

### U-OWN-012 | P0 — `remove_dataset` 清整个数据集
- **Pre**：`ds1`=`{s1,s2}`，`ds2`=`{s3}`
- **Steps**：`store.remove_dataset("ds1")`
- **Expect**：内存中 `ds1` 键**消失**（非 `set()`）；`ds2` 保留；文件同步

### U-OWN-013 | P1 — `remove_dataset` 不存在的 ds 静默
- **Steps**：`store.remove_dataset("never_existed")`
- **Expect**：不抛

### U-OWN-014 | P0 — 禁用持久化模式（`file_path=None`）
- **Steps**：`OwnershipStore(None, "http://test")`；`add("ds","s")`；`contains("ds","s")`
- **Expect**：第一步不读文件系统；第二步返回 True；磁盘上**没有**任何文件创建

### U-OWN-015 | P1 — 原子写使用 `.tmp + replace`
- **Steps**：monkeypatch `os.replace` 断言被调用；`os.rename`/直接写不应被调用
- **Expect**：`_save` 路径为先写 `<file>.tmp` 再 `os.replace`

### U-OWN-016 | P1 — 父目录自动创建
- **Pre**：`file_path = tmp_path/"a"/"b"/"owned.json"`，中间目录不存在
- **Steps**：`store.add("ds","s")` 触发首次 `_save`
- **Expect**：`a/b/` 被创建；`owned.json` 内容正确

### U-OWN-017 | P2 — 并发 `add` 线程串行（同一进程）
- **Pre**：`store` 单例
- **Steps**：10 条线程并发 `add("ds",f"s{i}")`
- **Expect**：最终 `_data["ds"]` 含全部 10 个 sid；文件最后一次落盘也应完整。**要求**：`OwnershipStore` 内部需加锁（见 [design_review.md D9](design_review.md#d9)）

---

## 2. `HTTPTransport` / `AsyncHTTPTransport` — `U-TR-*`

Mock：`respx.mock` 包住 `httpx.Client`。

### U-TR-001 | P0 — 构造不发请求
- **Steps**：`HTTPTransport("http://test", timeout=5)`
- **Expect**：`respx` 没有任何请求记录

### U-TR-002 | P0 — `api_key` 转 Authorization header
- **Pre**：`headers={"Authorization":"Bearer xxx"}`
- **Steps**：`.request("GET","/foo")` → 捕获 real request
- **Expect**：请求头 `Authorization: Bearer xxx`

### U-TR-003 | P0 — 2xx 原样返回 Response
- **Pre**：mock `GET /foo` → 200 `{"ok":true}`
- **Steps**：`resp = .request("GET","/foo")`
- **Expect**：`resp.status_code == 200`；`resp.json() == {"ok":true}`

### U-TR-004 | P0 — 404 → `NotFoundError`
- **Pre**：mock `/foo` → 404 `{"detail":"not found"}`
- **Steps**：`.request("GET","/foo")`
- **Expect**：`pytest.raises(NotFoundError) as e`；`e.value.status_code == 404`；`e.value.payload == {"detail":"not found"}`

### U-TR-005 | P0 — 400/422 → `ValidationError`
- **参数化**：`[400, 422]`
- **Pre**：mock → 对应状态码
- **Expect**：抛 `ValidationError`

### U-TR-006 | P0 — 400 含 `"user_config"` → `UserConfigDeregisterForbiddenError`
- **Pre**：mock → 400 `{"detail":"Cannot deregister user_config entries"}`
- **Expect**：抛 `UserConfigDeregisterForbiddenError`（也是 `ValidationError` 子类，`except ValidationError` 能捕获）

### U-TR-007 | P0 — 5xx → `ServerError`
- **参数化**：`[500, 502, 503]`
- **Expect**：抛 `ServerError`

### U-TR-008 | P0 — `httpx.ConnectError` → `A2XConnectionError`
- **Pre**：mock 抛 `httpx.ConnectError`
- **Expect**：抛 `A2XConnectionError`；`__cause__` 保留原 `ConnectError`

### U-TR-009 | P1 — `httpx.TimeoutException` → `A2XConnectionError`
- **Expect**：同上（设计把超时也归类为 ConnectionError）

### U-TR-010 | P1 — JSON body 传递正确
- **Steps**：`.request("POST","/x", json={"a":1})`
- **Expect**：request body 原文 `{"a":1}`，`Content-Type: application/json`

### U-TR-011 | P1 — query params 传递正确
- **Steps**：`.request("GET","/x", params={"mode":"single","service_id":"s"})`
- **Expect**：实际 URL 含 `?mode=single&service_id=s`

### U-TR-012 | P1 — `close()` 关闭底层 httpx.Client
- **Steps**：`t.close()`；再 `.request(...)` 
- **Expect**：第二次调用抛 `httpx` 或包装过的异常（底层 client 已关闭）

### U-TR-013 | P1 — 上下文管理器
- **Steps**：`with HTTPTransport(...) as t: t.request(...)` 之后
- **Expect**：退出时 `_client.is_closed is True`

### U-TR-014 | P0 — Async 版行为等价
- **参数化**：对 U-TR-001 ~ U-TR-013 每个同步 case 做 async 镜像
- **Steps**：用 `respx.mock` + `httpx.AsyncClient`；`await t.request(...)`
- **Expect**：相同状态码 → 相同异常类型；`aclose()` 生效

### U-TR-015 | P2 — `_wrap_http_error` 抽为模块函数两端共用
- **Steps**：分别从 `HTTPTransport` 和 `AsyncHTTPTransport` 触发同一 4xx 条件
- **Expect**：抛出的异常**类型与 payload 结构完全一致**（回归锚点）

### U-TR-016 | P1 — 响应体非 JSON（如空体、HTML）时 payload 处理
- **Pre**：mock 500 + `text/html` body `"<html>500</html>"`
- **Expect**：`ServerError.payload` 为 `None` 或 `{"_raw": "<html>500</html>"}`（规范需在设计中明确）

---

## 3. `models.py` — `U-MD-*`

无网络、无磁盘。纯 dataclass 测试。

### U-MD-001 | P0 — `DatasetCreateResponse.from_dict` 基本
- **In**：`{"dataset":"d","embedding_model":"m","formats":{"a2a":"v0.0"},"status":"created"}`
- **Expect**：所有字段映射，`isinstance DatasetCreateResponse`

### U-MD-002 | P0 — `from_dict` 容忍多余字段（forward-compat）
- **In**：加 `"extra_field":"ignored"` 到 U-MD-001 的 input
- **Expect**：不抛；返回实例；多余字段不出现在 dataclass 属性上

### U-MD-003 | P0 — `from_dict` 缺少必要字段
- **In**：去掉 `status`
- **Expect**：抛 `TypeError` / `KeyError`（明确契约）

### U-MD-004 | P0 — `PatchResponse.changed_fields` 是 list[str]
- **In**：`{...,"changed_fields":["description","skills"],"taxonomy_affected":true}`
- **Expect**：字段为 Python list 且元素为 str

### U-MD-005 | P0 — `AgentDetail.metadata` 保留完整 card（含 `agentTeamCount`）
- **In**：mock 响应 `{"id":"s","type":"a2a","name":"N","description":"D","metadata":{"protocolVersion":"0.0","name":"N","description":"D","agentTeamCount":3}}`
- **Expect**：`detail.metadata["agentTeamCount"] == 3`

### U-MD-006 | P1 — `AgentDetail.raw` 保留原始 dict
- **Expect**：`detail.raw` 是未经过滤的完整 dict（包含 U-MD-002 中被剔除的 extra 字段）

### U-MD-007 | P1 — `AgentBrief` 列表解析
- **In**：`[{"id":"s1","name":"N","description":"D"},{"id":"s2","name":"N2","description":"D2"}]`
- **Steps**：`[AgentBrief.from_dict(x) for x in input]`
- **Expect**：长度 2；类型正确

### U-MD-008 | P1 — `RegisterResponse.status` 枚举值
- **参数化**：`["registered","updated"]`
- **Expect**：两者都能 from_dict 成功

### U-MD-009 | P1 — `DeregisterResponse.status` 枚举值
- **参数化**：`["deregistered","not_found"]`
- **Expect**：同上

### U-MD-010 | P2 — dataclass 不可变或可变一致性
- **Steps**：尝试 `resp.status = "x"`
- **Expect**：若 `@dataclass(frozen=True)` 则抛 `FrozenInstanceError`；否则成功。规范需明确选择。

---

## 4. `errors.py` — `U-ER-*`

### U-ER-001 | P0 — 类层级
- **Steps**：`issubclass(NotFoundError, A2XHTTPError)`；`issubclass(A2XHTTPError, A2XError)`；`issubclass(UserConfigDeregisterForbiddenError, ValidationError)`；`issubclass(NotOwnedError, A2XError)`；`not issubclass(NotOwnedError, A2XHTTPError)`
- **Expect**：全部 True / True / True / True / True

### U-ER-002 | P0 — `NotOwnedError` 无 `status_code`
- **Steps**：`e = NotOwnedError("msg", dataset="d", service_id="s")`
- **Expect**：`e.status_code is None`；`e.payload is None`（或不存在该属性）

### U-ER-003 | P0 — `A2XHTTPError` 携带 `status_code` / `payload`
- **Steps**：`NotFoundError("msg", status_code=404, payload={"detail":"x"})`
- **Expect**：属性可访问

### U-ER-004 | P1 — `except A2XError` 能捕获所有子类
- **参数化**：每个子类
- **Steps**：`try: raise Cls(...); except A2XError: pass`
- **Expect**：不漏

### U-ER-005 | P1 — `except ValidationError` 捕获 `UserConfigDeregisterForbiddenError`
- **Expect**：设计中明确声明，必须成立

### U-ER-006 | P2 — 异常 `repr` / `str` 可读
- **Expect**：`str(NotOwnedError(...))` 含 dataset/service_id；便于日志排查
