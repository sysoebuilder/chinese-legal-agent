import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  IconCpu,
  IconKey,
  IconPlug,
  IconShield,
  IconCheck,
  IconX,
} from "../icons";
import { spring, reveal } from "../motion";
import type { AppConfigController } from "../useAppConfig";
import { ModelSettings } from "./ModelSettings";
import { PluginSettings } from "./PluginSettings";
import { PermissionsSettings } from "./PermissionsSettings";

export type SettingsTab = "models" | "plugins" | "permissions" | "connection";
const tabs = [
  { id: "models", label: "模型", icon: IconCpu },
  { id: "plugins", label: "插件", icon: IconPlug },
  { id: "permissions", label: "权限", icon: IconShield },
  { id: "connection", label: "连接", icon: IconKey },
] as const;

export function SettingsDialog({
  open,
  busy,
  onOpenChange,
  onSave,
  settings,
  initialTab = "models",
}: {
  open: boolean;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (token: string) => Promise<boolean>;
  settings: AppConfigController;
  initialTab?: SettingsTab;
}) {
  const [token, setToken] = useState("");
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  const [connecting, setConnecting] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const opener = useRef<HTMLElement | null>(null);
  const content = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  useEffect(() => {
    if (open) {
      setToken(sessionStorage.getItem("agent100.token") || "");
      setTab(settings.needsAuth ? "connection" : initialTab);
      setConnectionError("");
    } else setToken("");
  }, [open, initialTab]);
  useEffect(() => {
    if (open && settings.needsAuth) setTab("connection");
  }, [open, settings.needsAuth]);
  useEffect(() => {
    if (open) content.current?.scrollTo({ top: 0 });
  }, [tab, open]);
  const locked = !!settings.saving || connecting;
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!locked) onOpenChange(next);
      }}
    >
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild forceMount>
              <motion.div
                className="dialog-overlay"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={reduced ? { duration: 0 } : reveal}
              />
            </Dialog.Overlay>
            <Dialog.Content
              asChild
              forceMount
              onOpenAutoFocus={(event) => {
                event.preventDefault();
                opener.current = document.activeElement as HTMLElement;
                document
                  .getElementById(
                    `settings-tab-${settings.needsAuth ? "connection" : initialTab}`,
                  )
                  ?.focus();
              }}
              onCloseAutoFocus={(event) => {
                event.preventDefault();
                opener.current?.focus();
              }}
            >
              <motion.section
                className="settings-dialog app-settings"
                id="settings-dialog"
                initial={reduced ? false : { opacity: 0, y: 16, scale: 0.97 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 8, scale: 0.98 }}
                transition={reduced ? { duration: 0 } : spring}
              >
                <div className="dialog-heading">
                  <div>
                    <Dialog.Title>设置</Dialog.Title>
                    <Dialog.Description>
                      模型、工具、权限与连接，按需调整。
                    </Dialog.Description>
                  </div>
                  <Dialog.Close asChild>
                    <button
                      className="icon-button"
                      aria-label="关闭设置"
                      disabled={locked}
                    >
                      <IconX />
                    </button>
                  </Dialog.Close>
                </div>
                <div
                  className="settings-tabs"
                  role="tablist"
                  aria-label="设置分类"
                >
                  {tabs.map(({ id, label, icon: Icon }) => (
                    <button
                      type="button"
                      id={`settings-tab-${id}`}
                      key={id}
                      role="tab"
                      aria-selected={tab === id}
                      aria-controls={`settings-panel-${id}`}
                      tabIndex={tab === id ? 0 : -1}
                      onClick={() => setTab(id)}
                      onKeyDown={(event) => {
                        const index = tabs.findIndex((item) => item.id === tab);
                        const target =
                          event.key === "ArrowRight"
                            ? (index + 1) % tabs.length
                            : event.key === "ArrowLeft"
                              ? (index + tabs.length - 1) % tabs.length
                              : event.key === "Home"
                                ? 0
                                : event.key === "End"
                                  ? tabs.length - 1
                                  : -1;
                        if (target < 0) return;
                        event.preventDefault();
                        setTab(tabs[target].id);
                        document
                          .getElementById(`settings-tab-${tabs[target].id}`)
                          ?.focus();
                      }}
                    >
                      {tab === id && (
                        <motion.span
                          className="settings-tab-indicator"
                          layoutId="settings-tab-indicator"
                          transition={reduced ? { duration: 0 } : spring}
                        />
                      )}
                      <span className="settings-tab-label">
                        <Icon />
                        {label}
                      </span>
                    </button>
                  ))}
                </div>
                <div className="settings-scroll" ref={content}>
                  {(["models", "plugins", "permissions"] as const).map((id) => (
                    <motion.section
                      key={id}
                      id={`settings-panel-${id}`}
                      role="tabpanel"
                      aria-labelledby={`settings-tab-${id}`}
                      hidden={tab !== id}
                      initial={false}
                      animate={{
                        opacity: tab === id ? 1 : 0,
                        y: tab === id ? 0 : 7,
                      }}
                      transition={reduced ? { duration: 0 } : reveal}
                    >
                      {settings.config ? (
                        id === "models" ? (
                          <ModelSettings settings={settings} />
                        ) : id === "plugins" ? (
                          <PluginSettings settings={settings} />
                        ) : (
                          <PermissionsSettings settings={settings} />
                        )
                      ) : settings.loading ? (
                        <div
                          className="settings-loading"
                          role="status"
                          aria-label="正在加载配置"
                        >
                          <div className="skeleton" />
                          <div className="skeleton" />
                          <div className="skeleton" />
                        </div>
                      ) : (
                        <div className="settings-empty">
                          <h3>暂时无法读取配置</h3>
                          <p>{settings.error || "请检查连接后重试。"}</p>
                          <button
                            className="secondary-button"
                            onClick={() => void settings.reload()}
                          >
                            重新加载
                          </button>
                          <button
                            className="text-button"
                            onClick={() => setTab("connection")}
                          >
                            连接设置
                          </button>
                        </div>
                      )}
                    </motion.section>
                  ))}
                  <motion.section
                    id="settings-panel-connection"
                    role="tabpanel"
                    aria-labelledby="settings-tab-connection"
                    hidden={tab !== "connection"}
                    initial={false}
                    animate={{
                      opacity: tab === "connection" ? 1 : 0,
                      y: tab === "connection" ? 0 : 7,
                    }}
                    transition={reduced ? { duration: 0 } : reveal}
                  >
                    <form
                      className="connection-settings"
                      onSubmit={async (event) => {
                        event.preventDefault();
                        if (busy || locked) return;
                        setConnecting(true);
                        setConnectionError("");
                        try {
                          if (await onSave(token)) setTab("models");
                        } catch {
                          setConnectionError("连接失败，请检查令牌后重试。");
                        } finally {
                          setConnecting(false);
                        }
                      }}
                    >
                      <div className="settings-section-heading">
                        <h3>连接到你的助手</h3>
                        <p>接口启用了认证时，填写访问令牌。</p>
                      </div>
                      <div className="settings-field">
                        <label htmlFor="api-token">API 访问令牌</label>
                        <input
                          id="api-token"
                          type="password"
                          autoComplete="off"
                          placeholder="未启用认证可留空"
                          value={token}
                          disabled={locked}
                          onChange={(event) => setToken(event.target.value)}
                        />
                        <p className="field-hint">仅保存在当前浏览器会话中。</p>
                      </div>
                      {connectionError && (
                        <p className="field-error" role="alert">
                          {connectionError}
                        </p>
                      )}
                      <div className="settings-form-actions">
                        <span>未启用认证时可留空</span>
                        <button
                          className="primary-button"
                          type="submit"
                          disabled={busy || locked}
                        >
                          {connecting ? "连接中…" : "保存并连接"}
                        </button>
                      </div>
                    </form>
                  </motion.section>
                </div>
                <footer className="settings-footer">
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.div
                      key={settings.error || settings.notice || "hint"}
                      role={settings.error ? "alert" : "status"}
                      className={`settings-feedback${settings.error ? " is-error" : ""}`}
                      initial={reduced ? false : { opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -4 }}
                      transition={reduced ? { duration: 0 } : reveal}
                    >
                      {settings.notice && !settings.error && <IconCheck />}
                      {settings.error ||
                        settings.notice ||
                        "配置用于后续运行，当前回答不受影响。"}
                    </motion.div>
                  </AnimatePresence>
                </footer>
              </motion.section>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  );
}
