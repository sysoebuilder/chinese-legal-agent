# 模型配置

只有两个固定客户端：`glm` 对应 `GlmModelClient`，`deepseek` 对应 `DeepSeekClient`。模型名称由用户提供，不校验名称或调用 API 验证可用性。

## 1. 首次配置

首次运行：

```powershell
agent100 config
```

如果 `data/.env` 不存在，程序会进入首次配置：手动选择 GLM 或 DeepSeek，隐藏输入 API Key，然后输入模型名称。模型名称留空时分别使用 `GLM-5.3-flash` 或 `deepseek-flash`；Base URL 使用程序内置的官方地址，不要求用户填写。完成后自动创建 `data/.env` 和 `data/active-model.env`。

## 2. 选择当前模型

```powershell
agent100 config
```

已有 `data/.env` 时，命令显示配置完整的提供商并允许切换，输入模型名称或回车保留已有名称。

选择后完整替换 `data/active-model.env`，内容只有四项：

```dotenv
MODEL_PROVIDER='deepseek'
MODEL_NAME='deepseek-flash'
MODEL_API_KEY='your_deepseek_key'
MODEL_BASE_URL='https://api.deepseek.com'
```

`LLM_CLI_DATA_DIR` 可覆盖数据目录；默认数据目录位于当前项目根目录下。从项目根目录运行命令，确保配置和启动使用同一目录。当前文件包含明文 Key，不要提交或分享。

## 3. 启动

`agent100 newchat` 在启动时读取当前配置。四个模型字段只来自 `active-model.env`，不受系统环境变量或源 `.env` 中同名字段覆盖。配置文件缺失或连接配置无效时提示执行 `agent100 config`。

修改源 `.env` 不会自动更换运行中的模型或下次启动的模型。需要再次执行 `config` 才会更新当前快照；正在运行的会话保持原来的连接。

源 `.env` 中的 `ZAI_API_KEY`、QQ SMTP 变量仍供搜索、邮件等工具使用，与当前模型的四个字段分开。选择 GLM 时输入的 Key 同时可供 Web Search 使用；选择 DeepSeek 时，如需 Web Search，仍需在 `data/.env` 中另行添加 `ZAI_API_KEY`。

DeepSeek 的 `tokenization()` 使用最小流式请求读取输入用量，会产生 API 费用。

## English

On the first `agent100 config` run, select GLM or DeepSeek, securely enter the API key, and enter a model name or press Enter for the built-in default. Provider base URLs are built in and are not requested. The command creates `data/.env` and `data/active-model.env`.

The active file contains exactly four fields: `MODEL_PROVIDER`, `MODEL_NAME`, `MODEL_API_KEY`, and `MODEL_BASE_URL`. Runtime model settings come exclusively from this snapshot. Later `config` runs switch among providers already configured in `data/.env`.
