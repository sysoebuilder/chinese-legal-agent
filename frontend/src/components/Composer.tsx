import { useLayoutEffect, useRef } from "react";
import { IconArrowUp, IconSquare, IconX } from "../icons";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { spring, reveal } from "../motion";

interface Props {
  centered: boolean;
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onCancel: () => void;
  disabled: boolean;
  cancelVisible: boolean;
  cancelDisabled: boolean;
  status: string;
  error: string;
  onDismiss: () => void;
}
export function Composer(props: Props) {
  const input = useRef<HTMLTextAreaElement>(null);
  const reduced = useReducedMotion();
  useLayoutEffect(() => {
    if (!input.current) return;
    input.current.style.height = "auto";
    input.current.style.height = `${Math.min(input.current.scrollHeight, 160)}px`;
  }, [props.value]);
  return (
    <motion.div
      className={`composer-dock${props.centered ? " centered" : ""}`}
      layout={reduced ? false : "position"}
      transition={spring}
    >
      <AnimatePresence>
        {props.centered && (
          <motion.div
            className="empty-heading"
            initial={reduced ? false : { opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={reveal}
          >
            <span className="empty-mark" aria-hidden="true">
              a
            </span>
            <h2>新对话</h2>
          </motion.div>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {props.error && (
          <motion.div
            id="notice"
            className="notice"
            role="alert"
            initial={reduced ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            <span id="notice-text">{props.error}</span>
            <button
              className="icon-button"
              aria-label="关闭提示"
              onClick={props.onDismiss}
            >
              <IconX />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
      <motion.form
        id="composer"
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          if (!props.disabled && props.value.trim()) props.onSend();
        }}
      >
        <label className="sr-only" htmlFor="message-input">
          发送消息
        </label>
        <textarea
          ref={input}
          id="message-input"
          rows={1}
          maxLength={100000}
          placeholder="输入消息"
          aria-describedby="composer-hint"
          value={props.value}
          disabled={props.disabled}
          onChange={(event) => props.onChange(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              if (!props.disabled && props.value.trim()) props.onSend();
            }
          }}
        />
        <div className="composer-bottom">
          <span id="run-status" role="status">
            {props.status}
          </span>
          <div className="composer-buttons">
            {props.cancelVisible && (
              <button
                id="cancel-run"
                type="button"
                className="icon-button stop-button"
                aria-label="停止生成"
                title="停止生成"
                disabled={props.cancelDisabled}
                onClick={props.onCancel}
              >
                <IconSquare />
              </button>
            )}
            <button
              id="send"
              type="submit"
              className="send-button"
              aria-label="发送消息"
              title="发送消息"
              disabled={props.disabled || !props.value.trim()}
            >
              <IconArrowUp />
            </button>
          </div>
        </div>
      </motion.form>
      <p id="composer-hint" className="composer-hint">
        <span>
          <kbd>Enter</kbd> 发送
        </span>
        <span>
          <kbd>Shift + Enter</kbd> 换行
        </span>
      </p>
    </motion.div>
  );
}
