import { useEffect, useId, useRef, useState } from "react";
import { IconCheck, IconChevronDown, IconSettings } from "../icons";
import type { AppConfigController } from "../useAppConfig";

export function ModelSwitcher({
  settings,
  onConfigure,
}: {
  settings: AppConfigController;
  onConfigure: () => void;
}) {
  const models = settings.config?.modelconfig;
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const options = useRef<(HTMLButtonElement | null)[]>([]);
  const menuId = useId();
  const entries = Object.entries(models?.models || {});
  const disabled = settings.loading || !!settings.saving;
  useEffect(() => {
    if (!open) return;
    const selected = Math.max(
      0,
      entries.findIndex(([name]) => name === models?.active),
    );
    options.current[selected]?.focus();
    const dismiss = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !container.current?.contains(event.target)
      )
        setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open, models?.active]);
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
    <div
      className="model-switcher"
      ref={container}
      aria-busy={!!settings.saving}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.preventDefault();
          event.stopPropagation();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="model-switcher-trigger"
        aria-label="切换模型"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title="选择后续对话使用的模型"
        disabled={disabled}
        onClick={() => setOpen((previous) => !previous)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span>{models.models[models.active]?.model || models.active}</span>
        <IconChevronDown />
      </button>
      {open && (
        <div
          id={menuId}
          className="model-menu"
          role="listbox"
          aria-label="选择模型"
        >
          {entries.map(([name, model], index) => (
            <button
              key={name}
              ref={(element) => {
                options.current[index] = element;
              }}
              type="button"
              role="option"
              tabIndex={-1}
              aria-selected={name === models.active}
              disabled={disabled}
              onKeyDown={(event) => {
                const next =
                  event.key === "ArrowDown"
                    ? (index + 1) % entries.length
                    : event.key === "ArrowUp"
                      ? (index - 1 + entries.length) % entries.length
                      : event.key === "Home"
                        ? 0
                        : event.key === "End"
                          ? entries.length - 1
                          : null;
                if (next !== null) {
                  event.preventDefault();
                  options.current[next]?.focus();
                }
              }}
              onClick={() => {
                setOpen(false);
                trigger.current?.focus();
                if (name !== models.active) void settings.selectModel(name);
              }}
            >
              <span>{model.model || name}</span>
              {name === models.active && <IconCheck />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
