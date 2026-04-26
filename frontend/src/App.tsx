import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./AppShell.module.css";
import Sidebar, { type NavKey } from "./components/Sidebar";
import Header, { type SystemStatus } from "./components/Header";
import ChatWindow from "./components/ChatWindow";
import InputBox from "./components/InputBox";
import Dashboard from "./components/Dashboard";
import MetricsPanel from "./components/MetricsPanel";
import LogsPanel from "./components/LogsPanel";
import {
  chat,
  deleteSession,
  getLogs,
  getMetrics,
  getSession,
  getSessions,
  getStatus,
} from "./api";
import type { ChatMessage } from "./components/MessageBubble";
import type { SessionMeta } from "./api";

function uid() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const STORAGE_KEYS = {
  sessions: "mcpdischarge.sessions.v1",
};

function newConvId() {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

type View = NavKey;

const WELCOME: ChatMessage = {
  id: "welcome",
  role: "assistant",
  text: "Start by asking about a patient discharge.\n\nExample:\n✔ Discharge patient PAT-001 and generate invoice",
  ts: Date.now(),
};

export default function App() {
  const [view, setView] = useState<View>("assistant");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [metrics, setMetrics] = useState<any | null>(null);
  const [logs, setLogs] = useState<any | null>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  const [conversationId, setConversationId] = useState<string>(newConvId);
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

  const refreshSessionsRef = useRef<() => void>(() => {});

const refreshSessions = useCallback(async () => {
    try {
      const s = await getSessions();
      setSessions(s);
      try {
        localStorage.setItem(STORAGE_KEYS.sessions, JSON.stringify(s));
      } catch {
        // ignore storage failures (private mode / quota)
      }
    } catch {
      // ignore — backend may not be up yet
    }
  }, []);

  // Keep ref up-to-date so other callbacks can call it
  refreshSessionsRef.current = refreshSessions;

  const refreshMetricsAndLogs = useCallback(async () => {
    try {
      const [m, l] = await Promise.all([getMetrics(), getLogs(100)]);
      setMetrics(m);
      setLogs(l);
    } catch {
      // ignore
    }
  }, []);

  // Poll system status every 2.5s
  useEffect(() => {
    let stop = false;
    async function tick() {
      try {
        const s = await getStatus();
        if (!stop) setStatus(s);
      } catch {
        if (!stop) setStatus({ ehr: { connected: false }, pharmacy: { connected: false }, billing: { connected: false } });
      }
    }
    void tick();
    const t = window.setInterval(tick, 2500);
    return () => {
      stop = true;
      window.clearInterval(t);
    };
  }, []);

  // Hydrate cached sessions immediately, then keep refreshing in background.
  useEffect(() => {
    try {
      const cached = localStorage.getItem(STORAGE_KEYS.sessions);
      if (cached) {
        const parsed = JSON.parse(cached) as SessionMeta[];
        if (Array.isArray(parsed)) setSessions(parsed);
      }
    } catch {
      // ignore
    }

    void refreshSessions();
    const t = window.setInterval(() => {
      void refreshSessionsRef.current();
    }, 4000);
    return () => window.clearInterval(t);
  }, [refreshSessions]);

  // Auto-refresh metrics/logs when switching to those views
  useEffect(() => {
    if (view === "metrics" || view === "logs") {
      void refreshMetricsAndLogs();
    }
  }, [view, refreshMetricsAndLogs]);

  // While viewing Metrics/Logs, keep polling so the UI reflects live execution.
  useEffect(() => {
    if (view !== "metrics" && view !== "logs") return;
    const t = window.setInterval(() => {
      void refreshMetricsAndLogs();
    }, 2500);
    return () => window.clearInterval(t);
  }, [view, refreshMetricsAndLogs]);

  const pageTitle = useMemo(() => {
    if (view === "dashboard") return "Dashboard";
    if (view === "assistant") return "Discharge Assistant";
    if (view === "metrics") return "Metrics";
    return "Logs";
  }, [view]);

  async function send(text: string) {
    const t = text.trim();
    if (!t) return;
    setView("assistant");
    setMobileNavOpen(false);

    const userMsg: ChatMessage = { id: uid(), role: "user", text: t, ts: Date.now() };
    setMessages((m) => [...m.filter((x) => x.id !== "welcome"), userMsg]);
    setInput("");
    setBusy(true);

    try {
      const res = await chat({ message: t, conversation_id: conversationId });
      const assistant: ChatMessage = {
        id: uid(),
        role: "assistant",
        text: res.answer,
        ts: Date.now(),
        latencyMs: res.latency_ms,
        data: (res.data ?? null) as any,
      };
      setMessages((m) => [...m, assistant]);
      setActiveSessionId(conversationId);

      // Refresh side data after each reply
      void refreshMetricsAndLogs();
      void refreshSessions();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setMessages((m) => [...m, { id: uid(), role: "assistant", text: `❌ ${msg}`, ts: Date.now() }]);
    } finally {
      setBusy(false);
    }
  }

  function handleNewChat() {
    const newId = newConvId();
    setConversationId(newId);
    setActiveSessionId(null);
    setMessages([WELCOME]);
    setInput("");
    setView("assistant");
    setMobileNavOpen(false);
  }

  async function handleSelectSession(id: string) {
    try {
      const sess = await getSession(id);
      const loaded: ChatMessage[] = sess.messages.map((m) => ({
        id: uid(),
        role: m.role,
        text: m.text,
        ts: m.ts,
        latencyMs: m.latencyMs,
        data: m.data as any,
      }));
      setConversationId(id);
      setActiveSessionId(id);
      setMessages(loaded.length > 0 ? loaded : [WELCOME]);
      setView("assistant");
      setMobileNavOpen(false);
    } catch {
      // session may have been deleted; refresh list
      void refreshSessions();
    }
  }

  async function handleDeleteSession(id: string) {
    try {
      await deleteSession(id);
    } catch {
      // ignore
    }
    setSessions((prev) => {
      const next = prev.filter((s) => s.id !== id);
      try {
        localStorage.setItem(STORAGE_KEYS.sessions, JSON.stringify(next));
      } catch {
        // ignore
      }
      return next;
    });
    if (activeSessionId === id) {
      handleNewChat();
    }
  }

  return (
    <div className={styles.shell}>
      {/* Mobile overlay */}
      <div
        className={`${styles.mobileOverlay} ${mobileNavOpen ? styles.open : ""}`}
        onClick={() => setMobileNavOpen(false)}
      />

      <div className={`${styles.sidebarWrap} ${mobileNavOpen ? styles.mobileOpen : ""}`}>
        <Sidebar
          active={view}
          onSelect={(k) => {
            setView(k);
            setMobileNavOpen(false);
          }}
          collapsed={sidebarCollapsed}
          onToggleCollapsed={() => setSidebarCollapsed((s) => !s)}
          sessions={sessions}
          activeSessionId={activeSessionId}
          onSelectSession={handleSelectSession}
          onDeleteSession={handleDeleteSession}
          onNewChat={handleNewChat}
        />
      </div>

      <div className={styles.main}>
        <Header
          title={pageTitle}
          status={status}
          userName="Discharge User"
          onOpenMobileNav={() => setMobileNavOpen(true)}
        />

        <div className={styles.content}>
          {view === "dashboard" ? (
            <Dashboard status={status} onQuickAsk={(q) => send(q)} />
          ) : null}

          {view === "assistant" ? (
            <div className={styles.assistantLayout}>
              <div className={styles.chatCol}>
                <ChatWindow messages={messages} busy={busy} />
                <InputBox
                  value={input}
                  onChange={setInput}
                  onSend={() => send(input)}
                  disabled={busy}
                />
              </div>
            </div>
          ) : null}

          {view === "metrics" ? <MetricsPanel data={metrics as any} /> : null}
          {view === "logs" ? (
            <LogsPanel
              summary={(logs?.summary || null) as any}
              chatTraces={(logs?.chat || []) as any}
              calls={(logs?.calls || []) as any}
              rbacViolations={(logs?.rbac_violations || []) as any}
              alerts={(logs?.alerts || []) as any}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
