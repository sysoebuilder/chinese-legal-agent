import { expect, test } from "vitest";
import { createEventParser } from "./api";
import type { StreamEvent } from "./types";

test("SSE handles every split, CRLF, heartbeats and multiline JSON", () => {
  const stream =
    ': ping\r\n\r\nevent: answer.delta\r\ndata: {"text":\r\ndata: "你好\\n世界"}\r\n\r\nevent: run.state\ndata: {"answer":"完成"}\n\n';
  for (let split = 0; split <= stream.length; split++) {
    const events: StreamEvent[] = [],
      parse = createEventParser((event) => events.push(event));
    parse(stream.slice(0, split));
    parse(stream.slice(split));
    expect(events).toEqual([
      { event: "answer.delta", data: { text: "你好\n世界" } },
      { event: "run.state", data: { answer: "完成" } },
    ]);
  }
});
test("UTF-8 Chinese bytes can arrive one at a time", () => {
  const events: StreamEvent[] = [],
    parse = createEventParser((event) => events.push(event)),
    decoder = new TextDecoder();
  for (const byte of new TextEncoder().encode(
    'event: reasoning.delta\ndata: {"text":"中文思考"}\n\n',
  ))
    parse(decoder.decode(Uint8Array.of(byte), { stream: true }));
  parse(decoder.decode());
  expect(events[0].data.text).toBe("中文思考");
});
