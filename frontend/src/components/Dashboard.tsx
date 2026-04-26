import styles from "./Dashboard.module.css";
import type { SystemStatus } from "./Header";

type Props = {
  status: SystemStatus | null;
  onQuickAsk: (q: string) => void;
};

const quick = [
  "Discharge patient PAT-001 and generate invoice",
  "Discharge PAT-001 and replace unavailable drugs",
  "Check if Humira is available",
  "How many tool calls were made for PAT-001?"
];

function StatusTile({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className={styles.tile}>
      <div className={styles.tileLabel}>{label}</div>
      <div className={`${styles.tileValue} ${ok ? styles.ok : styles.bad}`}>{ok ? "Connected" : "Offline"}</div>
    </div>
  );
}

export default function Dashboard({ status, onQuickAsk }: Props) {
  return (
    <div className={styles.wrap}>
      <div className={styles.hero}>
        <div>
          <div className={styles.heroTitle}>Operational overview</div>
          <div className={styles.heroSub}>
            Verify connections and run common discharge workflows with PHI-safe defaults.
          </div>
        </div>
      </div>

      <div className={styles.grid}>
        <div className={styles.card}>
          <div className={styles.cardTitle}>System status</div>
          <div className={styles.statusGrid}>
            <StatusTile label="EHR" ok={!!status?.ehr.connected} />
            <StatusTile label="Pharmacy" ok={!!status?.pharmacy.connected} />
            <StatusTile label="Billing" ok={!!status?.billing.connected} />
          </div>
          <div className={styles.note}>
            If any service is offline, start the servers: <code>python src/servers/mcp_servers.py --all</code>
          </div>
        </div>

        <div className={styles.card}>
          <div className={styles.cardTitle}>Quick actions</div>
          <div className={styles.actions}>
            {quick.map((q) => (
              <button key={q} className={styles.actionBtn} onClick={() => onQuickAsk(q)}>
                {q}
              </button>
            ))}
          </div>
          <div className={styles.note}>
            Actions open in <strong>Discharge Assistant</strong>.
          </div>
        </div>
      </div>
    </div>
  );
}
