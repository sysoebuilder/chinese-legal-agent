# Run API、SSE 与部署

网页通过 Run API 调用 Agent、工具和 PostgreSQL checkpoint。
模型与凭据在网页设置中管理；工具审批和法律问题补充通过中断事件提交回应。

## Docker Compose 启动

打开 Docker Desktop，在项目根目录执行：

```powershell
docker compose up -d --build --wait
```

默认 `compose.yaml` 启动独立前端、API、Celery worker、PostgreSQL、Redis 和 Qdrant。PostgreSQL、Qdrant、Redis 的服务和数据库文件全部位于容器与 Docker 命名卷中。BM25 索引和安装缓存位于应用数据卷，法律离线资源位于资源卷；无需启动本机数据库。
Compose 还会运行一次性 `initialize` 容器：初始化共享配置 JSON，从 GitHub Release 恢复缺失的法律快照和 BM25 索引，并从 Hugging Face 下载缺失的重排模型。已有完整资源会跳过。初始化等待 Qdrant 健康，API 和 Worker 等待初始化成功退出。也可单独执行 `docker compose build initialize` 和 `docker compose run --rm initialize`，只启动 Qdrant 和初始化容器，无需 GPU worker。详见 [初始化容器](model-configuration.md#初始化容器)。
API 与 worker 共用 `Dockerfile` 构建的镜像，基础服务健康后才启动应用。
接口文档仍是 <http://127.0.0.1:8001/docs>。

网页聊天入口为 <http://127.0.0.1:3000/>。React + TypeScript + Vite 在浏览器中渲染界面；
独立的 `frontend` 容器用 Nginx 提供构建产物，FastAPI 不再提供 `/` 页面或 `/assets` 静态资源。
前端的 `/v1/` 请求由 Nginx 代理至 `api:8001`，SSE 禁用代理缓冲并使用长连接超时。
左侧选择历史会话；每次加载最多 10 轮/20 条消息，可继续加载更早的记录。
网页支持实时思考与回答、工具审批、补充信息、停止生成和失败重试。
切换会话或关闭页面只断开 SSE；重新打开运行时，恢复当前状态并继续监听；旧思考和回答增量不重放。
启用 `AGENT_API_TOKEN` 时，在左下角的连接设置中填写访问令牌，令牌仅保存在浏览器会话中。

页面源码位于 `frontend/src/`。本机开发时在 `frontend` 目录执行 `npm ci`、`npm run dev`，
打开 <http://127.0.0.1:5173/>；Vite 将 `/v1` 代理到本机 API 的 8001 端口。
前端镜像通过 Node.js 构建，再由 Nginx 提供资源，运行容器不启动 Vite 开发服务器。
模型配置在网页设置中管理，凭据保存在后端配置目录；开发与验证说明见 `frontend/README.md`。

主机 `data/` 挂载为 `/app/config`，通过 `AGENT_CONFIG_DIR` 读取 `models.json`、`mouldes.json` 和 `secrets.json`，构造 `APPConfig`；首次启动兼容迁移原来的 `.env` 和 `active-model.env`。配置查询及修改接口见 [应用配置](model-configuration.md)，保存使用文件锁和原子替换。`AGENT_DATA_DIR=/app/data` 独立指向 Docker 应用数据卷，数据库与索引不会写入主机配置目录。
Compose 覆盖数据库、Redis 和 Qdrant 地址，连接 `postgres`、`redis` 和 `http://qdrant:6333`，无需修改本机配置文件。
PostgreSQL、Redis 和 Qdrant 映射到本机 `127.0.0.1:5432`、`127.0.0.1:6379`、`127.0.0.1:6333`。本机 `data/.env` 使用以下连接配置（也见 `.env.example`）：

```dotenv
CHECKPOINT_DATABASE_URL=postgresql://agent100:agent100@127.0.0.1:5432/agent100?connect_timeout=10
AGENT_REDIS_URL=redis://127.0.0.1:6379/0
QDRANT_URL=http://127.0.0.1:6333
```

Compose 启动后，在网页模型设置中填写模型和凭据即可开始对话。API、Worker 和法律 MCP 共用数据库及 BM25 索引。

BM25 工作索引保存在应用数据卷，法律检索通过容器中的 Worker 或法律 MCP 执行。数据库连接仍可使用映射出的本机端口。当前部署不接入 Windows 终端 MCP。

如果 `docker` 不在当前终端的 PATH 中，本机可以用以下命令启动：

```powershell
& "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe" compose up -d --build --wait
```

后端 pip 安装步骤默认使用 PyPI，读取超时为 120 秒、连接重试为 10 次；构建隔离环境中的 setuptools/wheel 安装也使用这些设置。下载缓存保存在 BuildKit 中，重复构建复用已下载的包，不进入运行镜像。需要使用其他 Python 包索引时，可通过 `docker compose build --build-arg PIP_INDEX_URL=<索引地址> api` 指定，再执行 `docker compose up -d --wait`。这只影响项目依赖安装，CUDA 版 PyTorch 仍使用其官方 cu128 索引。

旧的手动 API 占用 8001 时，先在它的终端按 Ctrl+C 停止，或指定其他端口：

```powershell
$env:AGENT_API_PORT = '8002'
docker compose up -d --build --wait
```

查看状态、日志与停止：

```powershell
docker compose ps
docker compose logs -f api worker
docker compose down
```

`docker compose down` 保留数据卷，后续 `up` 直接读取卷中的现有数据。`down -v` 会删除数据库、索引和资源卷，必须先备份。

| 卷 | 内容 |
| --- | --- |
| `agent100_postgres` | 会话、运行记录、日志、checkpoint |
| `agent100_qdrant` | `npc_laws` 法律集合、`memories` 长期记忆集合 |
| `agent100_redis-data` | Celery 队列和 Redis 持久化数据 |
| `agent100_app-data` | `law-bm25/`、法规安装状态和临时缓存 |
| `agent100_law-assets` | 法律原始数据、向量离线包 |

备份统一存放在 `data/backups/`。配置继续保留在主机。全新空卷的法律数据和 BM25 索引由初始化容器从固定 GitHub Release 恢复；原始法律离线包无需下载。记忆和历史会话仍需单独恢复备份。

2026-10-07 已生成并验证完整备份 `data/backups/20261007-143524/`：PostgreSQL、Qdrant 和 Redis 在隔离容器中成功恢复，19 个 BM25/资源文件摘要一致。旧库及批量生成历史归档在 `data/backups/legacy-20261007/`。23 处旧工作目录、重复快照及原生数据库文件已移出项目；用户已手动删除暂存目录和两份旧 Docker 数据盘备份。详见 [迁移状态](reports/2026-10-07-container-storage-status.md)。当前 Docker 数据盘通过原生配置直接使用 `E:\DockerDesktopData\local\wsl\disk\docker_data.vhdx`；`C:\Users\17743\AppData\Local\Docker` 的目录联接已移除，C 盘旧数据盘已迁出，其两份临时备份现已由用户删除。Docker 启动时仍可能在 C 盘创建普通日志目录。实际配置 `C:\Users\17743\AppData\Roaming\Docker\settings-store.json` 中的 `CustomWslDistroDir` 指向上述 E 盘 WSL 目录；配置目录使用普通目录，不再依赖旧配置目录联接。20:03 已修正一次默认 C 盘重新注册问题并重新验证；C 盘停用的新建空盘残留见迁移记录。

### 备份与恢复

```powershell
python scripts/backup-data.py
```

此脚本暂时停止原先运行的前端、API 和 worker，导出 PostgreSQL、Qdrant 集合快照、BM25 索引、法律资源和 Redis 数据，最后尝试恢复服务；Docker 故障时需手动确认服务恢复。`manifest.json` 记录集合数量、文件大小及 SHA-256；没有 manifest 的目录视为未完成的备份。备份包含真实会话与记忆，不要提交到 Git。资源备份只归档原始法律与向量离线包；旧批量生成历史需另行归档，不参与运行和常规备份。

恢复到空卷时先创建容器和卷，再启动 PostgreSQL、Qdrant。以下 `<备份目录>` 指 `data/backups/<时间>`：

```powershell
docker compose create postgres qdrant redis api
docker compose up -d --wait postgres qdrant
docker compose cp <备份目录>/postgres.dump postgres:/tmp/restore.dump
docker compose exec -T postgres pg_restore --clean --if-exists --no-owner -U agent100 -d agent100 /tmp/restore.dump
docker compose exec -T postgres rm /tmp/restore.dump
curl.exe --fail -F "snapshot=@<备份目录>/npc_laws.snapshot" "http://127.0.0.1:6333/collections/npc_laws/snapshots/upload?priority=snapshot"
curl.exe --fail -F "snapshot=@<备份目录>/memories.snapshot" "http://127.0.0.1:6333/collections/memories/snapshots/upload?priority=snapshot"
```

在启动 API、worker 和 Redis 前，把 `law-bm25.tar.gz` 解压到 `agent100_app-data` 卷，把 `law-assets.tar` 解压到 `agent100_law-assets` 卷，把 `redis-data.tar.gz` 解压到空的 `agent100_redis-data` 卷。例如，在项目根目录执行：

```powershell
$backup = (Resolve-Path '<备份目录>').Path
docker run --rm --network none --mount "type=bind,source=$backup,target=/backup,readonly" --mount type=volume,source=agent100_app-data,target=/restore --entrypoint tar chinese-legal-agent:local -xzf /backup/law-bm25.tar.gz -C /restore
docker run --rm --network none --mount "type=bind,source=$backup,target=/backup,readonly" --mount type=volume,source=agent100_law-assets,target=/restore --entrypoint tar chinese-legal-agent:local -xf /backup/law-assets.tar -C /restore
docker run --rm --network none --mount "type=bind,source=$backup,target=/backup,readonly" --mount type=volume,source=agent100_redis-data,target=/restore --entrypoint tar chinese-legal-agent:local -xzf /backup/redis-data.tar.gz -C /restore
docker compose up -d --wait
```

不要向正在运行的数据库卷解压文件；恢复现有库前先停止写入并另行备份。迁移备份中的 `legacy-*` 文件保留旧库版本，不会自动覆盖当前库。

Linux 镜像安装 CUDA 12.8 版 PyTorch，Worker 通过 Compose 获取一张 NVIDIA GPU，使用 `prefork` worker。
法律和记忆检索共用的 BGE 重排模型保留自动选择逻辑：CUDA 可用时使用 GPU 和 float16；不可用时退回 CPU 和 float32。
Windows Docker Desktop 需要启用 WSL 2 后端，宿主机安装支持它的 NVIDIA 驱动；CUDA 运行库已包含在镜像中，无需在容器内安装显卡驱动。
API 和 Worker 使用镜像内的源码，不挂载主机源码。修改 Python 源码、依赖或 Dockerfile 后，需要重新构建并重建应用容器：

```powershell
docker compose up -d --build --wait api worker
docker compose exec worker python -c "import torch; from chinese_legal_agent.reranker import load_reranker; _, model = load_reranker(); print(torch.cuda.get_device_name(0), next(model.parameters()).device, next(model.parameters()).dtype)"
```

输出应包含 NVIDIA 显卡名称、`cuda:0` 和 `torch.float16`。API 不执行重排，因此不分配 GPU。
Windows 专用的 `run_command` 工具不向容器内 Agent 注册。
时间、搜索、邮件、记忆和法律工具继续可用。API 自带调度循环，不需要额外启动 beat。

## 分别启动服务（开发调试）

在项目根目录的 PowerShell 中安装新增依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

`data/.env` 中继续配置 `CHECKPOINT_DATABASE_URL` 等部署参数，模型和工具凭据放入 `data/secrets.json`。API 部署参数还可增加：

```dotenv
AGENT_REDIS_URL=redis://127.0.0.1:6379/0
AGENT_API_TOKEN=
AGENT_VISIBILITY_TIMEOUT=3600
```

数据库和 worker 都使用 Compose。先打开 Docker Desktop，再启动共享服务：

```powershell
.\scripts\start-postgres.ps1
.\scripts\start-qdrant.ps1
.\scripts\start-redis.ps1
```

Qdrant 作为独立服务运行，worker 完成某个任务时不会把其他任务使用的 Qdrant 关闭。
Redis 启动脚本运行 `docker compose -f compose.yaml up -d --wait redis`。
配置启用 AOF、持久卷和 noeviction，仅映射本机 6379 端口。
停止使用 `.\scripts\stop-redis.ps1`，保留持久卷中的队列数据。

需要调试本机 API 时，先停止容器 API（避免占用同一个端口），再在终端启动它：

```powershell
docker compose stop frontend api
.\scripts\start-api.ps1
```

```powershell
.\scripts\start-worker.ps1
```

`start-worker.ps1` 在后台启动 Compose worker，复用容器数据库、BM25 索引与法律资源，不创建本机工作库。接口说明：<http://127.0.0.1:8001/docs>。本机 API 默认只监听本机。

API 进程每两秒重新投递尚未成功送入 Redis 的任务。若需要 API 停止期间仍发布数据库内的入队意图，可另启 Celery beat：

```powershell
docker compose run --rm --no-deps worker celery -A chinese_legal_agent.api.celery_app:celery_app beat --loglevel=INFO --schedule=/app/data/celerybeat-schedule
```

beat 是入队意图的补偿发布器。已经入队的任务由 Redis/Celery 交付，不依赖 SSE 连接或 API 进程存活。

## 接口

| 方法与路径 | 行为 |
| --- | --- |
| `GET /v1/sessions` | 返回会话列表，供客户端选择对话 |
| `DELETE /v1/sessions/{thread_id}` | 删除会话、其全部 runs 和所有 namespace 的 checkpoint；成功返回 204 |
| `GET /v1/sessions/{thread_id}/messages` | 分页读取终端与 API 共用的消息历史，每页最多 10 轮 / 20 条消息 |
| `POST /v1/runs` | 创建一轮 Agent 运行，持久化后返回 202 |
| `GET /v1/runs/{run_id}` | 查询状态、中断和完整最终回答 |
| `GET /v1/runs/{run_id}/events` | 读取状态快照并订阅实时 SSE |
| `POST /v1/runs/{run_id}/resume` | 提交全部当前中断的回应，或重试失败的运行 |
| `GET /v1/sessions/{thread_id}/recovery` | 查询未完成的 API 任务、执行锁和可恢复断点 |
| `POST /v1/runs/{run_id}/reconnect` | Worker 已退出时，从当前任务的 checkpoint 重新入队 |
| `POST /v1/runs/{run_id}/cancel` | 请求协作取消 |

`run_id` 对应一轮执行；`thread_id` 对应持续的会话和 LangGraph checkpoint。
新建运行不传 `thread_id` 时创建会话；会话成功完成后，可用同一个 `thread_id` 提交下一轮消息。
同一会话存在排队、执行、等待输入或失败运行时，提交新运行返回 409；请先恢复或取消。
取消保留 checkpoint 和会话历史。等待运行进入 `cancelled` 后，可在同一会话继续发送消息；Worker 会清除已取消运行的待执行节点，不恢复旧审批或重跑旧工具。未保存结果的工具调用会补充停止说明，避免模型将它当作已经完成。此前因旧取消断点而失败的新运行，也可通过 `/resume` 重试。

历史会话列表中可点击垃圾桶按钮，确认后永久删除对应会话。删除当前会话后，页面切换到新对话并清除本地的会话和运行缓存。
删除接口在同一 PostgreSQL 事务中清理 `agent_runs`、`sessions` 以及 `checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 中对应 `thread_id` 的记录。
不存在的会话返回 404；Worker 仍持有会话执行锁或会话状态正在更新时返回 409，须先停止任务并等待结束，或稍后重试。
已入队的旧任务在会话删除后会被 Worker 忽略；删除过程中发生数据库错误时整笔事务回滚。

### 选择对话与读取历史

先调用 `GET /v1/sessions?limit=20&offset=0`，会话按最近更新时间排列：

```json
{
  "sessions": [{"thread_id": "会话 UUID", "name": "对话标题", "created_at": "时间", "updated_at": "时间"}],
  "has_more": false,
  "next_offset": null
}
```

`limit` 为会话数量（1–100），`next_offset` 用于读取下一页。
客户端保存选中的 `thread_id`，读取它的历史，并在下一次 `POST /v1/runs` 中携带该 ID。
选择会话只改变客户端当前查看的对话，不会执行 Agent，也不会设置服务器全局的当前会话。

历史调用 `GET /v1/sessions/{thread_id}/messages?limit=10`：

```json
{
  "thread_id": "会话 UUID",
  "messages": [
    {"id": "user-message-id", "role": "user", "content": "你好"},
    {"id": "assistant-message-id", "role": "assistant", "content": "你好，有什么可以帮你？"}
  ],
  "has_more": true,
  "next_before": "user-message-id"
}
```

默认返回最新 10 轮，页内按从旧到新的顺序排列；`limit` 取值 1–10，超过上限返回 422。
每轮只保留用户输入与最后一条普通回答，过滤系统提示、思考内容、工具调用及上下文总结。
未完成的轮次可能只有用户消息，因此每页最多 20 条，用户输入和回答不会被拆到不同页。
读取更早历史时，将返回的 `next_before` 作为 `before` 参数原样传回；追加新对话不会导致翻页位置偏移。
`has_more=false` 表示没有更早记录；不存在的会话返回 404，无效或其他会话的消息游标返回 422。

终端和 API 共用 `sessions` 目录及同一 `thread_id` 的 PostgreSQL checkpoint。
历史接口读取根图快照，按用户消息 ID 去重，并向旧快照补读上下文压缩前的轮次。
不依赖 API 专用的 `agent_runs` 表，也不会启动模型或工具；刚入队但尚未写入 checkpoint 的消息暂时不会显示。
新会话没有 checkpoint 时返回空消息列表。历史读取保留现有 checkpoint，不创建额外的消息表。

```powershell
$runBody = @{ message = '帮我查询当前时间' } | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8001/v1/runs' -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($runBody))
$run.run_id
$run.thread_id
curl.exe -N "http://127.0.0.1:8001/v1/runs/$($run.run_id)/events"
```

运行返回的 `provider` 和 `model` 表示提交时所选模型。模型参数在提交时冻结，Key 在 worker 执行时读取，不写入任务消息或 SSE。

## SSE 事件

SSE 连接先确认 Redis Pub/Sub 订阅，再读取 run 状态快照；之后直接转发实时事件，不按 0.25 秒轮询数据库。
事件只有 `event` 和 JSON `data`，不使用 `id`、`Last-Event-ID` 或 `after` 补读。

| 事件 | 数据内容 |
| --- | --- |
| `run.state` | 当前 RunView：状态、generation、revision、取消标记、全部中断、最终 answer/error |
| `reasoning.delta` | 模型显式返回的思考文本增量 |
| `answer.delta` | 回答文本增量，可能包含工具调用前的说明 |
| `tool.completed` | 工具名称和调用 ID，不包含原始工具结果 |

```text
event: reasoning.delta
data: {"message_id":"m1","generation":1,"node":"model","text":"模型返回的思考片段"}

event: run.state
data: {"run_id":"运行 UUID","thread_id":"会话 UUID","status":"waiting_approval","generation":1,"revision":3,"interrupts":[{"id":"interrupt-1","value":{"action_requests":[{"name":"send_email","args":{"to":"example@example.org"}}]}}],"cancel_requested":false,"answer":null,"error":null,"provider":"glm","model":"模型名","created_at":"时间","updated_at":"时间"}
```

思考流仅转发模型提供的 reasoning 内容；模型不返回时不会生成此类事件。
以 `run.state` 的 `status=succeeded` 和完整 `answer` 作为最终结果；不要把所有 `answer.delta` 简单拼接成最终回答。
状态提交成功后才发布通知；`revision` 只在状态变化时递增，用于丢弃延迟到达的旧快照。
增量携带 `message_id` 和 `generation`，客户端忽略旧执行代次的片段。

SSE 断开不会取消或重新执行任务。浏览器重连重新获取状态快照，然后继续接收新片段。
Redis Pub/Sub 不保存事件，不补发断线期间的思考和回答增量；刷新可能丢失尚未完成的实时文本，
最终回答、审批与取消状态仍保存在 PostgreSQL。对话历史继续从 checkpoint 读取。
删除事件表不修改 Agent 的 checkpoint 消息内容；模型完整消息自带的 reasoning 仍可能由 checkpointer 保存。

等待输入时 SSE 保持连接，worker 已释放；成功、失败、取消状态发送后关闭连接。
每 15 秒发送心跳并读取一次 run 状态，校准事务已提交但通知未送达的情况；不轮询 checkpoint。
Worker 流式取消检查最多每秒一次，模型/工具调用前和执行结束前强制检查。
实时通道故障不回滚任务状态，不因推送失败重执行 Agent。

升级时 Alembic `0003_live_run_events` 删除旧 `agent_run_events` 表，并把 run 的 `last_event_id` 改名为 `revision`。
旧增量事件随迁移删除；run、会话和 checkpoint 保留。重新构建并重启 API 与 worker，使用同一版本的前端。

## 回应中断

客户端从 `run.state.interrupts` 取得真实中断 ID，按待审批工具顺序逐项给出决策：

```json
{
  "responses": {
    "interrupt-1": {
      "decisions": [{ "type": "approve" }]
    }
  }
}
```

拒绝使用 `{ "type": "reject", "message": "取消发送" }`。
接口不支持编辑工具参数，只接受当前全部中断的回应。过期 ID、遗漏回应或非法决策返回 422。

法律补充信息中断使用：

```json
{
  "responses": {
    "interrupt-2": { "answer": "事情发生在上海" }
  }
}
```

提交到 `POST /v1/runs/{run_id}/resume`。恢复仍使用原 `run_id`、`thread_id` 和 checkpoint；重新入队后 worker 调用 `Command(resume=...)`。
失败的运行可提交 `{ "responses": {} }` 重试；如果 checkpoint 仍有待回应中断，会重新发送中断事件，随后提交回应。

Worker 意外退出可能留下 `running` 状态。前端在选择/刷新会话、SSE 首次异常断开或点击“检查断点”时查询会话的 `recovery`，
使用 `snapshot.next` 判定待执行节点，并确认该 checkpoint 属于当前 run、会话执行锁已释放，
才显示“从断点重连”。点击后 API 再次检查执行锁和 checkpoint，增加 generation 并重新入队；
Worker 使用空输入从原断点继续，不添加用户消息。若断点仍需要审批或补充信息，会再次显示对应表单。
正常执行、没有断点、旧任务断点和已经取消的任务均不允许重连。
刷新或更换浏览器后，选择会话即可从服务器查询其未完成的 API 任务，无需本地 run_id 缓存。
此入口恢复 `agent_runs` 中的任务；无法确认所属运行的历史断点不自动恢复。
断点查询没有固定轮询。Worker 退出不会必然断开浏览器到 API 的 SSE 连接，
所以在 SSE 仍正常但任务无进展时，可手动点击“检查断点”。SSE 的自动重连仍保留退避重试，
同一段没有新事件的连接故障只触发一次自动断点查询，避免故障期间重复读取 checkpoint。

## 任务持久化与执行边界

创建和恢复运行时，状态、入队意图在同一 PostgreSQL 事务中提交，提交后通过 Redis 通知客户端。
发布器成功向 Redis 发送消息后再清除入队意图；Redis 暂时不可用时运行保持 queued，自动重试发布。
数据库提交与 Redis 发布之间的崩溃窗口可能导致重复消息，因此不能假设一次投递。

worker 用 PostgreSQL 会话级 advisory lock 避免并发执行同一会话，用投递代次忽略旧的恢复消息。
模型输入使用固定消息 ID，并检查 checkpoint metadata；已完成的图不会因状态更新前的崩溃再次执行。
消息 late ack，worker 丢失后允许重新交付；强制退出后的恢复可能需要等待 Redis visibility timeout。

这些措施不保证外部副作用恰好执行一次。工具已经发出邮件、但节点结果尚未写入 checkpoint 时崩溃，恢复仍可能重复副作用。
需要恰好一次的工具应另外提供业务幂等键或执行记录。

取消在流式处理时和每次模型/工具调用前检查。已经发出的请求可能继续完成；不会强制终止进程。
原有后台记忆整理流程仍在 worker 进程内运行，尚未单独迁移为 Celery 任务。

## 访问与验证

当前面向本机个人使用，无多用户会话所有权模型。设置 `AGENT_API_TOKEN` 后，所有 `/v1` 接口都要求
`Authorization: Bearer <token>`。浏览器需要设置此请求头时，应使用 fetch 读取 SSE。

默认测试不连接模型、Redis 或 PostgreSQL：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_run_api.py -q
```

显式启用真实 PostgreSQL 测试。测试只操作随机创建的隔离 schema，退出后删除该 schema：

```powershell
$env:AGENT_TEST_POSTGRES = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_run_api_postgres.py -q
Remove-Item Env:AGENT_TEST_POSTGRES
```

Redis/Celery 实际交付测试同样使用隔离 PostgreSQL schema、Redis DB 15 中的随机队列和假 Agent，
不会调用真实模型或发送邮件：

```powershell
$env:AGENT_TEST_REDIS = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_run_api_celery.py -q
Remove-Item Env:AGENT_TEST_REDIS
```

参考：[FastAPI SSE](https://fastapi.tiangolo.com/tutorial/server-sent-events/)、
[LangGraph 中断恢复](https://docs.langchain.com/oss/python/langgraph/interrupts)、
[Celery Redis 交付配置](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)、
[Celery Windows 支持说明](https://docs.celeryq.dev/en/stable/faq.html#does-celery-support-windows)。
