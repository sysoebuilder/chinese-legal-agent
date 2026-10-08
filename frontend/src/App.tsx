import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { reveal } from "./motion";
import {
  IconLayoutSidebar,
  IconMoon,
  IconSun,
  IconPlus,
  IconSettings,
  IconX,
  IconCheck,
} from "./icons";
import { Composer } from "./components/Composer";
import { Sidebar } from "./components/Sidebar";
import { Message, LiveContent, Reasoning } from "./components/Message";
import { InterruptForm } from "./components/InterruptForm";
import { SettingsDialog, type SettingsTab } from "./components/SettingsDialog";
import { ModelSwitcher } from "./components/ModelSwitcher";
import { useAppConfig } from "./useAppConfig";
import { DeleteDialog } from "./components/DeleteDialog";
import { statusText, useChat } from "./useChat";
import type { Session } from "./types";

function Skeletons() {
  return (
    <>
      <div className="skeleton" />
      <div className="skeleton" />
      <div className="skeleton" />
    </>
  );
}

export function App() {
  const chat = useChat();
  const configuration = useAppConfig();
  const [draft, setDraft] = useState("");
  const [settings, setSettings] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("models");
  const [deleteTarget, setDeleteTarget] = useState<Session | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const reduced = useReducedMotion();
  const [copyError, setCopyError] = useState("");
  const [mobile, setMobile] = useState(
    () => matchMedia("(max-width:767px)").matches,
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const [theme, setTheme] = useState(
    () =>
      localStorage.getItem("agent100.theme") ||
      (matchMedia("(prefers-color-scheme:dark)").matches ? "dark" : "light"),
  );
  const scrollArea = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  const busy = chat.sending || !!chat.deletingThread;
  const empty =
    !chat.thread && !chat.loading && !chat.messages.length && !chat.live;
  const disabled =
    busy ||
    chat.loading ||
    !!(chat.run && !["succeeded", "cancelled"].includes(chat.run.status));
  const status = chat.deletingThread
    ? "正在删除会话…"
    : chat.sending
      ? "正在提交…"
      : chat.run?.cancel_requested
        ? "正在停止…"
        : chat.canReconnect
          ? "执行已中断"
          : chat.run
            ? statusText[chat.run.status]
            : "";
  const title =
    chat.sessions.find((session) => session.thread_id === chat.thread)?.name ||
    (chat.thread ? "对话" : "新对话");

  useEffect(() => {
    const screen = matchMedia("(max-width:767px)");
    const onChange = () => {
      setMobile(screen.matches);
      setSidebarOpen(false);
    };
    screen.addEventListener("change", onChange);
    return () => screen.removeEventListener("change", onChange);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    const screen = matchMedia("(prefers-color-scheme:dark)");
    const onChange = () => {
      if (!localStorage.getItem("agent100.theme"))
        setTheme(screen.matches ? "dark" : "light");
    };
    screen.addEventListener("change", onChange);
    return () => screen.removeEventListener("change", onChange);
  }, []);
  useEffect(() => {
    if (chat.needsAuth) {
      setSettingsTab("connection");
      setSettings(true);
      chat.dismissAuth();
    }
  }, [chat.needsAuth]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        (event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === "k" &&
        !settings &&
        !deleteOpen
      ) {
        event.preventDefault();
        newChat();
      }
      if (event.key === "Escape") setSidebarOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, settings, deleteOpen]);
  useLayoutEffect(() => {
    if (followBottom.current && scrollArea.current)
      scrollArea.current.scrollTop = scrollArea.current.scrollHeight;
  }, [chat.messages, chat.live, chat.thread, chat.loading]);

  function focusInput() {
    requestAnimationFrame(() =>
      document.getElementById("message-input")?.focus(),
    );
  }
  function newChat() {
    if (busy) return;
    chat.newChat();
    setDraft("");
    setCopyError("");
    setSidebarOpen(false);
    followBottom.current = true;
    focusInput();
  }
  function select(thread: string) {
    if (busy) return;
    followBottom.current = true;
    setDraft("");
    setCopyError("");
    setSidebarOpen(false);
    void chat.selectSession(thread);
  }
  async function deleteSession(session: Session) {
    if (busy) return;
    const selected = chat.thread === session.thread_id;
    if (await chat.deleteSession(session.thread_id)) {
      setDeleteOpen(false);
      if (selected) {
        setDraft("");
        setCopyError("");
        followBottom.current = true;
        focusInput();
      }
    } else {
      setDeleteError("删除未完成，请查看侧栏中的错误信息后重试。");
    }
  }
  async function loadOlder() {
    const area = scrollArea.current;
    if (!area) return;
    const height = area.scrollHeight,
      top = area.scrollTop,
      thread = chat.thread;
    followBottom.current = false;
    await chat.loadOlder();
    requestAnimationFrame(() => {
      if (localStorage.getItem("agent100.thread") === thread)
        area.scrollTop = top + area.scrollHeight - height;
    });
  }
  async function send() {
    if (await chat.send(draft)) {
      setDraft("");
      followBottom.current = true;
    }
  }
  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    localStorage.setItem("agent100.theme", next);
    setTheme(next);
  }
  function openSettings(tab: SettingsTab = "models") {
    setSettingsTab(tab);
    setSettings(true);
    setSidebarOpen(false);
  }

  return (
    <div
      className={`app-shell${sidebarOpen ? " sidebar-open" : ""}${sidebarHidden ? " sidebar-hidden" : ""}`}
    >
      <AnimatePresence>
        {mobile && sidebarOpen && (
          <motion.button
            className="scrim"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={reduced ? { duration: 0 } : reveal}
            aria-label="关闭历史对话栏"
            onClick={() => setSidebarOpen(false)}
          />
        )}
      </AnimatePresence>
      <Sidebar
        visible={mobile ? sidebarOpen : !sidebarHidden}
        sessions={chat.sessions}
        selected={chat.thread}
        loading={chat.sessionsLoading}
        error={chat.sessionError}
        hasMore={chat.offset !== null}
        busy={busy}
        deletingThread={chat.deletingThread}
        onSelect={select}
        onDelete={(session) => {
          setDeleteTarget(session);
          setDeleteError("");
          setDeleteOpen(true);
        }}
        onNew={newChat}
        onRefresh={() => void chat.loadSessions()}
        onMore={() => void chat.loadSessions(true)}
        onSettings={() => openSettings()}
        onClose={() => setSidebarOpen(false)}
      />
      <main className={`main${empty ? " is-empty" : ""}`}>
        <header className="header">
          <div className="header-title">
            <button
              className="icon-button"
              aria-label="切换历史对话栏"
              aria-expanded={mobile ? sidebarOpen : !sidebarHidden}
              onClick={() =>
                mobile
                  ? setSidebarOpen(!sidebarOpen)
                  : setSidebarHidden(!sidebarHidden)
              }
            >
              <IconLayoutSidebar />
            </button>
            <div className="title-slot">
              <AnimatePresence mode="wait" initial={false}>
                <motion.h1
                  key={title}
                  id="chat-title"
                  initial={reduced ? false : { opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={reduced ? { duration: 0 } : reveal}
                >
                  {title}
                </motion.h1>
              </AnimatePresence>
            </div>
          </div>
          <div className="header-actions">
            <ModelSwitcher
              settings={configuration}
              onConfigure={() => openSettings()}
            />
            <button
              className="icon-button"
              aria-label="打开设置"
              title="模型与插件设置"
              onClick={() => openSettings()}
            >
              <IconSettings />
            </button>
            <button
              className="icon-button header-new"
              aria-label="开启新对话"
              title="新对话"
              disabled={busy}
              onClick={newChat}
            >
              <IconPlus />
            </button>
            <button
              className="icon-button"
              aria-label="切换深浅色"
              title={theme === "dark" ? "切换浅色" : "切换深色"}
              onClick={toggleTheme}
            >
              <AnimatePresence mode="wait" initial={false}>
                <motion.span
                  key={theme}
                  initial={
                    reduced ? false : { opacity: 0, rotate: -30, scale: 0.8 }
                  }
                  animate={{ opacity: 1, rotate: 0, scale: 1 }}
                  exit={{ opacity: 0, rotate: 30, scale: 0.8 }}
                  transition={reduced ? { duration: 0 } : reveal}
                >
                  {theme === "dark" ? <IconSun /> : <IconMoon />}
                </motion.span>
              </AnimatePresence>
            </button>
          </div>
        </header>
        <AnimatePresence>
          {!settings &&
            (configuration.notice ||
              (configuration.config && configuration.error)) && (
              <motion.div
                className="config-toast"
                role={configuration.error ? "alert" : "status"}
                initial={reduced ? false : { opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={reduced ? { duration: 0 } : reveal}
              >
                {!configuration.error && <IconCheck />}
                <span>{configuration.error || configuration.notice}</span>
                {configuration.error && (
                  <button
                    className="icon-button"
                    aria-label="关闭配置提示"
                    onClick={configuration.dismissError}
                  >
                    <IconX />
                  </button>
                )}
              </motion.div>
            )}
        </AnimatePresence>
        <div
          id="scroll-area"
          ref={scrollArea}
          className="scroll-area"
          onScroll={(event) => {
            const area = event.currentTarget;
            followBottom.current =
              area.scrollHeight - area.clientHeight - area.scrollTop < 130;
          }}
        >
          <motion.section
            key={chat.thread || "new"}
            id="conversation"
            className="conversation"
            aria-label="对话消息"
            aria-busy={chat.loading}
            initial={reduced ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={reduced ? { duration: 0 } : reveal}
          >
            {chat.before && (
              <button
                className="older text-button"
                disabled={chat.paging}
                onClick={() => void loadOlder()}
              >
                加载更早的对话
              </button>
            )}
            <div id="messages">
              {chat.loading ? (
                <Skeletons />
              ) : (
                chat.messages.map((message) => (
                  <Message
                    key={message.id}
                    message={message}
                    onError={setCopyError}
                  />
                ))
              )}
            </div>
            {chat.live && chat.run && (
              <motion.div
                id="live-message"
                initial={reduced ? false : { opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={reveal}
              >
                <article className="message assistant">
                  <span className="message-avatar" aria-hidden="true">
                    a
                  </span>
                  <div className="message-main">
                    <div className="message-label">Agent100</div>
                    <Reasoning text={chat.live.reasoning} />
                    <LiveContent content={chat.live.content} />
                    <p
                      className={`live-status${["queued", "running"].includes(chat.run.status) && !chat.canReconnect ? " working" : ""}`}
                      role="status"
                    >
                      <span className="status-dot" aria-hidden="true" />
                      {chat.run.cancel_requested
                        ? "正在停止…"
                        : chat.canReconnect
                          ? "执行已中断"
                          : chat.live.status ||
                            (chat.run.status === "failed"
                              ? `执行失败：${chat.run.error || "请稍后重试"}`
                              : statusText[chat.run.status])}
                    </p>
                    {!!chat.run.interrupts.length && (
                      <InterruptForm
                        key={`${chat.run.run_id}:${chat.run.status}`}
                        run={chat.run}
                        busy={busy}
                        onSubmit={chat.resume}
                      />
                    )}
                    {chat.canReconnect ? (
                      <button
                        className="primary-button"
                        disabled={busy}
                        onClick={() => void chat.reconnectRun()}
                      >
                        从断点重连
                      </button>
                    ) : (
                      chat.run.status === "failed" && (
                        <button
                          className="primary-button"
                          disabled={busy}
                          onClick={() => void chat.resume({})}
                        >
                          重试本轮
                        </button>
                      )
                    )}
                    {!chat.canReconnect &&
                      chat.run.status === "running" &&
                      !chat.run.cancel_requested && (
                        <button
                          className="text-button"
                          disabled={busy || chat.checkingRecovery}
                          onClick={() => void chat.checkRecovery(true)}
                        >
                          {chat.checkingRecovery ? "检查中…" : "检查断点"}
                        </button>
                      )}
                  </div>
                </article>
              </motion.div>
            )}
          </motion.section>
        </div>
        <Composer
          centered={empty}
          value={draft}
          onChange={setDraft}
          onSend={() => void send()}
          onCancel={() => void chat.cancel()}
          disabled={disabled}
          cancelVisible={
            !!chat.run && !["succeeded", "cancelled"].includes(chat.run.status)
          }
          cancelDisabled={busy || !!chat.run?.cancel_requested}
          status={status}
          error={chat.error || copyError}
          onDismiss={() => {
            chat.dismissError();
            setCopyError("");
          }}
        />
      </main>
      <SettingsDialog
        open={settings}
        busy={busy}
        onOpenChange={setSettings}
        initialTab={settingsTab}
        settings={configuration}
        onSave={async (token) => {
          await chat.reconnect(token);
          return configuration.reload();
        }}
      />
      <DeleteDialog
        open={deleteOpen}
        session={deleteTarget}
        busy={busy}
        error={deleteError ? chat.sessionError || deleteError : ""}
        onOpenChange={setDeleteOpen}
        onDelete={async () => {
          if (deleteTarget) await deleteSession(deleteTarget);
        }}
      />
    </div>
  );
}
