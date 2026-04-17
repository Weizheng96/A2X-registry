# 边界与异常场景

专门覆盖**容易被忽视**的边界：文件系统异常、URL 编码、Unicode、并发竞争、大负载、独立打包、SDK 独立性校验。

---

## 1. URL 编码 — `X-URL-*`

`httpx` 对 path 参数自动编码，但需验证。

### X-URL-001 | P1 — dataset 名含空格
- **Steps**：`register_agent("my team", card)` mock
- **Expect**：实际 path `/api/datasets/my%20team/services/a2a`

### X-URL-002 | P1 — dataset 名含中文
- **参数化**：`["研究团队", "计划组"]`
- **Steps**：任意方法
- **Expect**：UTF-8 百分号编码（`%E7%A0%94...`）

### X-URL-003 | P1 — service_id 含 `/`（路径穿越隐患）
- **Steps**：`get_agent("ds","s/../x")`
- **Expect**：实际 URL `.../services?mode=single&service_id=s%2F..%2Fx`；**不**使其变成 `/services/s/../x`。验证查询字符串形式绕开路径穿越。

### X-URL-004 | P2 — service_id 空串
- **Steps**：`update_agent("ds","",{})`
- **Expect**：**规范需明确**。建议 local 抛 `ValueError("service_id must not be empty")`，不发请求。

### X-URL-005 | P2 — dataset 空串
- **Expect**：同 X-URL-004，建议本地抛

### X-URL-006 | P2 — 超长 service_id（> 200 字符）
- **Steps**：生成 500 字符 sid
- **Expect**：能发送；后端可能 400；SDK 不本地限制长度

---

## 2. 文件系统异常 — `X-IO-*`

### X-IO-001 | P0 — `_save` 遇到只读目录
- **Pre**：tmp_path 的父目录 chmod `0o555`
- **Steps**：register_agent 成功 → `_save` 失败
- **Expect**：根据 D8 决议：
  - 若 SDK 降级为 warning → 检查 `warnings.catch_warnings()` 捕获
  - 若 SDK 抛 OSError → register_agent 抛；但**后端已成功注册**。规范需明确
- **备注**：Windows 下 chmod 失效；此 case 用 monkeypatch `open` 抛 OSError 代替

### X-IO-002 | P1 — 磁盘满
- **Pre**：monkeypatch `os.replace` 抛 `OSError(ENOSPC)`
- **Steps**：register_agent
- **Expect**：同 X-IO-001

### X-IO-003 | P1 — `owned.json` 被外部进程同时修改（race snapshot）
- **Steps**：
  1. client 启动加载
  2. 外部进程改写文件（添加 ds_ext 段）
  3. client 调 `register_agent` 触发 `_save`
- **Expect**：设计是"读→改→写"，所以外部 ds_ext 会被保留（因为 save 时先读）；验证这个 invariant

### X-IO-004 | P1 — `owned.json` 带未来 schema（见 D11）
- **Pre**：tmp 文件 `{"schema_version":2,"data":{...}}`
- **Steps**：构造 client
- **Expect**：不崩溃；fallback 到空 `_data` 或按 v1 解析已知字段。规范需明确。

### X-IO-005 | P2 — 文件是符号链接
- **Pre**：`owned.json` 是指向其他文件的 symlink
- **Expect**：`_save` 不破坏 symlink（原子替换应作用于链接目标；或设计明确只支持普通文件）

### X-IO-006 | P2 — 权限：`.tmp` 文件残留
- **Pre**：monkeypatch `os.replace` 抛异常但 `.tmp` 已写
- **Steps**：第二次 `_save` 是否能覆盖残留 `.tmp`
- **Expect**：能正常工作（或 SDK 定期清理 `.tmp`）

---

## 3. 并发竞争 — `X-CC-*`

### X-CC-001 | P0 — 同进程多线程 `register_agent` 到同 OwnershipStore
- **Steps**：10 线程 × `register_agent` 到同一 client
- **Expect**：
  - 10 个都成功
  - `_owned._data["ds"]` 集合大小 10
  - **文件最终**反序列化含全部 10 个
- **验证**：D2/D9 的加锁实现是否生效

### X-CC-002 | P0 — 两个进程（不同 base_url 段）同文件
- **Steps**：subprocess A 和 B，各 `base_url=http://a` 和 `http://b`；各 register
- **Expect**：最终文件两段共存，无互相覆盖
- **备注**：此 case 对 RMW 友好（不同段）；D2 的问题主要在**同段**并发

### X-CC-003 | P1 — 两个进程同 base_url 同文件（D2 直接复现）
- **Steps**：subprocess A 和 B 同时 register
- **Expect**：若采纳文件锁方案 → 最终文件含两者；若未采纳 → **预期失败，记录已知缺陷**
- **备注**：此 case 是 D2 的回归锚点；是否 pass 取决于设计修复

### X-CC-004 | P1 — 异步内并发 `_save`（单进程多协程）
- **见 [async_tests.md A-CC-004](async_tests.md#a-cc-004)**

---

## 4. 大负载 — `X-SZ-*`

### X-SZ-001 | P2 — 超大 agent_card（~1 MB）
- **Steps**：注册一个含 500 个 skills 的 card
- **Expect**：不本地截断；HTTP 成功（后端有可能 400 body 太大，此时抛 ValidationError）；`_save` 正常

### X-SZ-002 | P2 — 大批量 register（1000 agent）
- **见 [e2e_tests.md E-PERF-002](e2e_tests.md#e-perf-002)**

### X-SZ-003 | P2 — `owned.json` 累计 10000 sid
- **Pre**：手动 seed 10000 条
- **Steps**：构造 client（load） / register 一个（触发 save）
- **Expect**：load < 500ms，save < 200ms（经验值，可调）；内存 < 50 MB

---

## 5. Unicode / 字符集 — `X-UC-*`

### X-UC-001 | P1 — agent_card 名含 emoji
- **In**：`{"name":"🤖 Planner","description":"D"}`
- **Expect**：JSON body 正确 UTF-8 编码；`get_agent` 返回相同

### X-UC-002 | P1 — description 含换行和 Tab
- **In**：`"line1\nline2\t\ttab"`
- **Expect**：原样保留（JSON `\n` `\t` 转义）

### X-UC-003 | P2 — `owned.json` 存含 Unicode 的 dataset 名
- **Expect**：文件用 UTF-8 写入，下次加载正确

---

## 6. SDK 独立性 — `X-DEP-*`

对应 `client_design.md §0` 的关键约束。

### X-DEP-001 | P0 — `src/client/` 目录无 `from src.xxx` import
- **Steps**：`grep -r "from src\." src/client/ | grep -v "from src\.client"`
- **Expect**：空输出；**CI 必跑**

### X-DEP-002 | P0 — 依赖仅 `httpx` + stdlib
- **Steps**：分析 `src/client/` 所有 .py 的 import 语句
- **Expect**：白名单：`httpx`, `asyncio`, `json`, `pathlib`, `dataclasses`, `typing`, `os`, `threading`, `warnings`, `urllib`, 其他 stdlib；**无** `pydantic`, `fastapi`, `chromadb` 等

### X-DEP-003 | P1 — 独立打包后可 import
- **Steps**：
  1. `cd src/client && python -m build`
  2. 在**全新 venv** 只 `pip install httpx dist/a2x_registry_client-*.whl`
  3. `python -c "from a2x_client import A2XClient; A2XClient('http://x')"`
- **Expect**：无 `ModuleNotFoundError`

### X-DEP-004 | P2 — 独立包类型检查通过（`mypy src/client/`）
- **Expect**：0 errors；`py.typed` 文件存在

---

## 7. 安全边界 — `X-SEC-*`

### X-SEC-001 | P1 — `api_key` 不出现在异常消息 / 日志
- **Steps**：触发异常；捕获异常 repr
- **Expect**：`api_key` 字符串不在 error.args / payload / __str__ 中

### X-SEC-002 | P1 — service_id 不被路径穿越
- **见 X-URL-003**

### X-SEC-003 | P2 — `owned.json` 只读权限（Unix）
- **Pre**：第一次 write 后文件 mode
- **Expect**：`0o600`（owner 读写，其他人无权限）；首次创建目录也应 `0o700`

---

## 8. 响应内容异常 — `X-RESP-*`

### X-RESP-001 | P1 — 后端返回 200 但 body 不是 JSON
- **Pre**：mock → 200 body `"OK"` (text/plain)
- **Steps**：`create_dataset`
- **Expect**：抛 `A2XError`（包含原 body 作为线索），不崩

### X-RESP-002 | P1 — 后端返回 200 空 body
- **Pre**：mock → 200 body `""`
- **Expect**：抛或返回带 `None` 字段的 dataclass；**规范需明确**

### X-RESP-003 | P1 — 200 but missing 必填字段
- **Pre**：mock → 200 `{"dataset":"d"}`（缺 `status`）
- **Expect**：抛（见 U-MD-003）

### X-RESP-004 | P2 — 5xx 带非 JSON body
- **Pre**：mock → 503 body `"<html>...</html>"`
- **Expect**：抛 `ServerError`；payload 为 None 或 raw text（见 U-TR-016 规范）

### X-RESP-005 | P2 — 3xx 重定向
- **Pre**：mock → 301 → 200
- **Expect**：httpx 默认不跟随重定向；行为取决于 httpx.Client(follow_redirects=False)；SDK 应显式禁用

---

## 9. 回归 / 互操作 — `X-REG-*`

### X-REG-001 | P1 — 本地后端 service.json 可被 CLI 读取
- **Steps**：SDK 注册后，用 `python -m src.register --status` 能看到该服务
- **Expect**：SDK 注册路径与 CLI register 路径落盘格式一致

### X-REG-002 | P2 — 后端 register-config 改动后 SDK 行为
- **Steps**：
  1. `create_dataset(ds, formats={"a2a":"v0.0"})`
  2. 直接 POST register-config 改为 `{"generic":"v0.0"}`（禁用 a2a）
  3. `register_agent(ds, card)`
- **Expect**：后端 400 → `ValidationError`

---

## 10. 配置矩阵 — `X-CFG-*`

### X-CFG-001 | P2 — base_url 有/无尾 `/`
- **参数化**：`["http://test","http://test/"]`
- **Expect**：两者行为一致（相对路径拼接无 `//`）

### X-CFG-002 | P2 — base_url 带路径前缀
- **参数化**：`"http://test/api-prefix"`
- **Steps**：`create_dataset`
- **Expect**：最终 URL 是 `http://test/api-prefix/api/datasets` 还是 `http://test/api/datasets`？**规范需明确** SDK 是"绝对 path 拼" 还是"相对 path 拼"。建议用 httpx 的 `httpx.URL.join` 语义并文档化。

### X-CFG-003 | P2 — base_url 是 HTTPS
- **Expect**：与 HTTP 等价（除连接建立开销）

---

## 11. 可观测性 — `X-OBS-*`

### X-OBS-001 | P2 — 默认 logger 不输出敏感数据
- **Steps**：配置 `logging.DEBUG`；触发 register
- **Expect**：log 不含 `api_key`、不含完整 agent_card（或只含 name）

### X-OBS-002 | P2 — 异常消息可定位
- **Steps**：每类异常 `str(e)` 包含 method + path + status_code
- **Expect**：便于一行日志调错
