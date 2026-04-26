import { useEffect, useRef } from "react";
import styles from "./InputBox.module.css";

type Props = {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled: boolean;
};

export default function InputBox({ value, onChange, onSend, disabled }: Props) {
  const ref = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!disabled) ref.current?.focus();
  }, [disabled]);

  return (
    <div className={styles.wrap}>
      <div className={styles.inner}>
        <input
          ref={ref}
          className={styles.input}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Ask about patient discharge, billing, or medications…"
          disabled={disabled}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (!disabled && value.trim().length > 0) onSend();
            }
          }}
        />
        <button className={styles.send} onClick={onSend} disabled={disabled || value.trim().length === 0}>
          {disabled ? <span className={styles.spinner} /> : "Send"}
        </button>
      </div>
      <div className={styles.hint}>
        Tip: include <code>PAT-001</code> for patient questions. Examples: “Discharge patient PAT-001 and generate
        invoice”, “Check if Humira is available”.
      </div>
    </div>
  );
}

