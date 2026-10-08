# 容器存储与项目清理结果（2026-10-07）

运行数据已经统一使用 Docker 命名卷。两个未使用的旧 Docker 卷已删除；用户随后手动删除了旧文件暂存目录及两份旧 Docker 数据盘备份，已确认三个目录均不存在，当前运行数据盘保留。

## 当前运行位置

| 卷 | 内容 |
| --- | --- |
| `agent100_postgres` | 会话、运行记录、日志、checkpoint |
| `agent100_qdrant` | 703271 条法律、107 条记忆 |
| `agent100_redis-data` | Redis/Celery 持久化数据 |
| `agent100_app-data` | BM25 索引、安装状态和应用缓存 |
| `agent100_law-assets` | 法律原始数据、向量离线包 |

Compose 启动直接读取以上卷。PostgreSQL、Qdrant 启动脚本以及 worker 启动脚本均使用 Compose；Qdrant 不再自动启动原生 Windows 服务。Windows Qdrant 可执行文件不再打包。

Docker 数据盘原生路径为 `E:\DockerDesktopData\local\wsl\disk\docker_data.vhdx`。`C:\Users\17743\AppData\Local\Docker` 不使用目录联接，启动后仅重建少量日志和运行时文件。此前藏在该目录联接下的旧 C 盘数据盘已迁至 E 盘备份目录。

## 保留的备份

- `data/backups/20261007-143524/`：完整当前备份，包含 PostgreSQL dump、法律及记忆快照、BM25、法律资源和 Redis；`manifest.json` 记录摘要。PostgreSQL、Qdrant、Redis 在隔离容器中恢复通过，全部 107 条记忆、24 条法律样本和 19 个索引/资源文件验证通过。结果见 `restore-verification.json`。
- `data/backups/legacy-20261007/`：旧主机库、批量生成历史、旧版本 PostgreSQL 记录和记忆。主机归档 4052 个文件逐一验证；两个旧卷归档分别有 1316、389 个文件，与源卷摘要一致。迁移日志和配置保存在 `audit/`。

## 实际删除

- 旧 Docker 卷 `agent100_postgres-data`。
- 旧 Docker 卷 `agent100_qdrant-data`。

当前使用的是没有 `-data` 后缀的 `agent100_postgres`、`agent100_qdrant`，未删除当前数据。

## 已移出项目并由用户删除的文件

23 处旧目录或文件，共 36,629,692,516 字节（约 34.1 GiB），此前暂存于以下目录，该目录现已由用户手动删除：

`E:\DockerDesktopData\backups\project-cleanup-hold-20261007`

包括：

- `data/container-storage/`、`data/postgres-db/`、`data/qdrant-server/`、`data/qdrant-memory-transfer-7edb008b/`、`data/redis/`。
- `data/law-bm25/`、`data/rag/`、`laws/raw/`、`laws/npc-law-vectors.zip` 及其校验文件。
- `data/snapshots/`、`data/container-bind-migration-20261007-120617/`、两份中断的旧备份目录。
- `data/postgresql/`、PostgreSQL Windows 安装包、`src/ai_agent_learning/runtime/qdrant.exe`、根目录 `qdrant.yaml`。
- 旧记忆导出、旧 PostgreSQL dump、恢复报告和主机法规安装状态文件。

逐项原路径、暂存路径和大小见 `data/backups/20261007-143524/cleanup-report.json`。自动删除操作曾被审批拒绝，返回 `blocked by policy`；随后用户自行删除了暂存目录，以及 `Docker-original-from-C-20261007-141100`、`Docker-from-C-20261007-140638` 两份旧数据盘备份。三个目录均已确认不存在，当前 `local\wsl\disk\docker_data.vhdx` 仍存在。

模型配置 `.env`、`active-model.env`、模型目录和 Windows MCP 保留。其他开发缓存不属于本次数据库清理范围。


## 20:03 修正 Docker 重新注册到 C 盘的问题

再次启动时，C 盘生成了约 1.53 GiB 的 `wsl/disk/docker_data.vhdx` 和约 96 MiB 的 `wsl/main/ext4.vhdx`。E 盘原数据盘仍存在，但这次启动未使用它。此前配置目录仍有联接：联接指向的 E 盘配置包含 `CustomWslDistroDir`，而移开联接后显露出的普通 C 盘配置（19:55 写入）没有这个字段。

处理结果：

- 实际配置目录 `C:\Users\17743\AppData\Roaming\Docker` 已恢复为普通目录，其 `settings-store.json` 设置 `CustomWslDistroDir=E:\DockerDesktopData\local\wsl`。这个小配置目录无需迁移；不要再把它替换为旧配置目录联接。
- 使用 `wsl --manage docker-desktop --move E:\DockerDesktopData\local\wsl\main` 移动当前有效发行版。注册表中的有效 `docker-desktop` BasePath 已由 WSL 更新到 E 盘，没有只修改旧的无效注册项。
- 重启 Docker Desktop 后，E 盘原 `docker_data.vhdx` 被实际使用；Compose 重新启动通过，6 个服务 healthy，法律 703271 条、记忆 107 条。
- C 盘约 1.53 GiB 的新建数据盘已确认不在使用（独占打开成功），未再写入。删除这个文件被自动审批拒绝，返回 `blocked by policy`，因此仍残留于 `C:\Users\17743\AppData\Local\Docker\wsl\disk\docker_data.vhdx`，可由用户手动删除。C 盘的原 `main/ext4.vhdx` 已由 WSL 移到 E 盘。

当前有效大文件位置仍为 `E:\DockerDesktopData\local\wsl\disk\docker_data.vhdx`；配置文件位置与数据盘位置是两个独立设置。
