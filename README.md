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

聊天在 Compose worker 中运行，复用卷中的数据库、法律资源和 BM25 索引。主流程在 `src/ai_agent_learning/langchain_agent/run.py` 中统一创建 Agent 并管理资源，`chat.py` 负责对话循环和流式事件处理。

以下使用 Windows PowerShell。当前命令执行工具仅支持 Windows；工作目录不构成文件访问沙箱。

```powershell
git clone https://github.com/sysoebuilder/agent100.git
cd agent100
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

首次运行 `agent100 config` 时，程序会自动检测配置：选择 GLM 或 DeepSeek，隐藏输入 API Key，再输入模型名称（回车使用默认名称）。服务地址使用内置默认值，无需填写。程序将模型、插件和密钥分别写入 `data/models.json`、`data/mouldes.json`、`data/secrets.json`，启动时统一加载为 `APPConfig`。之后再次运行 `config` 可从已配置的提供商中切换模型。配置接口和插件开关详见 [应用配置](docs/model-configuration.md)。

```powershell
.\.venv\Scripts\agent100.exe --version
.\.venv\Scripts\agent100.exe config
docker compose up -d --build --wait
docker compose run --rm worker agent100 chat
```

`chat` 是终端聊天入口，提供多轮持久化会话、流式终端输出、工具审批、上下文摘要与异步记忆整理；网页聊天入口见下方 Run API 说明。首次创建空卷时需要先恢复数据备份，步骤见 [部署说明](docs/run-api.md)。

法律检索另有独立的 stdio MCP Server，可供 MCP Host 启动并发现 `search_laws` 工具：

```powershell
docker compose run --rm -T --no-deps worker python -m ai_agent_learning.mcp_server
```

要让其他计算机通过 IP 和端口连接，可启动 Streamable HTTP：

```powershell
docker compose run --rm --no-deps -p 8000:8000 worker python -m ai_agent_learning.mcp_server --http --host 0.0.0.0 --port 8000
```

远端 MCP 地址为 `http://<公网 IP>:8000/mcp`。若本机在路由器后面，还需要将公网端口转发至这台计算机，并允许该端口通过主机防火墙；HTTP 模式目前不设鉴权。

法律检索复用 Compose 应用数据卷中的 BM25 索引和 Qdrant `npc_laws` 集合；模型与工具凭据读取主机 `data/secrets.json`，首次启动兼容迁移旧模型配置。先启动 Compose 数据库，在容器中运行法律 MCP 或聊天程序。Qdrant 不可用时报告错误，不再自动创建本机数据库。

Web Search 功能基于 GLM 搜索引擎实现，长期记忆使用 GLM Embedding-3 生成向量，两项功能都必须配置 GLM API Key（环境变量名为 `ZAI_API_KEY`）；即使当前对话模型选择 DeepSeek，该配置仍不可省略。邮件工具另需配置 `QQ_SMTP_USERNAME` 和 `QQ_SMTP_AUTH_CODE`，后者是 SMTP 授权码，不是邮箱登录密码。模型、搜索和向量化调用使用你自己的服务账号，可能产生费用。

所有会话的 LangGraph checkpoint 使用 PostgreSQL，在 `data/.env` 中设置 `CHECKPOINT_DATABASE_URL`，例如 `postgresql://agent100:<密码>@127.0.0.1:5432/agent100?connect_timeout=10`。程序首次连接时由官方 checkpointer 创建并升级所需表。Windows 使用线程调用官方同步 `PostgresSaver`，保留命令执行和 stdio MCP 所需的默认事件循环；其他平台使用 `AsyncPostgresSaver`。

会话、结构化日志和 checkpoint 保存在 PostgreSQL；法规和长期记忆保存在 Qdrant。数据库服务全部由 Compose 管理，数据位于 Docker 命名卷：`agent100_postgres`、`agent100_qdrant`、`agent100_redis-data`。BM25 索引位于 `agent100_app-data`，法律离线资源位于 `agent100_law-assets`。主机保留配置、MCP 运行日志和 `data/backups/` 快照。旧工作库已移出项目，暂存文件的删除状态见 [迁移记录](docs/reports/2026-10-07-container-storage-status.md)。程序退出不停止数据库，不再自动启动原生数据库。

以下脚本启动或停止 Compose 中的 PostgreSQL，保留命名卷中的数据；数据库映射到 `127.0.0.1:5432`。

```powershell
.\scripts\start-postgres.ps1
.\scripts\stop-postgres.ps1
```

### Run API 与 SSE

新增 FastAPI 运行入口：创建和查询运行，通过 SSE 接收模型思考、回答及工具审批/补充信息中断；客户端提交回应后从现有 checkpoint 恢复。Redis + Celery 调度任务，PostgreSQL 保存运行和可重连事件。终端模型配置和聊天入口继续可用。

安装 Python 3.11+ 并打开 Docker Desktop，在项目根目录运行 `./scripts/start-compose.ps1`。脚本会自动创建 Windows 本机 `.venv`、根据 `pyproject.toml` 安装依赖和 MCP provider，再执行 `docker compose up -d --build --wait`，启动 API、Celery worker、PostgreSQL、Redis 和 Qdrant。首次使用不用手动创建 `.venv` 或执行 pip；依赖声明没有改变时会跳过重复安装。`-PrepareOnly` 只准备本机 Python 环境，`-SkipBuild` 使用已有容器镜像，`-Reinstall` 强制重跑依赖安装。已有模型配置直接复用 `data/` 下的配置文件。容器 Worker 需要 NVIDIA GPU，使用 CUDA 12.8 版 PyTorch 和 float16 加速本地 BGE 重排模型。

Compose 的 PostgreSQL、Redis、Qdrant 分别映射到本机 `127.0.0.1:5432`、`127.0.0.1:6379`、`127.0.0.1:6333`，容器通过服务名连接。运行 `docker compose up -d --build --wait` 直接复用命名卷；`docker compose down` 保留数据，`down -v` 会删除数据卷。在容器中运行终端聊天：`docker compose run --rm worker agent100 chat`。备份：`python scripts/backup-data.py`，数据库快照、BM25 索引和资源归档统一保存在 `data/backups/<时间>/`。恢复步骤见 [部署说明](docs/run-api.md)。

Linux worker 通过 Windows 本机 MCP 执行 PowerShell。Windows 上的 Compose（2.36.0 或更新版本）通过 `windows_terminal` provider 自动启动宿主机 MCP，等认证就绪检查通过后再启动 worker。本机环境由 `start-compose.ps1` 自动准备，准备好后也可以直接使用 `docker compose up -d --build --wait`。无需单独开终端启动 MCP。首次启动会生成 `WINDOWS_MCP_TOKEN` 并把 `WINDOWS_MCP_URL=http://host.docker.internal:8765/mcp` 写入 `data/.env`；已有配置保留。服务仅监听 `127.0.0.1:8765`，初始化、发现和调用都要求 Bearer token。

服务及其执行的 PowerShell 命令均隐藏窗口运行，Compose 启动完成后可以关闭启动终端。重复 `compose up` 会复用同一进程；`docker compose down` 会停止 MCP，支持 provider stop 的 Compose 版本也可用 `docker compose stop`。MCP 进程状态和日志位于 `data/compose-mcp/`，`docker compose ps` 中不会显示一个 MCP 容器。进程异常退出后重新运行 `compose up` 即可恢复。原有 `./scripts/start-mcp-terminal.ps1` 保留用于手动调试；切换为 Compose 管理前需停止手动启动的 MCP，避免端口占用。Linux 主机部署时应移除这个 Windows provider 及 worker 对它的依赖，并配置独立的 Windows MCP 地址。

服务及启停管理都在 `src/ai_agent_learning/mcp_terminal.py`。不使用脚本或 Compose 时，在项目根目录执行：

```powershell
# 默认在后台启动；加 start 也可以。
.\.venv\Scripts\python.exe -m ai_agent_learning.mcp_terminal
# 停止后台服务。
.\.venv\Scripts\python.exe -m ai_agent_learning.mcp_terminal stop
# 前台调试，占用当前终端，Ctrl+C 停止。
.\.venv\Scripts\python.exe -m ai_agent_learning.mcp_terminal serve --configure
```

也可以直接运行该文件：`.\.venv\Scripts\python.exe src/ai_agent_learning/mcp_terminal.py start`。`start` 用独立后台 `pythonw.exe` 进程运行同一文件的 `serve` 模式，认证就绪后启动命令退出；启动器及实际解释器均无需控制台，后台进程脱离启动器的 Windows Job，输出写入日志。`serve` 只运行 HTTP 服务，不再次创建子进程。Compose 的 provider 程序也是该模块的入口，使用 `compose up/down/stop` 参数适配 Compose 的调用格式。

worker 中的工具名是 `windows_terminal__run_command`，`working_directory` 必须填写 Windows 上已有目录的绝对路径（例如 `E:\ai-agent-100-days-learning`）。执行复用现有终端工具，保留 1–300 秒超时、输出限制及子进程清理。配置了终端 MCP 而服务不可用时，任务会报告连接错误。Windows 的 `open_chat_agent` 会剔除该 MCP，继续使用内置 `run_command`。`WINDOWS_MCP_URL` 留空时不接入终端 MCP；token 允许执行当前 Windows 用户权限内的命令，应保存在本机配置中。

启动与接口示例见 [Run API 使用说明](docs/run-api.md)。React + TypeScript + Vite 前端独立部署在 `http://127.0.0.1:3000/`，API 默认监听 `127.0.0.1:8001`，交互接口说明位于 `http://127.0.0.1:8001/docs`。Compose 同时启动前端与后端；FastAPI 不提供页面或静态资源。网页与终端共用会话历史，支持实时思考/回答、工具审批、补充信息、停止生成和历史分页。前端开发与组件说明见 [frontend/README.md](frontend/README.md)。容器数据库使用独立持久卷，本机历史数据需要另行迁移；Windows 命令工具在 Linux 容器中不注册。

### 代码导航

| 路径 | 内容 |
| --- | --- |
| `src/ai_agent_learning/langchain_agent/` | LangChain/LangGraph Agent、会话、中间件与工具适配 |
| `src/ai_agent_learning/langchain_agent/tools/` | 工具实现、LangChain 注册与风险元数据；`run_command/` 包含 Windows 进程管理 |
| `src/ai_agent_learning/mcp_server/` | 独立 stdio MCP Server 与法律检索工具 |
| `src/ai_agent_learning/config/` | APPConfig、配置文件加载、旧配置迁移和原子更新 |
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

On the first `agent100 config` run, the program detects that configuration is missing, asks you to select GLM or DeepSeek, securely enter an API key, and enter a model name (Enter uses the default). Provider URLs are built in and are not requested. It stores models, plugins, and credentials in `data/models.json`, `data/mouldes.json`, and `data/secrets.json`, loaded into `APPConfig` at startup. Later `config` runs can switch among configured providers. See [application configuration](docs/model-configuration.md).

```powershell
.\.venv\Scripts\agent100.exe --version
.\.venv\Scripts\agent100.exe config
docker compose up -d --build --wait
docker compose run --rm worker agent100 chat
```

`chat` provides persistent multi-turn sessions, terminal streaming, tool approval, context summarization, and background memory processing. Restore a backup before using new empty volumes; see [deployment and recovery](docs/run-api.md).

Web Search is powered by the GLM search engine, and long-term memory uses GLM Embedding-3 for vectors. Both require a GLM API key (`ZAI_API_KEY`), even when DeepSeek is selected as the chat model. Email requires `QQ_SMTP_USERNAME` and `QQ_SMTP_AUTH_CODE` (an SMTP authorization code, not your mailbox password). Model, search, and embedding requests use your own service account and may incur charges.

All sessions use PostgreSQL checkpoints, configured through `CHECKPOINT_DATABASE_URL` in `data/.env`. The official checkpointer initializes and upgrades its own tables. Windows runs the synchronous `PostgresSaver` in worker threads to preserve subprocess support; other platforms use `AsyncPostgresSaver`.

Sessions, logs, and checkpoints use PostgreSQL; laws and long-term memory use Qdrant. Compose stores all database files in named Docker volumes (`agent100_postgres`, `agent100_qdrant`, `agent100_redis-data`). BM25 indexes use `agent100_app-data`, and offline law assets use `agent100_law-assets`. Host storage retains configuration, MCP logs, and backups under `data/backups/`. Old working directories have been moved outside the project; temporary copies still await deletion, as recorded in the [migration report](docs/reports/2026-10-07-container-storage-status.md). Run terminal chat inside the container with `docker compose run --rm worker agent100 chat`. Run `python scripts/backup-data.py` to save backups. `docker compose down` retains volumes; `down -v` deletes them. Qdrant no longer starts a native Windows database automatically.

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
