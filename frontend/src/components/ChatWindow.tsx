import { useEffect, useMemo, useRef } from "react";
import type { ChatMessage } from "./MessageBubble";
import MessageBubble from "./MessageBubble";
import styles from "./ChatWindow.module.css";
import type { LiveEvent } from "../App";

type Props = {
  messages: ChatMessage[];
  busy: boolean;
  liveEvents?: LiveEvent[];
};

export default function ChatWindow({ messages, busy, liveEvents = [] }: Props) {
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, busy, liveEvents.length]);

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
            {liveEvents.length === 0 ? (
              /* No tool calls yet — show generic spinner */
              <div className={styles.typingBubble}>
                <span className={styles.dot} />
                <span className={styles.dot} />
                <span className={styles.dot} />
                <span className={styles.typingLabel}>Processing…</span>
              </div>
            ) : (
              /* Live tool-call feed */
              <div className={styles.liveBubble}>
                <div className={styles.liveStepList}>
                  {liveEvents.map((ev) => {
                    const isRunning = ev.status === "running";
                    const isOk      = ev.status === "ok";
                    const rowCls    = `${styles.liveStep} ${
                      isRunning ? styles.liveStepRunning
                      : isOk    ? styles.liveStepOk
                                : styles.liveStepFail
                    }`;
                    return (
                      <div key={ev.step} className={rowCls}>
                        <span className={styles.liveStepIcon}>
                          {isRunning ? <span className={styles.spinner} /> : isOk ? "✔" : "✖"}
                        </span>
                        <span className={styles.liveServer}>{ev.server}</span>
                        <span className={styles.liveArrow}>→</span>
                        <span className={styles.liveTool}>{ev.tool}</span>
                        {ev.label ? <span className={styles.liveLabel}>({ev.label})</span> : null}
                        {!isRunning && ev.duration_ms != null
                          ? <span className={styles.liveDuration}>{ev.duration_ms}ms</span>
                          : null}
                        {ev.status === "error" && ev.error
                          ? <span className={styles.liveError}>{ev.error}</span>
                          : null}
                      </div>
                    );
                  })}
                </div>
                {/* Show dots while still waiting for more steps */}
                <div className={styles.livePending}>
                  <span className={styles.dot} />
                  <span className={styles.dot} />
                  <span className={styles.dot} />
                </div>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}

