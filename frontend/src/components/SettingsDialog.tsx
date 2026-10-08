import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { IconX } from "../icons";
import { spring, reveal } from "../motion";

export function SettingsDialog({
  open,
  busy,
  onOpenChange,
  onSave,
}: {
  open: boolean;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (token: string) => Promise<void>;
}) {
  const [token, setToken] = useState("");
  const reduced = useReducedMotion();
  useEffect(() => {
    if (open) setToken(sessionStorage.getItem("agent100.token") || "");
  }, [open]);
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
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
            <Dialog.Content asChild forceMount>
              <motion.section
                className="settings-dialog"
                id="settings-dialog"
                initial={reduced ? false : { opacity: 0, y: 16, scale: 0.97 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 8, scale: 0.98 }}
                transition={reduced ? { duration: 0 } : spring}
              >
                <div className="dialog-heading">
                  <Dialog.Title>连接设置</Dialog.Title>
                  <Dialog.Close asChild>
                    <button className="icon-button" aria-label="关闭设置">
                      <IconX />
                    </button>
                  </Dialog.Close>
                </div>
                <Dialog.Description>
                  已启用接口认证时，填写 API 的访问令牌。
                </Dialog.Description>
                <form
                  onSubmit={async (event) => {
                    event.preventDefault();
                    if (busy) return;
                    onOpenChange(false);
                    await onSave(token);
                  }}
                >
                  <label htmlFor="api-token">API 访问令牌</label>
                  <input
                    id="api-token"
                    type="password"
                    autoComplete="off"
                    placeholder="未启用认证可留空"
                    value={token}
                    onChange={(event) => setToken(event.target.value)}
                  />
                  <p className="helper">仅保存在当前浏览器会话中。</p>
                  <button
                    className="primary-button"
                    type="submit"
                    disabled={busy}
                  >
                    保存并连接
                  </button>
                </form>
              </motion.section>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  );
}
