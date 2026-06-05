# Cluster 分布式同步模块设计

让多个**仅间歇连通**的 A2X 注册中心实例在彼此可达时互相复制各自的注册表：查询任意一个实例都能拿到所有可达实例的服务；实例之间断开后，各自靠存活信标失活删除对方的数据。

模块是 **opt-in**：注册中心默认单机运行，直到执行 `a2x-registry cluster init` 生成 `cluster_state.json` 才启用。未启用时所有 `/api/cluster/*` 返回 404，读路径与单机完全一致。

---

## 1. 流程逻辑

### 1.1 整体介绍

- **一致性**：AP / 最终一致。gossip 复制 + 最后写入者胜（LWW）版本判新，无共识 / 多数派。
- **写入**：origin-only。每条记录只在它的来源实例可写，其他实例持有**只读、仅内存**的副本。全局身份键 = `(dataset, origin_id, service_id)`，所以两个实例注册同名服务（同 `service_id`）也不会冲突，且不存在写-写冲突。
- **版本**：`(updated_at_ms, node_id)`，逐记录单调递增（本机时钟回拨也不会让它变小），按字典序比较。
- **防回音**：水平分割（不回发给来源）+ 严格版本去重（版本不严格新于本地则丢弃且不转发），泛洪自然终止。
- **删除**：墓碑携带版本，按 LWW 压过更旧的存活值；保留 `beacon_ttl + beacon_grace` 后 GC。
- **失活驱逐**：**以注册中心节点为单位**。每个实例广播自己的存活信标（BEACON），接收方为**每个来源节点**维护一条租约；信标停了，该节点的全部记录一次性被驱逐（详见 §1.2 失活时序）。

三层职责：

| 层 | 职责 | 关键类型 |
|----|------|----------|
| L1 会话 | OPEN 握手 + 逐 namespace 鉴权、peer 表、keepalive/HOLD | `Peer`、`auth_handshake` |
| L2 存活 | 按来源节点的租约，由信标续期；静默则驱逐 | `LeaseTable[node_id]`、`BeaconSweeper`、`KeepaliveMonitor` |
| L3 复制 | 本地 CRUD 后推送、入站接受 + 转发、周期反熵 | `SyncEnvelope`、`AntiEntropySweeper` |

租约状态机复用共享的 `a2x_registry.common.lease.LeaseTable`（心跳模块也用它）。

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
| `beacon_ttl` | 30s | 来源租约有效期 |
| `beacon_grace` | 15s | 过期后的宽限期；墓碑保留 = ttl + grace |
| `beacon_interval` | 10s | 信标广播 / 巡检周期 |
| `keepalive_interval` | 10s | 直链保活周期 |
| `hold_timeout` | 30s | 直链静默多久后断会话 |
| `anti_entropy_interval` | 20s | 反熵对账 + GC 周期 |
| `http_timeout` | 5s | 单次对端调用超时 |

`A2X_REGISTRY_CLUSTER_ADVERTISE` 设置对端访问本实例所用的 base URL。

### 1.2 时序图

每一步标注"做了什么【对应接口】"。

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

**本地 CRUD 增量推送 + 链式转发（A–B–C）**

```mermaid
sequenceDiagram
    participant Ag as A 上的智能体
    participant A as 注册中心 A
    participant B as 注册中心 B
    participant C as 注册中心 C
    Ag->>A: 注册 / 更新 / 注销服务
    A-->>Ag: 200 OK（本地写完立即返回，不等同步）
    A->>B: 推送该变更增量【POST /updates】
    Note over B: 版本更新→落库；转发给除来源 A 外的邻居（水平分割）
    B->>C: 转发该增量【POST /updates】
    Note over C: 版本更新→落库；无其他下游，停止
    C-->>A: 若成环，回音转发回 A【POST /updates】
    Note over A: 版本非更新→丢弃且不再转发，泛洪终止
```

**存活信标与失活驱逐**（信号断开后删除不在局域网内的数据）

```mermaid
sequenceDiagram
    participant C as 注册中心 C
    participant B as 注册中心 B
    participant A as 注册中心 A
    loop 每 10s（beacon_interval）
        C->>B: 广播自己的存活信标【POST /beacons】
        B->>A: 转发信标（水平分割 + 按序号去重）【POST /beacons】
        Note over A: 续租来源 C：到期=now+30s，驱逐=now+45s
    end
    Note over B,C: C 远离，链路断开
    Note over A: 不再收到来源 C 的信标
    Note over A: 经 ttl+grace（~45s）→ 一次性驱逐所有来源=C 的记录
```

> 失活以**注册中心节点**为单位：一条来源租约管该节点的全部服务，不逐服务保活。直连邻居还有更快的 keepalive/HOLD（`hold_timeout=30s`）兜底断会话。从物理断开算起，默认约 **45 秒**（≈ ttl+grace，±信标间隔/巡检粒度）后该来源的服务从其它实例删除；可在 `ClusterConfig` 调小。

**周期反熵兜底**（修复推送丢包，保证最终收敛）

```mermaid
sequenceDiagram
    participant A as 注册中心 A
    participant B as 注册中心 B
    loop 每 20s（anti_entropy_interval）
        A->>B: 交换版本摘要找差异【GET /digest】
        B-->>A: 版本摘要
        A->>B: 补齐差量：拉缺失 + 推更新【POST /pulls · /updates】
        Note over A: 顺带 GC 过期墓碑
    end
```

### 1.3 部署图

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
```

- 每台主机 = 一组智能体 + 一个注册中心 server（FastAPI）+ 一个 cluster 模块（持有 foreign overlay 与来源租约）。
- 智能体只与**本地**注册中心交互（注册 / 查询）；查询时本地把"本地 entry + foreign 副本"合并返回。
- 实例间只通过 HTTP `/api/cluster/*` 通信。拓扑可以是星形、链形或环形；节点移动导致拓扑变化时，新链路自动建链对账、旧来源按租约失活。

### 1.4 类图

`ClusterStore` 是核心，聚合"持久化状态 + 会话表 + foreign 副本 + 来源租约"，并通过 `Transport` 与对端通信；三个后台守护线程驱动它做周期任务。

```mermaid
classDiagram
    class ClusterStore {
        +node_id
        +handle_open(body) 接收握手
        +serve_digest/pull/updates() 应答对端
        +handle_beacon/keepalive() 应答存活
        +connect_peer(addr) 发起建链+对账
        +reconcile(peer) 双向对账
        +on_local_mutation() 本地CRUD钩子
        +apply_inbound(env) 按LWW落库
        +emit_beacon() 广播存活
        +sweep_origins() 失活驱逐
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
    class LeaseTable~K~ {
        +install/renew/revoke()
        +sweep_tick(now)
    }
    class ClusterConfig {
        +beacon_ttl
        +beacon_grace
        +hold_timeout
        +anti_entropy_interval
    }
    class Transport {
        <<interface>>
        +open/digest/pull/updates()
        +beacon/keepalive()
    }
    class HttpTransport
    class AntiEntropySweeper
    class BeaconSweeper
    class KeepaliveMonitor

    ClusterStore --> ClusterState : 持久化状态
    ClusterStore --> "0..*" Peer : 会话表
    ClusterStore --> "0..*" SyncEnvelope : foreign overlay
    ClusterStore --> LeaseTable : 来源租约
    ClusterStore --> ClusterConfig : 配置
    ClusterStore --> Transport : 对端通信
    Transport <|.. HttpTransport
    AntiEntropySweeper --> ClusterStore : 周期对账+GC
    BeaconSweeper --> ClusterStore : 周期广播+驱逐
    KeepaliveMonitor --> ClusterStore : 周期保活+断链
```

---

## 2. 对外接口

### 2.1 整体介绍（所有接口一览）

**用户 / 运维使用的接口**

| 接口 | 作用 |
|------|------|
| `a2x-registry cluster init` | 生成本实例 node_id，启用集群（opt-in 开关） |
| `a2x-registry cluster add-peer <addr>` | 连接一个对端并完成首次对账（**主用触发命令**） |
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
| `POST /api/cluster/updates` | 接收增量（LWW 去重）并水平分割转发 |
| `POST /api/cluster/beacons` | 接收存活信标，续租来源并转发 |
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
  "local_records": 5, "tombstones": 0, "tracked_origins": 1 }
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

下面以**两台注册中心 A、B 互联**为例串成闭环：**启用集群 → 各自启动 → 建立连接 → 任一实例查询得到全网服务**。三台及以上同理（对每条要建立的链路各跑一次 `add-peer`）。单机调试可在同一台机器用不同端口模拟。

> 跨机 / 本机多实例都先设 `export NO_PROXY=127.0.0.1,localhost`（避免系统代理拦截 localhost，见 CLAUDE.md）。

### 3.1 [每个注册中心主机] 安装 + 启用集群

```bash
pip install git+https://github.com/Weizheng96/A2X-registry.git@v0.2.0

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

`A2X_REGISTRY_CLUSTER_ADVERTISE` = 对端回连本实例所用的 base URL；建链后两端互相记下对方地址用于同步。server 启动时会加载 cluster 模块并拉起后台守护线程（反熵 / 信标 / keepalive）。本机多实例调试时给每个实例不同的 `A2X_REGISTRY_HOME`、`A2X_REGISTRY_CLUSTER_STATE`、端口与 advertise 即可。

### 3.3 [A 主机] 建立连接

在任一台上把对端接进来即可（"先发现方"发起，一条命令完成建链 + 双向首次对账，两端随即收敛）：

```bash
a2x-registry cluster add-peer http://<B 的IP>:8000
```

- 默认同步两端命名空间的并集；要限定加 `--namespaces a,b`。
- 若对端某命名空间要求鉴权，加 `--token <provider/admin token>`（整体无鉴权则无需）。
- 三台及以上：对每条要建立的链路各跑一次 `add-peer`（链形 A-B、B-C 即可，A 经 B 间接学到 C）。

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

被动断开（对端漂走 / 宕机）**无需任何操作**：本端约 45s（`beacon_ttl + beacon_grace`）内收不到对端信标后自动失活删除其全部副本；对端恢复可达后再 `add-peer` 重新对账补齐。

---

## 4. 鲁棒性

- 无单点：实例对等，任一宕机不影响其余实例本地读写；恢复后经对账重新收敛。
- 分区容忍：分区是常态，双方各自可用，愈合后最终收敛。
- 守护线程（`AntiEntropySweeper` / `BeaconSweeper` / `KeepaliveMonitor`）每个 tick 都被 try/except 包裹，单次异常只记日志、不中断循环。
- 同步端点幂等；内存有界（foreign 记录随来源驱逐释放，墓碑按期 GC）；推送 best-effort，由反熵保证最终收敛。
