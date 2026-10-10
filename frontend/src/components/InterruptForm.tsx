import { useState } from "react";
import type { Responses, Run } from "../types";

export function InterruptForm({
  run,
  busy,
  onSubmit,
}: {
  run: Run;
  busy: boolean;
  onSubmit: (responses: Responses) => Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const supported = run.interrupts.every(
    (item) =>
      item.value.type === "legal_information_required" ||
      item.value.action_requests,
  );
  const setValue = (id: string, value: string) =>
    setValues((previous) => ({ ...previous, [id]: value }));
  const hasInput = run.interrupts.some(
    (item) => item.value.type === "legal_information_required",
  );
  const actionCount = run.interrupts.reduce(
    (count, item) => count + (item.value.action_requests?.length || 0),
    0,
  );
  function responsesFor(answers: Record<string, string>): Responses | null {
    const responses: Responses = {};
    for (const item of run.interrupts) {
      if (item.value.type === "legal_information_required") {
        const answer = answers[item.id]?.trim();
        if (!answer) return null;
        responses[item.id] = { answer };
      } else {
        const decisions = (item.value.action_requests || []).map(
          (_, index) => answers[`${item.id}-${index}`],
        );
        if (
          decisions.some((value) => value !== "approve" && value !== "reject")
        )
          return null;
        responses[item.id] = {
          decisions: decisions.map((type) => ({
            type: type as "approve" | "reject",
          })),
        };
      }
    }
    return responses;
  }
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const responses = responsesFor(values);
    if (!busy && supported && responses) await onSubmit(responses);
  }
  async function decide(id: string, decision: "approve" | "reject") {
    if (busy || !supported) return;
    const next = { ...values, [id]: decision };
    setValues(next);
    const responses = responsesFor(next);
    if (!hasInput && responses) await onSubmit(responses);
  }
  return (
    <form className="interrupt-form" onSubmit={submit}>
      <h3>
        {run.status === "waiting_input" ? "补充一点信息" : "请确认接下来的操作"}
      </h3>
      {!hasInput && actionCount > 1 && (
        <p className="approval-hint">逐项选择，全部选择后继续执行。</p>
      )}
      {run.interrupts.map((item) => (
        <div key={item.id}>
          {item.value.type === "legal_information_required" ? (
            <>
              <label htmlFor={`interrupt-${item.id}`}>
                {item.value.question || "请补充信息"}
              </label>
              <textarea
                id={`interrupt-${item.id}`}
                required
                disabled={busy}
                value={values[item.id] || ""}
                onChange={(event) => setValue(item.id, event.target.value)}
              />
            </>
          ) : (
            item.value.action_requests?.map((action, index) => {
              const id = `${item.id}-${index}`;
              return (
                <div key={id}>
                  <label id={`interrupt-${id}`}>
                    {action.name || "工具调用"}
                  </label>
                  <pre className="action-args">
                    {JSON.stringify(action.args || {}, null, 2)}
                  </pre>
                  <div
                    className="approval-actions"
                    role="group"
                    aria-labelledby={`interrupt-${id}`}
                  >
                    <button
                      className="approval-decision approval-agree"
                      type="button"
                      disabled={busy || !supported}
                      aria-pressed={values[id] === "approve"}
                      onClick={() => void decide(id, "approve")}
                    >
                      同意
                    </button>
                    <button
                      className="approval-decision"
                      type="button"
                      disabled={busy || !supported}
                      aria-pressed={values[id] === "reject"}
                      onClick={() => void decide(id, "reject")}
                    >
                      拒绝
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      ))}
      {supported && hasInput ? (
        <button className="primary-button" disabled={busy} type="submit">
          提交并继续
        </button>
      ) : !supported ? (
        <p>当前中断需要在终端处理。</p>
      ) : null}
    </form>
  );
}
