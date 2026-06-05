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
  pip install git+https://github.com/Weizheng96/A2X-registry.git@v0.3.0
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

## 3. 建立连接（在任一节点上做一次即可）

在**节点 A**上，把 B 接进来（"先发现方"发起，一条命令完成建链 + 双向首次对账）：

```bash
a2x-registry cluster add-peer http://<B_IP>:8000
```

返回 `{"peer": {"node_id": "B", ...}}` 即成功。

> **只需单向做一次**：发起方会与对端做双向对账（拉取对方、推送自己），两端随即收敛。在 B 上对 A 再做一次也无妨（幂等）。
> 链路层若有"发现对方"的自动化，也可直接调底层 HTTP：`POST http://<A_IP>:8000/api/cluster/peers`，body `{"address":"http://<B_IP>:8000"}`。

## 4. 验证

在 A 上注册一个服务，到 B 上查询应能看到它（反之亦然）：

```bash
# 在 A 上注册（dataset 用 default 或你已有的命名空间）
curl -s -X POST http://<A_IP>:8000/api/datasets/default/services/generic \
     -H 'Content-Type: application/json' -d '{"name":"svc-on-A","description":"hi"}'

# 在 B 上查询，应看到这条（source=cluster、带 origin_id，id 形如 A:generic_...）
curl -s http://<B_IP>:8000/api/datasets/default/services
```

也可用 `a2x-registry cluster status --server http://<A_IP>:8000` 查看会话与副本数。客户端 SDK 的 `list_agents`/`get_agent` 会自动包含这些同步来的副本，无需改代码。

> 同步来的记录是**只读副本**：要修改 / 注销，须到它的来源节点操作（改动会增量同步回来）。

---

## 5. 鉴权（可选）

- 若两节点都**没启用鉴权**（未跑 `a2x-registry auth init`）：集群完全开放，上面的步骤无需任何 token。
- 若对端某命名空间**要求鉴权**：`add-peer` 加 `--token <provider/admin token>`：
  ```bash
  a2x-registry cluster add-peer http://<B_IP>:8000 --token a2x_pat_xxx
  ```
  - 该命名空间在对端不存在 → 需 `admin` token（对端会建临时副本承载）。
  - 该命名空间在对端存在且要求鉴权 → 需该命名空间的 `provider`/`admin` token。
  - 不带 token → 只能同步对端不要求鉴权的命名空间。
- 启用鉴权后，握手会签发会话令牌，后续节点间调用自动携带校验，防止身份冒充。

---

## 6. 失活与运维注意

- **自动失活**：某节点漂走/宕机后，其它节点在约 **45 秒**（`beacon_ttl 30s + grace 15s`）内收不到它的存活信标，就自动删除它的全部副本，无需人工干预。它恢复可达后重新 `add-peer` 即可补齐。
- **advertise 必须可达**：`A2X_REGISTRY_CLUSTER_ADVERTISE` 要填**对端访问得到**的地址（局域网 IP / 域名），不能是 `127.0.0.1`（除非同机调试）。填错会导致对端无法回连本节点，反向同步失效。
- **重启后需重新建链**：同步来的副本只在内存、不落盘；server 重启后会话丢失，需重新 `add-peer`（本节点自有数据已持久化，不会丢）。
- **新建命名空间**：建链后新创建的 dataset 不会自动纳入已有会话，需再 `add-peer` 一次刷新（已有命名空间内的服务增删改实时同步，无需重连）。
- **localhost 代理**：若本机有系统代理（Clash/VPN），自己发的 HTTP 请求设 `export NO_PROXY=127.0.0.1,localhost`；`a2x-registry cluster` 系列命令内部已自动绕过代理。

---

## 7. 扩展到更多节点

拓扑可任意（星形 / 链形 / 环形），**对每条要建立的链路各 `add-peer` 一次**即可：

- **链形 A—B—C**：在 A 上 `add-peer B`，在 B 上 `add-peer C`。A 不直连 C，但会经 B 自动学到 C 的服务（链式转发）。
- **星形（B 为中心）**：在 B 上分别 `add-peer A`、`add-peer C`、`add-peer D`。
- 节点移动导致拓扑变化时，新链路自动建链对账、旧来源按失活规则清除，无需额外配置。

---

## 8. 环境变量速查

| 变量 | 作用 | 备注 |
|------|------|------|
| `A2X_REGISTRY_CLUSTER_ADVERTISE` | 对端回连本节点用的 base URL | 启动 server 前设置；填对端可达地址 |
| `A2X_REGISTRY_HOME` | 本节点数据目录（含 `cluster_state.json`、`database/`） | 同机多实例时每个实例设不同值 |
| `NO_PROXY` | 让自己发的 HTTP 不走系统代理 | 仅在有 Clash/VPN 时需要 |

更深入的协议 / 时序 / 接口细节见 [`docs/cluster_design.md`](docs/cluster_design.md)。
