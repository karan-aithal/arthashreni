import { fmtDate, fmtInr, fmtNum, fmtPct } from "@/lib/format";
import type { Measured, Status } from "@/lib/types";
import styles from "./M.module.scss";

export type Kind = "inr" | "pct" | "num" | "raw";

const LABEL: Record<Status, string> = {
  ok: "OK",
  stale: "Stale",
  missing: "Missing",
  conflicting: "Conflicting",
  estimated: "Estimated",
  quarantined: "Quarantined",
};

export function StatusBadge({ status }: { status: Status }) {
  // The status is always text, never colour alone.
  return (
    <span className={`${styles.badge} ${styles[status]}`} data-status={status}>
      {LABEL[status] ?? status}
    </span>
  );
}

function show(value: string, kind: Kind, dp?: number): string {
  if (kind === "inr") return fmtInr(value, dp);
  if (kind === "pct") return fmtPct(value, dp);
  if (kind === "num") return fmtNum(value, dp);
  return value;
}

/** One API number: formatted value, its quality status and its as-of date. `data-value` keeps the exact string. */
export function Num({ m, kind = "num", dp }: { m: Measured<string> | undefined; kind?: Kind; dp?: number }) {
  if (!m) return <span className={styles.m}>—</span>;
  const text = m.value === null ? "—" : show(m.value, kind, dp);
  const detail = [...m.notes, m.sources.length ? `sources: ${m.sources.join(", ")}` : ""].filter(Boolean);
  return (
    <span className={styles.m} data-measured data-value={m.value ?? ""} data-status={m.status}>
      <span className={styles.num}>{text}</span>
      <StatusBadge status={m.status} />
      <time className={styles.asof} dateTime={m.as_of}>
        as of {fmtDate(m.as_of)}
      </time>
      {detail.length > 0 && (
        <span className="visually-hidden" data-notes>
          {detail.join("; ")}
        </span>
      )}
    </span>
  );
}

/** A number the API sends bare (recommendation JSON). Shows the status it does carry, and the date it is for. */
export function Stated({ value, kind = "inr", status, asOf, dp }: { value: string; kind?: Kind; status?: string; asOf: string; dp?: number }) {
  return (
    <span className={styles.m} data-measured data-bare data-value={value} data-status={status ?? "n/a"}>
      <span className={styles.num}>{show(value, kind, dp)}</span>
      {status && <StatusBadge status={status as Status} />}
      <time className={styles.asof} dateTime={asOf}>as of {fmtDate(asOf)}</time>
    </span>
  );
}
