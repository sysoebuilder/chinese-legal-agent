import { IconChevronDown, IconSettings } from "../icons";
import type { AppConfigController } from "../useAppConfig";

export function ModelSwitcher({
  settings,
  onConfigure,
}: {
  settings: AppConfigController;
  onConfigure: () => void;
}) {
  const models = settings.config?.modelconfig;
  if (!models)
    return (
      <button
        className="model-settings-button"
        onClick={onConfigure}
        disabled={settings.loading}
      >
        <IconSettings />
        {settings.loading ? "加载模型…" : "模型设置"}
      </button>
    );
  return (
    <div className="model-switcher" aria-busy={!!settings.saving}>
      <select
        aria-label="切换模型"
        title="选择后续对话使用的模型"
        value={models.active}
        disabled={settings.loading || !!settings.saving}
        onChange={(event) => void settings.selectModel(event.target.value)}
      >
        {Object.entries(models.models).map(([name, model]) => (
          <option key={name} value={name}>
            {model.model || name}
          </option>
        ))}
      </select>
      <IconChevronDown />
    </div>
  );
}
