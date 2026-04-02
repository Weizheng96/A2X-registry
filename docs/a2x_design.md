# A2X 系统设计文档

**版本**: v0.1.0

本文档包含系统整体视图。各模块详细设计见：
- 构建模块：[build_design.md](build_design.md)
- 搜索模块：[search_design.md](search_design.md)
- 增量构建模块：[incremental_design.md](incremental_design.md)

---

## 系统整体视图

### 1. 流程逻辑说明

A2X 注册中心由四个模块组成，围绕核心数据结构 **分类树** 协同工作：

1. **构建模块**：从服务列表自动构建层次化分类树（详见 [build_design.md](build_design.md)）
2. **搜索模块**：基于分类树执行两阶段 LLM 递归检索，返回匹配服务
3. **增量构建模块**（待实现）：将增量新服务插入已有分类树，必要时合并分支
4. **优化模块**（待实现）：根据外部输入的频率日志动态调整树结构，降低高频服务的检索成本

**数据流**：
- 构建模块输出分类树 → 搜索模块、增量构建模块、优化模块共同使用
- 增量构建模块和优化模块修改分类树 → 搜索模块读取最新版本
- 频率日志由外部独立提供 → 优化模块消费

### 2. 对外调用接口

| 模块 | 输入 | 输出 | 状态 |
|:----:|:----:|:----:|:----:|
| **构建** | 服务列表 | 分类树（taxonomy.json + class.json） | ✅ |
| **搜索** | 查询 + 分类树 | 服务列表 + 搜索统计 | ✅ |
| **增量构建** | 新服务 + 分类树 | 更新后的分类树 | 待实现 |
| **优化** | 频率日志 + 分类树 | 优化后的分类树 | 待实现 |

### 3. 逻辑视图

```mermaid
graph LR
    SVC([服务数据]) --> BUILD[构建]
    BUILD --> TAX[(分类树)]

    QRY([查询]) --> SEARCH[搜索]
    TAX --> SEARCH
    SEARCH --> RES([服务列表])

    NEW([增量新服务]) --> REG[增量构建]
    TAX <--> REG

    FREQ([频率日志<br/>外部输入]) --> OPT[优化]
    TAX <--> OPT

    style TAX fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px
    style FREQ fill:#f3e5f5,stroke:#9c27b0
    style BUILD fill:#fff3e0,stroke:#ff9800
    style SEARCH fill:#e8f5e9,stroke:#4caf50
    style REG fill:#e3f2fd,stroke:#2196f3
    style OPT fill:#fce4ec,stroke:#e91e63
```

### 4. 顺序图

```mermaid
sequenceDiagram
    participant B as 构建
    participant T as 分类树
    participant S as 搜索
    participant F as 频率日志(外部)
    participant R as 增量构建
    participant O as 优化

    Note over B,T: 初始化阶段
    B->>T: 创建初始分类树

    Note over S,T: 运行时阶段
    loop 用户查询
        S->>T: 读取分类结构
        S->>S: 两阶段搜索(导航+筛选)
        S-->>S: 返回服务列表
    end

    Note over R,T: 增量构建(待实现)
    R->>T: 读取分类结构
    R->>T: 添加新服务/合并分支

    Note over O,T: 周期优化(待实现)
    F-->>O: 提供访问频率数据
    O->>T: 读取分类结构
    O->>T: 应用优化(提升/下沉)
```

### 5. 类图

```mermaid
classDiagram
    class A2XSearch {
        -categories: Dict
        -classes: Dict
        -services: Dict
        -llm: LLMClient
        +search(query) Tuple~List~SearchResult~, SearchStats~
    }

    class TaxonomyBuilder {
        -config: AutoHierarchicalConfig
        -taxonomy: Dict
        -class_data: Dict
        +build(resume: str) void
    }

    class LLMClient {
        <<src.common.llm_client>>
        -base_url: str
        -model: str
        -api_keys: List~str~
        +call(messages, temperature, max_tokens) LLMResponse
        +call_batch(prompts, system_prompt, temperature, max_workers) List~LLMResponse~
    }

    class IncrementalBuilder {
        +add_service(service) List~str~
        +remove_service(service_id) bool
    }

    class Optimizer {
        <<待实现>>
        +optimize(frequency_log, taxonomy) Taxonomy
    }

    TaxonomyBuilder ..> LLMClient : uses
    A2XSearch ..> LLMClient : uses
    IncrementalBuilder ..> LLMClient : uses
    Optimizer ..> LLMClient : uses
```
