import { useState } from "react";
import styles from "./LogsPanel.module.css";

type CallRow = {
  timestamp: string;
  server: string;
  tool: string;
  role: string;
  patient_id?: string | null;
  duration_ms: number;
  success: boolean;
  error?: string | null;
};

type AlertRow = {
  timestamp: string;
  level: string;
  source: string;
  message: string;
};

type Props = {
  summary?: {
    total_calls?: number;
    successful_calls?: number;
    failed_calls?: number;
    total_alerts?: number;
  } | null;
  chatTraces?: {
    timestamp: string;
    conversation_id?: string | null;
    role?: string | null;
    patient_id?: string | null;
    latency_ms: number;
    success: boolean;
    mcp_calls?: number;
    needs_clarification?: boolean;
    clarification_type?: string | null;
    error?: string | null;
  }[];
  calls: CallRow[];
  alerts?: AlertRow[];
};

type Tab = "chat" | "calls" | "alerts";

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return <span className={`${styles.badge} ${ok ? styles.ok : styles.bad}`}>{label}</span>;
}

function levelClass(level: string) {
  const l = (level || "").toLowerCase();
  if (l === "error" || l === "critical") return styles.bad;
  if (l === "warning") return styles.warn;
  return styles.ok;
}

function sortDesc<T extends { timestamp: string }>(rows: T[]): T[] {
  return [...rows].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );
}

export default function LogsPanel({ summary = null, chatTraces = [], calls, alerts = [] }: Props) {
  const [tab, setTab] = useState<Tab>("chat");

  // Sort DESC (latest first) — backend sorts too; this is a safety fallback.
  const sortedChatTraces = sortDesc(chatTraces);
  const sortedCalls = sortDesc(calls);
  const sortedAlerts = sortDesc(alerts);

  const failedCalls = calls.filter((c) => !c.success);
  const failedChats = chatTraces.filter((c) => !c.success);

  const totalCalls = summary?.total_calls ?? calls.length;
  const totalFailed = summary?.failed_calls ?? failedCalls.length;
  const totalAlerts = summary?.total_alerts ?? alerts.length;

  return (
    <div className={styles.wrap}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>Logs</div>
          <div className={styles.subtitle}>Gateway telemetry — chat requests, tool calls, alerts</div>
        </div>
        <div className={styles.counts}>
          <span className={styles.countBadge}>{totalCalls} calls</span>
          {totalFailed > 0 && (
            <span className={`${styles.countBadge} ${styles.countBad}`}>{totalFailed} failed</span>
          )}
          {totalAlerts > 0 && (
            <span className={`${styles.countBadge} ${styles.countWarn}`}>{totalAlerts} alerts</span>
          )}
        </div>
      </div>

      <div className={styles.tabs}>
        <button
          className={`${styles.tab} ${tab === "chat" ? styles.tabActive : ""}`}
          onClick={() => setTab("chat")}
        >
          Chat ({chatTraces.length})
        </button>
        <button
          className={`${styles.tab} ${tab === "calls" ? styles.tabActive : ""}`}
          onClick={() => setTab("calls")}
        >
          Tool Calls ({totalCalls})
        </button>
        <button
          className={`${styles.tab} ${tab === "alerts" ? styles.tabActive : ""}`}
          onClick={() => setTab("alerts")}
        >
          Alerts ({totalAlerts})
        </button>
      </div>

      {tab === "chat" && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Time</th>
                <th>Status</th>
                <th>Latency</th>
                <th>Patient</th>
                <th>Role</th>
                <th>Tool Calls</th>
                <th>Type</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {sortedChatTraces.length === 0 ? (
                <tr>
                  <td colSpan={8} className={styles.empty}>
                    No chat requests yet.
                  </td>
                </tr>
              ) : null}
              {sortedChatTraces.map((c, idx) => (
                <tr key={`${c.timestamp}-${idx}`} className={!c.success ? styles.rowFail : undefined}>
                  <td className={styles.mono}>{(c.timestamp || "").slice(11, 19)}</td>
                  <td>
                    <Badge ok={c.success} label={c.success ? "OK" : "FAIL"} />
                  </td>
                  <td className={styles.mono}>{Math.round(c.latency_ms)}ms</td>
                  <td className={styles.mono}>{c.patient_id ?? "-"}</td>
                  <td className={styles.mono}>{c.role ?? "-"}</td>
                  <td className={styles.mono}>{String(c.mcp_calls ?? 0)}</td>
                  <td className={styles.mono}>
                    {c.needs_clarification ? `clarify:${c.clarification_type ?? "unknown"}` : "-"}
                  </td>
                  <td className={styles.errCell} title={c.error ?? undefined}>
                    {!c.success ? c.error ?? "-" : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {failedChats.length > 0 ? (
            <div className={styles.empty}>
              {failedChats.length} chat failure(s). Check Alerts for details.
            </div>
          ) : null}
        </div>
      )}

      {tab === "calls" && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Time</th>
                <th>Server</th>
                <th>Tool</th>
                <th>Role</th>
                <th>Patient</th>
                <th>Latency</th>
                <th>Status</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {sortedCalls.length === 0 ? (
                <tr>
                  <td colSpan={8} className={styles.empty}>
                    No tool calls yet. Run a chat query first.
                  </td>
                </tr>
              ) : null}
              {sortedCalls.map((c, idx) => (
                <tr key={`${c.timestamp}-${idx}`} className={!c.success ? styles.rowFail : undefined}>
                  <td className={styles.mono}>{c.timestamp.slice(11, 19)}</td>
                  <td className={styles.cap}>{c.server}</td>
                  <td className={styles.mono}>{c.tool}</td>
                  <td className={styles.mono}>{c.role}</td>
                  <td className={styles.mono}>{c.patient_id ?? "-"}</td>
                  <td className={styles.mono}>{Math.round(c.duration_ms)}ms</td>
                  <td>
                    <Badge ok={c.success} label={c.success ? "OK" : "FAIL"} />
                  </td>
                  <td className={styles.errCell} title={c.error ?? undefined}>
                    {!c.success ? c.error ?? "-" : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "alerts" && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Time</th>
                <th>Level</th>
                <th>Source</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {sortedAlerts.length === 0 ? (
                <tr>
                  <td colSpan={4} className={styles.empty}>
                    No alerts recorded.
                  </td>
                </tr>
              ) : null}
              {sortedAlerts.map((a, idx) => (
                <tr key={`alert-${a.timestamp}-${idx}`}>
                  <td className={styles.mono}>{(a.timestamp || "").slice(11, 19)}</td>
                  <td>
                    <span className={`${styles.badge} ${levelClass(a.level)}`}>{a.level}</span>
                  </td>
                  <td className={styles.cap}>{a.source}</td>
                  <td className={styles.msgCell}>{a.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
