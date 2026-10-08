import { motion, useReducedMotion } from "motion/react";
import { spring } from "../motion";

export function Toggle({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  const reduced = useReducedMotion();
  return (
    <button
      type="button"
      className="setting-toggle"
      role="switch"
      aria-label={label}
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <motion.span
        initial={false}
        animate={{ x: checked ? 16 : 0 }}
        transition={reduced ? { duration: 0 } : spring}
      />
    </button>
  );
}
