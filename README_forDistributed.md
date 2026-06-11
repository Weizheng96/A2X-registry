# A2X 注册中心 · 分布式部署指南

让**多台注册中心实例**在彼此可达时自动同步注册表：在任一节点查询，都能看到所有可达节点的服务；某节点失联后，其它节点会自动删除它的数据。

该能力是 **opt-in（默认关闭）**：不执行 `cluster init` 的实例就是普通单机注册中心，行为与今天完全一致。

本文以**两个节点 A、B 互联**为例，A、B 的操作分开列出，照做即可。三节点及以上见文末「扩展到更多节点」。

> 命令以 bash 为例（Linux / macOS / WSL / Git Bash）。**Windows PowerShell** 把 `export NAME=值` 换成 `$env:NAME="值"`，其余命令相同。

---

## 0. 前置条件

- 两个节点各是一台主机（或同机不同端口），记其地址为 `http://<A_IP>:8000`、`http://<B_IP>:8000`。
- **网络**：两节点要能互相访问对方的 HTTP 端口（防火墙/安全组放行）。
- 安装（两台都装）：
  ```bash
  pip install git+https://github.com/Weizheng96/A2X-registry.git@v0.3.1
  ```

---

## 1. 在节点 A 上的操作

```bash
# (1) 启用集群：生成本节点身份（离线，写入 <A2X_REGISTRY_HOME>/cluster_state.json）
a2x-registry cluster init --node-id A

# (2) 声明"别人来找我用的地址"，必须是 B 能访问到的地址（不能填 127.0.0.1）
export A2X_REGISTRY_CLUSTER_ADVERTISE=http://<A_IP>:8000

# (3) 启动 server（监听所有网卡，方便 B 连入）
a2x-registry --host 0.0.0.0 --port 8000
```

> `--node-id` 可省略（默认随机 UUID），这里指定 `A` 只为示例里好认。`A2X_REGISTRY_CLUSTER_ADVERTISE` 必须在**启动 server 前**设好，server 会在启动时读取它。

## 2. 在节点 B 上的操作

与 A 完全对称，只是参数换成 B：

```bash
a2x-registry cluster init --node-id B
export A2X_REGISTRY_CLUSTER_ADVERTISE=http://<B_IP>:8000
a2x-registry --host 0.0.0.0 --port 8000
```

到此两个节点都已就绪，但还**没有建立连接**。

## 3. 组建集群（在任一节点上声明一次即可）

在**节点 A**上，把 B（以及更多成员）声明进同一个 cluster——系统据成员名册（roster）**自动建好全连接**，不必逐条手动连：

```bash
a2x-registry cluster set add http://<B_IP>:8000
# 多个一次性加：a2x-registry cluster set add http://<B_IP>:8000 http://<C_IP>:8000
```

返回 `{"cluster_id": "clu-…", "results": [{"node_id":"B","ok":true}, ...]}` 即成功。

> **声明式**：A 首次 `set add` 会自动铸一个 `cluster_id`，把列出的成员拉进来并全连接；之后系统据名册自动维护连接、增量同步、失活清理。每个注册中心只属一个 cluster（并发声明由版本号 LWW 收敛）。
> 链路层若有"发现对方"的自动化，也可直接调底层 HTTP：`POST http://<A_IP>:8000/api/cluster/set/add`，body `{"members":[{"address":"http://<B_IP>:8000"}]}`。
> （`add-peer`/`rm-peer` 是内部建连原语，可用于调试；日常运维用 `set`。）

## 4. 验证

在 A 上注册一个服务，到 B 上查询应能看到它（反之亦然）：

```bash
# 在 A 上注册（dataset 用 default 或你已有的命名空间）
curl -s -X POST http://<A_IP>:8000/api/datasets/default/services/generic \
     -H 'Content-Type: application/json' -d '{"name":"svc-on-A","description":"hi"}'

# 在 B 上查询，应看到这条（source=cluster、带 origin_id，id 形如 A:generic_...）
curl -s http://<B_IP>:8000/api/datasets/default/services
```

也可用 `a2x-registry cluster set show --server http://<A_IP>:8000` 看 cluster_id 与名册，或 `cluster status` 看会话与副本数。客户端 SDK 的 `list_agents`/`get_agent` 会自动包含这些同步来的副本，无需改代码。

> 同步来的记录是**只读副本**：要修改 / 注销，须到它的来源节点操作（改动会增量同步回来）。

---

## 5. 鉴权（可选）

- 若两节点都**没启用鉴权**（未跑 `a2x-registry auth init`）：集群完全开放，上面的步骤无需任何 token。
- 若被加成员**启用了鉴权**：`set add` 加 `--token <admin token>`（"拉人入群"按 admin 门控，语义同新建临时命名空间）：
  ```bash
  a2x-registry cluster set add http://<B_IP>:8000 --token a2x_pat_xxx
  ```
  - 被加成员开鉴权而 token 缺失/非 admin → 该成员被拒绝加入（`results` 里 `ok:false`，不会静默半连接）。
  - 无鉴权成员直接接受。
- 启用鉴权后，握手会签发会话令牌，后续节点间调用自动携带校验，防止身份冒充。

---

## 6. 失活与运维注意

- **主动移除**：`a2x-registry cluster set remove <node_id>` 把成员从 cluster 确定性移除——全网名册立即删除它、该成员退回单机，不依赖失活超时。
- **自动失活（兜底非优雅退出）**：某节点漂走/宕机但未 `set remove` 时，其直连 peer 在约 **30 秒**（`hold_timeout`）内收不到保活就断会话并删除它的全部副本。它恢复可达后，反熵自动补齐；该节点的 `cluster_id` 已落盘，**重启即据持久化名册自动重连**（无需再次 `set add`）。

  **可人工配置失活时间窗**：失活耗时 ≈ `hold_timeout`，通过环境变量在**启动 server 前**设置（与 `A2X_REGISTRY_CLUSTER_ADVERTISE` 一样，server 启动时读取）。无需改代码；墓碑保留与驱逐后抑制时长（`tombstone_retention = hold_timeout + keepalive_interval`）会自动跟随。

  ```bash
  # 例：把失活窗口从默认 30s 收紧到 ~10s（更快剔除离线节点）
  export A2X_REGISTRY_CLUSTER_HOLD_TIMEOUT=10        # 直链静默多久断会话(秒)，默认 30
  export A2X_REGISTRY_CLUSTER_KEEPALIVE_INTERVAL=5   # 保活周期(秒)，默认 10
  a2x-registry --host 0.0.0.0 --port 8000
  ```
  > Windows PowerShell：`$env:A2X_REGISTRY_CLUSTER_HOLD_TIMEOUT="10"` 等。

  | 环境变量 | 默认 | 作用 |
  |---|---|---|
  | `A2X_REGISTRY_CLUSTER_HOLD_TIMEOUT` | 30 | 直链静默多久断会话并驱逐其记录(秒)；**失活 ≈ 此值** |
  | `A2X_REGISTRY_CLUSTER_KEEPALIVE_INTERVAL` | 10 | 直链保活广播周期(秒)；墓碑/抑制保留 = hold + keepalive |
  | `A2X_REGISTRY_CLUSTER_ANTI_ENTROPY_INTERVAL` | 20 | 反熵对账 + GC 周期(秒) |
  | `A2X_REGISTRY_CLUSTER_HTTP_TIMEOUT` | 5 | 单次对端调用超时(秒) |
  | `A2X_REGISTRY_CLUSTER_BROADCAST_WORKERS` | 32 | 并发广播/保活线程池上限（规模化） |
  | `A2X_REGISTRY_CLUSTER_MERKLE_BUCKETS` | 256 | Merkle 反熵桶数；**须全网一致** |

  > 建议**各节点设成一致**；不一致只会导致各节点驱逐时刻略有先后（已由内部抑制机制兜底，不会误删）。非法值会被忽略并回退默认。
  > **大集群（数百~千节点）调参**：把心跳周期调到 30~60s（最有效的省流杠杆），并把 OS 文件句柄上限 `ulimit -n` 调到 ≥ 2×节点数（全连接每节点持 N−1 条长连接）。

- **advertise 必须可达**：`A2X_REGISTRY_CLUSTER_ADVERTISE` 要填**对端访问得到**的地址（局域网 IP / 域名），不能是 `127.0.0.1`（除非同机调试）。填错会导致对端无法回连本节点，反向同步失效。
- **重启自动重连**：`cluster_id` 与名册已落盘；server 重启后据此自动重连全连接、反熵补齐副本（副本本身只在内存，会重新同步）。无需再 `set add`。
- **新建命名空间**：建 cluster 后新创建的 dataset 不会自动纳入已有会话，需再 `set add`（或在各节点重连）一次刷新（已有命名空间内的服务增删改实时同步）。
- **localhost 代理**：若本机有系统代理（Clash/VPN），自己发的 HTTP 请求设 `export NO_PROXY=127.0.0.1,localhost`；`a2x-registry cluster` 系列命令内部已自动绕过代理。

---

## 7. 扩展到更多节点

底层拓扑是**全连接**（每对成员都直连，无转发），但你**不必手动建每条边**——成员控制面据名册自动维护：

- **一条命令成群**：在任一节点 `cluster set add <addr1> <addr2> ...` 把所有成员声明进同一 cluster，系统自动建好 `N×(N−1)/2` 条全连接边并持续维护。
- **扩容**：对集群中任一节点再 `set add <新成员地址>` 即可；新成员经 bootstrap 拿到全名册并连上每个现有成员。
- **缩容**：`set remove <node_id>` 确定性移除。
- **节点移动 / 拓扑变化**：注册中心**自身不做发现**——"成员进入可达范围"这一事件由**外部**（链路层发现守护进程或人工）感知后调用一次 `set add` 即可；其余（全连接、对账、失活清理、退老群）全自动。
  > 例：A 漂离原集群进入新集群 {D, E} —— 对 D 或 E 执行一次 `set add <A 地址>`，A 会**主动优雅退出**旧集群（旧成员立即移除它）、采纳新 cluster 并与 D、E 全连接对账。**这一步的触发需要外部感知，不会凭空发生。**

---

## 8. 环境变量速查

| 变量 | 作用 | 备注 |
|------|------|------|
| `A2X_REGISTRY_CLUSTER_ADVERTISE` | 对端回连本节点用的 base URL | 启动 server 前设置；填对端可达地址 |
| `A2X_REGISTRY_HOME` | 本节点数据目录（含 `cluster_state.json`、`database/`） | 同机多实例时每个实例设不同值 |
| `NO_PROXY` | 让自己发的 HTTP 不走系统代理 | 仅在有 Clash/VPN 时需要 |

更深入的协议 / 时序 / 接口细节见 [`docs/cluster_design.md`](docs/cluster_design.md)。
