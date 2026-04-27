export type ChatRequest = {
  message: string;
  role?: string;
  conversation_id?: string;
};

export type ChatResponse = {
  answer: string;
  data?: Record<string, unknown> | null;
  latency_ms: number;
  conversation_id?: string | null;
};

export async function chat(req: ChatRequest): Promise<ChatResponse> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req)
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
  }
  return (await res.json()) as ChatResponse;
}

export type StatusResponse = {
  ehr: { connected: boolean };
  pharmacy: { connected: boolean };
  billing: { connected: boolean };
  security?: { connected: boolean };
  telemetry?: { connected: boolean };
  azure_openai_configured: boolean;
};

export async function getStatus(): Promise<StatusResponse> {
  const res = await fetch("/api/status");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as StatusResponse;
}

export type MetricsResponse = Record<string, unknown>;

export async function getMetrics(): Promise<MetricsResponse> {
  const res = await fetch("/api/metrics");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as MetricsResponse;
}

export type LogsSummary = {
  total_calls?: number;
  successful_calls?: number;
  failed_calls?: number;
  success_rate_pct?: number;
  avg_duration_ms?: number;
  total_alerts?: number;
  total_rbac_violations?: number;
};

export type LogsResponse = {
  summary?: LogsSummary;
  chat?: any[];
  calls: any[];
  rbac_violations: any[];
  alerts: any[];
};

export async function getLogs(limit = 100): Promise<LogsResponse> {
  const res = await fetch(`/api/logs?limit=${encodeURIComponent(String(limit))}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as LogsResponse;
}

// ── Session types & API ────────────────────────────────────────────────────────

export type SessionMeta = {
  id: string;
  title: string;
  created_at: string;
  last_used: string;
  message_count: number;
};

export type SessionMessage = {
  role: "user" | "assistant";
  text: string;
  ts: number;
  latencyMs?: number;
  data?: Record<string, unknown> | null;
};

export type Session = SessionMeta & { messages: SessionMessage[] };

export async function getSessions(): Promise<SessionMeta[]> {
  const res = await fetch("/api/sessions");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const body = (await res.json()) as { sessions: SessionMeta[] };
  return body.sessions;
}

export async function getSession(id: string): Promise<Session> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Session;
}

export async function deleteSession(id: string): Promise<void> {
  await fetch(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}
