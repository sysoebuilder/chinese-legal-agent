import { useEffect, useRef, useState } from "react";
import type { PluginConfig } from "../types";
import type { AppConfigController } from "../useAppConfig";
import { Toggle } from "./Toggle";
import { KeyValueFields, fieldPairs, pairValues } from "./KeyValueFields";

const emptyPlugin: PluginConfig = {
  kind: "http",
  able: false,
  url: null,
  headers: {},
  command: null,
  args: [],
  env: {},
  cwd: null,
  read_timeout_seconds: 60,
  trust_env: true,
};

export function PluginConnectionEditor({
  settings,
  pluginId,
  onSaved,
  onCancel,
}: {
  settings: AppConfigController;
  pluginId: string | null;
  onSaved: (name: string) => void;
  onCancel: () => void;
}) {
  const original = {
    ...emptyPlugin,
    ...(pluginId ? settings.config!.mouldesconfig.plugins[pluginId] : {}),
  };
  const [name, setName] = useState(pluginId || "");
  const [kind, setKind] = useState(original.kind);
  const [able, setAble] = useState(original.able);
  const [url, setUrl] = useState(original.url || "");
  const [command, setCommand] = useState(original.command || "");
  const [args, setArgs] = useState(JSON.stringify(original.args, null, 2));
  const [cwd, setCwd] = useState(original.cwd || "");
  const [headers, setHeaders] = useState(() => fieldPairs(original.headers));
  const [environment, setEnvironment] = useState(() =>
    fieldPairs(original.env),
  );
  const [timeout, setTimeout] = useState(String(original.read_timeout_seconds));
  const [trustEnv, setTrustEnv] = useState(original.trust_env);
  const [validation, setValidation] = useState("");
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => {
    form.current
      ?.querySelector<HTMLElement>(
        "input:not(:disabled), select:not(:disabled)",
      )
      ?.focus();
  }, []);
  const busy = !!settings.saving || settings.loading;
  const prefix = `plugin-${pluginId || "new"}`;

  return (
    <form
      ref={form}
      className="plugin-connection-form"
      aria-label="插件连接配置"
      noValidate
      onSubmit={async (event) => {
        event.preventDefault();
        if (busy) return;
        setValidation("");
        let entry: PluginConfig;
        const id = name.trim();
        try {
          if (!/^[A-Za-z0-9_-]+$/.test(id) || id.includes("__"))
            throw new Error("插件名称仅允许字母、数字、单下划线和连字符。");
          if (
            !pluginId &&
            Object.hasOwn(settings.config!.mouldesconfig.plugins, id)
          )
            throw new Error("这个插件名称已存在，请换一个。");
          const seconds = Number(timeout);
          if (!Number.isFinite(seconds) || seconds <= 0)
            throw new Error("读取超时必须大于 0 秒。");
          if (kind === "http") {
            if (able && !url.trim())
              throw new Error("启用 HTTP 插件前请填写服务地址。");
            if (url.trim()) {
              let parsed: URL;
              try {
                parsed = new URL(url.trim());
              } catch {
                throw new Error("请填写完整的 HTTP(S) 服务地址。");
              }
              if (
                !["http:", "https:"].includes(parsed.protocol) ||
                parsed.username ||
                parsed.password
              )
                throw new Error(
                  "服务地址必须为 HTTP(S)，且不能包含账号或密码。",
                );
            }
          } else if (!command.trim())
            throw new Error("stdio 插件需要启动命令。");
          let argumentsList: string[] = [];
          if (kind === "stdio") {
            let parsed: unknown;
            try {
              parsed = JSON.parse(args);
            } catch {
              throw new Error(
                '启动参数需要填写 JSON 字符串数组，例如 ["-m", "my_server"]。',
              );
            }
            if (
              !Array.isArray(parsed) ||
              !parsed.every((item) => typeof item === "string")
            )
              throw new Error("启动参数中的每一项都必须是字符串。");
            argumentsList = parsed;
          }
          const environmentValues =
            kind === "stdio" ? pairValues(environment) : {};
          if (
            Object.keys(environmentValues).some(
              (key) => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(key),
            )
          )
            throw new Error(
              "环境变量名称必须以字母或下划线开头，只能包含字母、数字、下划线。",
            );
          entry = {
            kind,
            able,
            url: kind === "http" ? url.trim() || null : null,
            headers: kind === "http" ? pairValues(headers) : {},
            command: kind === "stdio" ? command.trim() : null,
            args: argumentsList,
            env: environmentValues,
            cwd: kind === "stdio" ? cwd.trim() || null : null,
            read_timeout_seconds: seconds,
            trust_env: trustEnv,
          };
        } catch (reason) {
          setValidation(
            reason instanceof Error ? reason.message : "请检查连接配置。",
          );
          return;
        }
        if (
          await settings.savePlugins(
            { plugins: { [id]: entry } },
            "插件连接已保存，后续运行生效。",
          )
        )
          onSaved(id);
      }}
    >
      <div className="settings-section-heading">
        <h3>{pluginId ? `配置 ${pluginId}` : "添加 MCP 插件"}</h3>
        <p>保存连接参数后，可在插件列表中启用。</p>
      </div>
      <div className="settings-field">
        <label htmlFor={`${prefix}-name`}>插件名称</label>
        <input
          id={`${prefix}-name`}
          value={name}
          disabled={busy || !!pluginId}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <div className="settings-field">
        <label htmlFor={`${prefix}-kind`}>连接方式</label>
        <select
          id={`${prefix}-kind`}
          value={kind}
          disabled={busy}
          onChange={(event) => setKind(event.target.value as "http" | "stdio")}
        >
          <option value="http">HTTP</option>
          <option value="stdio">stdio · 本地进程</option>
        </select>
      </div>
      {kind === "http" ? (
        <>
          <div className="settings-field">
            <label htmlFor={`${prefix}-url`}>MCP 服务地址</label>
            <input
              id={`${prefix}-url`}
              value={url}
              placeholder="https://example.com/mcp"
              disabled={busy}
              onChange={(event) => setUrl(event.target.value)}
            />
          </div>
          <KeyValueFields
            label="请求头"
            pairs={headers}
            onChange={setHeaders}
            disabled={busy}
          />
        </>
      ) : (
        <>
          <div className="settings-field">
            <label htmlFor={`${prefix}-command`}>启动命令</label>
            <input
              id={`${prefix}-command`}
              value={command}
              placeholder="python"
              disabled={busy}
              onChange={(event) => setCommand(event.target.value)}
            />
            <p className="field-hint">
              进程在 Worker 所在环境运行；容器部署时使用容器内的命令和路径。
            </p>
          </div>
          <div className="settings-field">
            <label htmlFor={`${prefix}-args`}>启动参数</label>
            <textarea
              id={`${prefix}-args`}
              value={args}
              rows={3}
              disabled={busy}
              onChange={(event) => setArgs(event.target.value)}
            />
            <p className="field-hint">
              JSON 字符串数组，例如 ["-m", "my_server"]。
            </p>
          </div>
          <div className="settings-field">
            <label htmlFor={`${prefix}-cwd`}>工作目录</label>
            <input
              id={`${prefix}-cwd`}
              value={cwd}
              placeholder="留空使用 Worker 默认目录"
              disabled={busy}
              onChange={(event) => setCwd(event.target.value)}
            />
          </div>
          <KeyValueFields
            label="环境变量"
            pairs={environment}
            onChange={setEnvironment}
            disabled={busy}
          />
        </>
      )}
      <p className="field-hint">
        {
          "凭据在下方单独保存。请求头或环境变量可使用 ${KEY_NAME} 引用，例如 Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}。"
        }
      </p>
      <div className="settings-field">
        <label htmlFor={`${prefix}-timeout`}>读取超时（秒）</label>
        <input
          id={`${prefix}-timeout`}
          type="number"
          min="0.1"
          step="any"
          value={timeout}
          disabled={busy}
          onChange={(event) => setTimeout(event.target.value)}
        />
      </div>
      <div className="setting-row">
        <div className="setting-row-copy">
          <h3>继承系统代理</h3>
          <p>HTTP 连接使用 Worker 的代理环境变量。</p>
        </div>
        <Toggle
          label="继承系统代理"
          checked={trustEnv}
          disabled={busy || kind !== "http"}
          onChange={setTrustEnv}
        />
      </div>
      <div className="setting-row">
        <div className="setting-row-copy">
          <h3>保存后启用</h3>
        </div>
        <Toggle
          label="启用此连接"
          checked={able}
          disabled={busy}
          onChange={setAble}
        />
      </div>
      {validation && (
        <p className="field-error" role="alert">
          {validation}
        </p>
      )}
      <div className="settings-form-actions">
        <button
          className="text-button"
          type="button"
          disabled={busy}
          onClick={onCancel}
        >
          取消编辑
        </button>
        <button className="primary-button" type="submit" disabled={busy}>
          {settings.saving === "plugins" ? "保存中…" : "保存连接"}
        </button>
      </div>
    </form>
  );
}
