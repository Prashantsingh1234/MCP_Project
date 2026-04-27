import { useMemo } from "react";
import styles from "./MetricsPanel.module.css";

export type MetricsSummary = {
  total_calls?: number;
  successful_calls?: number;
  failed_calls?: number;
  success_rate_pct?: number;
  avg_duration_ms?: number;
  total_alerts?: number;
};

function Card({ title, value, sub }: { title: string; value: string; sub?: string }) {
  return (
    <div className={styles.card}>
      <div className={styles.cardTitle}>{title}</div>
      <div className={styles.cardValue}>{value}</div>
      {sub ? <div className={styles.cardSub}>{sub}</div> : null}
    </div>
  );
}

export default function MetricsPanel({ data }: { data: MetricsSummary | null }) {
  const calls = data?.total_calls ?? 0;
  const okCalls = data?.successful_calls ?? 0;
  const alerts = data?.total_alerts ?? 0;
  const successRate = data?.success_rate_pct ?? 0;
  const avgMs = data?.avg_duration_ms ?? 0;

  const avgLabel = useMemo(() => {
    const v = Number(avgMs) || 0;
    if (v >= 100) return `${Math.round(v)}ms`;
    if (v >= 10) return `${Math.round(v)}ms`;
    // Avoid rounding tiny but real latencies down to 0ms.
    return `${v.toFixed(1)}ms`;
  }, [avgMs]);

  const subtitle = useMemo(() => {
    if (!data) return "Load metrics from gateway";
    return "Gateway telemetry (local)";
  }, [data]);

  return (
    <div className={styles.wrap}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>Metrics</div>
          <div className={styles.subtitle}>{subtitle}</div>
        </div>
      </div>

      <div className={styles.grid}>
        <Card title="Tool calls" value={String(calls)} sub="Total MCP tool calls recorded" />
        <Card title="Successful" value={String(okCalls)} sub="Tool calls that succeeded" />
        <Card title="Success rate" value={`${Math.round(successRate)}%`} sub="Across all tool calls" />
        <Card title="Avg latency" value={avgLabel} sub="Average per tool call" />
        <Card title="Alerts" value={String(alerts)} sub="Warnings and errors raised" />
      </div>
    </div>
  );
}
