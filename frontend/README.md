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
模型和工具凭据只存在后端配置中，网页设置仅接收 API 访问令牌。

## 代码与验证

- `src/App.tsx`：页面布局。
- `src/components/`：侧栏、消息、输入框、中断表单、设置弹窗。
- `src/useChat.ts`：会话、历史、运行状态和 SSE 重连。
- Worker 退出后遗留的 `running` 任务：选择/刷新会话、SSE 首次异常断开或点击“检查断点”时查询服务端执行锁与 checkpoint；符合条件时显示“从断点重连”，审批不会自动提交。没有固定间隔的断点查询。
- SSE 首帧为 `run.state`，之后接收无事件游标的临时增量；刷新从服务端恢复状态、审批和最终回答。
- `src/api.ts`、`src/types.ts`：HTTP/SSE 处理与后端接口类型。
- `src/styles.css`：主题、布局与响应式样式。

```powershell
npm run build
npm test
npm run test:browser
```

`build` 先检查 TypeScript 再生成页面；`test` 验证 SSE 分帧和中文解码。
浏览器测试需先启动 Vite，首次使用运行 `npx playwright install chromium`。
也可用 `CHAT_TEST_URL` 指向 Compose 的 3000 端口验证构建后的界面。
测试拦截 `/v1` 请求，验证历史分页、Markdown 清理、实时回答与思考、会话恢复、
审批、补充信息、停止、断线重连（状态快照与后续实时片段，不重放旧增量）、失败恢复、认证，以及桌面/手机布局，
不调用真实模型或工具。截图写入忽略的 `data/chat-preview/`。
