import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "motion/react";
import { IconCheck, IconPlus } from "../icons";
import type { ModelEntry } from "../types";
import type { AppConfigController } from "../useAppConfig";

const defaults: Record<ModelEntry["provider"], ModelEntry> = {
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
};
const keyName = (name: string) =>
  `MODEL_${name.replace(/[^A-Za-z0-9_]/g, "_").toUpperCase()}_API_KEY`;

export function ModelSettings({ settings }: { settings: AppConfigController }) {
  const config = settings.config!;
  const { models, active } = config.modelconfig;
  const [selected, setSelected] = useState(active);
  const [entry, setEntry] = useState<ModelEntry>({ ...models[active] });
  const [name, setName] = useState(active);
  const [isNew, setIsNew] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [activate, setActivate] = useState(true);
  const [validation, setValidation] = useState("");
  const list = useRef<HTMLElement>(null);
  const reduced = useReducedMotion();
  useEffect(() => {
    const container = list.current;
    const item = container?.querySelector<HTMLElement>(".selected");
    if (!container || !item || container.scrollWidth <= container.clientWidth)
      return;
    container.scrollTo({
      left:
        item.offsetLeft -
        container.offsetLeft -
        (container.clientWidth - item.clientWidth) / 2,
      behavior: reduced ? "instant" : "smooth",
    });
  }, [selected, isNew, reduced]);
  const busy = !!settings.saving || settings.loading;
  const duplicate = isNew && Object.hasOwn(models, name.trim());
  const changed =
    isNew ||
    !!apiKey.trim() ||
    JSON.stringify(entry) !== JSON.stringify(models[selected]) ||
    (activate && selected !== active);

  function select(id: string) {
    setSelected(id);
    setName(id);
    setEntry({ ...models[id] });
    setIsNew(false);
    setApiKey("");
    setActivate(id === active);
    setValidation("");
  }
  function add() {
    let id = "custom",
      index = 2;
    while (Object.hasOwn(models, id)) id = `custom-${index++}`;
    setIsNew(true);
    setSelected("");
    setName(id);
    setEntry({ ...defaults.deepseek, api_key_ref: keyName(id) });
    setApiKey("");
    setActivate(true);
    setValidation("");
  }

  return (
    <div className="model-settings-layout">
      <aside className="model-config-list" aria-label="模型配置列表" ref={list}>
        <div className="setting-section-label">
          已配置的模型<span>{Object.keys(models).length}</span>
        </div>
        {Object.entries(models).map(([id, model]) => (
          <button
            type="button"
            key={id}
            className={`model-config-item${!isNew && selected === id ? " selected" : ""}`}
            aria-label={`编辑模型：${id}`}
            aria-pressed={!isNew && selected === id}
            disabled={busy}
            onClick={() => select(id)}
          >
            <span className="model-config-item-title">
              {model.model || id}
              {id === active && <IconCheck />}
            </span>
            <span className="model-config-item-meta">
              {model.provider === "glm" ? "智谱 GLM" : "DeepSeek"}
              <span>{id === active ? "使用中" : id}</span>
            </span>
          </button>
        ))}
        <button
          type="button"
          className={`add-model${isNew ? " selected" : ""}`}
          disabled={busy}
          onClick={add}
        >
          <IconPlus />
          添加模型
        </button>
      </aside>
      <form
        className="model-config-form"
        aria-label="模型配置"
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy || duplicate) return;
          try {
            const url = new URL(entry.base_url.trim());
            if (
              !["https:", "http:"].includes(url.protocol) ||
              url.username ||
              url.password
            )
              throw new Error();
          } catch {
            setValidation("请填写不含用户名或密码的 HTTP(S) 服务地址。");
            return;
          }
          const normalized = {
            ...entry,
            model: entry.model.trim(),
            base_url: entry.base_url.trim(),
            api_key_ref: entry.api_key_ref.trim(),
          };
          setValidation("");
          if (
            await settings.saveModel(name.trim(), normalized, apiKey, activate)
          ) {
            setEntry(normalized);
            setSelected(name.trim());
            setIsNew(false);
            setApiKey("");
          }
        }}
      >
        <div className="settings-section-heading">
          <h3>{isNew ? "添加一个模型" : "模型配置"}</h3>
          <p>
            {isNew ? "连接你想使用的模型。" : "调整连接参数，或更新访问密钥。"}
          </p>
        </div>
        <fieldset disabled={busy}>
          {isNew && (
            <div className="settings-field">
              <label htmlFor="model-config-name">配置名称</label>
              <input
                id="model-config-name"
                value={name}
                required
                pattern={"[A-Za-z0-9_\\-]+"}
                maxLength={80}
                onChange={(event) => {
                  setName(event.target.value);
                  setEntry({
                    ...entry,
                    api_key_ref: keyName(event.target.value),
                  });
                }}
              />
              <p className="field-hint">
                用字母、数字或短横线区分配置，例如 work。
              </p>
              {duplicate && (
                <p className="field-error" role="alert">
                  这个名称已存在，请换一个。
                </p>
              )}
            </div>
          )}
          <div className="settings-field">
            <label htmlFor="model-provider">提供商</label>
            <select
              id="model-provider"
              value={entry.provider}
              onChange={(event) => {
                const provider =
                  event.target.value === "glm" ? "glm" : "deepseek";
                setEntry({
                  ...defaults[provider],
                  ...(isNew ? { api_key_ref: keyName(name) } : {}),
                });
                setApiKey("");
              }}
            >
              <option value="glm">智谱 GLM</option>
              <option value="deepseek">DeepSeek</option>
            </select>
          </div>
          <div className="settings-field">
            <label htmlFor="model-id">模型名称</label>
            <input
              id="model-id"
              value={entry.model}
              placeholder="例如 deepseek-flash"
              autoComplete="off"
              onChange={(event) =>
                setEntry({ ...entry, model: event.target.value })
              }
            />
          </div>
          <div className="settings-field">
            <label htmlFor="model-base-url">服务地址</label>
            <input
              id="model-base-url"
              type="url"
              required
              value={entry.base_url}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) =>
                setEntry({ ...entry, base_url: event.target.value })
              }
            />
          </div>
          <div className="settings-field">
            <label htmlFor="model-api-key">
              API Key
              {config.secretsconfig.keys[entry.api_key_ref] && (
                <span className="field-state">
                  <IconCheck />
                  已设置
                </span>
              )}
            </label>
            <input
              id="model-api-key"
              type="password"
              autoComplete="new-password"
              value={apiKey}
              placeholder={
                config.secretsconfig.keys[entry.api_key_ref]
                  ? "已设置，留空保留"
                  : "输入 API Key"
              }
              onChange={(event) => setApiKey(event.target.value)}
            />
            <p className="field-hint">密钥仅保存到服务端，留空不会修改。</p>
          </div>
          <details className="model-advanced">
            <summary>更多设置</summary>
            <div className="settings-field">
              <label htmlFor="model-key-ref">密钥名称</label>
              <input
                id="model-key-ref"
                required
                pattern="[A-Za-z_][A-Za-z0-9_]*"
                value={entry.api_key_ref}
                spellCheck={false}
                onChange={(event) =>
                  setEntry({ ...entry, api_key_ref: event.target.value })
                }
              />
              <p className="field-hint">多个模型可使用同名密钥。</p>
            </div>
          </details>
          {(isNew || selected !== active) && (
            <label className="settings-checkbox">
              <input
                type="checkbox"
                checked={activate}
                onChange={(event) => setActivate(event.target.checked)}
              />
              保存后使用此模型
            </label>
          )}
        </fieldset>
        {validation && (
          <p className="field-error" role="alert">
            {validation}
          </p>
        )}
        <div className="settings-form-actions">
          <span>
            {!isNew && selected === active
              ? "当前默认模型"
              : "保存后可在顶部切换"}
          </span>
          <button
            className="primary-button"
            type="submit"
            disabled={busy || duplicate || !changed}
          >
            {settings.saving === "model" ? "保存中…" : "保存模型"}
          </button>
        </div>
      </form>
    </div>
  );
}
