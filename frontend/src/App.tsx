import { useEffect, useMemo, useRef, useState } from "react";
import { chat, type ChatResponse } from "./api";

type Role = "discharge_coordinator" | "billing_agent" | "pharmacy_agent" | "clinical_agent";

type ChatMessage =
  | { id: string; role: "user"; text: string; ts: number }
  | { id: string; role: "assistant"; text: string; ts: number; raw?: ChatResponse };

function nowId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const SUGGESTIONS = [
  "Show discharge medications for PAT-001",
  "Generate invoice for PAT-001",
  "Get billing-safe summary for PAT-006",
  "Check stock for Farxiga"
];

export default function App() {
  const [role, setRole] = useState<Role>("discharge_coordinator");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [serverOk, setServerOk] = useState<null | boolean>(null);
  const [messages, setMessages] = useState<ChatMessage[]>(() => [
    {
      id: nowId(),
      role: "assistant",
      text:
        "MCPDischarge Chat is ready.\n\nTry one of these:\n" +
        SUGGESTIONS.map((s) => `- ${s}`).join("\n"),
      ts: Date.now()
    }
  ]);

  const listRef = useRef<HTMLDivElement | null>(null);
  const canSend = input.trim().length > 0 && !busy;

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, busy]);

  useEffect(() => {
    let cancelled = false;
    fetch("/healthz")
      .then((r) => r.ok)
      .then((ok) => {
        if (!cancelled) setServerOk(ok);
      })
      .catch(() => {
        if (!cancelled) setServerOk(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function send(text: string) {
    const userText = text.trim();
    if (!userText) return;

    const userMsg: ChatMessage = { id: nowId(), role: "user", text: userText, ts: Date.now() };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setBusy(true);

    try {
      const res = await chat({ message: userText, role });
      const a: ChatMessage = {
        id: nowId(),
        role: "assistant",
        text: res.answer,
        ts: Date.now(),
        raw: res
      };
      setMessages((m) => [...m, a]);
    } catch (e) {
      const errText = e instanceof Error ? e.message : String(e);
      setMessages((m) => [
        ...m,
        { id: nowId(), role: "assistant", text: `Error: ${errText}`, ts: Date.now() }
      ]);
    } finally {
      setBusy(false);
    }
  }

  const statusLabel = useMemo(() => {
    if (serverOk === null) return "Checking gateway...";
    return serverOk ? "Gateway online" : "Gateway offline";
  }, [serverOk]);

  return (
    <div className="page">
      <header className="topbar">
        <div className="brand">
          <div className="logo">M</div>
          <div>
            <div className="title">MCPDischarge Chat</div>
            <div className={`status ${serverOk ? "ok" : serverOk === false ? "bad" : ""}`}>
              {statusLabel}
            </div>
          </div>
        </div>

        <div className="controls">
          <label className="label">
            Role
            <select
              className="select"
              value={role}
              onChange={(e) => setRole(e.target.value as Role)}
              disabled={busy}
            >
              <option value="discharge_coordinator">discharge_coordinator</option>
              <option value="billing_agent">billing_agent</option>
              <option value="pharmacy_agent">pharmacy_agent</option>
              <option value="clinical_agent">clinical_agent</option>
            </select>
          </label>
        </div>
      </header>

      <main className="layout">
        <aside className="sidebar">
          <div className="card">
            <div className="cardTitle">Quick prompts</div>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)} disabled={busy}>
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="cardTitle">Notes</div>
            <div className="cardBody">
              <div className="small">
                - Include a patient id like <code>PAT-001</code> for patient questions.
              </div>
              <div className="small">
                - Drug questions work without a patient id (e.g. “Check stock for Farxiga”).
              </div>
            </div>
          </div>
        </aside>

        <section className="chat">
          <div className="messages" ref={listRef}>
            {messages.map((m) => (
              <div key={m.id} className={`msg ${m.role}`}>
                <div className="bubble">
                  <pre className="text">{m.text}</pre>
                  {m.role === "assistant" && m.raw?.latency_ms != null ? (
                    <div className="meta">latency: {m.raw.latency_ms}ms</div>
                  ) : null}
                </div>
              </div>
            ))}
            {busy ? (
              <div className="msg assistant">
                <div className="bubble">
                  <div className="typing">
                    <span />
                    <span />
                    <span />
                  </div>
                </div>
              </div>
            ) : null}
          </div>

          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              if (canSend) void send(input);
            }}
          >
            <input
              className="input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder='Ask: "Generate invoice for PAT-001"...'
              disabled={busy}
            />
            <button className="send" type="submit" disabled={!canSend}>
              Send
            </button>
          </form>
        </section>
      </main>
    </div>
  );
}

