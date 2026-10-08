import { Fragment } from "react";
import { motion, useReducedMotion } from "motion/react";
import { spring, reveal } from "../motion";
import {
  IconPlus,
  IconRefresh,
  IconMessage,
  IconSettings,
  IconTrash,
  IconX,
} from "../icons";
import type { Session } from "../types";

function sessionGroup(session: Session) {
  const date = new Date(session.updated_at),
    today = new Date();
  today.setHours(0, 0, 0, 0);
  return date >= today
    ? "今天"
    : date >= new Date(today.getTime() - 6 * 86400000)
      ? "最近七天"
      : "更早";
}
interface Props {
  visible: boolean;
  sessions: Session[];
  selected: string | null;
  loading: boolean;
  error: string;
  hasMore: boolean;
  busy: boolean;
  deletingThread: string | null;
  onSelect: (thread: string) => void;
  onDelete: (session: Session) => void;
  onNew: () => void;
  onRefresh: () => void;
  onMore: () => void;
  onSettings: () => void;
  onClose: () => void;
}
export function Sidebar(props: Props) {
  const reduced = useReducedMotion();
  return (
    <motion.aside
      id="sidebar"
      className="sidebar"
      aria-label="历史对话"
      aria-hidden={!props.visible}
      inert={!props.visible}
      initial={false}
      animate={{
        x: props.visible ? 0 : "-105%",
        opacity: props.visible ? 1 : 0,
      }}
      transition={reduced ? { duration: 0 } : spring}
    >
      <div className="brand">
        <span className="brand-mark">a</span>
        <span>
          Agent<span className="brand-number">100</span>
        </span>
        <button
          className="icon-button mobile-only"
          aria-label="关闭侧栏"
          onClick={props.onClose}
        >
          <IconX />
        </button>
      </div>
      <motion.button
        id="new-chat"
        className="new-chat"
        onClick={props.onNew}
        disabled={props.busy}
        whileTap={reduced ? undefined : { scale: 0.98 }}
      >
        <IconPlus />
        新对话<kbd className="shortcut">Ctrl K</kbd>
      </motion.button>
      <div className="sidebar-heading">
        <span>历史对话</span>
        <button
          className="icon-button"
          aria-label="刷新历史对话"
          disabled={props.loading || props.busy}
          onClick={props.onRefresh}
        >
          <IconRefresh />
        </button>
      </div>
      <nav id="sessions" className="sessions" aria-label="选择对话">
        {props.loading && !props.sessions.length ? (
          <>
            <div className="skeleton" />
            <div className="skeleton" />
            <div className="skeleton" />
          </>
        ) : (
          props.sessions.map((session, index) => {
            const group = sessionGroup(session);
            return (
              <Fragment key={session.thread_id}>
                {(index === 0 ||
                  group !== sessionGroup(props.sessions[index - 1])) && (
                  <div className="session-group">{group}</div>
                )}
                <motion.div
                  className={`session-row${session.thread_id === props.selected ? " selected" : ""}`}
                  initial={reduced ? false : { opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={reveal}
                >
                  {session.thread_id === props.selected && (
                    <motion.span
                      className="session-selection"
                      layoutId="session-selection"
                      transition={reduced ? { duration: 0 } : spring}
                      aria-hidden="true"
                    />
                  )}
                  <button
                    className={`session${session.thread_id === props.selected ? " active" : ""}`}
                    title={session.name}
                    aria-current={
                      session.thread_id === props.selected ? "page" : undefined
                    }
                    disabled={props.busy}
                    onClick={() => props.onSelect(session.thread_id)}
                  >
                    <IconMessage />
                    <span className="session-name">{session.name}</span>
                  </button>
                  <button
                    className="icon-button session-delete"
                    title={
                      props.deletingThread === session.thread_id
                        ? "正在删除…"
                        : "删除会话"
                    }
                    aria-label={`删除会话：${session.name || "未命名会话"}`}
                    aria-busy={props.deletingThread === session.thread_id}
                    disabled={props.busy}
                    onClick={() => props.onDelete(session)}
                  >
                    <IconTrash />
                  </button>
                </motion.div>
              </Fragment>
            );
          })
        )}
        {!props.loading && !props.sessions.length && (
          <p className="empty-sessions">暂无对话</p>
        )}
      </nav>
      <div
        id="session-error"
        className="sidebar-error"
        role="alert"
        hidden={!props.error}
      >
        {props.error}
      </div>
      {props.hasMore && (
        <button
          className="text-button"
          disabled={props.loading || props.busy}
          onClick={props.onMore}
        >
          更多对话
        </button>
      )}
      <div className="sidebar-footer">
        <button
          className="settings-button"
          aria-label="设置"
          onClick={props.onSettings}
        >
          <IconSettings />
          <span>连接设置</span>
        </button>
      </div>
    </motion.aside>
  );
}
