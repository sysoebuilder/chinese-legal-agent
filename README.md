# agent100

**从模型 API 到工具调用：一个持续迭代的 Python Agent CLI。**  
**From model APIs to tool use: an evolving Python Agent CLI.**

Version **0.5.0** · Python **3.11+** · Command **`agent100`**

[中文](#中文) · [English](#english)

## 中文

### 项目与求职方向

我正在寻找 **AI Agent / LLM 应用开发相关岗位**。agent100 是我的个人学习与求职作品，用来展示如何把模型 API 接入推进到可运行、可追踪、有执行边界的 Agent 应用。

项目随我的 100 天 Agent 学习计划迭代。当前版本基于 LangChain 与 LangGraph，实现可持久化、可中断和可扩展的 Agent 流程。目前是持续迭代的个人项目，尚未进行生产环境验证。

如果你正在招聘 Agent 开发者，欢迎通过邮箱 [1774364027w@gmail.com](mailto:1774364027w@gmail.com) 联系我，也可以访问 [GitHub 个人主页](https://github.com/sysoebuilder) 或在本仓库发起技术交流。我希望围绕工具调用、工作流编排和 LLM 应用可靠性开展工作。

### 已实现的能力

| 能力 | 实现内容 |
| --- | --- |
| 多轮对话 | GLM / DeepSeek 流式输出、终端 Markdown 展示、PostgreSQL 会话与 LangGraph 状态持久化 |
| Agent 循环 | LangGraph 驱动模型调用、工具执行、结果回传、中断与恢复 |
| 工具系统 | LangChain `StructuredTool` 统一参数模型和异步调用 |
| 内置工具 | 当前时间、基于 GLM 搜索引擎的网络搜索、QQ SMTP 邮件发送、Windows PowerShell 命令执行 |
| 执行控制 | 风险等级检查；聊天模式下，带副作用的工具执行前展示参数并请求确认 |
| 失败恢复 | `ToolRetryMiddleware` 只重试网络超时、连接异常和可恢复 HTTP 状态 |
| 上下文管理 | LangChain 摘要中间件在达到阈值后压缩旧消息并保留近期对话 |
| 长期记忆 | 每轮对话后异步整理记忆，使用 GLM Embedding-3 和 Qdrant 完成检索、去重与更新 |
| 记忆检索 | 模型在回答需要依赖以往记忆时调用工具，结合全局 `key` 精确查询与向量相似度查询返回相关记忆 |
| 记忆管理 | 分页查看记忆；按 ID 删除，或修改业务字段并重新向量化；修改和删除均需人工确认 |
| 工程基础 | 结构化日志、模块化代码、pytest 测试、wheel 与源码发行包构建 |

### 快速开始

项目根目录可通过 `.\.venv\Scripts\python.exe run.py` 启动聊天。主流程在 `src/ai_agent_learning/langchain_agent/run.py` 中统一创建 Agent 并管理资源，`chat.py` 负责对话循环和流式事件处理。

以下使用 Windows PowerShell。当前命令执行工具仅支持 Windows；工作目录不构成文件访问沙箱。

```powershell
git clone https://github.com/sysoebuilder/agent100.git
cd agent100
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

首次运行 `agent100 config` 时，程序会自动检测配置：选择 GLM 或 DeepSeek，隐藏输入 API Key，再输入模型名称（回车使用默认名称）。服务地址使用内置默认值，无需填写。程序会创建 `data/.env` 并把当前选择写入 `data/active-model.env`。之后再次运行 `config` 可从已配置的提供商中切换模型。详见 [模型配置](docs/model-configuration.md)。

```powershell
.\.venv\Scripts\agent100.exe --version
.\.venv\Scripts\agent100.exe config
.\.venv\Scripts\agent100.exe newchat
```

`newchat` 是当前唯一的聊天入口，提供多轮持久化会话、流式终端输出、工具审批、上下文摘要与异步记忆整理。

法律检索另有独立的 stdio MCP Server，可供 MCP Host 启动并发现 `search_laws` 工具：

```powershell
.\.venv\Scripts\python.exe -m ai_agent_learning.mcp_server
```

要让其他计算机通过 IP 和端口连接，可启动 Streamable HTTP：

```powershell
.\.venv\Scripts\python.exe -m ai_agent_learning.mcp_server --http --host 0.0.0.0 --port 8000
```

远端 MCP 地址为 `http://<公网 IP>:8000/mcp`。若本机在路由器后面，还需要将公网端口转发至这台计算机，并允许该端口通过主机防火墙；HTTP 模式目前不设鉴权。

它暂时复用当前项目的 `data/law-bm25` 索引、Qdrant `npc_laws` 集合和 `data/.env` 中的 `ZAI_API_KEY`；默认连接 `http://127.0.0.1:6333`，也可用 `QDRANT_URL`、`LLM_CLI_DATA_DIR` 覆盖。MCP Server 启动时会检查 Qdrant：已有服务可用时直接复用，默认本机服务不可用时自动启动，并在退出时关闭自行启动的进程；远程地址不可用时报告错误。原有 LangChain 法律检索工具仍可使用。

Web Search 功能基于 GLM 搜索引擎实现，长期记忆使用 GLM Embedding-3 生成向量，两项功能都必须配置 GLM API Key（环境变量名为 `ZAI_API_KEY`）；即使当前对话模型选择 DeepSeek，该配置仍不可省略。邮件工具另需配置 `QQ_SMTP_USERNAME` 和 `QQ_SMTP_AUTH_CODE`，后者是 SMTP 授权码，不是邮箱登录密码。模型、搜索和向量化调用使用你自己的服务账号，可能产生费用。

所有会话的 LangGraph checkpoint 使用 PostgreSQL，在 `data/.env` 中设置 `CHECKPOINT_DATABASE_URL`，例如 `postgresql://agent100:<密码>@127.0.0.1:5432/agent100?connect_timeout=10`。程序首次连接时由官方 checkpointer 创建并升级所需表。Windows 使用线程调用官方同步 `PostgresSaver`，保留命令执行和 stdio MCP 所需的默认事件循环；其他平台使用 `AsyncPostgresSaver`。

会话目录 `sessions`、结构化日志 `logs` 和 checkpoint 全部保存在同一个 PostgreSQL 数据库中。会话和 checkpoint 使用同一个 `thread_id` 关联；日志通过 `session_id`、`trace_id` 查询关联事件，`details_json` 使用 JSONB。启动聊天时自动运行 Alembic 升级业务表；也可单独运行 `.\.venv\Scripts\python.exe -m ai_agent_learning.database`。SQLite 存储及旧会话回退已移除，不导入旧数据。法规和长期记忆仍使用当前 Qdrant 服务，数据位于 `data/qdrant-server/`；历史嵌入式 Qdrant 目录不再使用。可用 `LLM_CLI_DATA_DIR` 指定其他目录。输入 `exit` 或 `quit` 时，程序会等待已入队的记忆任务处理完成。不要提交真实 `.env`、个人会话或记忆数据。

本机已初始化项目内 PostgreSQL 时，可用以下脚本启动或停止。数据库仅监听 `127.0.0.1:5432`，运行文件位于 `data/postgresql/`，数据库文件位于 `data/postgres-db/`；重启电脑后需先启动数据库。脚本支持 `-DataDirectory` 指定其他数据目录。

```powershell
.\scripts\start-postgres.ps1
.\scripts\stop-postgres.ps1
```

### 代码导航

| 路径 | 内容 |
| --- | --- |
| `src/ai_agent_learning/langchain_agent/` | LangChain/LangGraph Agent、会话、中间件与工具适配 |
| `src/ai_agent_learning/langchain_agent/tools/` | 工具实现、LangChain 注册与风险元数据；`run_command/` 包含 Windows 进程管理 |
| `src/ai_agent_learning/mcp_server/` | 独立 stdio MCP Server 与法律检索工具 |
| `src/ai_agent_learning/memory/` | 候选记忆提取、向量化、旧记忆检索、决策及 Qdrant 持久化 |
| `src/ai_agent_learning/ui/` | 终端展示与工具确认 |
| `tests/` | LangChain Agent、工具、配置与终端相关测试 |
| `docs/` | 实现说明与设计细节 |

迁移记录：[LangChain/LangGraph 迁移路线](docs/langchain-langgraph-migration-roadmap.md)

### 测试与打包

```powershell
.\.venv\Scripts\python.exe -m pip install pytest build
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe -m build
```

构建产物位于 `dist/`：`.whl` 用于安装，`.tar.gz` 为源码发行包。项目尚未提供独立 EXE。

## English

### Project and career interests

I am looking for **AI Agent / LLM application engineering roles**. agent100 is my personal learning and portfolio project, demonstrating the progression from a model API integration to a runnable agent with execution controls and traceable behavior.

The project evolves alongside my 100-day Agent learning plan. The current version uses LangChain and LangGraph for persistent, interruptible, and extensible agent execution. This is an actively developed personal project; it has not been validated for production use.

If you are hiring Agent developers, please contact me at [1774364027w@gmail.com](mailto:1774364027w@gmail.com), visit my [GitHub profile](https://github.com/sysoebuilder), or start a technical discussion in this repository. My interests include tool use, workflow orchestration, and reliable LLM applications.

### Implemented features

| Capability | Implementation |
| --- | --- |
| Multi-turn chat | GLM / DeepSeek streaming, terminal Markdown rendering, PostgreSQL sessions, and LangGraph checkpoints |
| Agent loop | LangGraph model/tool execution with interrupts and resume support |
| Tool system | LangChain `StructuredTool` adapters with typed arguments and async execution |
| Built-in tools | Current time, GLM-powered web search, QQ SMTP email, and Windows PowerShell commands |
| Execution controls | Risk checks; chat mode displays arguments and requests approval before side-effecting tools run |
| Failure recovery | `ToolRetryMiddleware` retries network timeouts, connection failures, and recoverable HTTP statuses |
| Context management | LangChain summarization middleware compacts old messages and retains recent context |
| Long-term memory | Background processing after each turn with GLM Embedding-3 and Qdrant retrieval, deduplication, and updates |
| Memory retrieval | Lets the model call a tool when an answer depends on previous memory, combining global exact `key` lookup with vector similarity search |
| Memory management | Lists memories by page; updates business fields with a new embedding or deletes by ID, with human approval for changes |
| Engineering foundations | Structured logs, modular code, pytest tests, and wheel/source distribution builds |

### Quick start

The commands below use Windows PowerShell. The command execution tool currently requires Windows; its working directory is not a filesystem sandbox.

```powershell
git clone https://github.com/sysoebuilder/agent100.git
cd agent100
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

On the first `agent100 config` run, the program detects that configuration is missing, asks you to select GLM or DeepSeek, securely enter an API key, and enter a model name (Enter uses the default). Provider URLs are built in and are not requested. It creates `data/.env` and writes the active selection to `data/active-model.env`. Later `config` runs can switch among configured providers. See [model configuration](docs/model-configuration.md).

```powershell
.\.venv\Scripts\agent100.exe --version
.\.venv\Scripts\agent100.exe config
.\.venv\Scripts\agent100.exe newchat
```

`newchat` is the only chat entry point. It provides persistent multi-turn sessions, terminal streaming, tool approval, context summarization, and background memory processing.

Web Search is powered by the GLM search engine, and long-term memory uses GLM Embedding-3 for vectors. Both require a GLM API key (`ZAI_API_KEY`), even when DeepSeek is selected as the chat model. Email requires `QQ_SMTP_USERNAME` and `QQ_SMTP_AUTH_CODE` (an SMTP authorization code, not your mailbox password). Model, search, and embedding requests use your own service account and may incur charges.

All sessions use PostgreSQL checkpoints, configured through `CHECKPOINT_DATABASE_URL` in `data/.env`. The official checkpointer initializes and upgrades its own tables. Windows runs the synchronous `PostgresSaver` in worker threads to preserve subprocess support; other platforms use `AsyncPostgresSaver`.

Sessions (`sessions`), structured logs (`logs`), and checkpoints all use the same PostgreSQL database. Sessions and checkpoints share `thread_id`; logs expose `session_id`, `trace_id`, and JSONB `details_json`. Chat startup runs Alembic migrations for business tables automatically; run `.\.venv\Scripts\python.exe -m ai_agent_learning.database` to upgrade them separately. SQLite storage and legacy session fallback have been removed, without importing historical data. Laws and long-term memories continue to use the current Qdrant server in `data/qdrant-server/`; obsolete embedded Qdrant stores are no longer used. Set `LLM_CLI_DATA_DIR` to override the location. A project-local PostgreSQL installation can be started with `scripts/start-postgres.ps1` and stopped with `scripts/stop-postgres.ps1`; start it again after rebooting Windows. On `exit` or `quit`, the program waits for queued memory jobs to finish. Keep real `.env` files, personal sessions, and memory data out of version control. The current terminal interface is primarily in Chinese.

### Code map and development

`langchain_agent/` contains the LangChain/LangGraph runtime, sessions, middleware, and registered tools with their implementations; `memory/` handles extraction, embedding, retrieval, decisions, and Qdrant persistence; `ui/` handles terminal interaction. These modules live under `src/ai_agent_learning/`.

`tests/` covers model adapters, agent behavior, tools, runtime, and terminal behavior. `docs/` contains implementation notes, primarily in Chinese.

Migration notes: [LangChain/LangGraph migration roadmap](docs/langchain-langgraph-migration-roadmap.md)

```powershell
.\.venv\Scripts\python.exe -m pip install pytest build
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe -m build
```

Build artifacts are written to `dist/`: a `.whl` installation package and a `.tar.gz` source distribution. A standalone executable is not currently provided.
