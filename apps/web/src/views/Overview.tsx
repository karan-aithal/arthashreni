import { Num, Stated } from "@/components/M";
import type { Health, Portfolio, Review, Risk } from "@/lib/types";

export function OverviewView({ portfolio: p, risk, review, health }: { portfolio: Portfolio; risk: Risk; review: Review; health: Health }) {
  const rec = review.recommendation;
  const xirr = p.returns.find((r) => r.measure === "XIRR");
  return (
    <>
      <h1>Overview</h1>
      <p>{p.dataset} dataset, valued as of {p.as_of}. FX rate type: {p.fx_rate_type}.</p>
      {health.blocks_monthly_review && (
        <p className="banner bad" role="alert">The monthly review is blocked: unresolved ledger differences or quarantined data.</p>
      )}
      <h2>Portfolio</h2>
      <dl className="kv">
        <dt>Market value</dt><dd><Num m={p.totals.value_inr} kind="inr" /></dd>
        <dt>Cost</dt><dd><Num m={p.totals.cost_inr} kind="inr" /></dd>
        <dt>Unrealised P&amp;L</dt><dd><Num m={p.totals.unrealised_inr} kind="inr" /></dd>
        <dt>Unrealised return</dt><dd><Num m={p.totals.unrealised_pct} kind="pct" /></dd>
        {xirr && (<><dt>XIRR (money-weighted)</dt><dd><Num m={xirr.result} kind="pct" /></dd></>)}
        <dt>Holdings</dt><dd>{p.totals.holdings}</dd>
      </dl>
      <h2>Risk</h2>
      <dl className="kv">
        <dt>Risk level</dt><dd data-testid="risk-level">{risk.level.level}</dd>
        <dt>Signals available</dt><dd>{risk.level.available} of {risk.level.total}</dd>
        <dt>Data confidence</dt><dd>{risk.confidence.level}</dd>
        <dt>New alerts</dt><dd>{risk.alerts.length}</dd>
      </dl>
      <h2>This month</h2>
      <dl className="kv">
        <dt>Recommended action</dt><dd>{rec.overall_action}</dd>
        <dt>Label</dt><dd>{rec.label}</dd>
        <dt>Budget</dt><dd><Stated value={rec.budget_inr} asOf={rec.review_date} /></dd>
        <dt>Approval</dt><dd>{rec.human_approval_required ? "Needs your approval; nothing is executed" : ""}</dd>
      </dl>
      <h2>Data quality</h2>
      <dl className="kv">
        <dt>Quarantined items</dt><dd>{health.pending_quarantine}</dd>
        <dt>Ledger differences</dt><dd>{health.ledger_differences}</dd>
      </dl>
    </>
  );
}
