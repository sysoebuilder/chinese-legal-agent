import { memo, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { IconCheck, IconCopy, IconChevronDown } from "../icons";
import { spring, reveal } from "../motion";
import DOMPurify from "dompurify";
import { marked } from "marked";
import type { Message as ChatMessage } from "../types";

const Markdown = memo(function Markdown({ content }: { content: string }) {
  const html = useMemo(
    () =>
      DOMPurify.sanitize(marked.parse(content, { async: false }), {
        FORBID_TAGS: ["img", "style", "form", "input", "button"],
        FORBID_ATTR: ["style"],
      }),
    [content],
  );
  return (
    <div
      className="message-content"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
});

export function Reasoning({ text }: { text?: string }) {
  const [open, setOpen] = useState(false);
  const reduced = useReducedMotion();
  if (!text) return null;
  return (
    <div className="reasoning">
      <button
        className="reasoning-toggle"
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span>思考过程</span>
        <motion.span
          animate={{ rotate: open ? 180 : 0 }}
          transition={reduced ? { duration: 0 } : reveal}
        >
          <IconChevronDown />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            className="reasoning-panel"
            initial={{ height: reduced ? "auto" : 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: reduced ? "auto" : 0, opacity: 0 }}
            transition={reduced ? { duration: 0 } : reveal}
          >
            <div className="reasoning-content">{text}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export const Message = memo(function Message({
  message,
  onError,
}: {
  message: ChatMessage;
  onError: (message: string) => void;
}) {
  const reduced = useReducedMotion();
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      onError("复制失败，请手动选择文字复制。");
    }
  }
  return (
    <motion.article
      className={`message ${message.role}`}
      data-message-id={message.id}
      initial={reduced ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? { duration: 0 } : spring}
    >
      {message.role === "assistant" && (
        <span className="message-avatar" aria-hidden="true">
          a
        </span>
      )}
      <div className="message-main">
        <div className="message-label">
          {message.role === "user" ? "你" : "Agent100"}
        </div>
        <Reasoning text={message.reasoning} />
        {message.role === "user" ? (
          <div className="message-content">{message.content}</div>
        ) : (
          <Markdown content={message.content} />
        )}
        {message.role === "assistant" && message.content && (
          <button
            className="icon-button copy-button"
            aria-label="复制回答"
            title="复制回答"
            onClick={copy}
          >
            {copied ? <IconCheck /> : <IconCopy />}
          </button>
        )}
      </div>
    </motion.article>
  );
});

export function LiveContent({ content }: { content: string }) {
  return <Markdown content={content} />;
}
