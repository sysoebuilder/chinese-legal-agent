import { useEffect, useRef, useState } from "react";
import { ApiError, json, readEvents, request, retryDelay } from "./api";
import type {
  HistoryPage,
  LiveMessage,
  Message,
  Responses,
  Recovery,
  Run,
  Session,
  SessionPage,
  StreamEvent,
} from "./types";

export const terminal = new Set(["succeeded", "failed", "cancelled"]);
export const statusText = {
  queued: "正在等待执行…",
  running: "正在思考与生成…",
  waiting_approval: "等待你的确认",
  waiting_input: "需要你补充信息",
  succeeded: "回答完成",
  failed: "本轮执行失败，可重试或停止",
  cancelled: "已停止，可继续发送消息",
};
interface ChatState {
  thread: string | null;
  sessions: Session[];
  offset: number | null;
  messages: Message[];
  before: string | null;
  run: Run | null;
  live: LiveMessage | null;
  sending: boolean;
  deletingThread: string | null;
  loading: boolean;
  paging: boolean;
  sessionsLoading: boolean;
  sessionError: string;
  error: string;
  needsAuth: boolean;
  canReconnect: boolean;
  checkingRecovery: boolean;
}
const initial: ChatState = {
  thread: null,
  sessions: [],
  offset: null,
  messages: [],
  before: null,
  run: null,
  live: null,
  sending: false,
  deletingThread: null,
  loading: false,
  paging: false,
  sessionsLoading: false,
  sessionError: "",
  error: "",
  needsAuth: false,
  canReconnect: false,
  checkingRecovery: false,
};
function savedRuns(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem("agent100.runs") || "{}");
  } catch {
    return {};
  }
}
function rememberRun(thread: string, id: string | null) {
  const runs = savedRuns();
  if (id) runs[thread] = id;
  else delete runs[thread];
  localStorage.setItem("agent100.runs", JSON.stringify(runs));
}
function emptyLive(): LiveMessage {
  return { content: "", reasoning: "", status: "" };
}

export function useChat() {
  const [state, setState] = useState<ChatState>(initial);
  // 异步任务读取即时快照；epoch 阻止上一会话的结果覆盖当前界面。
  const current = useRef(state);
  const epoch = useRef(0);
  const mounted = useRef(false);
  const sessionRequest = useRef(0);
  const stream = useRef<AbortController | null>(null);
  const historyRequest = useRef<AbortController | null>(null);
  const recoveryRequest = useRef<AbortController | null>(null);
  const reasonings = useRef(new Map<string, string>());

  function update(patch: Partial<ChatState>) {
    if (!mounted.current) return;
    current.current = { ...current.current, ...patch };
    setState(current.current);
  }
  function active(version: number) {
    return mounted.current && epoch.current === version;
  }
  function busy() {
    return current.current.sending || !!current.current.deletingThread;
  }
  function report(error: unknown) {
    if (error instanceof Error && error.name === "AbortError") return;
    const needsAuth = error instanceof ApiError && error.status === 401;
    update({
      error: needsAuth
        ? "需要 API 访问令牌，请在连接设置中填写。"
        : error instanceof Error
          ? error.message
          : "连接暂时不可用。",
      needsAuth,
    });
  }

  async function loadSessions(append = false) {
    if (current.current.deletingThread) return false;
    const requestId = ++sessionRequest.current;
    update({ sessionsLoading: true, sessionError: "" });
    try {
      const offset = append ? current.current.offset || 0 : 0;
      const page = await json<SessionPage>(
        `/v1/sessions?limit=30&offset=${offset}`,
      );
      if (!mounted.current || requestId !== sessionRequest.current)
        return false;
      update({
        sessions: append
          ? [...current.current.sessions, ...page.sessions]
          : page.sessions,
        offset: page.has_more ? page.next_offset : null,
      });
      return true;
    } catch (error) {
      if (mounted.current && requestId === sessionRequest.current) {
        update({
          sessionError: error instanceof Error ? error.message : "历史加载失败",
        });
        report(error);
      }
      return false;
    } finally {
      if (requestId === sessionRequest.current)
        update({ sessionsLoading: false });
    }
  }

  async function loadHistory(
    thread: string,
    version: number,
    older = false,
    signal?: AbortSignal,
  ) {
    const before = older ? current.current.before : null;
    const page = await json<HistoryPage>(
      `/v1/sessions/${thread}/messages?limit=10${before ? `&before=${encodeURIComponent(before)}` : ""}`,
      undefined,
      signal,
    );
    if (!active(version)) return false;
    const ids = new Set(page.messages.map((message) => message.id));
    const messages = older
      ? [
          ...page.messages,
          ...current.current.messages.filter((message) => !ids.has(message.id)),
        ]
      : page.messages;
    update({
      messages: messages.map((message) => ({
        ...message,
        reasoning: reasonings.current.get(message.id),
      })),
      before: page.has_more ? page.next_before : null,
    });
    return true;
  }

  function resetView(thread: string | null) {
    stream.current?.abort();
    historyRequest.current?.abort();
    recoveryRequest.current?.abort();
    recoveryRequest.current = null;
    epoch.current++;
    reasonings.current.clear();
    if (thread) localStorage.setItem("agent100.thread", thread);
    else localStorage.removeItem("agent100.thread");
    update({
      thread,
      messages: [],
      before: null,
      run: null,
      live: null,
      error: "",
      loading: false,
      paging: false,
      canReconnect: false,
      checkingRecovery: false,
    });
    return epoch.current;
  }

  async function selectSession(thread: string) {
    if (busy() || thread === current.current.thread) return;
    const version = resetView(thread);
    const controller = new AbortController();
    historyRequest.current = controller;
    update({ loading: true });
    try {
      await loadHistory(thread, version, false, controller.signal);
      if (!active(version)) return;
      const recovery = await json<Recovery>(
        `/v1/sessions/${thread}/recovery`,
        undefined,
        controller.signal,
      );
      if (!active(version)) return;
      const run = recovery.run;
      update({ run, canReconnect: recovery.can_reconnect });
      if (!run) {
        rememberRun(thread, null);
        return;
      }
      update({ live: emptyLive() });
      if (!terminal.has(run.status)) void monitor(run, version);
    } catch (error) {
      if (active(version)) report(error);
    } finally {
      if (active(version)) update({ loading: false });
    }
  }

  async function loadOlder() {
    const { thread, paging, before } = current.current;
    if (!thread || paging || !before || current.current.deletingThread) return;
    const version = epoch.current;
    update({ paging: true });
    try {
      await loadHistory(thread, version, true);
    } catch (error) {
      if (active(version)) report(error);
    } finally {
      if (active(version)) update({ paging: false });
    }
  }

  async function monitor(initialRun: Run, version: number) {
    stream.current?.abort();
    const controller = new AbortController();
    stream.current = controller;
    let run = initialRun,
      failures = 0,
      recoveryChecked = false,
      generation = initialRun.generation;
    const answers = new Map<string, string>(),
      thoughts = new Map<string, string>();
    const valid = () => active(version) && !controller.signal.aborted;

    function onEvent(event: StreamEvent) {
      if (!valid()) return;
      const { data } = event;
      let live = current.current.live || emptyLive();
      if (event.event === "run.state") {
        const snapshot = data as Run;
        if (snapshot.run_id !== run.run_id || snapshot.revision <= run.revision)
          return;
        if (generation !== snapshot.generation) {
          generation = snapshot.generation;
          answers.clear();
          thoughts.clear();
          live = emptyLive();
        }
        run = snapshot;
        if (run.status === "succeeded")
          live.content = run.answer || "本轮对话已完成。";
        if (snapshot.revision > initialRun.revision) recoveryChecked = false;
      } else {
        if (data.generation !== generation || run.status !== "running") return;
        recoveryChecked = false;
        if (
          event.event === "answer.delta" ||
          event.event === "reasoning.delta"
        ) {
          const pieces = event.event === "answer.delta" ? answers : thoughts;
          const key = data.message_id || data.node || "message";
          pieces.set(key, (pieces.get(key) || "") + (data.text || ""));
          live = {
            ...live,
            reasoning: [...thoughts.values()].join("\n\n"),
            content:
              event.event === "answer.delta" ? pieces.get(key)! : live.content,
          };
        }
      }
      live = {
        ...live,
        status:
          event.event === "tool.completed"
            ? `已完成：${data.tool_name || "工具调用"}`
            : "",
      };
      update({
        run,
        live,
        ...(event.event !== "run.state" || run.revision > initialRun.revision
          ? { canReconnect: false }
          : {}),
      });
    }

    while (valid()) {
      try {
        await readEvents(run.run_id, controller.signal, onEvent);
        if (!valid()) return;
        const latest = await json<Run>(
          `/v1/runs/${run.run_id}`,
          undefined,
          controller.signal,
        );
        if (!valid()) return;
        run = latest;
        update({ run });
        failures = 0;
        if (run.status === "succeeded") {
          update({
            live: {
              ...(current.current.live || emptyLive()),
              content: run.answer || "本轮对话已完成。",
            },
          });
          await finishHistory(
            run,
            version,
            [...thoughts.values()].join("\n\n"),
            controller.signal,
          );
          if (valid()) void loadSessions();
          return;
        }
        if (run.status === "cancelled") rememberRun(run.thread_id, null);
        if (terminal.has(run.status)) return;
        if (!recoveryChecked) {
          recoveryChecked = true;
          void checkRecovery();
        }
      } catch (error) {
        if (!valid()) return;
        if (error instanceof ApiError && [401, 404].includes(error.status)) {
          report(error);
          return;
        }
        failures++;
        if (!recoveryChecked) {
          recoveryChecked = true;
          void checkRecovery();
        }
        update({
          live: {
            ...(current.current.live || emptyLive()),
            status: "连接中断，正在重新连接…",
          },
        });
      }
      await retryDelay(
        Math.min(1000 * 2 ** failures, 10000),
        controller.signal,
      );
    }
  }

  async function finishHistory(
    run: Run,
    version: number,
    reasoning: string,
    signal: AbortSignal,
  ) {
    try {
      await loadHistory(run.thread_id, version, false, signal);
      if (!active(version) || signal.aborted) return;
      const last = current.current.messages.at(-1);
      if (reasoning && last?.role === "assistant") {
        reasonings.current.set(last.id, reasoning);
        update({
          messages: current.current.messages.map((message) =>
            message.id === last.id ? { ...message, reasoning } : message,
          ),
        });
      }
      rememberRun(run.thread_id, null);
      update({ live: null });
    } catch (error) {
      if (active(version)) report(error);
    }
  }

  async function send(message: string): Promise<boolean> {
    const { thread, run, sending, loading } = current.current;
    if (
      !message.trim() ||
      sending ||
      current.current.deletingThread ||
      loading ||
      (run && !["succeeded", "cancelled"].includes(run.status))
    )
      return false;
    const version = epoch.current;
    update({ sending: true, error: "" });
    try {
      const run = await json<Run>("/v1/runs", {
        message: message.trim(),
        ...(thread ? { thread_id: thread } : {}),
      });
      if (!active(version)) return false;
      rememberRun(run.thread_id, run.run_id);
      localStorage.setItem("agent100.thread", run.thread_id);
      update({
        thread: run.thread_id,
        run,
        live: emptyLive(),
        messages: [
          ...current.current.messages,
          {
            id: `run:${run.run_id}:user`,
            role: "user",
            content: message.trim(),
          },
        ],
      });
      void monitor(run, version);
      void loadSessions();
      return true;
    } catch (error) {
      if (active(version)) report(error);
      return false;
    } finally {
      if (active(version)) update({ sending: false });
    }
  }

  async function resume(responses: Responses) {
    const run = current.current.run;
    if (!run || busy()) return;
    const version = epoch.current;
    update({ sending: true, error: "" });
    try {
      const next = await json<Run>(`/v1/runs/${run.run_id}/resume`, {
        responses,
      });
      if (!active(version)) return;
      update({ run: next, live: emptyLive(), canReconnect: false });
      void monitor(next, version);
    } catch (error) {
      if (active(version)) report(error);
    } finally {
      if (active(version)) update({ sending: false });
    }
  }

  async function reconnectRun() {
    const run = current.current.run;
    if (!run || busy() || !current.current.canReconnect)
      return;
    const version = epoch.current;
    update({ sending: true, error: "" });
    try {
      const next = await json<Run>(`/v1/runs/${run.run_id}/reconnect`, {});
      if (!active(version)) return;
      update({ run: next, live: emptyLive(), canReconnect: false });
      void monitor(next, version);
    } catch (error) {
      if (active(version)) report(error);
    } finally {
      if (active(version)) update({ sending: false });
    }
  }

  async function cancel() {
    const run = current.current.run;
    if (!run || busy()) return;
    const version = epoch.current;
    update({ sending: true, error: "" });
    try {
      const next = await json<Run>(`/v1/runs/${run.run_id}/cancel`, {});
      if (!active(version)) return;
      update({ run: next });
      if (next.status === "cancelled") rememberRun(next.thread_id, null);
      else if (
        !stream.current ||
        stream.current.signal.aborted ||
        run.status === "failed"
      )
        void monitor(next, version);
    } catch (error) {
      if (active(version)) report(error);
    } finally {
      if (active(version)) update({ sending: false });
    }
  }

  async function deleteSession(thread: string): Promise<boolean> {
    if (busy()) return false;
    update({ deletingThread: thread, sessionError: "" });
    try {
      await request(`/v1/sessions/${encodeURIComponent(thread)}`, {
        method: "DELETE",
      });
      if (!mounted.current) return false;
      // 旧列表请求不能在删除后把会话重新放回侧栏。
      sessionRequest.current++;
      rememberRun(thread, null);
      if (current.current.thread === thread) resetView(null);
      update({
        sessions: current.current.sessions.filter(
          (session) => session.thread_id !== thread,
        ),
        offset:
          current.current.offset === null
            ? null
            : Math.max(0, current.current.offset - 1),
        sessionsLoading: false,
        deletingThread: null,
      });
      void loadSessions();
      return true;
    } catch (error) {
      if (mounted.current) {
        update({
          sessionError: error instanceof Error ? error.message : "删除会话失败",
        });
        report(error);
      }
      return false;
    } finally {
      update({ deletingThread: null });
    }
  }

  function newChat() {
    if (!busy()) resetView(null);
  }
  async function reconnect(token: string) {
    if (busy()) return;
    sessionStorage.setItem("agent100.token", token.trim());
    const thread = current.current.thread;
    resetView(null);
    update({ needsAuth: false });
    await loadSessions();
    if (thread) await selectSession(thread);
  }

  async function checkRecovery(manual = false) {
    const { thread, run } = current.current;
    if (!thread || !run || busy() || recoveryRequest.current) return;
    const version = epoch.current;
    const controller = new AbortController();
    recoveryRequest.current = controller;
    update({ checkingRecovery: true });
    try {
      const recovery = await json<Recovery>(
        `/v1/sessions/${thread}/recovery`,
        undefined,
        controller.signal,
      );
      if (!active(version) || controller.signal.aborted || busy())
        return;
      const latest = current.current.run;
      if (
        recovery.run?.run_id === latest?.run_id &&
        recovery.run &&
        latest &&
        recovery.run.revision >= latest.revision
      ) {
        update({ run: recovery.run, canReconnect: recovery.can_reconnect });
        if (manual && !recovery.can_reconnect)
          update({
            live: {
              ...(current.current.live || emptyLive()),
              status: recovery.busy
                ? "Worker 仍在执行，请等待"
                : "当前没有可恢复的断点",
            },
          });
      } else if (!recovery.run) update({ canReconnect: false });
    } catch (error) {
      if (active(version) && !controller.signal.aborted) {
        update({ canReconnect: false });
        if (manual) report(error);
      }
    } finally {
      if (recoveryRequest.current === controller) {
        recoveryRequest.current = null;
        if (active(version)) update({ checkingRecovery: false });
      }
    }
  }

  useEffect(() => {
    mounted.current = true;
    void loadSessions().then((loaded) => {
      const previous = localStorage.getItem("agent100.thread");
      if (loaded && mounted.current && previous) void selectSession(previous);
    });
    return () => {
      mounted.current = false;
      epoch.current++;
      sessionRequest.current++;
      stream.current?.abort();
      historyRequest.current?.abort();
      recoveryRequest.current?.abort();
    };
  }, []);

  return {
    ...state,
    send,
    resume,
    reconnectRun,
    checkRecovery,
    cancel,
    deleteSession,
    newChat,
    selectSession,
    loadOlder,
    loadSessions,
    reconnect,
    dismissError: () => update({ error: "" }),
    dismissAuth: () => update({ needsAuth: false }),
  };
}
export type Chat = ReturnType<typeof useChat>;
