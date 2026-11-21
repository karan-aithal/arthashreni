// Contract tests: each page renders exactly the values the API returned (fixtures are generated from the live
// API by tests/test_phase6_contract.py), every number carries its status and as-of, and the pages are accessible.
import { render } from "@testing-library/react";
import { axe } from "vitest-axe";
import { describe, expect, it } from "vitest";
import type { ReactElement } from "react";
import type { Health, Imports, M, Portfolio, Quarantine, Review, Risk, Transactions } from "@/lib/types";
import { OverviewView } from "@/views/Overview";
import { PortfolioView } from "@/views/Portfolio";
import { TransactionsView } from "@/views/Transactions";
import { DataQualityView } from "@/views/DataQuality";
import { RiskView } from "@/views/Risk";
import { ReportView } from "@/views/Report";
import portfolio from "@fixtures/portfolio.json";
import risk from "@fixtures/risk.json";
import review from "@fixtures/review.json";
import transactions from "@fixtures/transactions.json";
import imports from "@fixtures/imports.json";
import health from "@fixtures/sources-health.json";
import quarantine from "@fixtures/quarantine.json";

const P = portfolio as unknown as Portfolio;
const R = risk as unknown as Risk;
const V = review as unknown as Review;
const T = transactions as unknown as Transactions;
const I = imports as unknown as Imports;
const H = health as unknown as Health;
const Q = quarantine as unknown as Quarantine;

const isM = (o: unknown): o is M =>
  typeof o === "object" && o !== null && "value" in o && "status" in o && "as_of" in o && "sources" in o;

/** Every Measured anywhere inside an API response. */
function measuredIn(o: unknown, out: M[] = []): M[] {
  if (isM(o)) out.push(o);
  else if (Array.isArray(o)) o.forEach((x) => measuredIn(x, out));
  else if (typeof o === "object" && o !== null) Object.values(o).forEach((x) => measuredIn(x, out));
  return out;
}

// The Measured values each page promises to show (everything else in the response is deliberately not on the page).
const xirr = P.returns.find((r) => r.measure === "XIRR")!.result;
const required: Record<string, M[]> = {
  Overview: [P.totals.value_inr, P.totals.cost_inr, P.totals.unrealised_inr, P.totals.unrealised_pct, xirr],
  Portfolio: [
    ...P.holdings.flatMap((h) => [h.units, h.price, h.value_inr, h.cost_inr, h.unrealised_inr, h.unrealised_pct]),
    ...P.returns.map((r) => r.result),
    ...Object.values(P.allocation).flatMap((d) => d.flatMap((s) => [s.value_inr, s.weight])),
    P.concentration.hhi, P.concentration.top_n_weight, P.realised.gain_inr, P.invested_inr, ...Object.values(P.income),
    ...P.fx_attribution.flatMap((f) => [f.attribution.local_return, f.attribution.fx_return, f.attribution.interaction, f.attribution.inr_return]),
    ...P.stats.flatMap((s) => [s.volatility, s.drawdown.depth, s.drawdown.current_depth]),
    ...Object.values(P.look_through.exposure),
  ],
  "Transactions and Imports": T.items.flatMap((x) => [x.quantity, x.price, x.fees, x.net_cash_inr]),
  "Risk Monitor": [...R.signals.map((s) => s.value), ...R.scenarios.flatMap((s) => [s.total_inr, s.total_pct])],
  "Data Quality and Sources": [],
  "Monthly Report": [],
};

const pages: [string, () => ReactElement][] = [
  ["Overview", () => <OverviewView portfolio={P} risk={R} review={V} health={H} />],
  ["Portfolio", () => <PortfolioView p={P} />],
  ["Transactions and Imports", () => <TransactionsView t={T} imports={I} />],
  ["Data Quality and Sources", () => <DataQualityView health={H} quarantine={Q} />],
  ["Risk Monitor", () => <RiskView r={R} />],
  ["Monthly Report", () => <ReportView review={V} />],
];

describe.each(pages)("%s", (name, view) => {
  it("shows no number that the API did not send", () => {
    const { container } = render(view());
    const known = new Set(measuredIn(P).concat(measuredIn(R), measuredIn(T)).map((m) => m.value));
    const bare = new Set<string>(); // recommendation numbers are bare strings
    V.recommendation.lines.forEach((l) => bare.add(l.amount_inr));
    V.recommendation.scenarios.forEach((s) => (bare.add(s.total_inr), bare.add(s.total_pct)));
    Object.values(V.recommendation.envelope).forEach((v) => typeof v === "string" && bare.add(v));
    bare.add(V.recommendation.budget_inr);
    for (const el of container.querySelectorAll<HTMLElement>("[data-measured]")) {
      const v = el.dataset.value ?? "";
      expect(v === "" || known.has(v) || bare.has(v), `unexpected value ${v}`).toBe(true);
    }
  });

  it("puts status and as-of next to every number, and they match the API", () => {
    const { container } = render(view());
    const byValue = new Map<string, M[]>();
    [...measuredIn(P), ...measuredIn(R), ...measuredIn(T)].forEach((m) => m.value !== null && byValue.set(m.value, [...(byValue.get(m.value) ?? []), m]));
    for (const el of container.querySelectorAll<HTMLElement>("[data-measured]")) {
      const badge = el.querySelector("[data-status]");
      const time = el.querySelector("time");
      expect(time, "as-of missing").not.toBeNull();
      if (el.dataset.status === "n/a") continue;
      expect(time?.textContent).toMatch(/^as of \d{4}-\d{2}-\d{2}$/);
      if (el.dataset.status !== "n/a" && el.dataset.bare === undefined) {
        expect(badge?.textContent?.length, "status missing").toBeGreaterThan(0);
        const candidates = byValue.get(el.dataset.value ?? "");
        if (candidates) {
          expect(candidates.some((m) => m.status === el.dataset.status && m.as_of === time?.getAttribute("datetime"))).toBe(true);
        }
      }
    }
  });

  it("renders every Measured of its source data exactly", () => {
    const { container } = render(view());
    const shown = [...container.querySelectorAll<HTMLElement>("[data-measured]")].map((e) => `${e.dataset.value}|${e.dataset.status}`);
    const wanted = required[name].filter((m) => m.value !== null);
    if (name === "Data Quality and Sources" || name === "Monthly Report") return; // bare numbers only; covered below
    expect(wanted.length).toBeGreaterThan(0);
    for (const m of wanted) expect(shown, `${m.value} not rendered`).toContain(`${m.value}|${m.status}`);
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<main>{view()}</main>);
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("page content matches the API", () => {
  it("overview", () => {
    const { getByTestId, container } = render(<OverviewView portfolio={P} risk={R} review={V} health={H} />);
    expect(getByTestId("risk-level").textContent).toBe(R.level.level);
    expect(container.querySelector(`[data-value="${P.totals.value_inr.value}"]`)).not.toBeNull();
  });
  it("portfolio has one row per holding, valued as the API says", () => {
    const { container } = render(<PortfolioView p={P} />);
    const rows = container.querySelector("table")!.querySelectorAll("tbody tr");
    expect(rows.length).toBe(P.holdings.length);
    P.holdings.forEach((h, i) => {
      expect(rows[i].querySelector("th")?.textContent).toBe(h.position.asset.name);
      expect(rows[i].querySelector(`[data-value="${h.value_inr.value}"]`)).not.toBeNull();
    });
  });
  it("transactions list every returned row and import batch", () => {
    const { container } = render(<TransactionsView t={T} imports={I} />);
    const tables = container.querySelectorAll("table");
    expect(tables[0].querySelectorAll("tbody tr").length).toBe(T.items.length);
    expect(tables[1].querySelectorAll("tbody tr").length).toBe(I.items.length);
  });
  it("data quality shows blocking state and every source", () => {
    const { container } = render(<DataQualityView health={H} quarantine={Q} />);
    expect(container.querySelectorAll("table tbody tr").length).toBe(H.sources.length);
    expect(container.textContent).toContain(H.blocks_monthly_review ? "blocked" : "Nothing blocks");
  });
  it("stale data is labelled stale, not shown as ok", () => {
    const stale = measuredIn(P).find((m) => m.status === "stale");
    expect(stale).toBeDefined();
    const { container } = render(<PortfolioView p={P} />);
    expect(container.querySelector('[data-measured] > [data-status="stale"]')?.textContent).toBe("Stale");
  });
  it("risk monitor shows the level and labels scenarios as not predictions", () => {
    const { getByTestId, container } = render(<RiskView r={R} />);
    expect(getByTestId("risk-level").textContent).toBe(R.level.level);
    expect(container.textContent).toContain("scenarios, not predictions");
    expect(container.querySelectorAll("table")[1].querySelectorAll("tbody tr").length).toBe(R.scenarios.length);
  });
  it("report shows every recommendation number exactly as sent", () => {
    const { container } = render(<ReportView review={V} />);
    const shown = new Set([...container.querySelectorAll<HTMLElement>("[data-value]")].map((e) => e.dataset.value));
    const rec = V.recommendation;
    [...rec.lines.map((l) => l.amount_inr), ...Object.entries(rec.envelope).filter(([k]) => k.endsWith("_inr")).map(([, v]) => v),
     ...rec.scenarios.flatMap((s) => [s.total_inr, s.total_pct])].forEach((v) => expect(shown.has(v as string), String(v)).toBe(true));
  });
  it("report always says approval is needed and shows every line", () => {
    const { getByTestId, container } = render(<ReportView review={V} />);
    expect(getByTestId("overall-action").textContent).toBe(V.recommendation.overall_action);
    expect(container.textContent).toContain("Human approval required");
    expect(container.querySelectorAll("article").length).toBe(V.recommendation.lines.length);
  });
});
