# PostgreSQL 存储切换与旧数据清理修改报告

日期：2026-10-05（Asia/Shanghai）

项目：`E:\ai-agent-100-days-learning`

本次将聊天运行时的 checkpoint、会话目录和结构化日志统一切换到 PostgreSQL，取消旧 SQLite 会话回退。按照用户要求，没有导入旧 SQLite 数据，并已删除指定的旧数据库、旧 JSONL 日志和临时测试文件。

## 1. 修改范围和最终存储分工

| 数据或功能 | 修改前 | 修改后 |
| --- | --- | --- |
| LangGraph checkpoint | 原先在 SQLite；第一轮切换后，新会话在 PostgreSQL，旧会话仍可回退 SQLite | 全部通过 PostgreSQL checkpointer 读写，取消 SQLite 分支 |
| 会话目录 `sessions` | SQLite 中保存会话 ID、名称和时间 | PostgreSQL `public.sessions` |
| 结构化日志 `logs` | SQLite `logs` 表 | PostgreSQL `public.logs`，详细内容使用 JSONB |
| 业务表结构管理 | 在存储类初始化或查询时执行建表 SQL | Alembic 版本迁移，聊天启动时升级到 `head` |
| 法规向量、长期记忆向量 | 当前 Qdrant 服务端 | 继续使用当前 Qdrant 服务端 |
| 历史嵌入式 Qdrant 数据 | 位于旧 `memory/qdrant`、`rag/qdrant` 目录 | 指定目录已删除 |

**PostgreSQL 承担聊天的关系数据与运行状态；当前 Qdrant 服务继续承担法规和长期记忆的向量检索。** 本次没有将向量检索改写为 PostgreSQL/pgvector，也没有删除当前 Qdrant 服务的数据目录。


## 2. 文件修改清单

| 文件 | 修改内容 |
| --- | --- |
| [checkpoint.py](/E:/ai-agent-100-days-learning/src/ai_agent_learning/langchain_agent/checkpoint.py) | 保留 PostgreSQL saver 和 Windows 线程适配；移除 SQLite checkpoint 探测与回退；简化 `open_checkpointer` 参数 |
| [logging_config.py](/E:/ai-agent-100-days-learning/src/ai_agent_learning/logging_config.py) | 替换日志数据库驱动，保留后台队列写入，改用 JSONB 保存日志详细内容 |
| [database.py](/E:/ai-agent-100-days-learning/src/ai_agent_learning/database.py) | 新增连接配置校验、读取配置和 Alembic 程序化升级入口 |
| [migrations/env.py](/E:/ai-agent-100-days-learning/src/ai_agent_learning/migrations/env.py) | 使用应用传入的 SQLAlchemy 连接执行迁移 |
| [0001_sessions_logs.py](/E:/ai-agent-100-days-learning/src/ai_agent_learning/migrations/versions/0001_sessions_logs.py) | 新增会话表、日志表和对应索引的初始迁移 |
| [migrations/__init__.py](/E:/ai-agent-100-days-learning/src/ai_agent_learning/migrations/__init__.py)、[versions/__init__.py](/E:/ai-agent-100-days-learning/src/ai_agent_learning/migrations/versions/__init__.py) | 将迁移目录作为 Python 包，随项目代码提供 |
| [pyproject.toml](/E:/ai-agent-100-days-learning/pyproject.toml) | 移除 SQLite checkpoint 依赖声明，声明 PostgreSQL、Psycopg、Alembic 和 SQLAlchemy 依赖 |
| [.env.example](/E:/ai-agent-100-days-learning/.env.example) | 将连接串说明更新为 checkpoint、会话表和日志表共用的数据库 |
| [README.md](/E:/ai-agent-100-days-learning/README.md) | 更新中英文存储说明、迁移命令、数据库启动方式和 Qdrant 数据位置 |
| [test_checkpoint.py](/E:/ai-agent-100-days-learning/tests/test_checkpoint.py) | 移除旧 SQLite 恢复测试，验证 PostgreSQL 配置、资源释放及平台适配 |
| [test_postgres_storage.py](/E:/ai-agent-100-days-learning/tests/test_postgres_storage.py) | 新增真实 PostgreSQL 迁移、会话、日志和 checkpoint 集成测试 |

[start-postgres.ps1](/E:/ai-agent-100-days-learning/scripts/start-postgres.ps1) 和 [stop-postgres.ps1](/E:/ai-agent-100-days-learning/scripts/stop-postgres.ps1) 是此前安装本地 PostgreSQL 时已添加的脚本，本轮继续使用。

## 3. checkpoint 的具体变化

原先 `open_checkpointer` 接收 PostgreSQL 连接串、SQLite 连接和可选的会话 `thread_id`。如果该 ID 在旧 SQLite 中有 checkpoint，就选择 SQLite。

现在入口为：

```python
async with open_checkpointer(database_url) as saver:
    ...
```

工厂只负责打开 PostgreSQL saver。会话 ID 在执行图时通过 `configurable.thread_id` 提供，工厂不绑定某一个会话。

Windows 分支继续使用 `ThreadedPostgresSaver`，继承官方同步 `PostgresSaver`，通过 `asyncio.to_thread()` 实现异步读写接口，包括读取、列举、保存、写入中间结果、删除 thread 和读取 channel 历史。连接建立、`setup()` 和连接关闭也在线程中执行。

这样保留了 Windows 默认的 Proactor 事件循环，兼容项目中的命令执行和 stdio MCP 子进程。其他平台继续使用官方 `AsyncPostgresSaver`。

缺少配置或连接串语法无效时直接报错，不会创建 SQLite 替代数据库。语法错误提示不包含原始连接串；连接串语法校验与数据库实际认证是两个步骤。

## 4. 会话存储的具体变化

会话数据模型从 `SqliteSession` 改名为 `Session`；存储类从 `SqliteSessionStore` 改为 `PostgresSessionStore`；选择函数从 `select_sqlite_session` 改为 `select_session`。

保留的行为包括：

1. 列出会话，按最近更新时间倒序排列。
2. 选择新会话时返回 `None`，不立刻写入数据库。
3. 收到第一条有效消息后生成 UUID，创建会话记录。
4. 保存模型生成的标题，刷新最后更新时间。
5. 下次启动时从 PostgreSQL 查询并选择已有会话。

SQL 参数由 Psycopg 的 `%s` 占位符传递，会话名称不会拼接到 SQL 文本中。数据库操作在线程中执行；会话存储使用独立的 `autocommit=True` 连接，退出上下文时关闭。

数据库时间列使用带时区的时间戳，Python 创建时间时使用 UTC。读取后转换为 ISO 格式字符串，以兼容现有会话模型和终端显示。

### 会话与 checkpoint 如何关联

```text
创建会话并生成 UUID
        ↓
sessions.thread_id
        ↓ 同一个值
调用图时的 config["configurable"]["thread_id"]
        ↓
checkpoints.thread_id
```

日志中的 `session_id` 也记录这个会话 ID，`trace_id` 用于关联一轮执行。当前没有添加这些表之间的数据库外键；关联由应用传递相同 ID 实现。

## 5. 日志写入的具体变化

`SQLiteLogHandler` 替换为 `PostgresLogHandler`，仍保留以下流程：

```text
业务代码 logger.info / logger.exception
        ↓
LocalQueueHandler
        ↓
进程内日志队列
        ↓
QueueListener 后台线程
        ↓
PostgresLogHandler → PostgreSQL logs 表
```

日志使用独立数据库连接，不与会话或 checkpointer 共用连接。普通字段仍可直接用 SQL 筛选，包括事件名、会话 ID、追踪 ID、模型、工具和耗时。

`details_json` 从 SQLite 的 JSON 字符串改为 PostgreSQL JSONB，通过 `psycopg.types.json.Jsonb` 写入。异常对象保留到后台线程，再格式化保存为异常堆栈。退出时先停止 listener、处理队列中已入队的日志，再关闭数据库连接。

沿用原先日志失败不打断 Agent 的行为：写入异常被捕获，该条日志可能丢失。目前没有自动重连、持久化日志队列或失败重试机制。

## 6. PostgreSQL 表结构

实际确认的数据库：PostgreSQL **17.11**，地址 `127.0.0.1:5432`，数据库名和用户名均为 `agent100`。密码继续保存在项目的私有 `.env` 中，本报告不记录密码。

### `sessions`

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `thread_id` | TEXT，主键 | 会话唯一 ID，与 checkpoint 使用同一个值 |
| `name` | TEXT，非空，默认空字符串 | 会话标题 |
| `created_at` | TIMESTAMPTZ，非空 | 创建时间 |
| `updated_at` | TIMESTAMPTZ，非空 | 最后更新时间 |

索引：`sessions_updated_at`，按 `updated_at DESC` 建立。

### `logs`

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `id` | BIGINT IDENTITY，主键 | 自动生成的日志 ID |
| `timestamp` | TIMESTAMPTZ，非空 | 日志时间 |
| `level`、`logger`、`message` | TEXT，非空 | 等级、日志来源和消息 |
| `event` | TEXT | 结构化事件名 |
| `session_id`、`trace_id` | TEXT | 会话关联和执行追踪 |
| `model` | TEXT | 模型名称 |
| `tool_name`、`tool_call_id` | TEXT | 工具调用信息 |
| `duration_ms` | DOUBLE PRECISION | 耗时，单位毫秒 |
| `error_type` | TEXT | 错误类型 |
| `details_json` | JSONB，非空 | 完整结构化日志内容 |
| `exception` | TEXT | 异常堆栈 |

索引：`logs_timestamp`、`logs_event`、`logs_session_id`、`logs_trace_id`。

### 框架表及版本表

| 表 | 管理者及用途 |
| --- | --- |
| `checkpoints` | LangGraph：checkpoint 的状态和元数据 |
| `checkpoint_blobs` | LangGraph：序列化的 channel 数据 |
| `checkpoint_writes` | LangGraph：执行过程中的中间写入 |
| `checkpoint_migrations` | LangGraph：框架表结构版本 |
| `alembic_version` | Alembic：项目业务表结构版本 |

检查时 `alembic_version` 为 `0001_sessions_logs`，`sessions` 和 `logs` 均为 0 条。测试数据放在独立 schema 中，测试结束后已删除。

## 7. Alembic 和启动流程

Alembic 管理 `sessions` 和 `logs`；LangGraph 表继续由官方 saver 的 `setup()` 管理。两套迁移各自维护版本表。

`migrate_database()` 使用程序化配置调用 `command.upgrade(config, "head")`，SQLAlchemy 负责迁移执行，底层连接仍由 Psycopg 创建。迁移使用事务，并取得 PostgreSQL advisory transaction lock，将多个进程同时启动时的业务表升级串行化。

聊天启动的顺序现在是：

1. 读取私有 `.env`，获取并校验 `CHECKPOINT_DATABASE_URL`。已有环境变量优先。
2. 在线程中执行 Alembic 业务表升级。
3. 在线程中初始化 PostgreSQL 日志写入器。
4. 读取模型配置，启动或复用当前 Qdrant 服务。
5. 打开会话存储连接和 PostgreSQL checkpointer，执行 saver `setup()`。
6. 连接配置中的 MCP Server，显示并选择会话。
7. 创建模型、记忆 worker 和 Agent，进入聊天循环。
8. 退出时关闭记忆 worker、MCP、checkpointer、会话连接和自行管理的 Qdrant 进程，最后刷新并关闭日志连接。

表结构升级不会把旧 SQLite 数据复制到 PostgreSQL。此次切换也没有清空已经存在的 PostgreSQL checkpoint。

## 8. 依赖调整

| 依赖 | 项目声明 | 当前环境已安装版本 |
| --- | --- | --- |
| `langgraph-checkpoint-postgres` | `>=3.1.2,<4` | 3.1.2 |
| `psycopg[binary]` | `>=3.3.6,<4` | 3.3.6 |
| `alembic` | `>=1.20,<2` | 1.20.0 |
| `sqlalchemy` | `>=2.0,<3` | 2.0.54 |

`langgraph-checkpoint-sqlite` 已从项目依赖声明中移除。没有专门卸载虚拟环境中以前安装的 SQLite 相关包，当前聊天代码不再引用它们。

## 9. 已删除和保留的文件

以下目标均已删除，并检查不存在：

| 绝对路径 | 内容 |
| --- | --- |
| `E:\ai-agent-100-days-learning\data\langgraph-checkpoints.sqlite` | 旧 checkpoint、会话和日志数据库 |
| `E:\ai-agent-100-days-learning\data\memory\qdrant` | 旧嵌入式长期记忆数据库 |
| `E:\ai-agent-100-days-learning\data\rag\qdrant` | 旧嵌入式法规数据库 |
| `E:\ai-agent-100-days-learning\data\logs\app.jsonl` | 旧 JSONL 日志 |
| `E:\ai-agent-100-days-learning\data\checkpoint-smoke.py` | 上一轮切换时的临时验证脚本 |
| `E:\ai-agent-100-days-learning\data\test-postgres-storage` | 本轮测试产生的临时目录 |

旧 SQLite 的 `-wal`、`-shm` 和 `-journal` 文件也在数据库清理检查范围内。目录删除前检查了解析后的绝对路径属于项目 `data` 目录，并检查目标及内部没有文件系统链接。

当前 Qdrant 数据目录 `E:\ai-agent-100-days-learning\data\qdrant-server` 已保留。PostgreSQL 安装目录、数据目录、真实配置、法规原始资源和 BM25 索引继续保留。

正式测试代码保留在项目的 `tests/` 中；本次删除的是旧临时测试脚本和测试输出目录。

旧数据库及 JSONL 日志是按要求直接删除，本轮没有制作数据备份，因此修改后的程序不能恢复这些已删除的旧会话。

## 10. 验证结果及覆盖范围

上一轮代码修改完成后执行全量测试：**73 项通过**，含真实 PostgreSQL 集成测试。只有一条已有的 `langchain-community` 弃用提示。此次补充清理只删除日志和临时文件，没有再次修改运行代码，也没有重复执行全套测试。

| 验证内容 | 结果 |
| --- | --- |
| 连接串缺失和语法无效 | 正确报错，语法错误信息不暴露原始连接串 |
| Windows saver 生命周期 | 连接、初始化和释放在线程中执行，初始化失败也释放连接 |
| saver 异步接口 | 委托到线程中的同步实现，传递读写参数 |
| 其他平台分支 | 使用官方异步 saver 的模拟生命周期测试通过 |
| Alembic 重复执行 | 可重复升级，版本记录正确，时间和 JSONB 类型正确 |
| 会话存储 | 创建、改名、更新时间、排序及关闭后重连读取通过 |
| SQL 参数处理 | 含引号和 SQL 片段的标题作为普通文本保存 |
| 日志 | 中文文本、关联字段、JSONB 布尔值和异常堆栈写入正确，退出时队列刷新通过 |
| checkpoint | 使用会话 ID 保存状态，关闭重连后恢复待处理 interrupt，并成功继续执行 |
| Windows 子进程 | 图恢复后仍可创建并执行异步子进程 |
| 聊天生命周期 | 选择新会话后立即退出不创建记录，资源按预期关闭 |
| 集成测试隔离 | 独立 schema 在测试结束后删除，未留下测试 schema |
| 依赖与差异格式 | `pip check` 无损坏依赖；`git diff --check` 无差异格式错误 |
| 补充清理 | 指定旧日志、临时脚本和测试目录均已删除 |

真实数据库验证运行在当前 Windows 环境。其他平台只有分支模拟测试，未在 Linux/macOS 实际数据库环境中执行。本轮未进行真实模型、邮件或远程 MCP 调用的端到端验证。

集成测试使用 `CHECKPOINT_TEST_DATABASE_URL`，未设置时默认跳过。测试创建独立 schema，并在结束时删除；连接角色需要具备创建 schema 的权限。

## 11. 日常运行和查询

本地数据库仍是项目目录内的独立 PostgreSQL 进程，当前未配置 Windows 服务或开机自启。聊天程序连接数据库，不负责启动 PostgreSQL。

### 启动、检查、停止数据库

```powershell
& 'E:\ai-agent-100-days-learning\scripts\start-postgres.ps1'

& 'E:\ai-agent-100-days-learning\data\postgresql\pgsql\bin\pg_isready.exe' -h 127.0.0.1 -p 5432

& 'E:\ai-agent-100-days-learning\scripts\stop-postgres.ps1'
```

手动启动的等价命令：

```powershell
& 'E:\ai-agent-100-days-learning\data\postgresql\pgsql\bin\pg_ctl.exe' `
  -D 'E:\ai-agent-100-days-learning\data\postgres-db' `
  -l 'E:\ai-agent-100-days-learning\data\postgres.log' `
  -w start
```

### 单独升级业务表

```powershell
Set-Location 'E:\ai-agent-100-days-learning'
& 'E:\ai-agent-100-days-learning\.venv\Scripts\python.exe' -m ai_agent_learning.database
```

此命令只升级业务表。LangGraph 表在打开 checkpointer 时由 `setup()` 升级。迁移脚本虽提供 `downgrade()`，它会删除业务表；日常运行不需要执行它。

### pgAdmin 中查询

展开服务器 `agent100 → 数据库 agent100 → 架构 public → 表`，刷新后可以看到会话、日志及 checkpoint 表。右键表选择查看数据，或在 Query Tool 执行：

```sql
-- 最近使用的会话
SELECT thread_id, name, created_at, updated_at
FROM public.sessions
ORDER BY updated_at DESC
LIMIT 20;

-- 最近的结构化日志
SELECT id, timestamp, level, event, session_id, trace_id, message
FROM public.logs
ORDER BY id DESC
LIMIT 100;

-- 读取 JSONB 中的附加字段
SELECT id, event, details_json ->> 'tool_name' AS tool_name,
       details_json ->> 'approved' AS approved
FROM public.logs
ORDER BY id DESC
LIMIT 100;

-- 按会话串联日志：替换示例 ID
SELECT timestamp, event, trace_id, message, details_json
FROM public.logs
WHERE session_id = '替换为实际 thread_id'
ORDER BY timestamp, id;

-- 查看该会话的 checkpoint 索引
SELECT thread_id, checkpoint_id, parent_checkpoint_id, metadata
FROM public.checkpoints
WHERE thread_id = '替换为实际 thread_id'
ORDER BY checkpoint_id DESC
LIMIT 20;
```

checkpoint 中部分内容使用框架序列化格式，查看完整对话状态时使用图的 `aget_state()` 更合适。

## 12. 当前实现的边界

业务表、日志和 checkpoint 虽然共用同一个数据库，但使用独立连接，没有把一次聊天的所有写入组成一个共同事务。会话记录成功写入后，如果后续图执行失败，该会话可能暂时没有 checkpoint。

数据库不可用时不会回退到 SQLite。日志队列是进程内队列，突然终止进程可能丢失尚未写入的日志；当前没有补偿或自动重试。

本次完成的是存储实现和表结构管理切换，没有增加用户权限体系、日志保留策略、连接池配置或数据库备份机制。现有 Qdrant 法规检索、记忆流程和用户故意设置的工具审批配置保持原有行为。
