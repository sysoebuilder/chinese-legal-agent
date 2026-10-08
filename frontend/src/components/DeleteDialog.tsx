import * as Dialog from "@radix-ui/react-dialog";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { Session } from "../types";
import { IconTrash, IconX } from "../icons";
import { spring, reveal } from "../motion";

export function DeleteDialog({
  open,
  session,
  busy,
  error,
  onOpenChange,
  onDelete,
}: {
  open: boolean;
  session: Session | null;
  busy: boolean;
  error: string;
  onOpenChange: (open: boolean) => void;
  onDelete: () => Promise<void>;
}) {
  const reduced = useReducedMotion();
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!busy) onOpenChange(next);
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
            <Dialog.Content asChild forceMount>
              <motion.section
                className="settings-dialog delete-dialog"
                initial={reduced ? false : { opacity: 0, y: 16, scale: 0.97 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 8, scale: 0.98 }}
                transition={reduced ? { duration: 0 } : spring}
              >
                <div className="dialog-heading">
                  <span className="dialog-symbol" aria-hidden="true">
                    <IconTrash />
                  </span>
                  <Dialog.Close asChild>
                    <button
                      className="icon-button"
                      aria-label="关闭删除确认"
                      disabled={busy}
                    >
                      <IconX />
                    </button>
                  </Dialog.Close>
                </div>
                <Dialog.Title>删除会话</Dialog.Title>
                <Dialog.Description>
                  删除“{session?.name || "未命名会话"}”后，聊天记录将无法恢复。
                </Dialog.Description>
                {error && (
                  <p className="dialog-error" role="alert">
                    {error}
                  </p>
                )}
                <div className="dialog-actions">
                  <Dialog.Close asChild>
                    <button className="secondary-button" disabled={busy}>
                      取消
                    </button>
                  </Dialog.Close>
                  <button
                    className="primary-button"
                    disabled={busy}
                    onClick={() => void onDelete()}
                  >
                    {busy ? "正在删除…" : "确认删除"}
                  </button>
                </div>
              </motion.section>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  );
}
