import styles from "./MessageBubble.module.css";

export type MessageRole = "user" | "assistant";

export type ChatMessage = {
  id: string;
  role: MessageRole;
  text: string;
  ts: number;
  latencyMs?: number;
  data?: Record<string, unknown> | null;
};

// ── Line classifier ────────────────────────────────────────────────────────────

type LineKind = "sectionHeader" | "flagHeader" | "listItem" | "flag" | "separator" | "plain";
type FlagType = "success" | "warning" | "error";

interface ParsedLine {
  kind: LineKind;
  text: string;
  flagType?: FlagType;
  icon?: string;
  badges?: string[];
  // Structured drug fields (present when kind === "listItem" and drug pattern matched)
  drugName?: string;
  brands?: string;
  dosePart?: string;
  unitsPart?: string;
}

function parseBadges(text: string): { clean: string; badges: string[] } {
  const badges: string[] = [];
  const clean = text.replace(/\[([^\]]+)\]/g, (_, b) => { badges.push(b); return ""; }).trim();
  return { clean, badges };
}

function parseLine(line: string): ParsedLine {
  const t = line.trim();

  // Horizontal separator
  if (/^-{3,}$/.test(t)) return { kind: "separator", text: t };

  // Flag header: ✔/⚠/❌ followed by text ending with ":"
  const flagStart = t.startsWith("✔") || t.startsWith("⚠") || t.startsWith("❌");
  if (flagStart) {
    const icon = t[0];
    const ft: FlagType = icon === "✔" ? "success" : icon === "⚠" ? "warning" : "error";
    const rest = t.slice(1).trim();
    if (rest.endsWith(":")) {
      return { kind: "flagHeader", text: rest.slice(0, -1).trim(), icon, flagType: ft };
    }
    const { clean, badges } = parseBadges(rest);
    return { kind: "flag", text: clean, icon, flagType: ft, badges };
  }

  // Section header: ends with ":" but not a list item
  if (t.endsWith(":") && !t.startsWith("*") && !t.startsWith("-")) {
    return { kind: "sectionHeader", text: t.slice(0, -1).trim() };
  }

  // List item: starts with "* " or "- "
  if (t.startsWith("* ") || t.startsWith("- ")) {
    const content = t.slice(2).trim();
    const { clean, badges } = parseBadges(content);

    // Drug card pattern: "Name (brands) — doses — N units"
    const parts = clean.split(" — ");
    if (parts.length >= 2) {
      const brandsMatch = parts[0].trim().match(/^(.+?)\s+\(([^)]+)\)\s*$/);
      if (brandsMatch) {
        return {
          kind: "listItem",
          text: clean,
          badges,
          drugName: brandsMatch[1].trim(),
          brands: brandsMatch[2].trim(),
          dosePart: parts[1]?.trim(),
          unitsPart: parts[2]?.trim(),
        };
      }
    }

    return { kind: "listItem", text: clean, badges };
  }

  return { kind: "plain", text: t };
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function Badge({ text }: { text: string }) {
  const u = text.toUpperCase();
  const cls = [
    styles.badge,
    u === "SPECIALTY" ? styles.badgeSpecialty
      : u === "CONTROLLED" ? styles.badgeControlled
      : u === "URGENT" ? styles.badgeUrgent
      : styles.badgeInfo,
  ].join(" ");
  return <span className={cls}>{text}</span>;
}

function Badges({ list }: { list?: string[] }) {
  if (!list || list.length === 0) return null;
  return <>{list.map((b, i) => <Badge key={i} text={b} />)}</>;
}

/** Inline bold: **text** → <strong>text</strong> */
function InlineText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  if (parts.length === 1) return <>{text}</>;
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("**") && p.endsWith("**")
          ? <strong key={i}>{p.slice(2, -2)}</strong>
          : <span key={i}>{p}</span>
      )}
    </>
  );
}

function DrugItem({ parsed }: { parsed: ParsedLine }) {
  if (parsed.drugName && parsed.brands) {
    return (
      <div className={styles.drugCard}>
        <div className={styles.drugMain}>
          <span className={styles.drugName}>{parsed.drugName}</span>
          <span className={styles.drugBrands}>({parsed.brands})</span>
        </div>
        <div className={styles.drugMeta}>
          {parsed.dosePart && <span className={styles.drugDoses}>{parsed.dosePart}</span>}
          {parsed.unitsPart && <span className={styles.drugUnits}>{parsed.unitsPart}</span>}
          <Badges list={parsed.badges} />
        </div>
      </div>
    );
  }
  return (
    <div className={styles.listItem}>
      <span className={styles.listBullet} />
      <span className={styles.listText}>
        <InlineText text={parsed.text} />
        {parsed.badges && parsed.badges.length > 0 && <> <Badges list={parsed.badges} /></>}
      </span>
    </div>
  );
}

function flagHeaderCls(ft?: FlagType) {
  if (ft === "success") return styles.successHeader;
  if (ft === "warning") return styles.warningHeader;
  if (ft === "error") return styles.errorHeader;
  return "";
}

function flagLineCls(ft?: FlagType) {
  if (ft === "success") return styles.success;
  if (ft === "warning") return styles.warning;
  if (ft === "error") return styles.error;
  return "";
}

function flagDotCls(ft?: FlagType) {
  if (ft === "success") return styles.dot_success;
  if (ft === "warning") return styles.dot_warning;
  if (ft === "error") return styles.dot_error;
  return "";
}

// ── Main component ─────────────────────────────────────────────────────────────

function asStringMap(v: unknown): Record<string, string> | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const out: Record<string, string> = {};
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    if (typeof val === "string") out[k] = val;
  }
  return Object.keys(out).length > 0 ? out : null;
}

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const parsed = message.text
    .split("\n")
    .filter((l) => l.trim().length > 0)
    .map(parseLine);

  const pdfUrl = typeof message.data?.invoice_pdf_url === "string"
    ? (message.data.invoice_pdf_url as string) : null;
  const htmlUrl = typeof message.data?.invoice_html_url === "string"
    ? (message.data.invoice_html_url as string) : null;

  const pdfUrls = asStringMap(message.data?.invoice_pdf_urls);
  const htmlUrls = asStringMap(message.data?.invoice_html_urls);
  const invoicePatients = Array.from(new Set([
    ...Object.keys(pdfUrls || {}),
    ...Object.keys(htmlUrls || {}),
  ])).sort();

  const isAssistant = message.role === "assistant";

  return (
    <div className={`${styles.row} ${isAssistant ? styles.assistant : styles.user}`}>
      {isAssistant && (
        <div className={styles.avatar} aria-hidden="true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="8" r="4" /><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7" />
          </svg>
        </div>
      )}

      <div className={styles.bubble}>
        <div className={styles.body}>
          {parsed.length === 0 && <span className={styles.plainLine}>&nbsp;</span>}

          {parsed.map((p, i) => {
            switch (p.kind) {
              case "separator":
                return <hr key={i} className={styles.separator} />;

              case "sectionHeader":
                return (
                  <div key={i} className={styles.sectionHeader}>
                    <span className={styles.sectionHeaderIcon}>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                        <path d="M4 6h16M4 12h16M4 18h10" />
                      </svg>
                    </span>
                    {p.text}
                  </div>
                );

              case "flagHeader":
                return (
                  <div key={i} className={`${styles.flagHeader} ${flagHeaderCls(p.flagType)}`}>
                    <span className={styles.flagHeaderIcon}>{p.icon}</span>
                    <span>{p.text}</span>
                  </div>
                );

              case "flag":
                return (
                  <div key={i} className={`${styles.flagLine} ${flagLineCls(p.flagType)}`}>
                    <span className={`${styles.flagDot} ${flagDotCls(p.flagType)}`}>
                      {p.icon}
                    </span>
                    <span className={styles.flagText}>
                      <InlineText text={p.text} />
                      {p.badges && p.badges.length > 0 && <> <Badges list={p.badges} /></>}
                    </span>
                  </div>
                );

              case "listItem":
                return <DrugItem key={i} parsed={p} />;

              default:
                return (
                  <div key={i} className={styles.plainLine}>
                    <InlineText text={p.text} />
                  </div>
                );
            }
          })}
        </div>

        {/* Meta bar */}
        {isAssistant && typeof message.latencyMs === "number" && (
          <div className={styles.meta}>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
            </svg>
            {Math.round(message.latencyMs).toLocaleString()}ms
          </div>
        )}

        {/* Invoice actions */}
        {isAssistant && invoicePatients.length > 0 && (
          <div className={styles.actionsMulti}>
            <div className={styles.invoiceList}>
              {invoicePatients.map((pid) => {
                const pPdf = pdfUrls?.[pid] || null;
                const pHtml = htmlUrls?.[pid] || null;
                return (
                  <div key={pid} className={styles.invoiceRow}>
                    <div className={styles.invoiceLabel}>{pid}</div>
                    <div className={styles.invoiceBtns}>
                      {pPdf && (
                        <a className={styles.actionBtn} href={pPdf} target="_blank" rel="noreferrer">
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
                          </svg>
                          PDF
                        </a>
                      )}
                      {pHtml && (
                        <a className={styles.actionBtnSecondary} href={pHtml} target="_blank" rel="noreferrer">
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" />
                          </svg>
                          Preview
                        </a>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {isAssistant && invoicePatients.length === 0 && (pdfUrl || htmlUrl) && (
          <div className={styles.actions}>
            {pdfUrl && (
              <a className={styles.actionBtn} href={pdfUrl} target="_blank" rel="noreferrer">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                  <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                Download PDF
              </a>
            )}
            {htmlUrl && (
              <a className={styles.actionBtnSecondary} href={htmlUrl} target="_blank" rel="noreferrer">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" />
                </svg>
                Preview Invoice
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
