import { StatusBadge } from "@/components/M";
import type { Health, Quarantine, Status } from "@/lib/types";

export function DataQualityView({ health, quarantine }: { health: Health; quarantine: Quarantine }) {
  return (
    <>
      <h1>Data quality and sources</h1>
      <p>{health.dataset} dataset.</p>
      {health.blocks_monthly_review ? (
        <p className="banner bad" role="alert">Monthly review is blocked until ledger differences and quarantined items are resolved.</p>
      ) : (
        <p className="banner">Nothing blocks the monthly review.</p>
      )}
      <dl className="kv">
        <dt>Ledger differences</dt><dd>{health.ledger_differences}</dd>
        <dt>Quarantined items waiting</dt><dd>{health.pending_quarantine}</dd>
      </dl>
      <h2>Sources</h2>
      <div className="scroll"><table>
        <caption>Source health</caption>
        <thead><tr><th scope="col">Source</th><th scope="col">State</th><th scope="col">Freshness</th><th scope="col">Latest data</th><th scope="col">Last success</th><th scope="col">Quota today</th></tr></thead>
        <tbody>{health.sources.map((s) => (
          <tr key={s.id}><th scope="row">{s.name}</th><td>{s.state.replace(/_/g, " ")}</td>
            <td>{s.freshness ? <StatusBadge status={s.freshness as Status} /> : "—"}{s.freshness_note ? ` ${s.freshness_note}` : ""}</td>
            <td>{s.latest_valid_at ?? "—"}</td><td>{s.last_success_at ?? "—"}</td>
            <td>{s.calls_today}{s.daily_quota !== null ? ` of ${s.daily_quota}` : ""}</td></tr>))}
        </tbody>
      </table></div>
      <h2>Quarantine queue</h2>
      {quarantine.length === 0 ? <p>The queue is empty.</p> : (
        <ul>{quarantine.map((q, i) => (<li key={i}>{JSON.stringify(q)}</li>))}</ul>)}
      <p>Accepting, correcting or rejecting a quarantined item needs a reason and stays in the CLI.</p>
    </>
  );
}
