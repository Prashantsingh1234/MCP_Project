import { useEffect, useMemo, useRef } from "react";
import type { ChatMessage } from "./MessageBubble";
import MessageBubble from "./MessageBubble";
import styles from "./ChatWindow.module.css";

type Props = {
  messages: ChatMessage[];
  busy: boolean;
};

export default function ChatWindow({ messages, busy }: Props) {
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, busy]);

  const empty = useMemo(() => messages.length === 0, [messages.length]);

  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <div className={styles.headerTitle}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#2563eb" strokeWidth="2.5" strokeLinecap="round">
            <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
          </svg>
          Conversation
        </div>
        <div className={styles.headerMeta}>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          </svg>
          PHI-safe · RBAC enforced
        </div>
      </div>

      <div className={styles.list} ref={listRef}>
        {empty ? (
          <div className={styles.empty}>
            <div className={styles.emptyTitle}>Start by asking about a patient discharge</div>
            <div className={styles.emptyBody}>
              Example: <code>Discharge patient PAT-001 and generate invoice</code>
            </div>
          </div>
        ) : null}

        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}

        {busy ? (
          <div className={styles.typingRow}>
            <div className={styles.typingBubble}>
              <span className={styles.dot} />
              <span className={styles.dot} />
              <span className={styles.dot} />
              <span className={styles.typingLabel}>Processing…</span>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

