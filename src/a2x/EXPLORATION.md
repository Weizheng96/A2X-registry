# A2X 探索记录

本文档记录已探索过的优化方向及其结果，避免重复探索无效方向。

---

## 早期探索摘要 (v0.0.1–v0.0.3)

以下实验在早期架构上进行，与当前 auto_hierarchical 方案关系不大，仅保留结论。

| # | 方向 | 分支 | 结果 | 关键教训 |
|---|------|------|------|----------|
| 1 | 顶层分类数量→10 | `exp/top-level-10` | ❌ Recall -4.8% | 10个顶层过于粗粒度，聚类分布不均 |
| 2 | Independent逐服务YES/NO | `exp/independent-mode` | ❌ Recall -1%, 调用+52% | 缺乏上下文的YES/NO判断不如batch模式 |
| 3 | Bottom-up LLM Grouping | `exp/bottom-up-llm-grouping` | ⚠️ Recall 66.3% | Correcting子实验使Recall再降7%，重定位破坏搜索路径 |
| 4 | LLM Top-Down构建 | `exp/llm-topdown-framework` | ✅ Recall +5.5% | **LLM自顶向下设计分类优于纯聚类**，已合并 |
| 5 | Intent-Driven分类描述 | `exp/intent-driven-taxonomy` | ❌ Recall -12.3% | 分类描述过长反而干扰LLM判断，简洁更有效 |
| 6 | 移除通用分类 | `exp/no-utilities-category` | ❌ Recall -4.1% | 通用分类提供兜底机制，不应消除 |
| 7 | 查询示例增强描述 | `exp/query-example-descriptions` | ❌ Recall -8.6% | 重新生成taxonomy引入随机性，无法隔离变量 |
| 8 | 查询示例增强搜索(后处理) | `exp/query-example-categories` | ⚠️ Recall ±0% | 示例查询对LLM判断相关性作用有限 |
| 9 | Multi-Path多路径搜索 | `exp/multi-path-search` | ⚠️ Recall +13.4% | 本质是暴力扩大探索范围，Token成本+51%，不可取 |
| 10 | ToolRet_clean数据集构建 | — | ✅ 基线建立 | 数据清洗正面影响，ToolRet_clean→ToolRet_new成为主评估集 |
| 11 | ToolRet_clean+自动层级 | `exp/v0.3_clean-hier` | ❌ Recall 77.2% < 手工83.5% | 自动3层导航引入级联剪枝错误 |
| 12 | Phase1用服务描述 | `exp/v0.3_desc-phase1` | ⚠️ Recall ±0% | Phase1非瓶颈，仅靠name已够设计顶层分类 |

**核心经验**：
- **简洁的分类描述 > 详细的意图描述**（实验5、7、8）
- **分类质量 > 搜索策略**（实验9证明暴力搜索不如改进分类）
- **50q采样存在严重偏差**，重要实验必须运行全量测试（实验13中偏差达5.84%）

---

## 关键实验详情

### 13. 先验分类保留模式 → v0.0.4

**分支**: `exp/v0.3_iterative-preserve-ref` | **状态**: ✅ 已合并

**方案**: 用户提供先验分类时，迭代过程保留不删除/合并，只允许新增。双重保障：LLM prompt约束 + 程序化后验检查。

| 指标 | preserve-ref (1878q) | manual (1878q) | v0.0.3纯自动 (1878q) |
|------|:---:|:---:|:---:|
| Recall | **82.98%** | 82.43% | 77.23% |
| Avg LLM Calls | 2.70 | 2.77 | 8.16 |

**结论**: 保留高质量先验分类直接消除分类退化问题，实现"人工分类质量+自动化服务分配"的最佳组合。

---

### 14. 纯自动层次化构建 (auto_hierarchical) → v0.0.7

**分支**: `exp/v0.6_auto_hierarchical` | **状态**: ✅ 已合并

**方案**: 关键词提取→LLM聚合为顶层分类→逐服务分类/精炼→递归细分大节点(>40服务)。完全替代人工分类定义。

| 指标 | auto_hierarchical (1714q) | hierarchical v0.0.6 (1714q) |
|------|:---:|:---:|
| **Recall** | **92.11%** | 90.34% |
| Hit Rate | 94.57% | 92.94% |
| Avg LLM Calls | 20.53 | — |

**结论**: 首次纯自动方案超越半自动方案。纯自动关键词提取+分类设计在全量数据上表现更均衡。

---

### 15. 统一BFS架构重构 → v0.0.8

**分支**: main | **状态**: ✅ 已合并

**方案**: v0.0.7的两阶段流程(root_builder扁平+hierarchical递归)重构为统一BFS递归。root和child使用同一代码路径(NodeSplitter.split_node())，简化架构。新增confusable阈值(3+)控制cross-domain多父归属。

| 指标 | v0.0.8 auto_hierarchical (1714q) | v0.0.7 (1714q) | v0.0.6 hierarchical (1714q) |
|------|:---:|:---:|:---:|
| **Recall** | 89.41% | 92.11% | 90.34% |
| Hit Rate | 92.36% | 94.57% | 92.94% |
| 分类数 | 251 (209叶子) | 330 (279叶子) | — |

**结论**: 架构更合理但Recall略降2.7%。统一BFS产生更紧凑的分类结构(251 vs 330)，但部分服务归属变化导致召回下降。**当前目标：Recall>0.9 且 Tokens<10k。**

---

### 16. 统一节点选择（单次LLM调用同时选分类和服务）

**分支**: `exp/v0.8_unified-node-selection` | **状态**: ❌ 失败

**方案**: 将每个节点原来的两次LLM调用（选子分类+选服务）合并为一次。用`[CATEGORY]`和`[SERVICE]`标记混合编号，LLM一次选择。同时不使用后处理。

| 指标 | v0.0.9 baseline (50q) | unified (50q) | 变化 |
|------|:---:|:---:|:---:|
| **Recall** | **91.60%** | 84.33% | **-7.27%** |
| Precision | 11.0% | 8.13% | -2.87% |
| Tokens/q | 8,787 | 13,613 | **+55%** |
| Hit Rate | 96.00% | 92.00% | -4.00% |
| LLM Calls | 10.56 | 8.66 | -1.90 |

**失败原因**：
1. **Root节点信息过载**：root有205个服务+19个子分类=224个item，LLM被大量服务描述淹没，漏选关键子分类（8/11错误为导航失败）
2. **Tokens反增**：服务描述（长文本）混入分类选择prompt，增加55%
3. **关注点分散**：宏观导航（分类相关性）和微观匹配（服务选择）是不同粒度的决策，混合处理两头不讨好

**教训**：分类选择和服务选择应分开处理。分类选择需宏观判断（领域相关性），服务选择需微观匹配（具体功能），统一处理会互相干扰。

---

### 17. Token优化：generic_ratio分类 + 移除后处理 → v0.0.10

**分支**: `exp/v0.9_token_optimization` → 已合并 | **状态**: ✅ 已合并

**方案**: 用 generic_ratio（1/3）替代 confidence 阈值分类。三类服务：正常（1-N/3个匹配）、泛分类（>N/3→留父节点）、未分类（0→留父节点）。移除后处理模块。

| 指标 | v0.0.9 baseline (1714q) | v0.0.10 (1714q) |
|------|:---:|:---:|
| **Recall** | **91.16%** | 89.00% |
| Precision | 14.40% | **16.01%** |
| **Avg Tokens/q** | 14,027 | **7,051** |
| F1 | 24.87% | **27.14%** |
| Root 服务数 | 241 | 37 |

**结论**: Token 目标达成（-50%），Precision 提升。Recall 从 91.16% 降至 89.00%（-2.16%），略低于 90% 目标。核心改善来自 root 服务数大幅减少（241→37）。

---

### 18. get_one 精度模式 + 架构重构 → v0.1.1

**分支**: `exp/v1.0_getone_mode` | **状态**: ✅ 已合并

**方案**: 新增 `mode="get_one"` 搜索模式——分类导航和服务筛选阶段各只选一个最相关的结果，以精度换召回。同时对 build/search/evaluation 模块进行全面重构。

| 指标 | get_all (1714q) | get_one (1714q) |
|------|:---:|:---:|
| **Precision** | 16.06% | **49.12%** |
| Recall | **89.19%** | 42.99% |
| Hit Rate | **92.59%** | 49.53% |
| Avg Tokens/q | 7,069 | **3,648** |
| Avg LLM Calls | 7.96 | **3.42** |

**关键发现**:
- get_one 严格 Precision 为 49%，但人工抽样的**功能精度达 ~98%**（找到的服务几乎都与请求相关）
- 严格指标低估的原因：ground truth 不完整，未标注所有等价/相关服务（如 `FlightBooking` vs `book_flight`）
- 因此 README 中不引用 get_one 的评估指标，避免误导

**架构重构**:
- NodeDesigner → CategoryDesigner（含 design + refine）
- SearchStats 内置线程锁
- 构建断点续传（resume="no"/"keyword"/"yes"）
- split_node / evaluate_batch 拆分为小方法
- max_tokens 魔法数字提取为 config 常量

---

## 待探索方向

### 当前目标
**get_all**: Recall > 0.9 + Precision > 0.1 + Avg Tokens/q < 10,000（Tokens 已达标，Recall 差 1%）
**get_one**: 需要更完善的 ground truth 才能准确评估

### 优化方向
1. **完善 ground truth**：标注等价/相关服务，使 get_one 的严格指标更准确
2. **分析 v0.1.1 错误案例**：257 个 get_all 错误中 156 个导航失败，分析根因
3. **优化子分类边界**：改进分类设计 prompt，减少导航错误
4. **调整 generic_ratio**：尝试更宽松的阈值（如 0.5）允许更多多归属

### 已验证无效的方向（勿重复）
- 详细/冗长的分类描述（实验5、7）
- 移除通用/兜底分类（实验6）
- 暴力扩大搜索范围代替改进分类（实验9）
- 独立YES/NO判断代替batch评估（实验2）
- 统一节点选择：混合分类和服务到单次LLM调用（实验16）

---

## 版本对照

### ToolRet_new 全量 (1839 services, 1714 queries)

| 方案 | Recall | Precision | Tokens/q | 查询数 |
|------|:------:|:---------:|:--------:|:------:|
| auto_hierarchical v0.0.7 | **92.11%** | 10.66% | 15,117 | 1714 |
| auto_hierarchical v0.0.9 | 91.16% | 14.40% | 14,027 | 1714 |
| hierarchical v0.0.6 | 90.34% | 14.72% | — | 1714 |
| vector R@100 | 90.02% | — | 0 | 1714 |
| **v0.1.1 get_all** | 89.19% | 16.06% | **7,069** | 1714 |
| **v0.1.1 get_one** | 42.99% | **49.12%** | **3,648** | 1714 |
| auto_hierarchical v0.0.8 | 89.41% | 8.49% | 15,832 | 1714 |
| vector R@10 | 69.06% | — | 0 | 1714 |

### 其他方法基线

| 方法 | Recall/R@10 | Hit Rate/Hit@10 | Avg Tokens/q | 查询数 |
|------|:-----------:|:---------------:|:------------:|:------:|
| traditional (MCP-style) | 83.67% | 86.00% | 66,568 | 50 |
| vector@10 | 69.26% | 75.96% | 0 | 1714 |

---

## 分支列表

| 分支名 | 状态 | 说明 |
|--------|------|------|
| `main` | 活跃 | v0.1.3 当前版本 |
| `exp/v1.0_getone_mode` | 已合并 | v0.1.1, get_one 精度模式 + 全面重构 |
| `exp/v0.9_token_optimization` | 已合并 | v0.0.10, Tokens 7,051 (-50%) |
| `exp/v0.6_auto_hierarchical` | 已合并 | v0.0.7, 全量Recall 92.11% |
| `exp/v0.3_iterative-preserve-ref` | 已合并 | v0.0.4, 全量Recall 82.98% |
| `exp/llm-topdown-framework` | 已合并 | v0.0.2, Recall 68.3% |
| `exp/v0.8_unified-node-selection` | ❌ 失败 | 统一节点选择, Recall 84.3% |
| 其他早期分支 | 保留 | 见早期探索摘要表格 |
