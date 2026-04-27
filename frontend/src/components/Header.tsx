import styles from "./Header.module.css";

export type SystemStatus = {
  ehr: { connected: boolean };
  pharmacy: { connected: boolean };
  billing: { connected: boolean };
  security?: { connected: boolean };
  telemetry?: { connected: boolean };
};

type Props = {
  title: string;
  status: SystemStatus | null;
  userName?: string;
  onOpenMobileNav: () => void;
};

function Pill({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span className={`${styles.pill} ${ok ? styles.ok : styles.bad}`}>
      <span className={styles.dot} />
      {label}: {ok ? "Connected" : "Offline"}
    </span>
  );
}

function initials(name?: string) {
  const n = (name || "User").trim();
  const parts = n.split(/\s+/).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase()).join("");
}

export default function Header({ title, status, userName, onOpenMobileNav }: Props) {
  return (
    <header className={styles.header}>
      <div className={styles.left}>
        <button className={styles.mobileNavBtn} onClick={onOpenMobileNav} aria-label="Open navigation">
          <span className={styles.burger} />
        </button>
        <div>
          <div className={styles.title}>{title}</div>
          <div className={styles.subtitle}>Enterprise discharge coordination</div>
        </div>
      </div>

      <div className={styles.center}>
        {status ? (
          <div className={styles.statusRow}>
            <Pill label="EHR" ok={status.ehr.connected} />
            <Pill label="Pharmacy" ok={status.pharmacy.connected} />
            <Pill label="Billing" ok={status.billing.connected} />
            <Pill label="Security" ok={status.security?.connected ?? false} />
            <Pill label="Telemetry" ok={status.telemetry?.connected ?? false} />
          </div>
        ) : (
          <div className={styles.statusLoading}>Checking system status…</div>
        )}
      </div>

      <div className={styles.right}>
        <div className={styles.avatar} aria-label="User">
          {initials(userName)}
        </div>
      </div>
    </header>
  );
}

