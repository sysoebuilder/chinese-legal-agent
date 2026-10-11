// 使用浏览器拦截 API，验证设置行为；不修改真实配置或调用模型。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const origin = process.env.CHAT_TEST_URL || "http://127.0.0.1:5173";
const output = path.resolve(__dirname, "../data/chat-preview");
fs.mkdirSync(output, { recursive: true });

const builtin = (able = true) => ({ kind: "builtin", able });
let config = {
  modelconfig: {
    active: "glm",
    models: {
      glm: {
        provider: "glm",
        model: "GLM-5.3-flash",
        base_url: "https://open.bigmodel.cn/api/paas/v4/",
        api_key_ref: "ZAI_API_KEY",
      },
      deepseek: {
        provider: "deepseek",
        model: "deepseek-flash",
        base_url: "https://api.deepseek.com",
        api_key_ref: "DEEPSEEK_API_KEY",
      },
    },
  },
  mouldesconfig: {
    legal: { able: true },
    plugins: {
      current_time: builtin(),
      web_search: builtin(),
      search_memory: builtin(),
      list_memories: builtin(),
      send_email: builtin(),
      run_command: builtin(),
      update_memory: builtin(),
      delete_memory: builtin(),
      github: {
        kind: "http",
        able: true,
        url: "https://api.githubcopilot.com/mcp/",
        headers: {
          Authorization: "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}",
          "X-Old": "remove-me",
        },
        read_timeout_seconds: 60,
        trust_env: true,
      },
      cloudflare_docs: {
        kind: "http",
        able: true,
        url: "https://docs.mcp.cloudflare.com/mcp",
      },
      windows_terminal: { kind: "http", able: false, url: null },
    },
  },
  secretsconfig: { keys: { ZAI_API_KEY: true, DEEPSEEK_API_KEY: true } },
  permissionsconfig: { mode: "unrestricted" },
};
const writes = [],
  errors = [];
let requireToken = false,
  failSecrets = false,
  failPlugins = false,
  failModels = false,
  failPermissions = false,
  configUnavailable = false;
const session = {
  thread_id: "10000000-0000-4000-8000-000000000001",
  name: "一个已有对话",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 960 },
      colorScheme: "light",
    });
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (
        message.type() === "error" &&
        /Invalid regular expression|uncontrolled input|React has detected/.test(
          message.text(),
        )
      )
        errors.push(message.text());
    });
    await page.route("**/v1/**", async (route) => {
      const request = route.request(),
        url = new URL(request.url());
      const send = (body, status = 200) =>
        route.fulfill({
          status,
          contentType: "application/json",
          body: JSON.stringify(body),
        });
      if (
        requireToken &&
        request.headers().authorization !== "Bearer browser-test-token"
      )
        return send({ detail: "需要有效的 Bearer token" }, 401);
      if (url.pathname === "/v1/sessions")
        return send({
          sessions: [session],
          has_more: false,
          next_offset: null,
        });
      if (url.pathname.endsWith("/messages"))
        return send({
          thread_id: session.thread_id,
          messages: [],
          has_more: false,
          next_before: null,
        });
      if (url.pathname.endsWith("/recovery"))
        return send({
          run: null,
          can_reconnect: false,
          busy: false,
          pending_nodes: [],
        });
      if (url.pathname === "/v1/config") {
        if (configUnavailable) return send({ detail: "配置暂时不可用" }, 503);
        return send(config);
      }
      if (request.method() === "PATCH") {
        const section = url.pathname.split("/").at(-1),
          body = request.postDataJSON();
        writes.push({ section, body });
        await new Promise((resolve) => setTimeout(resolve, 120));
        if (section === "secrets") {
          if (failSecrets) return send({ detail: "凭据保存失败" }, 503);
          for (const [key, value] of Object.entries(body.keys)) {
            if (value === null) delete config.secretsconfig.keys[key];
            else config.secretsconfig.keys[key] = !!value;
          }
          return send(config.secretsconfig);
        }
        if (section === "models") {
          if (failModels) return send({ detail: "模型设置保存失败" }, 503);
          Object.assign(config.modelconfig.models, body.models || {});
          if (body.active) config.modelconfig.active = body.active;
          return send(config.modelconfig);
        }
        if (section === "mouldes") {
          if (failPlugins) return send({ detail: "插件设置保存失败" }, 503);
          if (body.legal) config.mouldesconfig.legal = body.legal;
          for (const [id, patch] of Object.entries(body.plugins || {}))
            config.mouldesconfig.plugins[id] = {
              ...config.mouldesconfig.plugins[id],
              ...patch,
            };
          return send(config.mouldesconfig);
        }
        if (section === "permissions") {
          if (failPermissions) return send({ detail: "权限保存失败" }, 503);
          config.permissionsconfig = body;
          return send(config.permissionsconfig);
        }
      }
      return send({ detail: "不存在" }, 404);
    });
    await page.goto(origin);
    async function saveModel() {
      const response = page.waitForResponse(
        (item) =>
          item.url().endsWith("/v1/config/models") &&
          item.request().method() === "PATCH",
      );
      await page.getByRole("button", { name: "保存模型", exact: true }).click();
      await response;
      await page.waitForFunction(
        () =>
          !document
            .querySelector('.model-config-form button[type="submit"]')
            .textContent.includes("保存中"),
      );
    }
    async function savePlugins() {
      const response = page.waitForResponse(
        (item) =>
          item.url().endsWith("/v1/config/mouldes") &&
          item.request().method() === "PATCH",
      );
      await page.getByRole("button", { name: "保存选择", exact: true }).click();
      await response;
      await page.waitForFunction(
        () =>
          !document
            .querySelector('.plugin-settings button[type="submit"]')
            .textContent.includes("保存中"),
      );
    }
    const switcher = page.getByRole("button", { name: "切换模型" });
    await switcher.waitFor();
    assert.ok((await switcher.textContent()).includes(config.modelconfig.models.glm.model));
    await page.screenshot({
      path: path.join(output, "config-desktop-chat.png"),
    });
    // 切换模型仅提交 active，不重置当前对话。
    await page.getByRole("button", { name: session.name, exact: true }).click();
    await page.waitForFunction(
      () =>
        document.querySelector("#chat-title")?.textContent === "一个已有对话",
    );
    await switcher.click();
    await page.getByRole("option", { name: "deepseek-flash", exact: true }).click();
    await page
      .getByText("模型已切换，下一次发送时使用。", { exact: true })
      .waitFor();
    assert.deepEqual(writes.at(-1), {
      section: "models",
      body: { active: "deepseek" },
    });
    assert.equal(await page.locator("#chat-title").textContent(), session.name);

    await page.getByRole("button", { name: "打开设置", exact: true }).click();
    await page.getByLabel("模型名称", { exact: true }).waitFor();
    assert.equal(
      await page.getByLabel("模型名称", { exact: true }).inputValue(),
      "deepseek-flash",
    );
    await page.waitForTimeout(300);
    await page.screenshot({
      path: path.join(output, "config-desktop-models.png"),
    });
    await page.getByLabel("模型名称", { exact: true }).fill("deepseek-work");
    await page
      .getByLabel("服务地址", { exact: true })
      .fill("https://model.example/v1");
    await page
      .getByLabel("API Key", { exact: false })
      .fill("browser-only-dummy-key");
    await page.getByRole("tab", { name: "插件", exact: true }).click();
    await page.getByRole("tab", { name: "模型", exact: true }).click();
    assert.equal(
      await page.getByLabel("模型名称", { exact: true }).inputValue(),
      "deepseek-work",
    );
    await saveModel();
    await page.getByText("模型配置已保存。", { exact: true }).waitFor();
    assert.deepEqual(
      writes.slice(-2).map((write) => write.section),
      ["secrets", "models"],
    );
    assert.equal(
      await page.getByLabel("API Key", { exact: false }).inputValue(),
      "",
    );
    assert.equal(config.modelconfig.models.deepseek.model, "deepseek-work");
    assert.equal(
      await page.evaluate(() =>
        JSON.stringify({ ...localStorage, ...sessionStorage }).includes(
          "browser-only-dummy-key",
        ),
      ),
      false,
    );

    // 添加模型、重名校验和新配置激活。
    await page.getByRole("button", { name: "添加模型", exact: true }).click();
    await page.getByLabel("配置名称", { exact: true }).fill("glm");
    await page
      .getByText("这个名称已存在，请换一个。", { exact: true })
      .waitFor();
    assert.ok(
      await page
        .getByRole("button", { name: "保存模型", exact: true })
        .isDisabled(),
    );
    await page.getByLabel("配置名称", { exact: true }).fill("work");
    await page.getByLabel("提供商", { exact: true }).selectOption("glm");
    await page.getByLabel("模型名称", { exact: true }).fill("GLM-work");
    await page
      .getByLabel("API Key", { exact: false })
      .fill("new-model-dummy-key");
    await saveModel();
    await page.getByText("模型配置已保存。", { exact: true }).waitFor();
    assert.equal(config.modelconfig.active, "work");
    assert.equal(
      config.modelconfig.models.work.api_key_ref,
      "MODEL_WORK_API_KEY",
    );

    // 两步保存第二步失败时清楚反馈，不把未保存内容标为成功。
    failModels = true;
    await page.getByLabel("模型名称", { exact: true }).fill("GLM-unsaved");
    await page
      .getByLabel("API Key", { exact: false })
      .fill("partial-save-dummy-key");
    await saveModel();
    await page
      .getByRole("alert")
      .filter({ hasText: "密钥已保存，模型设置未保存" })
      .waitFor();
    assert.equal(config.modelconfig.models.work.model, "GLM-work");
    failModels = false;
    await saveModel();
    await page.getByText("模型配置已保存。", { exact: true }).waitFor();

    await page.getByRole("tab", { name: "插件", exact: true }).click();
    await page
      .getByRole("switch", { name: "启用法律子图", exact: true })
      .click();
    await page
      .getByRole("switch", { name: "启用网络搜索", exact: true })
      .click();
    assert.ok(
      await page
        .getByRole("switch", { name: "启用Windows 远程终端", exact: true })
        .isDisabled(),
    );
    await savePlugins();
    await page
      .getByText("插件选择已保存，后续运行生效。", { exact: true })
      .waitFor();
    assert.deepEqual(writes.at(-1), {
      section: "mouldes",
      body: {
        legal: { able: false },
        plugins: { web_search: { able: false } },
      },
    });
    await page.waitForTimeout(300);
    await page
      .locator(".settings-scroll")
      .evaluate((element) => (element.scrollTop = 0));
    await page.screenshot({
      path: path.join(output, "config-desktop-plugins.png"),
    });
    failPlugins = true;
    await page
      .getByRole("switch", { name: "启用发送邮件", exact: true })
      .click();
    await savePlugins();
    await page
      .getByRole("alert")
      .filter({ hasText: "插件设置保存失败" })
      .waitFor();
    assert.equal(
      await page
        .getByRole("switch", { name: "启用发送邮件", exact: true })
        .getAttribute("aria-checked"),
      "false",
    );
    assert.equal(config.mouldesconfig.plugins.send_email.able, true);
    failPlugins = false;
    await savePlugins();
    await page
      .getByText("插件选择已保存，后续运行生效。", { exact: true })
      .waitFor();

    async function saveConnection() {
      const response = page.waitForResponse(
        (item) =>
          item.url().endsWith("/v1/config/mouldes") &&
          item.request().method() === "PATCH",
      );
      await page.getByRole("button", { name: "保存连接", exact: true }).click();
      await response;
      await page.waitForFunction(
        () =>
          !document
            .querySelector('.plugin-connection-form button[type="submit"]')
            ?.textContent.includes("保存中"),
      );
    }
    async function changeSecret(name, button) {
      const response = page.waitForResponse(
        (item) =>
          item.url().endsWith("/v1/config/secrets") &&
          item.request().method() === "PATCH",
      );
      await page
        .getByRole("button", { name: `${button} ${name}`, exact: true })
        .click();
      await response;
      await page.waitForFunction(
        () => !document.querySelector("#new-secret-name")?.disabled,
      );
    }
    // HTTP 连接全部字段可编辑，移除请求头能够落到实际保存值。
    await page.getByRole("button", { name: "配置GitHub", exact: true }).click();
    await page
      .getByLabel("MCP 服务地址", { exact: true })
      .fill("https://github.example/mcp");
    await page.getByLabel("读取超时（秒）", { exact: true }).fill("120");
    await page
      .getByRole("switch", { name: "继承系统代理", exact: true })
      .click();
    await page
      .getByRole("button", { name: "移除请求头 2", exact: true })
      .click();
    await saveConnection();
    assert.deepEqual(config.mouldesconfig.plugins.github.headers, {
      Authorization: "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}",
    });
    assert.equal(
      config.mouldesconfig.plugins.github.url,
      "https://github.example/mcp",
    );
    assert.equal(config.mouldesconfig.plugins.github.read_timeout_seconds, 120);
    assert.equal(config.mouldesconfig.plugins.github.trust_env, false);
    // 保存失败保留草稿，切换设置标签也不会清空。
    await page.getByRole("button", { name: "配置GitHub", exact: true }).click();
    await page
      .getByLabel("MCP 服务地址", { exact: true })
      .fill("https://retry.example/mcp");
    await page.getByRole("tab", { name: "模型", exact: true }).click();
    await page.getByRole("tab", { name: "插件", exact: true }).click();
    failPlugins = true;
    await saveConnection();
    assert.equal(
      await page.getByLabel("MCP 服务地址", { exact: true }).inputValue(),
      "https://retry.example/mcp",
    );
    assert.equal(
      config.mouldesconfig.plugins.github.url,
      "https://github.example/mcp",
    );
    failPlugins = false;
    await saveConnection();

    // 新建 stdio 连接：参数、工作目录、环境变量引用与校验。
    await page
      .getByRole("button", { name: "添加 MCP 插件", exact: true })
      .click();
    await page.getByLabel("插件名称", { exact: true }).fill("github");
    await page.getByRole("button", { name: "保存连接", exact: true }).click();
    await page
      .getByText("这个插件名称已存在，请换一个。", { exact: true })
      .waitFor();
    await page.getByLabel("插件名称", { exact: true }).fill("demo_process");
    await page.getByLabel("连接方式", { exact: true }).selectOption("stdio");
    await page.getByLabel("启动命令", { exact: true }).fill("python");
    await page.getByLabel("启动参数", { exact: true }).fill("[1]");
    await page.getByRole("button", { name: "保存连接", exact: true }).click();
    await page
      .getByText("启动参数中的每一项都必须是字符串。", { exact: true })
      .waitFor();
    await page
      .getByLabel("启动参数", { exact: true })
      .fill('["-m", "demo_server"]');
    await page.getByLabel("工作目录", { exact: true }).fill("/app/tools");
    await page
      .getByRole("button", { name: "添加环境变量", exact: true })
      .click();
    await page.getByLabel("环境变量名称 1", { exact: true }).fill("TOKEN");
    await page
      .getByLabel("环境变量值 1", { exact: true })
      .fill("${CUSTOM_PLUGIN_TOKEN}");
    await page.getByRole("switch", { name: "启用此连接", exact: true }).click();
    await saveConnection();
    const stdio = config.mouldesconfig.plugins.demo_process;
    assert.equal(stdio.kind, "stdio");
    assert.deepEqual(stdio.args, ["-m", "demo_server"]);
    assert.equal(stdio.cwd, "/app/tools");
    assert.deepEqual(stdio.env, { TOKEN: "${CUSTOM_PLUGIN_TOKEN}" });
    assert.equal(
      await page
        .getByRole("switch", { name: "启用demo_process", exact: true })
        .getAttribute("aria-checked"),
      "true",
    );

    // 所有凭据都通过 secrets 接口保存，失败时保留输入，成功后清空。
    await page
      .getByRole("button", { name: "设置 CUSTOM_PLUGIN_TOKEN", exact: true })
      .click();
    await page
      .getByLabel("更新 CUSTOM_PLUGIN_TOKEN", { exact: true })
      .fill("plugin-dummy-secret");
    failSecrets = true;
    await changeSecret("CUSTOM_PLUGIN_TOKEN", "保存凭据");
    assert.equal(
      await page
        .getByLabel("更新 CUSTOM_PLUGIN_TOKEN", { exact: true })
        .inputValue(),
      "plugin-dummy-secret",
    );
    assert.equal(config.secretsconfig.keys.CUSTOM_PLUGIN_TOKEN, undefined);
    failSecrets = false;
    await changeSecret("CUSTOM_PLUGIN_TOKEN", "保存凭据");
    assert.equal(
      await page
        .getByLabel("更新 CUSTOM_PLUGIN_TOKEN", { exact: true })
        .inputValue(),
      "",
    );
    assert.deepEqual(writes.at(-1), {
      section: "secrets",
      body: { keys: { CUSTOM_PLUGIN_TOKEN: "plugin-dummy-secret" } },
    });
    await changeSecret("CUSTOM_PLUGIN_TOKEN", "停用凭据");
    assert.equal(config.secretsconfig.keys.CUSTOM_PLUGIN_TOKEN, false);
    assert.equal(writes.at(-1).body.keys.CUSTOM_PLUGIN_TOKEN, "");
    await changeSecret("CUSTOM_PLUGIN_TOKEN", "删除保存值");
    assert.equal(config.secretsconfig.keys.CUSTOM_PLUGIN_TOKEN, undefined);
    assert.equal(writes.at(-1).body.keys.CUSTOM_PLUGIN_TOKEN, null);
    await page
      .getByLabel("更新 CUSTOM_PLUGIN_TOKEN", { exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: path.join(output, "config-desktop-secrets.png"),
    });
    await page.getByLabel("凭据名称", { exact: true }).fill("INVALID-NAME");
    await page.getByLabel("凭据值", { exact: true }).fill("added-dummy-secret");
    await page.getByRole("button", { name: "添加凭据", exact: true }).click();
    await page
      .getByText(
        "凭据名称必须以字母或下划线开头，只能包含字母、数字、下划线。",
        { exact: true },
      )
      .waitFor();
    await page
      .getByLabel("凭据名称", { exact: true })
      .fill("EXTRA_PLUGIN_TOKEN");
    const added = page.waitForResponse(
      (item) =>
        item.url().endsWith("/v1/config/secrets") &&
        item.request().method() === "PATCH",
    );
    await page.getByRole("button", { name: "添加凭据", exact: true }).click();
    await added;
    await page.waitForFunction(
      () => document.querySelector("#new-secret-value").value === "",
    );
    assert.equal(config.secretsconfig.keys.EXTRA_PLUGIN_TOKEN, true);
    assert.equal(
      await page.evaluate(
        () =>
          JSON.stringify({ ...localStorage, ...sessionStorage }).includes(
            "plugin-dummy-secret",
          ) ||
          JSON.stringify({ ...localStorage, ...sessionStorage }).includes(
            "added-dummy-secret",
          ),
      ),
      false,
    );
    // 连接类型切换会清空失效的 stdio 参数。
    await page
      .getByRole("button", { name: "配置demo_process", exact: true })
      .click();
    await page.getByLabel("连接方式", { exact: true }).selectOption("http");
    await page
      .getByLabel("MCP 服务地址", { exact: true })
      .fill("https://demo.example/mcp");
    await saveConnection();
    assert.deepEqual(config.mouldesconfig.plugins.demo_process.env, {});
    assert.deepEqual(config.mouldesconfig.plugins.demo_process.args, []);
    assert.equal(config.mouldesconfig.plugins.demo_process.command, null);
    await page.keyboard.press("Escape");
    await page.waitForFunction(
      () => !document.getElementById("settings-dialog"),
    );
    assert.equal(
      await page.evaluate(() =>
        document.activeElement.getAttribute("aria-label"),
      ),
      "打开设置",
    );
    await page.reload();
    await switcher.waitFor();
    assert.equal(await switcher.inputValue(), "work");
    await page.getByRole("button", { name: "打开设置", exact: true }).click();
    await page.getByRole("tab", { name: "插件", exact: true }).click();
    assert.equal(
      await page
        .getByRole("switch", { name: "启用法律子图", exact: true })
        .getAttribute("aria-checked"),
      "false",
    );
    assert.equal(
      await page
        .getByRole("switch", { name: "启用发送邮件", exact: true })
        .getAttribute("aria-checked"),
      "false",
    );

    // 权限只有两档；保存失败保留草稿，刷新后仍显示已保存设置。
    await page.getByRole("tab", { name: "权限", exact: true }).click();
    const restricted = page.getByRole("radio", { name: "限制", exact: true });
    const unrestricted = page.getByRole("radio", {
      name: "无限制",
      exact: true,
    });
    assert.equal(await page.locator(".permission-options input").count(), 2);
    assert.ok(await unrestricted.isChecked());
    const savePermissionsButton = page.getByRole("button", {
      name: "保存权限",
      exact: true,
    });
    assert.ok(await savePermissionsButton.isDisabled());
    async function savePermissions() {
      const response = page.waitForResponse(
        (item) =>
          item.url().endsWith("/v1/config/permissions") &&
          item.request().method() === "PATCH",
      );
      await savePermissionsButton.click();
      await response;
      await page.waitForFunction(
        () =>
          !document
            .querySelector('.permissions-settings button[type="submit"]')
            .textContent.includes("保存中"),
      );
    }
    await restricted.check();
    await page.getByRole("tab", { name: "模型", exact: true }).click();
    await page.getByRole("tab", { name: "权限", exact: true }).click();
    assert.ok(await restricted.isChecked());
    failPermissions = true;
    await savePermissions();
    await page.getByRole("alert").filter({ hasText: "权限保存失败" }).waitFor();
    assert.ok(await restricted.isChecked());
    assert.equal(config.permissionsconfig.mode, "unrestricted");
    failPermissions = false;
    await savePermissions();
    assert.deepEqual(writes.at(-1), {
      section: "permissions",
      body: { mode: "restricted" },
    });
    assert.ok(await savePermissionsButton.isDisabled());
    await page.screenshot({
      path: path.join(output, "config-desktop-permissions.png"),
    });
    await page.reload();
    await page.getByRole("button", { name: "打开设置", exact: true }).click();
    await page.getByRole("tab", { name: "权限", exact: true }).click();
    assert.ok(await restricted.isChecked());
    await unrestricted.check();
    await savePermissions();
    assert.equal(config.permissionsconfig.mode, "unrestricted");
    await page
      .getByText("Agent 权限已保存，后续运行生效。", { exact: true })
      .waitFor();

    // 连接失败保留表单；成功后展示最新配置。
    await page.getByRole("tab", { name: "连接", exact: true }).click();
    requireToken = true;
    await page.getByLabel("API 访问令牌", { exact: true }).fill("wrong-token");
    await page.getByRole("button", { name: "保存并连接", exact: true }).click();
    await page
      .getByRole("alert")
      .filter({ hasText: "请先填写 API 访问令牌" })
      .waitFor();
    await page
      .getByLabel("API 访问令牌", { exact: true })
      .fill("browser-test-token");
    await page.getByRole("button", { name: "保存并连接", exact: true }).click();
    await page.waitForFunction(
      () =>
        document
          .getElementById("settings-tab-models")
          ?.getAttribute("aria-selected") === "true",
    );
    assert.equal(
      await page.evaluate(() => sessionStorage.getItem("agent100.token")),
      "browser-test-token",
    );

    await page.getByRole("button", { name: "关闭设置", exact: true }).click();
    await page.waitForFunction(
      () => !document.getElementById("settings-dialog"),
    );
    await page.getByRole("button", { name: "切换深浅色", exact: true }).click();
    await page.getByRole("button", { name: "打开设置", exact: true }).click();
    await page.waitForTimeout(300);
    await page.screenshot({
      path: path.join(output, "config-dark-models.png"),
    });
    await page.getByRole("tab", { name: "权限", exact: true }).click();
    await page.waitForTimeout(300);
    await page.screenshot({
      path: path.join(output, "config-dark-permissions.png"),
    });
    await page.getByRole("button", { name: "关闭设置", exact: true }).click();
    await page.waitForFunction(
      () => !document.getElementById("settings-dialog"),
    );
    await page.getByRole("button", { name: "切换深浅色", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "打开设置", exact: true }).click();
    await page.waitForTimeout(300);
    await page.screenshot({
      path: path.join(output, "config-mobile-models.png"),
    });
    const box = await page.locator("#settings-dialog").boundingBox();
    assert.ok(
      box.x >= 0 &&
        box.x + box.width <= 391 &&
        box.y >= 0 &&
        box.y + box.height <= 845,
    );
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.getByRole("tab", { name: "插件", exact: true }).click();
    await page.waitForTimeout(300);
    await page.screenshot({
      path: path.join(output, "config-mobile-plugins.png"),
    });
    await page.getByRole("button", { name: "配置GitHub", exact: true }).click();
    await page
      .getByLabel("MCP 服务地址", { exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: path.join(output, "config-mobile-plugin-editor.png"),
    });
    await page.setViewportSize({ width: 320, height: 640 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.getByRole("tab", { name: "插件", exact: true }).focus();
    await page.keyboard.press("ArrowRight");
    assert.equal(
      await page
        .getByRole("tab", { name: "权限", exact: true })
        .getAttribute("aria-selected"),
      "true",
    );
    await page.waitForTimeout(300);
    await page.screenshot({
      path: path.join(output, "config-mobile-permissions.png"),
    });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.setViewportSize({ width: 320, height: 640 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.keyboard.press("ArrowRight");
    assert.equal(
      await page
        .getByRole("tab", { name: "连接", exact: true })
        .getAttribute("aria-selected"),
      "true",
    );

    // 无配置时可重试恢复，而不是留下一个空白面板。
    configUnavailable = true;
    await page.reload();
    await page.getByRole("button", { name: "模型设置", exact: true }).click();
    await page
      .getByRole("heading", { name: "暂时无法读取配置", exact: true })
      .waitFor();
    configUnavailable = false;
    await page.getByRole("button", { name: "重新加载", exact: true }).click();
    await page.getByLabel("模型名称", { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    console.log(
      "Config browser checks passed: models, plugins, secrets, permissions roundtrip and retry, persistence, auth, dark/mobile layouts, keyboard and load recovery.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
