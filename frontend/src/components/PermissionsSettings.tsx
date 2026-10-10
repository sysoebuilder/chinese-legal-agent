import { useEffect, useRef, useState } from "react";
import type { PermissionsConfig } from "../types";
import type { AppConfigController } from "../useAppConfig";

const modes: {
  value: PermissionsConfig["mode"];
  label: string;
  description: string;
}[] = [
  {
    value: "restricted",
    label: "限制",
    description:
      "发送邮件、执行命令、修改或删除记忆等操作，执行前需要你的审批。查询工具直接执行。",
  },
  {
    value: "unrestricted",
    label: "无限制",
    description:
      "已启用的工具直接执行，发送邮件、执行命令和修改数据均无需审批。",
  },
];

export function PermissionsSettings({
  settings,
}: {
  settings: AppConfigController;
}) {
  const savedMode = settings.config!.permissionsconfig.mode;
  const [mode, setMode] = useState(savedMode);
  const previousMode = useRef(savedMode);
  useEffect(() => {
    const previous = previousMode.current;
    setMode((draft) => (draft === previous ? savedMode : draft));
    previousMode.current = savedMode;
  }, [savedMode]);
  const busy = !!settings.saving || settings.loading;
  return (
    <form
      className="permissions-settings"
      onSubmit={async (event) => {
        event.preventDefault();
        if (busy || mode === savedMode) return;
        await settings.savePermissions(mode);
      }}
    >
      <div className="settings-section-heading">
        <h3>Agent 权限</h3>
        <p>选择工具执行前是否需要你的审批。</p>
      </div>
      <fieldset className="permission-options" disabled={busy}>
        <legend className="sr-only">Agent 权限模式</legend>
        {modes.map((option) => (
          <label className="permission-option" key={option.value}>
            <input
              type="radio"
              name="agent-permission-mode"
              value={option.value}
              checked={mode === option.value}
              onChange={() => setMode(option.value)}
              aria-labelledby={`permission-title-${option.value}`}
              aria-describedby={`permission-description-${option.value}`}
            />
            <span className="setting-row-copy">
              <span
                className="permission-option-title"
                id={`permission-title-${option.value}`}
              >
                {option.label}
              </span>
              <span
                className="permission-option-description"
                id={`permission-description-${option.value}`}
              >
                {option.description}
              </span>
            </span>
          </label>
        ))}
      </fieldset>
      <p className="field-hint">
        仅作用于已启用的工具。法律流程需要补充信息时，仍会向你提问。
      </p>
      <div className="settings-form-actions">
        <span>保存后在后续运行中生效。</span>
        <button
          className="primary-button"
          type="submit"
          disabled={busy || mode === savedMode}
        >
          {settings.saving === "permissions" ? "保存中…" : "保存权限"}
        </button>
      </div>
    </form>
  );
}
