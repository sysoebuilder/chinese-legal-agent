# React 聊天前端

React + TypeScript + Vite 单页应用，界面在浏览器中渲染。
Motion 提供消息进入、提示和弹窗动效，遵守系统减少动画设置；
Radix Dialog 处理弹窗焦点与键盘，Tabler 提供图标。
黑白配色、蓝色强调色以及历史、SSE、审批和恢复功能沿用原界面。

## Compose

在项目根目录运行：

```powershell
docker compose up -d --build --wait
```

- 前端：<http://127.0.0.1:3000/>，独立 `frontend` 服务。
- 后端文档：<http://127.0.0.1:8001/docs>，独立 `api` 服务。

前端镜像先用 Node.js 构建 Vite 项目，再用 Nginx 提供 `dist` 文件。
`/v1/` 通过 Nginx 代理至容器网络中的 `api:8001`，SSE 不缓冲，允许长时间保持连接。
FastAPI 不再托管 HTML、JS、CSS，也不提供 `/` 页面路由。
可以用 `AGENT_WEB_PORT` 覆盖 Compose 的前端宿主端口。

## 本机开发

保持 API 在 8001 运行，在本目录执行：

```powershell
npm ci
npm run dev
```

打开 <http://127.0.0.1:5173/>，Vite 将 `/v1` 转发到 `http://127.0.0.1:8001`。
开发服务器使用 `API_PROXY_TARGET` 环境变量选择其他 API 地址。
顶部模型下拉框可直接切换后续对话使用的模型。侧栏“模型与插件”和顶部设置按钮打开统一设置面板：

- **模型**：选择已有配置、修改提供商/模型名称/服务地址，更新 API Key，或添加新模型。密钥输入留空时保留原值，保存后清空输入；真实密钥不会从后端读取或存入浏览器缓存。
- **插件**：按需选择内置工具与 MCP 插件，单独控制法律子图；点击“保存选择”后生效。点击外部插件的“配置”可编辑 HTTP 地址、请求头、超时和代理设置，或 stdio 命令、参数、工作目录、环境变量；也可添加自定义 MCP 插件。stdio 进程在 Worker 所在环境运行。
- **凭据**：插件页下方管理模型与插件共用的 Key/Token，支持新增、更新、停用及删除保存值。留空保留原值；停用写入空字符串，删除写入 `null` 后允许环境变量回退。凭据只显示文件中的配置状态，失败时保留输入，成功后清空，不写入浏览器缓存。Windows 终端 Token 默认自动生成，可继续留空。
- **连接**：配置 API 访问令牌，仅保存在当前浏览器会话。

前端读取 `GET /v1/config`，分别通过 `PATCH /v1/config/models`、`/mouldes`、`/secrets` 保存。切换模型不会重置当前对话；设置用于后续运行，当前回答保持原来的配置。
插件请求头和环境变量使用 `${KEY_NAME}` 引用凭据。两种映射保存时完整替换，移除的行会从配置中删除；其他未提交字段保持原值。连接参数与凭据分别保存，避免把 Token 写进公开的插件配置。
配置加载、保存失败及部分保存均有可重试的反馈；设置标签支持方向键切换，动效遵守系统的减少动画偏好。

## 代码与验证

- `src/App.tsx`：页面布局。
- `src/components/`：侧栏、消息、输入框、中断表单、设置弹窗。
- `src/useChat.ts`：会话、历史、运行状态和 SSE 重连。
- `src/useAppConfig.ts`：配置加载、模型切换和分区保存。
- Worker 退出后遗留的 `running` 任务：选择/刷新会话、SSE 首次异常断开或点击“检查断点”时查询服务端执行锁与 checkpoint；符合条件时显示“从断点重连”，审批不会自动提交。没有固定间隔的断点查询。
- SSE 首帧为 `run.state`，之后接收无事件游标的临时增量；刷新从服务端恢复状态、审批和最终回答。
- `src/api.ts`、`src/types.ts`：HTTP/SSE 处理与后端接口类型。
- `src/styles.css`：主题、布局与响应式样式。

```powershell
npm run build
npm test
npm run test:browser
npm run test:config-browser
```

`build` 先检查 TypeScript 再生成页面；`test` 验证 SSE 分帧和中文解码。
浏览器测试需先启动 Vite，首次使用运行 `npx playwright install chromium`。
也可用 `CHAT_TEST_URL` 指向 Compose 的 3000 端口验证构建后的界面。
测试拦截 `/v1` 请求，验证历史分页、Markdown 清理、实时回答与思考、会话恢复、
审批、补充信息、停止、断线重连（状态快照与后续实时片段，不重放旧增量）、失败恢复、认证，以及桌面/手机布局，
不调用真实模型或工具。截图写入忽略的 `data/chat-preview/`。

`test:config-browser` 额外验证模型切换与新增、HTTP/stdio 插件编辑和新增、请求头移除、凭据新增/更新/停用/删除、密钥只写入、草稿保留、失败重试、插件和法律开关、刷新持久化、认证、键盘操作和桌面/手机/深色布局，同样拦截请求，不修改真实配置。
