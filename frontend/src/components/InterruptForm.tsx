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
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const responses: Responses = {};
    for (const item of run.interrupts) {
      if (item.value.type === "legal_information_required") {
        const answer = values[item.id]?.trim();
        if (!answer) return;
        responses[item.id] = { answer };
      } else {
        const decisions = (item.value.action_requests || []).map(
          (_, index) => values[`${item.id}-${index}`],
        );
        if (
          decisions.some((value) => value !== "approve" && value !== "reject")
        )
          return;
        responses[item.id] = {
          decisions: decisions.map((type) => ({
            type: type as "approve" | "reject",
          })),
        };
      }
    }
    await onSubmit(responses);
  }
  return (
    <form className="interrupt-form" onSubmit={submit}>
      <h3>
        {run.status === "waiting_input" ? "补充一点信息" : "请确认接下来的操作"}
      </h3>
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
                  <label htmlFor={`interrupt-${id}`}>
                    {action.name || "工具调用"}
                  </label>
                  <pre className="action-args">
                    {JSON.stringify(action.args || {}, null, 2)}
                  </pre>
                  <select
                    id={`interrupt-${id}`}
                    required
                    disabled={busy}
                    value={values[id] || ""}
                    onChange={(event) => setValue(id, event.target.value)}
                  >
                    <option value="">选择是否允许</option>
                    <option value="approve">允许执行</option>
                    <option value="reject">拒绝执行</option>
                  </select>
                </div>
              );
            })
          )}
        </div>
      ))}
      {supported ? (
        <button className="primary-button" disabled={busy} type="submit">
          提交并继续
        </button>
      ) : (
        <p>当前中断需要在终端处理。</p>
      )}
    </form>
  );
}
