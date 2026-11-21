import { Num } from "@/components/M";
import type { Portfolio } from "@/lib/types";

const DIMS: [string, string][] = [
  ["asset_class", "Asset class"], ["country", "Country"], ["currency", "Currency"], ["sector", "Sector"], ["market_cap", "Market cap"],
];

export function PortfolioView({ p }: { p: Portfolio }) {
  return (
    <>
      <h1>Portfolio</h1>
      <p>{p.dataset} dataset as of {p.as_of}. Values in INR at the {p.fx_rate_type} rate.</p>
      <h2>Holdings</h2>
      <div className="scroll"><table>
        <caption>Holdings</caption>
        <thead><tr><th scope="col">Holding</th><th scope="col">Units</th><th scope="col">Price</th><th scope="col">Value</th><th scope="col">Cost</th><th scope="col">Unrealised</th><th scope="col">Return</th></tr></thead>
        <tbody>{p.holdings.map((h) => (
          <tr key={h.position.asset.id}>
            <th scope="row">{h.position.asset.name}</th>
            <td><Num m={h.units} dp={3} /></td><td><Num m={h.price} dp={4} /></td>
            <td><Num m={h.value_inr} kind="inr" /></td><td><Num m={h.cost_inr} kind="inr" /></td>
            <td><Num m={h.unrealised_inr} kind="inr" /></td><td><Num m={h.unrealised_pct} kind="pct" /></td>
          </tr>))}
        </tbody>
      </table></div>
      <p>Total: <Num m={p.totals.value_inr} kind="inr" /> (invested <Num m={p.invested_inr} kind="inr" />)</p>
      {p.totals.unvalued.length > 0 && <p className="banner">Not valued (no usable price): {p.totals.unvalued.join(", ")}</p>}

      <h2>Returns</h2>
      <div className="scroll"><table>
        <caption>Returns, each with the measure used and why</caption>
        <thead><tr><th scope="col">Measure</th><th scope="col">Result</th><th scope="col">Why this measure</th></tr></thead>
        <tbody>{p.returns.map((r) => (
          <tr key={r.measure}><th scope="row">{r.measure}</th><td><Num m={r.result} kind="pct" /></td><td>{r.why}</td></tr>))}
        </tbody>
      </table></div>

      <h2>Allocation</h2>
      {DIMS.map(([key, label]) => (
        <div className="scroll" key={key}><table>
          <caption>By {label.toLowerCase()}</caption>
          <thead><tr><th scope="col">{label}</th><th scope="col">Value</th><th scope="col">Weight</th></tr></thead>
          <tbody>{(p.allocation[key] ?? []).map((s) => (
            <tr key={s.key}><th scope="row">{s.key}</th><td><Num m={s.value_inr} kind="inr" /></td><td><Num m={s.weight} kind="pct" /></td></tr>))}
          </tbody>
        </table></div>))}

      <h2>Concentration</h2>
      <dl className="kv">
        <dt>Top {p.concentration.top_n} weight</dt><dd><Num m={p.concentration.top_n_weight} kind="pct" /></dd>
        <dt>HHI</dt><dd><Num m={p.concentration.hhi} dp={4} /></dd>
      </dl>

      <h2>Realised, income and costs</h2>
      <dl className="kv">
        <dt>Realised proceeds</dt><dd><Num m={p.realised.proceeds_inr} kind="inr" /></dd>
        <dt>Realised gain (before tax)</dt><dd><Num m={p.realised.gain_inr} kind="inr" /></dd>
        {Object.entries(p.income).map(([k, m]) => (<><dt key={`${k}-t`}>{k.replace(/_inr$/, "").replace(/_/g, " ")}</dt><dd key={`${k}-d`}><Num m={m} kind="inr" /></dd></>))}
      </dl>

      <h2>FX attribution</h2>
      <p>INR return = local return + FX effect + interaction.</p>
      <div className="scroll"><table>
        <caption>FX attribution</caption>
        <thead><tr><th scope="col">Holding</th><th scope="col">Local</th><th scope="col">FX</th><th scope="col">Interaction</th><th scope="col">INR return</th></tr></thead>
        <tbody>{p.fx_attribution.map((f) => (
          <tr key={f.asset}><th scope="row">{f.asset}</th>
            <td><Num m={f.attribution.local_return} kind="pct" /></td><td><Num m={f.attribution.fx_return} kind="pct" /></td>
            <td><Num m={f.attribution.interaction} kind="pct" /></td><td><Num m={f.attribution.inr_return} kind="pct" /></td></tr>))}
        </tbody>
      </table></div>

      <h2>Volatility and drawdown</h2>
      <div className="scroll"><table>
        <caption>Volatility and drawdown (trailing year)</caption>
        <thead><tr><th scope="col">Holding</th><th scope="col">Volatility</th><th scope="col">Max drawdown</th><th scope="col">Current drawdown</th></tr></thead>
        <tbody>{p.stats.map((s) => (
          <tr key={s.asset.id}><th scope="row">{s.asset.name}</th><td><Num m={s.volatility} kind="pct" /></td>
            <td><Num m={s.drawdown.depth} kind="pct" /></td><td><Num m={s.drawdown.current_depth} kind="pct" /></td></tr>))}
        </tbody>
      </table></div>

      <h2>Look-through exposure</h2>
      {Object.keys(p.look_through.exposure).length === 0 ? <p>No look-through data.</p> : (
        <ul>{Object.entries(p.look_through.exposure).map(([k, m]) => (<li key={k}>{k}: <Num m={m} kind="pct" /></li>))}</ul>)}
      {p.look_through.without_data.length > 0 && <p>No holdings data for: {p.look_through.without_data.join(", ")}</p>}
    </>
  );
}
