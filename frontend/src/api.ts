import type { StreamEvent } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function request(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(options.headers);
  const token = sessionStorage.getItem("agent100.token");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      typeof body.detail === "string"
        ? body.detail
        : `请求失败（${response.status}）`,
      response.status,
    );
  }
  return response;
}

export async function json<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const options: RequestInit =
    body === undefined
      ? { signal }
      : { method: "POST", body: JSON.stringify(body), signal };
  return (await request(path, options)).json();
}

// 网络分块不是事件边界，等待完整 SSE 帧后才解析。
export function createEventParser(onEvent: (event: StreamEvent) => void) {
  let buffer = "";
  return (chunk: string) => {
    buffer += chunk;
    let boundary: RegExpExecArray | null;
    while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
      const frame = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary[0].length);
      let event = "message";
      const data: string[] = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") event = value;
        if (field === "data") data.push(value);
      }
      if (data.length) onEvent({ event, data: JSON.parse(data.join("\n")) });
    }
  };
}

export async function readEvents(
  runId: string,
  signal: AbortSignal,
  onEvent: (event: StreamEvent) => void,
) {
  const response = await request(`/v1/runs/${runId}/events`, {
    signal,
  });
  if (
    !response.headers.get("content-type")?.includes("text/event-stream") ||
    !response.body
  )
    throw new Error("未收到事件流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parse = createEventParser(onEvent);
  try {
    while (true) {
      const { value, done } = await reader.read();
      parse(decoder.decode(value, { stream: !done }));
      if (done) break;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function retryDelay(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done, { once: true });
  });
}
