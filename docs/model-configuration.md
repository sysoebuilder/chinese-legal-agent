# 应用配置

终端聊天与 API 启动时读取三个 JSON 文件并创建 `APPConfig`：

| 文件 | APPConfig 字段 | 用途 |
| --- | --- | --- |
| `models.json` | `modelconfig` | 当前模型、可选模型、地址与 Key 引用 |
| `mouldes.json` | `mouldesconfig` | 内置工具、MCP 插件和法律子图开关 |
| `secrets.json` | `secretsconfig` | 模型和插件所用的 Key |

默认目录为项目的 `data/`，可通过 `LLM_CLI_DATA_DIR` 修改默认数据目录，或用 `LLM_CLI_CONFIG_DIR` 单独指定配置目录。Compose 将主机 `data/` 挂载为 `/app/config`，API 和 Worker 共用配置文件。

首次加载创建缺少的文件。三个文件都不存在时，自动迁移同目录的 `.env` 和 `active-model.env`，保留旧文件；此后模型设置以 JSON 为准。数据库、Redis、Qdrant 等部署参数仍从环境变量或 `.env` 读取。无效 JSON 会报错，不会被默认值覆盖。

## 模型与凭据

运行 `agent100 config` 可选择 GLM 或 DeepSeek、隐藏输入 Key 并填写模型名称。保存时模型写入 `models.json`，Key 写入 `secrets.json`，不再生成包含 Key 的模型快照。默认模型分别为 `GLM-5.3-flash` 和 `deepseek-flash`；模型名称不做远程可用性验证。

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

`mouldes.json` 的 `plugins` 按工具名或 MCP 服务名配置 `able`。内置插件使用 `kind: "builtin"`，HTTP 和 stdio MCP 分别使用 `http` 和 `stdio`。默认保留原有内置工具、GitHub 和 Cloudflare Docs。Windows MCP 的初始地址和开关根据首次加载时的 `WINDOWS_MCP_URL` 设置，之后通过 JSON 或 API 修改。

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

主图新增 `check_legal_enabled` 节点：

```text
START → check_legal_enabled
          ├─ legal.able=true  → legal_flow → 原有法律/通用流程
          └─ legal.able=false → general_agent → END
```

关闭法律子图会跳过法律分类、检索和补充信息流程，清除当前路由的活动案件引用并保留历史案件。法律检索工具只供法律子图使用。内置命令仅在 Windows 注册；Windows 上不重复连接同名终端 MCP。

## 查询和修改接口

接口复用现有 `AGENT_API_TOKEN` Bearer 鉴权，可通过后端 `/docs` 查看。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/v1/config` | 查询三个配置字段 |
| GET / PATCH | `/v1/config/models` | 查询或修改模型设置 |
| GET / PATCH | `/v1/config/mouldes` | 查询或修改插件和法律开关 |
| GET / PATCH | `/v1/config/secrets` | 查询 Key 状态，添加、替换或删除 Key |

PATCH 递归合并指定字段，保留未提交的配置。`able` 只接受 JSON 布尔值。未知字段、无效模型引用或错误类型返回 422，文件不可写返回 503。保存使用文件锁和临时文件原子替换。

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
