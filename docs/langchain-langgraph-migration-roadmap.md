# LangChain + LangGraph 学习与全量迁移路线图

> 项目：`ai-agent-100-days-learning`  
> 目标：在保留现有业务能力的前提下，将 Agent 运行框架逐步迁移到 LangChain + LangGraph。  
> 核心原则：先建立适配层，再迁移流程；先并行验证，再替换旧实现；不按文件一次性重写。

## 当前迁移结果

截至 Day 22，通用聊天主流程已经完成迁移：

- `newchat` 是唯一聊天入口，使用 LangChain `create_agent` 和 LangGraph 运行时。
- GLM、DeepSeek 已使用对应的 LangChain 模型适配器。
- 六个现有工具已经迁移为 `StructuredTool`。
- 工具审批、网络故障重试、上下文摘要和基础观测已经接入中间件。
- SQLite 同时保存会话目录和 LangGraph checkpoint，`session.thread_id` 用作图的 `thread_id`。
- 每轮完成后，记忆整理通过后台队列与下一轮用户输入并行运行。
- 旧版 `chat` 已从主项目移除并在 E 盘单独归档。
- `taskplan` 功能确认弃用，旧 Planner–Executor 代码已经删除，不纳入后续迁移。

本文后续阶段保留为迁移过程和学习记录；其中关于新旧版本并行、Planner–Executor
迁移及回退旧入口的内容不再代表当前项目状态。

## 一、先建立正确概念

### 1. LangChain 负责“组件标准化”

重点学习以下组件：

- `HumanMessage`、`AIMessage`、`SystemMessage`、`ToolMessage`
- Chat Model 的 `invoke`、`ainvoke`、`astream`
- `@tool`、`StructuredTool`、Pydantic 参数模型
- `bind_tools`
- `with_structured_output`
- Tool middleware

LangChain 在本项目中的主要作用：统一模型、消息、工具及结构化输出接口。

### 2. LangGraph 负责“执行流程”

重点学习以下概念：

- `State`：整个流程共享的数据
- `Node`：执行一个明确步骤的函数
- `Edge`：节点之间的固定连接
- `Conditional Edge`：根据状态选择下一步
- `Command`：更新状态并控制跳转
- `Checkpointer`：保存线程的执行状态
- `interrupt`：暂停流程，等待用户审批或补充信息
- Streaming：向终端持续发送模型和节点事件
- Store：跨线程保存长期信息

LangGraph 在本项目中的主要作用：替换自定义 Agent 主循环，承载会话状态、工具调用、审批和恢复流程。

### 3. 必须区分的三类状态

| 类型 | 用途 | 当前项目对应 |
|---|---|---|
| Graph State | 当前一次工作流中的共享数据 | `AgentState`、步骤结果、法律检索状态 |
| Checkpointer | 保存某个对话执行到了哪里 | Session、暂停与恢复 |
| Long-term Store | 跨对话保存用户事实与长期记忆 | 现有 Qdrant Memory |

不能因为使用 Checkpointer 就删除 Qdrant 长期记忆。

## 二、最终目标架构

```text
Terminal UI
    │
    ▼
Main LangGraph
    ├── General Agent Node
    ├── Legal QA Subgraph
    │   ├── Analyze Query
    │   ├── Check Location
    │   ├── Retrieve Laws
    │   ├── Validate Retrieval
    │   └── Generate Answer / Refuse
    ├── Approval Flow
    └── Memory Flow
        ├── Recall Memory
        └── Save Memory
            │
            ▼
Existing Business Layer
    ├── BM25
    ├── Qdrant
    ├── GLM Embedding
    ├── Web Search
    ├── Email
    └── PowerShell Process Control
```

LangGraph 决定“下一步做什么”，LangChain统一“用什么接口调用”，现有代码继续提供真正的业务能力。

## 三、迁移纪律

1. 不在第一阶段删除现有 `AgentLoop`。
2. 不直接修改六个工具的核心 `handler`。
3. 新旧实现并行存在，通过配置选择运行方式。
4. 每迁移一个边界，都要先完成测试和人工验收。
5. 新实现达到旧实现能力后，才删除对应旧代码。
6. 法律检索质量以现有 Recall@K 测试集为基线。
7. 高风险工具必须先接入审批，再允许进入新 Agent。
8. 每个阶段单独提交 Git，确保可以逐阶段回滚。

## 四、阶段 0：冻结当前基线

### 学习目标

- 理解“业务代码”和“Agent 框架代码”的边界。
- 明确迁移前系统已经具备的能力。

### 实施任务

- 运行现有单元测试并记录结果。
- 运行法律检索 Recall@K 评测并保存结果。
- 手工验证：普通问答、法律查询、记忆查询、命令审批、邮件审批。
- 记录当前终端流式体验和主要错误处理行为。
- 创建迁移分支，例如 `langgraph-migration`。

### 验收标准

- 有一份迁移前测试结果。
- 有一份法律检索质量基线。
- 当前代码已经提交，工作区没有来源不明的修改。

### 推荐提交

```text
dayXX-test:冻结LangGraph迁移前功能与评测基线
```

## 五、阶段 1：学习 LangChain 最小集合

### 学习顺序

1. LangChain 消息对象
2. Chat Model 的同步与异步调用
3. `@tool` 和 Pydantic 参数 Schema
4. `bind_tools`
5. `ToolMessage` 与 `tool_call_id`
6. `with_structured_output`

### 练习任务

- 使用 LangChain 模型接口完成一次普通问答。
- 使用 `with_structured_output` 返回一个简单 Pydantic 对象。
- 将 `current_time` 包装成 LangChain Tool。
- 手动执行模型返回的工具调用，并把 `ToolMessage` 传回模型。

### 暂时不要学习

- 旧版 `LLMChain`
- 大量预制 Chain
- 全部 Document Loader
- 全部 VectorStore 集成
- 复杂 LCEL 表达式

### 验收标准

- 能解释模型为什么不会自动执行工具。
- 能解释 `AIMessage.tool_calls` 和 `ToolMessage` 的关系。
- `current_time` 能通过 LangChain 完成一轮工具调用。

### 推荐提交

```text
dayXX-feature:完成LangChain模型消息与工具调用实验
```

## 六、阶段 2：建立工具适配层

### 目标

保留现有工具核心实现，只为 LangChain 提供新的调用外壳。

### 建议目录

```text
src/ai_agent_learning/
├── tools/
│   ├── builtin/                 # 保持现有 handler
│   ├── contracts.py             # 迁移期保留
│   ├── executor.py              # 迁移期保留
│   └── langchain_adapter.py     # 新增
└── langgraph_agent/             # 后续新增
```

### 迁移顺序

1. `current_time`
2. `search_memory`
3. `search_laws`
4. `web_search`
5. `send_email`
6. `run_command`

### 实施要求

- 每个工具使用明确的 Pydantic 输入模型。
- 保留原有工具描述中的法律时效性和地域限制。
- 保留结构化 `dict` 返回，不要过早转成纯文本。
- 查询类工具可直接调用原有 `handler`。
- 副作用工具先经过原有 `ToolExecutor`，后续再迁移到 middleware。
- 统一工具异常格式，避免把 Python traceback 直接交给模型。

### 安全检查

- `send_email` 必须要求审批。
- `run_command` 必须要求审批。
- 修正 `run_command` 的副作用声明：它可能修改系统和文件，不能视为无副作用工具。
- 保留命令超时、输出截断和 Windows Job Object 清理逻辑。

### 验收标准

- 六个工具均可被 LangChain 识别。
- 参数 Schema 与当前 JSON Schema 的约束基本一致。
- 原有 Agent 仍可继续使用旧工具注册表。
- 新旧入口调用同一个 `handler` 时，业务结果一致。

### 推荐提交

```text
dayXX-feature:新增现有工具的LangChain适配层
```

## 七、阶段 3：学习 LangGraph 基础图

### 学习顺序

1. `StateGraph`
2. `State`
3. Node
4. `START` 和 `END`
5. 普通 Edge
6. Conditional Edge
7. 编译和调用图
8. `recursion_limit`
9. Streaming

### 最小练习图

```text
START
  ↓
call_model
  ↓
是否存在工具调用？
  ├── 是 → tools → call_model
  └── 否 → END
```

### 练习限制

- 只注册 `current_time`。
- 不接长期记忆。
- 不接 Session。
- 不迁移终端 UI。
- 不加入法律专用逻辑。

### 验收标准

- 能画出节点和边。
- 能解释每个 State 字段由哪个节点写入。
- 能通过条件边完成工具循环。
- 达到限制时能够停止，而不是无限循环。

### 推荐提交

```text
dayXX-feature:实现首个LangGraph工具调用工作流
```

## 八、阶段 4：迁移法律问答子图

### 目标

先迁移最有业务价值、最需要确定性控制的法律问答流程。

### 建议状态

```python
class LegalAgentState(TypedDict):
    messages: list
    query: str
    location: str | None
    legal_issues: list
    retrieval_cases: list
    law_results: list
    retrieval_sufficient: bool
    refusal_reason: str | None
```

### 建议流程

```text
START
  ↓
analyze_query
  ↓
check_location
  ├── 缺少必要地域 → ask_location / interrupt
  └── 信息充分
          ↓
build_retrieval_cases
          ↓
retrieve_laws
          ↓
validate_retrieval
  ├── 空结果或不足 → refuse_legal_conclusion
  └── 有可靠候选 → generate_legal_answer
                         ↓
                        END
```

### 程序级保证

- 缺少必要地域信息时，不允许直接选择某地方法规。
- 法律检索为空时，不允许进入“确定法律结论”节点。
- 失效、未生效或时效未知的结果不得直接作为现行依据。
- 模型可以判断候选内容是否相关，但不能绕过空结果分支。
- 检索工具只负责召回，最终回答节点负责筛选实际使用的法规。

### 测试场景

- 全国性法律问题。
- 明确地区的地方性问题。
- 必须知道地区但用户未提供地区。
- 法律检索返回空数组。
- 只返回失效法规。
- 工具连接失败。
- 同时包含多个法律争议点。

### 验收标准

- 空召回时稳定拒答，不依赖模型“自觉”。
- 地域不足时稳定暂停并询问。
- 恢复后从暂停位置继续执行。
- Recall@K 不低于迁移前基线。
- 不修改原有 BM25/Qdrant 核心算法。

### 推荐提交

```text
dayXX-feature:实现可暂停和拒答的法律问答子图
```

## 九、阶段 5：检查点、会话和人工审批

### 学习内容

- Checkpointer
- `thread_id`
- `interrupt()`
- `Command(resume=...)`
- 人工审批 middleware
- 图恢复语义

### 实施任务

- 给每次终端会话分配稳定的 `thread_id`。
- 地区追问使用 `interrupt` 暂停。
- `send_email` 执行前暂停并显示完整参数。
- `run_command` 执行前暂停并显示命令、目录和风险。
- 支持批准、拒绝，必要时支持编辑参数。
- 验证程序重启后能否恢复未完成流程。

### 验收标准

- 暂停不会丢失消息和状态。
- 拒绝工具后，模型能收到明确的拒绝结果。
- 不会因为恢复而重复执行已经产生副作用的工具。
- 每个副作用调用都能关联原始工具调用 ID。

### 推荐提交

```text
dayXX-feature:接入LangGraph检查点与高风险工具审批
```

## 十、阶段 6：迁移终端流式 UI

### 实施任务

- 将模型 token 事件映射到当前 Rich 输出。
- 将节点开始、结束映射为状态提示。
- 将工具开始、成功、失败映射为工具面板。
- 保留“正在处理”提示，但避免第三方库日志破坏 UI。
- 区分模型文本、工具状态和最终回答。
- 中断时显示用户需要补充或审批的内容。

### 验收标准

- 用户能够看到持续的模型输出。
- 工具调用期间不会表现为无响应。
- 工具日志不会混入最终回答。
- 最终回答只显示一次。

### 推荐提交

```text
dayXX-feature:将终端流式界面接入LangGraph事件
```

## 十一、阶段 7：迁移上下文与记忆

### 短期上下文

- 使用 Checkpointer 保存消息和 Graph State。
- 保留现有上下文压缩策略作为独立节点或 middleware。
- 到达 token 阈值时触发摘要，不要每轮都总结。
- 摘要后保留最近若干条原始消息。

### 长期记忆

- 保留现有 Qdrant 集合和 Embedding。
- `search_memory` 继续作为工具或固定召回节点。
- 记忆写入放在主回答完成之后。
- 记忆整理失败不能阻止用户拿到主回答。
- 批量整理逻辑继续保留，减少 GLM 强制思考调用次数。

### 验收标准

- 同一 `thread_id` 能恢复短期会话。
- 新线程可以召回长期记忆。
- 摘要不会覆盖长期记忆。
- 记忆保存失败不会让整个 AgentRun 失败。

### 推荐提交

```text
dayXX-feature:迁移会话上下文并接回长期记忆
```

## 十二、阶段 8：迁移 Planner–Executor

### 建议图结构

```text
plan
  ↓
select_ready_steps
  ↓
execute_steps
  ↓
evaluate_results
  ├── 完成 → synthesize → END
  ├── 可恢复失败 → replan
  └── 不可恢复失败 → fail → END
```

### 迁移原则

- 现有 `TaskPlan` 数据结构先保留。
- Planner 使用结构化输出生成计划。
- Executor 只执行依赖已经满足的步骤。
- 并行步骤使用明确的状态 reducer 合并结果。
- 重规划必须有次数上限。
- 计划执行状态写入 Checkpointer。

### 验收标准

- 计划步骤依赖关系正确。
- 工具失败后能按策略重试、重规划或停止。
- 中断恢复不会重复已经完成的副作用步骤。
- 最终回答能够综合多个步骤结果。

### 推荐提交

```text
dayXX-feature:使用LangGraph重构PlannerExecutor工作流
```

## 十三、阶段 9：全量切换与清理

### 切换前检查

- 普通问答通过。
- 六个工具通过。
- 法律问答场景通过。
- 法律 Recall@K 不下降。
- 长期记忆通过。
- 规划执行通过。
- 人工审批通过。
- 程序中断与恢复通过。
- 终端流式输出通过。
- 错误和超时场景通过。

### 删除旧代码的条件

只有满足以下条件才能删除旧模块：

1. 新旧功能映射已经记录。
2. 新实现有对应测试。
3. 新实现已经成为默认入口。
4. 已经经过一段时间的实际使用。
5. Git 中存在可以恢复旧实现的稳定提交。

### 可能被替换的旧模块

- 自定义 `AgentLoop`
- 部分 `AgentState` / `AgentStep`
- 工具注册表
- 通用模型工具调用解析
- 部分 Session 管理
- 部分工具调度代码

### 必须保留或重新封装的能力

- 法律 BM25/Qdrant 检索
- GLM Embedding
- 法规时效性与地域规则
- 长期记忆提取、去重和更新
- Windows 子进程树清理
- 邮件发送实现
- 终端 UI
- RAG 评测集和评测脚本

### 推荐提交

```text
dayXX-refactor:切换默认入口到LangGraph并清理旧Agent循环
```

## 十四、推荐学习日安排

| 学习日 | 内容 | 当天成果 |
|---|---|---|
| Day 1 | LangChain 消息与模型 | 完成普通异步问答 |
| Day 2 | Tool 与 Pydantic Schema | 包装 `current_time` |
| Day 3 | Tool calling 完整循环 | 手动完成模型—工具—模型 |
| Day 4 | LangGraph State/Node/Edge | 构建固定顺序图 |
| Day 5 | Conditional Edge | 构建最小工具循环 |
| Day 6 | ToolNode 与错误处理 | 接入三个只读工具 |
| Day 7 | Streaming | 接入简化终端输出 |
| Day 8 | Checkpointer | 保存并恢复对话 |
| Day 9 | Interrupt/Command | 实现人工补充信息 |
| Day 10 | 法律查询状态设计 | 建立法律问答子图骨架 |
| Day 11 | 法律召回与空结果分支 | 实现程序级拒答 |
| Day 12 | 地域与时效校验 | 实现地域追问与时效路由 |
| Day 13 | 高风险工具审批 | 接入邮件和命令审批 |
| Day 14 | 长期记忆 | 接回召回与批量写入 |
| Day 15 | Planner–Executor | 构建规划执行子图 |
| Day 16 | 回归测试 | 对比新旧实现 |
| Day 17 | 默认入口切换 | LangGraph 成为主运行时 |
| Day 18 | 清理与文档 | 移除确认无用的旧胶水代码 |

学习日可以拉长，不要为了追求日期进度跳过验收。

## 十五、每阶段通用检查清单

### 功能

- [ ] 正常输入有正确结果
- [ ] 空输入或空召回有明确行为
- [ ] 工具异常不会导致无限循环
- [ ] 最大步骤或递归限制有效
- [ ] 结构化输出校验有效

### 安全

- [ ] 副作用工具需要审批
- [ ] 拒绝后不会继续执行
- [ ] 重试不会重复产生副作用
- [ ] 命令执行仍然清理整个进程树
- [ ] 敏感参数不会无意写入日志

### 状态

- [ ] State 字段有明确所有者
- [ ] 并行写入字段有 reducer
- [ ] 暂停恢复不会重复节点副作用
- [ ] Checkpointer 与长期记忆职责没有混淆

### 质量

- [ ] 法律 Recall@K 未下降
- [ ] 地方性法规不会在未知地域时直接适用
- [ ] 空法律召回不会产生确定结论
- [ ] 时效未知的法规会明确提示核实

### 工程

- [ ] 新功能有测试
- [ ] 原有测试继续通过
- [ ] 每阶段有独立 Git 提交
- [ ] 可以通过配置回退旧实现

## 十六、遇到问题时的判断方法

### “这是 LangChain 问题还是 LangGraph 问题？”

- 模型、消息、工具 Schema、结构化输出：先查 LangChain。
- 状态、节点、跳转、暂停、恢复、持久化：先查 LangGraph。
- BM25、Qdrant、法律数据：属于项目业务层。

### “这个逻辑应该用提示词还是程序分支？”

满足以下任一条件时，优先程序分支：

- 不允许模型绕过。
- 涉及副作用或安全风险。
- 需要暂停等待用户。
- 失败后必须采用确定的恢复路径。
- 需要测试稳定复现。

语言表达、候选相关性判断、内容总结可以交给模型。

### “工具应该是 Node 还是 Tool？”

- 希望模型自行决定是否调用：Tool。
- 业务流程规定必须执行：Node。
- 调用后必须按确定规则分支：通常使用 Node，或者 Tool 后增加校验 Node。
- 法律召回在法律子图中建议作为固定 Node；在全能型 Agent 中也可以同时暴露为 Tool。

## 十七、下一步

通用 Agent 迁移已经完成，下一阶段聚焦法律模块可靠性：

1. 判断用户问题是否属于法律问题。
2. 法律问题固定先查询本地法律库。
3. 对候选法规执行时效和地域适用性过滤。
4. 空召回或没有可用依据时，由程序分支阻止生成法律结论。
5. 验证最终回答确实以检索结果为依据。

该部分涉及不可绕过的业务约束，适合在现有 Agent 外增加明确的 LangGraph 节点和条件边，
而不是只依赖系统提示词。
