# Cluster 分布式同步模块设计

让多个 A2X 注册中心实例组成**全连接（full-mesh）**集群，彼此复制各自的注册表：查询任意一个实例都能拿到全网所有实例的服务；某实例离开或宕机后，其余实例靠直链 HOLD 超时删除它的数据。

模块是 **opt-in**：注册中心默认单机运行，直到执行 `a2x-registry cluster init` 生成 `cluster_state.json` 才启用。未启用时所有 `/api/cluster/*` 返回 404，读路径与单机完全一致。

> **部署前提：全连接。** 本模块采用直接广播、**不做转发（no relay）**：一条记录由它的来源实例直接发给所有 peer。因此每个成员必须与其它每个成员都建立会话（两两 `add-peer`）。非全连接（如链形 A–B–C 而 A、C 未直连）下，未直连的节点收不到对方的数据。

---

## 1. 流程逻辑

### 1.1 整体介绍

- **拓扑**：全连接。每个成员与其它每个成员都有一条直连会话，互为直接 peer。
- **一致性**：AP / 最终一致。直接广播复制 + 最后写入者胜（LWW）版本判新，无共识 / 多数派。
- **写入**：origin-only。每条记录只在它的来源实例可写，其他实例持有**只读、仅内存**的副本。全局身份键 = `(dataset, origin_id, service_id)`，所以两个实例注册同名服务（同 `service_id`）也不会冲突，且不存在写-写冲突。
- **版本**：`(updated_at_ms, node_id)`，逐记录单调递增（本机时钟回拨也不会让它变小），按字典序比较。
- **传播**：本地 CRUD 后把增量**直发所有 peer**；入站只按 LWW 落库，**不转发**。全连接下来源的一次广播即覆盖全网，天然无环、无需水平分割。
- **删除**：墓碑携带版本，按 LWW 压过更旧的存活值；保留 `tombstone_retention`（= `hold_timeout + keepalive_interval`）后 GC。
- **失活驱逐**：**以注册中心节点为单位**，统一走 HOLD 一条路径。全连接下来源必是某条直连会话——该会话因 `rm-peer` 或 HOLD 超时断开，就是驱逐其全部记录的信号。驱逐后对该来源设一段**抑制冷却**，防止反熵在其它节点尚未驱逐时把它的记录重新拉回来（详见 §1.2 失活时序）。

三层职责：

| 层 | 职责 | 关键类型 |
|----|------|----------|
| L1 会话 | OPEN 握手 + 逐 namespace 鉴权、peer 表、keepalive/HOLD | `Peer`、`auth_handshake` |
| L2 存活 | 直链 keepalive 续 HOLD；静默超时则断会话并驱逐其记录 | `KeepaliveMonitor` |
| L3 复制 | 本地 CRUD 后直发所有 peer、入站按 LWW 落库、周期反熵兜底 | `SyncEnvelope`、`AntiEntropySweeper` |

**数据模型（同步信封 `cluster/envelope.py`）**

```
dataset: str
service_id: str
origin_id: str                 # 所属节点；全局键 = (dataset, origin_id, service_id)
version: (updated_at_ms, node_id)
tombstone: bool
payload: {"entry": <RegistryEntry>, "wrapped": <列表输出行>} | None
```

**持久化状态（`cluster_state.json`，`cluster/state.py`）** —— 唯一落盘的集群状态；foreign 副本不落盘（重连后重新同步）。

```
node_id: str                   # 稳定 UUID 身份
version_clock: int             # 最后发出的版本时间戳(ms)
local_versions: {dataset\0sid: [ms, node_id]}
tombstones:     {dataset\0sid: {version, deleted_at_ms}}
```

位置：`A2X_REGISTRY_CLUSTER_STATE`，否则 `<A2X_REGISTRY_HOME>/cluster_state.json`。

**配置（`cluster/config.py` 默认值）**

| 字段 | 默认 | 含义 |
|------|------|------|
| `keepalive_interval` | 10s | 直链保活广播周期 |
| `hold_timeout` | 30s | 直链静默多久后断会话并驱逐其记录 |
| `anti_entropy_interval` | 20s | 反熵对账 + GC 周期 |
| `http_timeout` | 5s | 单次对端调用超时 |

- 派生量 `tombstone_retention = hold_timeout + keepalive_interval`（默认 40s）：墓碑保留期，同时也是驱逐后的抑制冷却时长——保证所有 peer 都已检测到失活并驱逐其副本之后，才允许 GC / 解抑制，从而杜绝复活。
- 以上每项都可在启动 server 前用 `A2X_REGISTRY_CLUSTER_<字段大写>` 环境变量覆盖（如 `A2X_REGISTRY_CLUSTER_HOLD_TIMEOUT=60`，由 `ClusterConfig.from_env` 读取；非法值回退默认）。`A2X_REGISTRY_CLUSTER_ADVERTISE` 则设置对端访问本实例所用的 base URL。

### 1.2 时序图

每一步标注“做了什么【对应接口】”。

**建链 + 初始全量对账**（A 主动连接 B）

```mermaid
sequenceDiagram
    participant A as 注册中心 A
    participant B as 注册中心 B
    A->>B: 发起同步会话：声明身份、要同步的命名空间、鉴权 token【POST /sessions】
    B->>B: 逐命名空间鉴权；启用鉴权时签发会话令牌
    B-->>A: 返回身份、接受的命名空间、会话令牌
    A->>B: 请求版本摘要：看对端有哪些记录及版本【GET /digest】
    B-->>A: 返回 {记录键: 版本} 摘要
    A->>B: 拉取我方缺失/过时的记录【POST /pulls】
    B-->>A: 返回完整记录信封；A 按 LWW 落库
    A->>B: 推送我方比对端新的记录【POST /updates】
    Note over A,B: 双方收敛，进入稳态
```

**本地 CRUD 增量直发（全连接，无转发）**

```mermaid
sequenceDiagram
    participant Ag as A 上的智能体
    participant A as 注册中心 A
    participant B as 注册中心 B
    participant C as 注册中心 C
    Ag->>A: 注册 / 更新 / 注销服务
    A-->>Ag: 200 OK（本地写完立即返回，不等同步）
    A->>B: 直接推送该变更增量【POST /updates】
    A->>C: 直接推送该变更增量【POST /updates】
    Note over B: 版本更新→落库；不再转发
    Note over C: 版本更新→落库；不再转发
```

**失活驱逐（直链 HOLD）**——成员离开 / 宕机后删除它的数据

```mermaid
sequenceDiagram
    participant C as 注册中心 C
    participant A as 注册中心 A
    loop 每 10s（keepalive_interval）
        C->>A: 直链保活【POST /keepalives】
        Note over A: 刷新 C 的 HOLD 计时
    end
    Note over A,C: C 离开 / 宕机，链路断开
    Note over A: 不再收到 C 的保活
    Note over A: 静默超过 hold_timeout（30s）→ 断会话 + 驱逐所有来源=C 的记录 + 设抑制冷却
```

> 失活以**注册中心节点**为单位：断一条会话即驱逐该来源的全部服务，不逐服务保活。从物理断开算起，默认约 **30 秒**（≈ `hold_timeout`，±保活间隔/巡检粒度）后该来源的服务从其它实例删除；可在 `ClusterConfig` 调小。驱逐后对该来源设一段抑制冷却（`tombstone_retention`，默认 40s），期间反熵不会把它的记录重新拉回；该来源重新建链（`add-peer`）即解除抑制。

**周期反熵兜底**（修复推送丢包，保证最终收敛）

```mermaid
sequenceDiagram
    participant A as 注册中心 A
    participant B as 注册中心 B
    loop 每 20s（anti_entropy_interval）
        A->>B: 交换版本摘要找差异【GET /digest】
        B-->>A: 版本摘要
        A->>B: 补齐差量：拉缺失 + 推更新【POST /pulls · /updates】
        Note over A: 顺带 GC 过期墓碑、清理过期抑制
    end
```

### 1.3 部署图

全连接：每对实例都直连。

```mermaid
flowchart LR
    subgraph HA[注册中心 A 主机]
        AgA[智能体们] -->|本地读写| RA[(本地注册表<br/>service.json)]
        RA --- CA[cluster 模块<br/>node_id=A<br/>foreign overlay]
    end
    subgraph HB[注册中心 B 主机]
        AgB[智能体们] -->|本地读写| RB[(本地注册表<br/>service.json)]
        RB --- CB[cluster 模块<br/>node_id=B<br/>foreign overlay]
    end
    subgraph HC[注册中心 C 主机]
        AgC[智能体们] -->|本地读写| RC[(本地注册表<br/>service.json)]
        RC --- CC[cluster 模块<br/>node_id=C<br/>foreign overlay]
    end
    CA <-->|HTTP 同步| CB
    CB <-->|HTTP 同步| CC
    CA <-->|HTTP 同步| CC
```

- 每台主机 = 一组智能体 + 一个注册中心 server（FastAPI）+ 一个 cluster 模块（持有 foreign overlay 与会话表）。
- 智能体只与**本地**注册中心交互（注册 / 查询）；查询时本地把“本地 entry + foreign 副本”合并返回。
- 实例间只通过 HTTP `/api/cluster/*` 通信，**拓扑必须是全连接**（每对成员都两两建会话）。注册中心**自身不做邻居发现/自动连接**：每条链路由外部（链路层发现或人工）触发一次 `add-peer`（`POST /api/cluster/peers`），触发后握手 + 对账全自动；成员离开后其数据按 HOLD 自动失活清除。

### 1.4 类图

`ClusterStore` 是核心，聚合“持久化状态 + 会话表 + foreign 副本”，并通过 `Transport` 与对端通信；两个后台守护线程驱动它做周期任务。

```mermaid
classDiagram
    class ClusterStore {
        +node_id
        +handle_open(body) 接收握手
        +serve_digest/pull/updates() 应答对端
        +handle_keepalive() 应答保活
        +connect_peer(addr) 发起建链+对账
        +disconnect_peer(id) 断会话+驱逐+抑制
        +reconcile(peer) 双向对账
        +on_local_mutation() 本地CRUD钩子
        +apply_inbound(env) 按LWW落库
        +emit_keepalive() 直链保活
        +check_hold() HOLD超时驱逐
        +gc_tombstones() 墓碑回收
        +foreign_rows(ds) 读路径合并
    }
    class ClusterState {
        +node_id
        +version_clock
        +local_versions
        +tombstones
        +load/init/save()
    }
    class SyncEnvelope {
        +dataset
        +service_id
        +origin_id
        +version
        +tombstone
        +payload
    }
    class Peer {
        +node_id
        +address
        +namespaces
        +last_seen
        +token
    }
    class ClusterConfig {
        +keepalive_interval
        +hold_timeout
        +anti_entropy_interval
        +tombstone_retention
    }
    class Transport {
        <<interface>>
        +open/digest/pull/updates()
        +keepalive()
    }
    class HttpTransport
    class AntiEntropySweeper
    class KeepaliveMonitor

    ClusterStore --> ClusterState : 持久化状态
    ClusterStore --> "0..*" Peer : 会话表
    ClusterStore --> "0..*" SyncEnvelope : foreign overlay
    ClusterStore --> ClusterConfig : 配置
    ClusterStore --> Transport : 对端通信
    Transport <|.. HttpTransport
    AntiEntropySweeper --> ClusterStore : 周期对账+GC
    KeepaliveMonitor --> ClusterStore : 周期保活+HOLD驱逐
```

---

## 2. 对外接口

### 2.1 整体介绍（所有接口一览）

**用户 / 运维使用的接口**

| 接口 | 作用 |
|------|------|
| `a2x-registry cluster init` | 生成本实例 node_id，启用集群（opt-in 开关） |
| `a2x-registry cluster add-peer <addr>` | 连接一个对端并完成首次对账（**主用触发命令**；全连接需对每对成员各跑一次） |
| `a2x-registry cluster rm-peer <node_id>` | 主动断开某对端，并删除它的副本 |
| `a2x-registry cluster status` | 查看本节点同步状态 |
| `GET /api/cluster/state` | 同上，HTTP 形式的状态快照 |
| `GET /api/cluster/peers` | 列出当前会话 |
| `POST /api/cluster/peers` | `add-peer` 的底层 HTTP；外部系统 / 链路发现守护进程也可直接调 |
| `DELETE /api/cluster/peers/{node_id}` | `rm-peer` 的底层 HTTP |
| `GET /api/datasets/{ds}/services`（既有） | 查询服务列表，**自动合并对端同步来的副本** |

**节点间协议接口**（由其它注册中心自动调用，用户一般不直接使用）

| 接口 | 作用 |
|------|------|
| `POST /api/cluster/sessions` | 接收握手，逐命名空间鉴权 + 签发会话令牌 |
| `GET /api/cluster/digest` | 返回 `{记录键: 版本}` 摘要 |
| `POST /api/cluster/pulls` | 按键返回完整记录信封 |
| `POST /api/cluster/updates` | 接收增量（LWW 去重）；**不转发** |
| `POST /api/cluster/keepalives` | 直链保活，刷新 HOLD 计时 |

> 未执行 `cluster init` 时，所有 `/api/cluster/*` 返回 404。

### 2.2 用户接口详解（输入 / 输出示例）

**`cluster init`** —— 离线生成身份，不经 HTTP。

```bash
a2x-registry cluster init
```
```
Cluster initialized.
  node_id : reg-99f79b5f9d04
  state   : ~/.a2x_registry/cluster_state.json
```

**`cluster add-peer`** —— 连接对端 + 首次对账（主用命令）。全部字段：

```bash
a2x-registry cluster add-peer <address> [--namespaces a,b] [--token T] [--server URL]
```

| 字段 | 必填 | 不填时的效果 |
|------|------|--------------|
| `<address>` | ✅ | 对端 base URL，如 `http://10.0.0.2:8000` |
| `--namespaces a,b` | 可选 | **同步两端命名空间的并集（全部）** |
| `--token T` | 可选 | 只能同步对端**不要求鉴权**的命名空间；对端整体未启用鉴权时本就无需此项 |
| `--server URL` | 可选 | 默认本机 `http://127.0.0.1:8000` |

底层等价 HTTP `POST /api/cluster/peers`，请求体（`namespaces`、`token` 可省略，语义同上）：

```json
{ "address": "http://10.0.0.2:8000", "namespaces": ["translators"], "token": "a2x_pat_xxx" }
```

返回：

```json
{ "peer": { "node_id": "reg-b1c2d3", "address": "http://10.0.0.2:8000",
            "namespaces": ["translators", "default"] } }
```

**`cluster rm-peer`** —— 断开并清除该对端副本。

```bash
a2x-registry cluster rm-peer reg-b1c2d3
```
```json
{ "node_id": "reg-b1c2d3", "removed": true }
```

**`cluster status` / `GET /api/cluster/state`** —— 同步状态快照。

```json
{ "node_id": "reg-a1b2c3", "advertise": "http://10.0.0.1:8000",
  "peers": [ { "node_id": "reg-b1c2d3", "address": "http://10.0.0.2:8000",
               "namespaces": ["translators"] } ],
  "foreign_records": 12, "foreign_by_namespace": { "translators": 12 },
  "local_records": 5, "tombstones": 0 }
```

**`GET /api/datasets/{ds}/services`** —— 普通查询，集群启用后自动合并对端副本（客户端无需改动）。

```bash
curl http://127.0.0.1:8000/api/datasets/translators/services
```
```json
[
  { "id": "generic_8f3c", "name": "EN-ZH Translator", "source": "api_config" },
  { "id": "reg-b1c2d3:generic_1a2b", "name": "ZH-EN Translator",
    "origin_id": "reg-b1c2d3", "source": "cluster" }
]
```

> 带 `origin_id` 且 `source=cluster` 的是对端同步来的**只读副本**，id 形如 `对端node_id:服务id`，可直接传给 `GET /api/datasets/{ds}/services/{id}` 取详情。要修改它须到来源实例操作（origin-only）。

### 2.3 鉴权与会话令牌（仅在启用鉴权时生效）

- 握手时逐命名空间授权（复用 auth 模块；对端整体未启用鉴权 = 全放行）：对端**没有**的命名空间需 `admin` token 才创建临时副本；对端**有**的命名空间，不要求鉴权则放行，否则需该命名空间的 `provider`/`admin` token。即 `add-peer` 的 `--token`。
- 握手成功且对端启用鉴权时，对端签发一个会话令牌，双方留存；之后每次内部 RPC 经 `X-Cluster-Session` 头携带，对端据此校验 `from_node`。令牌无效 → 降级为匿名（只能访问非 `auth_required` 命名空间），从而伪造身份无法触达特权命名空间。
- **安全假设**：身份与授权由握手 token + 会话令牌保证；不对载荷做端到端加密，依赖部署层 TLS / 可信链路。

---

## 3. 使用流程

下面以**两台注册中心 A、B 互联**为例串成闭环：**启用集群 → 各自启动 → 建立连接 → 任一实例查询得到全网服务**。三台及以上同理——**全连接**要求对每一对成员各跑一次 `add-peer`。单机调试可在同一台机器用不同端口模拟。

> 跨机 / 本机多实例都先设 `export NO_PROXY=127.0.0.1,localhost`（避免系统代理拦截 localhost，见 CLAUDE.md）。

### 3.1 [每个注册中心主机] 安装 + 启用集群

```bash
pip install git+https://github.com/Weizheng96/A2X-registry.git@v0.3.1

# 启用集群：生成本实例的 node_id（写入 <A2X_REGISTRY_HOME>/cluster_state.json）
a2x-registry cluster init
```

`cluster init` 是**离线**操作，只生成身份文件，不经 HTTP、与 server 进程无关：

```
Cluster initialized.
  node_id : reg-99f79b5f9d04
  state   : /home/you/.a2x_registry/cluster_state.json
Restart the registry server for the cluster module to load.
```

> 不执行 `cluster init` 的实例就是普通单机注册中心，`/api/cluster/*` 全 404，行为与今天一致。鉴权是另一项可选特性，需要时按 [服务端 auth 流程](https://github.com/Weizheng96/A2X-registry) 先 `a2x-registry auth init`。

### 3.2 [每个注册中心主机] 启动 server（声明对外地址）

```bash
# A 主机
export A2X_REGISTRY_CLUSTER_ADVERTISE=http://<A 的IP>:8000
a2x-registry --host 0.0.0.0 --port 8000

# B 主机
export A2X_REGISTRY_CLUSTER_ADVERTISE=http://<B 的IP>:8000
a2x-registry --host 0.0.0.0 --port 8000
```

`A2X_REGISTRY_CLUSTER_ADVERTISE` = 对端回连本实例所用的 base URL；建链后两端互相记下对方地址用于同步。server 启动时会加载 cluster 模块并拉起后台守护线程（反熵 / keepalive）。本机多实例调试时给每个实例不同的 `A2X_REGISTRY_HOME`、`A2X_REGISTRY_CLUSTER_STATE`、端口与 advertise 即可。

### 3.3 [A 主机] 建立连接

在任一台上把对端接进来即可（“先发现方”发起，一条命令完成建链 + 双向首次对账，两端随即收敛）：

```bash
a2x-registry cluster add-peer http://<B 的IP>:8000
```

- 默认同步两端命名空间的并集；要限定加 `--namespaces a,b`。
- 若对端某命名空间要求鉴权，加 `--token <provider/admin token>`（整体无鉴权则无需）。
- 三台及以上：**全连接**，对每一对成员各跑一次 `add-peer`（如 A-B、B-C、A-C）。非全连接时未直连的节点学不到对方数据（无转发）。

返回示例见 [§2.2](#22-用户接口详解输入--输出示例)。

### 3.4 验证

```bash
a2x-registry cluster status        # 应看到 peers 里有对端、foreign_records > 0
curl http://127.0.0.1:8000/api/datasets/translators/services
```

查询结果里 `source=cluster`、带 `origin_id` 的行即对端同步来的**只读副本**（id 形如 `对端node_id:服务id`）。客户端 SDK 的 `list_agents`/`get_agent` 也会自动看到这些副本，无需改代码。要修改某副本须到它的来源实例操作（origin-only），改动会增量同步回来。

### 3.5 断开

```bash
a2x-registry cluster rm-peer reg-b1c2d3     # 主动断开 + 删除该对端副本
```

被动断开（对端离开 / 宕机）**无需任何操作**：本端约 30s（`hold_timeout`）内收不到对端保活后自动断会话、删除其全部副本（并设抑制冷却防反熵复活）；对端恢复可达后再 `add-peer` 重新对账补齐。

---

## 4. 鲁棒性

- 无单点：实例对等，任一宕机不影响其余实例本地读写；恢复后经对账重新收敛。
- 分区容忍：分区是常态，双方各自可用，愈合后最终收敛。
- 守护线程（`AntiEntropySweeper` / `KeepaliveMonitor`）每个 tick 都被 try/except 包裹，单次异常只记日志、不中断循环。
- 同步端点幂等；内存有界（foreign 记录随来源驱逐释放，墓碑按期 GC，抑制项过期清理）；推送 best-effort，由反熵保证最终收敛。
- 无环：全连接下来源直发、入站不转发，不存在泛洪回音；驱逐后的抑制冷却杜绝“一节点已驱逐、另一节点经反熵复活”的乒乓。
