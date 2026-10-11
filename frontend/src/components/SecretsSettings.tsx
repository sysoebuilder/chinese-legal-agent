import { useState } from "react";
import type { AppConfigController } from "../useAppConfig";

function SecretRow({
  name,
  settings,
}: {
  name: string;
  settings: AppConfigController;
}) {
  const [value, setValue] = useState("");
  const [expanded, setExpanded] = useState(false);
  const keys = settings.config!.secretsconfig.keys;
  const exists = Object.hasOwn(keys, name);
  const busy = !!settings.saving || settings.loading;
  const save = async (next: string | null) => {
    if (await settings.saveSecrets({ [name]: next })) setValue("");
  };
  return (
    <div className="secret-row">
      <div className="setting-row">
        <div className="setting-row-copy">
          <h3>{name}</h3>
          <p>
            {keys[name]
              ? "文件已配置"
              : exists
                ? "文件中已停用"
                : "文件未配置，可从环境变量读取"}
          </p>
        </div>
        <button
          className="text-button"
          type="button"
          aria-expanded={expanded}
          aria-controls={`secret-${name}`}
          onClick={() => setExpanded(!expanded)}
        >
          设置 {name}
        </button>
      </div>
      {expanded && (
        <form
          id={`secret-${name}`}
          className="secret-edit"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!busy && value.trim()) await save(value.trim());
          }}
        >
          <div className="settings-field">
            <label htmlFor={`secret-value-${name}`}>更新 {name}</label>
            <input
              id={`secret-value-${name}`}
              type="password"
              autoComplete="off"
              value={value}
              placeholder="留空保留原值"
              disabled={busy}
              onChange={(event) => setValue(event.target.value)}
            />
          </div>
          {name === "WINDOWS_MCP_TOKEN" && (
            <p className="field-hint">
              宿主终端默认自动生成 Token，通常可留空。手动设置时需与宿主 MCP
              服务一致。
            </p>
          )}
          <p className="field-hint">
            停用会保存空值；删除保存值后，允许使用环境变量中的同名凭据。
          </p>
          <div className="secret-actions">
            <button
              className="primary-button"
              type="submit"
              disabled={busy || !value.trim()}
            >
              保存凭据 {name}
            </button>
            <button
              className="text-button"
              type="button"
              disabled={busy || (exists && !keys[name])}
              onClick={() => void save("")}
            >
              停用凭据 {name}
            </button>
            <button
              className="text-button"
              type="button"
              disabled={busy || !exists}
              onClick={() => void save(null)}
            >
              删除保存值 {name}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

export function SecretsSettings({
  settings,
}: {
  settings: AppConfigController;
}) {
  const config = settings.config!;
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [validation, setValidation] = useState("");
  const busy = !!settings.saving || settings.loading;
  const names = new Set(Object.keys(config.secretsconfig.keys));
  for (const model of Object.values(config.modelconfig.models))
    names.add(model.api_key_ref);
  for (const plugin of Object.values(config.mouldesconfig.plugins)) {
    for (const text of Object.values({ ...plugin.headers, ...plugin.env })) {
      for (const match of text.matchAll(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g))
        names.add(match[1]);
    }
  }
  if (config.mouldesconfig.plugins.send_email) {
    names.add("QQ_SMTP_USERNAME");
    names.add("QQ_SMTP_AUTH_CODE");
  }
  return (
    <section className="secrets-settings" aria-label="凭据管理">
      <div className="settings-section-heading">
        <h3>凭据</h3>
        <p>
          管理模型与插件共用的 Key 和
          Token。已有值只显示配置状态；新输入不会保存在浏览器缓存中。
        </p>
      </div>
      {[...names].sort().map((key) => (
        <SecretRow key={key} name={key} settings={settings} />
      ))}
      <form
        className="secret-add-form"
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy) return;
          setValidation("");
          const key = name.trim();
          if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
            setValidation(
              "凭据名称必须以字母或下划线开头，只能包含字母、数字、下划线。",
            );
            return;
          }
          if (names.has(key)) {
            setValidation("这个凭据名称已存在，请在对应行中更新。");
            return;
          }
          if (!value.trim()) {
            setValidation("请填写凭据值。");
            return;
          }
          if (await settings.saveSecrets({ [key]: value.trim() })) {
            setName("");
            setValue("");
          }
        }}
      >
        <h3>添加凭据</h3>
        <div className="settings-field">
          <label htmlFor="new-secret-name">凭据名称</label>
          <input
            id="new-secret-name"
            value={name}
            placeholder="CUSTOM_PLUGIN_TOKEN"
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="settings-field">
          <label htmlFor="new-secret-value">凭据值</label>
          <input
            id="new-secret-value"
            type="password"
            autoComplete="off"
            value={value}
            disabled={busy}
            onChange={(event) => setValue(event.target.value)}
          />
        </div>
        {validation && (
          <p className="field-error" role="alert">
            {validation}
          </p>
        )}
        <div className="settings-form-actions">
          <span>保存后可在连接参数中引用</span>
          <button
            className="primary-button"
            type="submit"
            disabled={busy || !name.trim() || !value.trim()}
          >
            添加凭据
          </button>
        </div>
      </form>
    </section>
  );
}
