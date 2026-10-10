export interface FieldPair {
  id: string;
  name: string;
  value: string;
}

export const fieldPairs = (values: Record<string, string> = {}): FieldPair[] =>
  Object.entries(values).map(([name, value]) => ({
    id: crypto.randomUUID(),
    name,
    value,
  }));

export function pairValues(pairs: FieldPair[]): Record<string, string> {
  const values: Record<string, string> = Object.create(null);
  for (const pair of pairs) {
    const name = pair.name.trim();
    if (!name) {
      if (pair.value) throw new Error("请填写每一行的名称，或移除空行。");
      continue;
    }
    if (Object.hasOwn(values, name)) throw new Error(`名称 ${name} 重复。`);
    values[name] = pair.value;
  }
  return values;
}

export function KeyValueFields({
  label,
  pairs,
  onChange,
  disabled,
}: {
  label: string;
  pairs: FieldPair[];
  onChange: (pairs: FieldPair[]) => void;
  disabled: boolean;
}) {
  return (
    <fieldset className="connection-map" disabled={disabled}>
      <legend>{label}</legend>
      {pairs.map((pair, index) => (
        <div className="connection-map-row" key={pair.id}>
          <input
            aria-label={`${label}名称 ${index + 1}`}
            placeholder="名称"
            value={pair.name}
            onChange={(event) =>
              onChange(
                pairs.map((row) =>
                  row.id === pair.id
                    ? { ...row, name: event.target.value }
                    : row,
                ),
              )
            }
          />
          <input
            aria-label={`${label}值 ${index + 1}`}
            placeholder="值或 ${KEY_NAME}"
            value={pair.value}
            onChange={(event) =>
              onChange(
                pairs.map((row) =>
                  row.id === pair.id
                    ? { ...row, value: event.target.value }
                    : row,
                ),
              )
            }
          />
          <button
            type="button"
            className="text-button"
            aria-label={`移除${label} ${index + 1}`}
            onClick={() => onChange(pairs.filter((row) => row.id !== pair.id))}
          >
            移除
          </button>
        </div>
      ))}
      <button
        type="button"
        className="text-button"
        onClick={() =>
          onChange([...pairs, { id: crypto.randomUUID(), name: "", value: "" }])
        }
      >
        添加{label}
      </button>
    </fieldset>
  );
}
