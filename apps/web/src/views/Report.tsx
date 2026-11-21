import { Stated } from "@/components/M";
import type { Review } from "@/lib/types";

export function ReportView({ review }: { review: Review }) {
  const r = review.recommendation;
  const e = r.envelope;
  return (
    <>
      <h1>Monthly report {r.period}</h1>
      <p className="banner">{r.label}. {review.stored ? "Stored recommendation." : "Not yet stored: this is what the engines produce now."} Read-only: approval is recorded elsewhere and nothing is executed.</p>
      <dl className="kv">
        <dt>Review date</dt><dd>{r.review_date}</dd>
        <dt>Risk level</dt><dd>{r.risk_level}</dd>
        <dt>Overall action</dt><dd data-testid="overall-action">{r.overall_action}</dd>
        <dt>Data confidence</dt><dd>{r.confidence}</dd>
        <dt>Human approval required</dt><dd>{r.human_approval_required ? "Yes" : "No"}</dd>
      </dl>
      {r.missing.length > 0 && <div className="banner" role="note"><strong>Missing for a personalised plan:</strong><ul>{r.missing.map((x) => <li key={x}>{x}</li>)}</ul></div>}
      <h2>Budget envelope</h2>
      <dl className="kv">
        <dt>Opening budget</dt><dd><Stated value={e.opening_budget_inr} asOf={r.review_date} /></dd>
        <dt>Committed SIPs</dt><dd><Stated value={e.committed_sip_inr} asOf={r.review_date} /></dd>
        <dt>New allocations</dt><dd><Stated value={e.new_allocations_inr} asOf={r.review_date} /></dd>
        <dt>Reserve transfers</dt><dd><Stated value={e.reserve_transfers_inr} asOf={r.review_date} /></dd>
        <dt>Unallocated</dt><dd><Stated value={e.unallocated_inr} asOf={r.review_date} /></dd>
      </dl>
      <h2>Lines</h2>
      {r.lines.length === 0 ? <p>No lines.</p> : r.lines.map((l, i) => (
        <article key={i}>
          <h3>{l.subject}: {l.action} <Stated value={l.amount_inr} status={l.data_freshness} asOf={r.review_date} /></h3>
          <dl className="kv">
            <dt>Reasons</dt><dd>{l.reasons.join("; ")}</dd><dt>Risks</dt><dd>{l.risks.join("; ")}</dd>
            <dt>Evidence</dt><dd>{l.evidence_ids.join(", ")}</dd><dt>Invalid if</dt><dd>{l.invalidation_conditions.join("; ")}</dd>
            <dt>Data freshness</dt><dd>{l.data_freshness}</dd><dt>Confidence</dt><dd>{l.confidence}</dd>
          </dl>
        </article>))}
      {r.warnings.length > 0 && (<><h2>Warnings</h2><ul>{r.warnings.map((w) => <li key={w}>{w}</li>)}</ul></>)}
      {r.assumptions.length > 0 && (<><h2>Assumptions</h2><ul>{r.assumptions.map((w) => <li key={w}>{w}</li>)}</ul></>)}
      <h2>Stress scenarios (scenarios, not predictions)</h2>
      <div className="scroll"><table>
        <caption>Stress scenarios</caption>
        <thead><tr><th scope="col">Scenario</th><th scope="col">Impact (INR)</th><th scope="col">Impact (%)</th></tr></thead>
        <tbody>{r.scenarios.map((s) => (
          <tr key={s.id}><th scope="row">{s.label}</th><td><Stated value={s.total_inr} status={s.status} asOf={r.review_date} /></td><td><Stated value={s.total_pct} kind="pct" status={s.status} asOf={r.review_date} /></td></tr>))}
        </tbody>
      </table></div>
      <p>Sources: {r.sources.join(", ")}</p>
    </>
  );
}
