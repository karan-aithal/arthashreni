import { Num } from "@/components/M";
import type { Risk } from "@/lib/types";

export function RiskView({ r }: { r: Risk }) {
  return (
    <>
      <h1>Risk monitor</h1>
      <p>{r.dataset} dataset, day {r.day}. A rating is not a prediction, and an index fall alone never triggers a sale.</p>
      <dl className="kv">
        <dt>Risk level</dt><dd data-testid="risk-level">{r.level.level}</dd>
        <dt>Signals available</dt><dd>{r.level.available} of {r.level.total}</dd>
        <dt>Data confidence</dt><dd>{r.confidence.level}</dd>
      </dl>
      {r.confidence.reasons.length > 0 && <ul>{r.confidence.reasons.map((x) => <li key={x}>{x}</li>)}</ul>}
      {r.level.drivers.length > 0 && (<><h2>What drives the level</h2><ul>{r.level.drivers.map((x) => <li key={x}>{x}</li>)}</ul></>)}
      {r.level.missing.length > 0 && (<><h2>Signals with no data</h2><ul>{r.level.missing.map((x) => <li key={x}>{x}</li>)}</ul></>)}
      <h2>Alerts</h2>
      {r.alerts.length === 0 ? <p>No new alerts.</p> : r.alerts.map((a) => (
        <article key={a.key}>
          <h3>{a.level}: {a.what}</h3>
          <dl className="kv">
            <dt>When</dt><dd>{a.when}</dd><dt>Classification</dt><dd>{a.classification}</dd>
            <dt>Evidence</dt><dd>{a.evidence.join(", ")}</dd>
            <dt>Possible responses</dt><dd>{a.responses.join("; ")}</dd>
            <dt>If you do nothing</dt><dd>{a.if_nothing}</dd><dt>Watch next</dt><dd>{a.watch}</dd>
          </dl>
        </article>))}
      <h2>Signals</h2>
      <div className="scroll"><table>
        <caption>Signals</caption>
        <thead><tr><th scope="col">Signal</th><th scope="col">Subject</th><th scope="col">Value</th><th scope="col">Level</th></tr></thead>
        <tbody>{r.signals.map((s) => (
          <tr key={s.id}><th scope="row">{s.id}</th><td>{s.subject}</td><td><Num m={s.value} dp={4} /></td><td>{s.level ?? "no data"}</td></tr>))}
        </tbody>
      </table></div>
      <h2>Stress scenarios (scenarios, not predictions)</h2>
      <div className="scroll"><table>
        <caption>Stress scenarios</caption>
        <thead><tr><th scope="col">Scenario</th><th scope="col">Impact (INR)</th><th scope="col">Impact (%)</th></tr></thead>
        <tbody>{r.scenarios.map((s) => (
          <tr key={s.id}><th scope="row">{s.label}<br /><small>{s.note}</small></th><td><Num m={s.total_inr} kind="inr" /></td><td><Num m={s.total_pct} kind="pct" /></td></tr>))}
        </tbody>
      </table></div>
    </>
  );
}
