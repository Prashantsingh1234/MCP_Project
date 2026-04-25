export type ChatRequest = {
  message: string;
  role?: string;
};

export type ChatResponse = {
  answer: string;
  data?: Record<string, unknown> | null;
  latency_ms: number;
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

