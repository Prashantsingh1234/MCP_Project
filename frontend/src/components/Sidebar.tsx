import { IconChat, IconDashboard, IconLogs, IconMetrics } from "./icons";
import styles from "./Sidebar.module.css";
import type { SessionMeta } from "../api";

export type NavKey = "dashboard" | "assistant" | "metrics" | "logs";

type Props = {
  active: NavKey;
  onSelect: (key: NavKey) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  sessions: SessionMeta[];
  activeSessionId: string | null;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onNewChat: () => void;
};

const navItems: { key: NavKey; label: string; icon: JSX.Element }[] = [
  { key: "dashboard", label: "Dashboard", icon: <IconDashboard /> },
  { key: "assistant", label: "Discharge Assistant", icon: <IconChat /> },
  { key: "metrics", label: "Metrics", icon: <IconMetrics /> },
  { key: "logs", label: "Logs", icon: <IconLogs /> }
];

function formatRelativeTime(isoStr: string): string {
  try {
    const diff = Date.now() - new Date(isoStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch {
    return "";
  }
}

export default function Sidebar({
  active,
  onSelect,
  collapsed,
  onToggleCollapsed,
  sessions,
  activeSessionId,
  onSelectSession,
  onDeleteSession,
  onNewChat,
}: Props) {
  const recentSessions = sessions.slice(0, 5);

  return (
    <aside className={`${styles.sidebar} ${collapsed ? styles.collapsed : ""}`}>
      {/* Brand row */}
      <div className={styles.brandRow}>
        <div className={styles.logo}>M</div>
        {!collapsed && (
          <div className={styles.brandText}>
            <div className={styles.brandTitle}>MCPDischarge</div>
            <div className={styles.brandSub}>Hospital discharge</div>
          </div>
        )}
        <button className={styles.collapseBtn} onClick={onToggleCollapsed} aria-label="Toggle sidebar">
          <span className={styles.collapseIcon}>{collapsed ? "›" : "‹"}</span>
        </button>
      </div>

      {/* Nav items */}
      <nav className={styles.nav}>
        {navItems.map((it) => (
          <button
            key={it.key}
            className={`${styles.navItem} ${active === it.key ? styles.active : ""}`}
            onClick={() => onSelect(it.key)}
            title={collapsed ? it.label : undefined}
          >
            <span className={styles.icon}>{it.icon}</span>
            {!collapsed && <span className={styles.label}>{it.label}</span>}
          </button>
        ))}
      </nav>

      {/* Recent chats — only visible when expanded */}
      {!collapsed && (
        <div className={styles.recentsSection}>
          <div className={styles.recentsHeader}>
            <span className={styles.recentsTitle}>Recent chats</span>
            <button className={styles.newChatBtn} onClick={onNewChat} title="New chat">
              +
            </button>
          </div>

          {recentSessions.length === 0 ? (
            <div className={styles.recentsEmpty}>No chats yet</div>
          ) : (
            <div className={styles.recentsList}>
              {recentSessions.map((s) => (
                <div
                  key={s.id}
                  className={`${styles.recentItem} ${activeSessionId === s.id ? styles.recentActive : ""}`}
                >
                  <button
                    className={styles.recentTitle}
                    onClick={() => onSelectSession(s.id)}
                    title={s.title}
                  >
                    <span className={styles.recentTitleText}>{s.title}</span>
                    <span className={styles.recentTime}>{formatRelativeTime(s.last_used)}</span>
                  </button>
                  <button
                    className={styles.recentDeleteBtn}
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteSession(s.id);
                    }}
                    title="Delete chat"
                    aria-label="Delete chat"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Footer */}
      {!collapsed && (
        <div className={styles.footer}>
          <div className={styles.footerTitle}>Environment</div>
          <div className={styles.footerBody}>Local (SSE)</div>
        </div>
      )}
    </aside>
  );
}
