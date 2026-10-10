# 应用配置

API 与 Worker 启动时读取四个 JSON 文件并创建 `APPConfig`：

| 文件 | APPConfig 字段 | 用途 |
| --- | --- | --- |
| `models.json` | `modelconfig` | 当前模型、可选模型、地址与 Key 引用 |
| `mouldes.json` | `mouldesconfig` | 内置工具、MCP 插件和法律子图开关 |
| `secrets.json` | `secretsconfig` | 模型和插件所用的 Key |
| `permissions.json` | `permissionsconfig` | Agent 工具审批模式 |

默认目录为项目的 `data/`，可通过 `AGENT_DATA_DIR` 修改默认数据目录，或用 `AGENT_CONFIG_DIR` 单独指定配置目录。Compose 将主机 `data/` 挂载为 `/app/config`，API 和 Worker 共用配置文件。

首次加载创建缺少的文件。四个文件都不存在时，自动迁移同目录的 `.env` 和 `active-model.env`，保留旧文件；此后模型设置以 JSON 为准。数据库、Redis、Qdrant 等部署参数仍从环境变量或 `.env` 读取。无效 JSON 会报错，不会被默认值覆盖。

## 初始化容器

独立的一次性 `initialize` 服务通过 `Dockerfile.init` 构建，初始化配置、法律数据和重排模型。镜像只安装配置解析、HTTP 下载和 Hugging Face 下载依赖，不安装 PyTorch/CUDA，也不加载模型。它等待 Qdrant 健康后运行，不依赖 PostgreSQL、Redis 或模型 API Key。

在项目根目录单独执行：

```powershell
docker compose build initialize
docker compose run --rm initialize
```

本机 `data/` 挂载到容器 `/app/config`。初始化会创建缺少的 `models.json`、`mouldes.json`、`secrets.json`、`permissions.json`，保留已有文件及旧配置迁移行为；重复执行不会重置模型、插件、密钥或权限。无效 JSON 或目录不可写时以非零状态退出，日志不输出凭据。没有旧配置时，密钥文件为空，可随后通过网页设置填写。

配置初始化后依次检查法律资源和重排模型：

- 法律数据来自 [laws-20261007 Release](https://github.com/sysoebuilder/chinese-legal-agent/releases/tag/laws-20261007)，先校验固定 SHA-256 的下载清单，再逐个校验附件。缺少法律集合时，拼接并解压快照，恢复 Qdrant `npc_laws` 的 703,271 条记录；缺少 BM25 索引时，只下载索引包并恢复到 `/app/data/law-bm25`。两者完整时跳过大文件下载。现有非空集合数量或向量配置不匹配时退出，不覆盖它。
- 重排模型来自官方 `BAAI/bge-reranker-v2-m3`，固定到版本 `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`。检查配置、分词器和 Safetensors 权重完整性，缺失或损坏时只下载对应文件。初始化容器将本机 `models/` 挂载为可写 `/app/models`，API 和 Worker 继续只读使用同一目录。

法律分卷和快照缓存保存在应用数据卷 `/app/data/law-initialize`。下载失败保留 `.partial` 文件，下次使用 HTTP Range 续传并校验；Hugging Face 下载使用模型目录中的自身缓存。首次空卷安装需要下载约 3.42 GB 法律资源；模型缺失时还需约 2.29 GB。法律缓存、解压快照、工作索引和模型合计需要约 10 GB 可用空间，Qdrant 存储另计。

只生成配置，不连接 Qdrant 或下载模型：

```powershell
docker compose run --rm --no-deps -e LAWS_INIT_ENABLED=false -e RERANKER_INIT_ENABLED=false initialize
```

Qdrant 已运行时可使用 `docker compose run --rm --no-deps initialize`。如需使用其他法律数据发布版本，必须同时设置 `LAWS_MANIFEST_URL` 和 `LAWS_MANIFEST_SHA256`；初始化不会自动追随最新 Release。

常规 `docker compose up -d --build --wait` 会自动运行初始化容器，API 和 Worker 通过 `service_completed_successfully` 等待其成功完成。初始化结束后容器处于正常退出状态，不是持续运行的服务。

初始化不创建 Windows 终端 MCP 插件，旧 `.env` 中的 `WINDOWS_MCP_URL` 也不会启用它。当前 Agent 忽略旧配置中名为 `windows_terminal` 的插件及其工具，Compose 不启动宿主机终端服务。

也可以在本机执行同一入口：

```powershell
.\.venv\Scripts\python.exe -m chinese_legal_agent.initialize
# 指定其他目录：
.\.venv\Scripts\python.exe -m chinese_legal_agent.initialize --config-dir ./data
# 本机同时初始化法律和重排模型，需要先提供可访问的 QDRANT_URL：
.\.venv\Scripts\python.exe -m chinese_legal_agent.initialize --laws --reranker
```

本机入口默认只处理配置，可用 `--laws`、`--reranker` 启用资源阶段，或用 `--no-laws`、`--no-reranker` 显式跳过。`--data-dir` 指定索引/缓存目录，`--model-dir` 指定模型目录。此入口不生成部署参数 `.env`，不启动数据库服务；启用法律阶段时会恢复空的 Qdrant 法律集合。

## 模型与凭据

通过网页的模型设置选择 GLM 或 DeepSeek、填写 Key 与模型名称。保存时模型写入 `models.json`，Key 写入 `secrets.json`，不生成包含 Key 的模型快照。默认模型分别为 `GLM-5.3-flash` 和 `deepseek-flash`；模型名称不做远程可用性验证。

`models.json` 示例：

```json
{
  "active": "deepseek",
  "models": {
    "deepseek": {
      "provider": "deepseek",
      "model": "deepseek-flash",
      "base_url": "https://api.deepseek.com",
      "api_key_ref": "DEEPSEEK_API_KEY"
    }
  }
}
```

`active` 对应 `models` 的键，支持配置多个模型。`provider` 支持 `glm` 和 `deepseek`。`api_key_ref` 是 secrets 的键名，不是 Key 本身。

`secrets.json` 使用 `{"keys":{"DEEPSEEK_API_KEY":"your-key"}}` 的结构。也可保存 `ZAI_API_KEY`、`GITHUB_PERSONAL_ACCESS_TOKEN`、`QQ_SMTP_USERNAME` 和 `QQ_SMTP_AUTH_CODE` 等工具凭据。文件中的值优先，未设置的键兼容从环境变量读取；空字符串明确禁用凭据，不会回退。Web Search 和记忆向量化仍需要 `ZAI_API_KEY`。

`secrets.json` 保存明文凭据，默认 `data/` 已被 Git 忽略。

## 插件和法律子图

`mouldes.json` 的 `plugins` 按工具名或 MCP 服务名配置 `able`。内置插件使用 `kind: "builtin"`，HTTP 和 stdio MCP 分别使用 `http` 和 `stdio`。默认保留原有内置工具、GitHub 和 Cloudflare Docs。Windows 终端 MCP 不参与当前 Agent 的工具连接。

以下局部配置可发送给修改接口：

```json
{
  "legal": {"able": false},
  "plugins": {
    "web_search": {"able": false},
    "github": {
      "kind": "http",
      "able": true,
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": {"Authorization": "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}"}
    }
  }
}
```

`able=false` 的插件不会向模型注册工具，也不会启动或连接 MCP。MCP 工具名为 `服务名__工具名`，整组跟随所属插件开关。HTTP `headers` 和 stdio `env` 支持 `${KEY_NAME}` 引用 secrets，请将真实 Key 放在 secrets 中。

网页设置的“插件”页支持新增和编辑 HTTP/stdio MCP 连接，填写地址或命令、参数、工作目录、请求头、环境变量、超时和代理选项；内置工具继续只配置开关。下方“凭据”区域列出已保存及模型/插件引用的 Key，支持新增、更新、停用、删除保存值。输入留空保留原值，成功后清空，失败时保留供重试。界面只显示文件配置状态，未写入 JSON 的环境变量不会被显示为“文件已配置”。

主图新增 `check_legal_enabled` 节点：

```text
START → check_legal_enabled
          ├─ legal.able=true  → legal_flow → 原有法律/通用流程
          └─ legal.able=false → general_agent → END
```

关闭法律子图会跳过法律分类、检索和补充信息流程，清除当前路由的活动案件引用并保留历史案件。法律检索工具只供法律子图使用。内置命令仅在 Windows 注册；Windows 上不重复连接同名终端 MCP。

## Agent 权限

网页设置的“权限”页提供两档选择，保存到独立的 `permissions.json`：

- `restricted`（限制）：中、高、严重风险工具执行前需要审批，低风险查询工具直接执行。内置邮件、终端命令、记忆修改和删除属于高风险；MCP 工具按注册的风险元数据处理。
- `unrestricted`（无限制）：所有已启用工具跳过工具审批，包括邮件。沿用原来的空审批风险集合，缺少权限文件时默认此模式。

权限不启用被关闭的工具，也不取消法律流程中的补充信息提问。新运行提交时保存权限模式，Worker 执行及暂停恢复均使用此模式；切换设置后，新提交的运行使用新模式，已提交的运行和已打开的终端会话保持原模式。已有待审批任务仍需通过恢复接口提交审批回应。升级前没有保存权限模式的旧运行兼容使用当前配置。

```http
PATCH /v1/config/permissions
Content-Type: application/json

{"mode":"restricted"}
```

GET 和 PATCH 均返回 `{"mode":"restricted"}` 或 `{"mode":"unrestricted"}`。仅接受这两个模式，未知字段或无效类型返回 422。

## 查询和修改接口

接口复用现有 `AGENT_API_TOKEN` Bearer 鉴权，可通过后端 `/docs` 查看。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/v1/config` | 查询四个配置字段 |
| GET / PATCH | `/v1/config/models` | 查询或修改模型设置 |
| GET / PATCH | `/v1/config/mouldes` | 查询或修改插件和法律开关 |
| GET / PATCH | `/v1/config/secrets` | 查询 Key 状态，添加、替换或删除 Key |
| GET / PATCH | `/v1/config/permissions` | 查询或设置 Agent 权限模式 |

PATCH 递归合并指定字段，保留未提交的配置。插件的 `headers` 和 `env` 若出现在提交中则完整替换，空对象清除映射；未提交时保留原值。`able` 只接受 JSON 布尔值。未知字段、无效模型引用或错误类型返回 422，文件不可写返回 503。保存使用文件锁和临时文件原子替换。

关闭法律子图与网络搜索：

```http
PATCH /v1/config/mouldes
Content-Type: application/json

{"legal":{"able":false},"plugins":{"web_search":{"able":false}}}
```

修改当前模型及模型名称：

```http
PATCH /v1/config/models
Content-Type: application/json

{"active":"deepseek","models":{"deepseek":{"model":"deepseek-flash"}}}
```

更新 Key；`null` 删除文件中的值，删除后允许回退到环境变量：

```http
PATCH /v1/config/secrets
Content-Type: application/json

{"keys":{"DEEPSEEK_API_KEY":"new-key","OLD_KEY":null}}
```

secrets 查询及修改响应只返回 `{"keys":{"DEEPSEEK_API_KEY":true}}` 这样的状态。模型和插件查询会返回其参数，连接参数中应使用凭据引用。

修改成功后刷新 API 的应用配置。Worker 每次创建 Agent 时读取最新插件和法律开关；提交运行时冻结模型名称、地址和 Key 引用，执行时在本机解析 Key，Key 不进入队列或事件。正在执行的运行与终端会话保留已加载的配置，终端重启后生效。已有暂停断点恢复时按原待执行节点继续，不重走入口开关。
