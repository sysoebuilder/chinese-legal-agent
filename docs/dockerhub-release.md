# Docker Hub 发布版部署（0.5.0）

发布版使用 Docker Hub 上已构建的镜像。只需下载 `compose.release.yaml`，无需安装 Git、Python、Node.js 或获取项目源码。

| 服务 | 镜像 |
| --- | --- |
| API、Worker | `sysoe/chinese-legal-agent:0.5.0` |
| 初始化 | `sysoe/chinese-legal-agent-init:0.5.0` |
| 前端 | `sysoe/chinese-legal-agent-web:0.5.0` |

PostgreSQL、Redis 和 Qdrant 由 Compose 一并启动。

## 1. 准备运行环境

安装并启动 Docker Desktop，使用 Linux containers，确保容器可以使用 NVIDIA GPU。本次发布的三个业务镜像均为 `linux/amd64`。

初始化需要访问 GitHub Release 和 Hugging Face，拉取镜像需要访问 Docker Hub。后端包含 PyTorch 和 CUDA，首次下载较大，请保持网络连接。

## 2. 下载并启动

在 PowerShell 中执行：

```powershell
New-Item -ItemType Directory -Path chinese-legal-agent -Force | Out-Null
Set-Location chinese-legal-agent
Invoke-WebRequest -Uri https://raw.githubusercontent.com/sysoebuilder/chinese-legal-agent/v0.5.0/compose.release.yaml -OutFile compose.release.yaml
docker compose -f compose.release.yaml pull
docker compose -f compose.release.yaml up -d --wait
```

Compose 会自动创建本机 `data/`、`models/` 目录及持久化数据卷。初始化容器创建配置、下载并恢复法律库和 BM25 索引、下载重排模型；API 和 Worker 在初始化成功后启动。首次运行可能需要较长时间，无需提前填写模型 API Key。

查看初始化进度时，另开 PowerShell，进入同一目录执行：

```powershell
docker compose -f compose.release.yaml logs -f initialize
```

如初始化因网络问题失败，修复网络后重新执行 `up -d --wait`，已有下载缓存会复用。

## 3. 打开网页

访问 <http://127.0.0.1:3000>，在左下角设置中选择 GLM 或 DeepSeek，填写 API Key 并保存。API 文档位于 <http://127.0.0.1:8001/docs>。

**必须配置智谱 API Key（`ZAI_API_KEY`）。本项目的向量化依赖智谱 GLM Embedding-3，法律语义检索和长期记忆需要该 Key，联网搜索也使用智谱服务。即使使用 DeepSeek 对话模型，也必须配置智谱 Key。** 初始化下载资源不需要 API Key，运行项目时应在网页设置中完成配置。

## 日常管理

在保存 Compose 文件的目录执行：

```powershell
# 查看服务状态
docker compose -f compose.release.yaml ps -a

# 查看业务日志
docker compose -f compose.release.yaml logs -f api worker

# 停止服务，保留数据
docker compose -f compose.release.yaml down

# 再次启动
docker compose -f compose.release.yaml up -d --wait
```

配置和模型保存在本机 `data/`、`models/`，数据库、法律索引和队列保存在 Docker 命名卷。`down -v` 会删除命名卷中的数据，普通停止使用 `down`。

发布版项目名为 `chinese-legal-agent`，数据卷名称均以 `chinese-legal-agent_` 开头。

该文件固定使用 `0.5.0`，不会随 `latest` 自动升级。后续升级时，先备份数据，再将三个业务镜像的标签一起改为对应发布版本，并执行 `pull` 和 `up -d --wait`。
