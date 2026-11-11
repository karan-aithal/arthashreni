"""Phase 4 gate: the DEMO portfolio's valuation, P&L, returns, FX attribution, allocation and
concentration equal the figures derived independently in tests/golden/derive_calcs.py (no arthashreni
imports there), every number carries its status, and the API and CLI show them."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from arthashreni.api.app import app as api_app
from arthashreni.api.auth import hash_password
from arthashreni.cli import app as cli_app
from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope
from arthashreni.demo import seed_demo
from arthashreni.demo_market import DEMO_TODAY, load_demo_market
from arthashreni.dq.measured import QualityStatus
from arthashreni.portfolio import PortfolioReport, build_report

pytestmark = pytest.mark.usefixtures("ledger_db")
EXPECTED = json.loads((Path(__file__).parent / "golden" / "expected_calcs.json").read_text())
D = Decimal
runner = CliRunner()
# pragma: allowlist nextline secret
PASSWORD = "correct horse battery"


@pytest.fixture(scope="module", autouse=True)
def demo_world(ledger_db: str, tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    mp = pytest.MonkeyPatch()
    mp.setenv("ARTHA_RAW_DIR", str(tmp_path_factory.mktemp("raw")))
    get_settings.cache_clear()
    seed_demo()
    load_demo_market()
    engine = create_engine(get_settings().db_owner_url.get_secret_value())
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO ref.local_user (id, username, password_hash) VALUES (1, 'demo', :h)"),
            {"h": hash_password(PASSWORD)},
        )
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ref.auth_sessions"))
        conn.execute(text("DELETE FROM ref.local_user"))
    engine.dispose()
    mp.undo()
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def report() -> PortfolioReport:
    with session_scope("demo") as db:
        return build_report(db, "demo", DEMO_TODAY)


def near(value: Decimal | None, expected: str, tol: str = "1e-9") -> None:
    assert value is not None
    assert abs(value - D(expected)) <= D(tol), f"{value} != {expected}"


def exact4(value: Decimal | None, expected: str) -> None:
    assert value is not None and value.quantize(D("0.0001")) == D(expected), f"{value} != {expected}"


def test_holdings_are_valued_from_the_right_price_and_rate(report: PortfolioReport) -> None:
    by_ref = {h.position.asset.name: h for h in report.holdings}
    names = {
        "AMFI:900001": "Demo Growth Equity Fund - Direct Growth",
        "TICKER:XNAS:DTECH": "Demo Tech Inc",
        "TICKER:XNSE:DEMOETF": "Demo India ETF",
        "TICKER:XNSE:DIPO": "Demo IPO Ltd",
        "TICKER:XNSE:DNEW": "Demo New Co",
    }
    assert {h.position.asset.name for h in report.holdings} == set(names.values())
    for ref, exp in EXPECTED["holdings"].items():
        h = by_ref[names[ref]]
        assert h.units.value == D(exp["units"]) and h.price.value is not None
        assert h.price.value.quantize(D("0.0001")) == D(exp["price"])
        assert h.price.as_of.date() == date.fromisoformat(exp["price_date"])
        exact4(h.value_inr.value, exp["value_inr"])
        exact4(h.cost_inr.value, exp["cost_inr"])
        exact4(h.unrealised_inr.value, exp["unrealised_inr"])
    assert report.fx_rate_type == EXPECTED["fx_rate_used"]["type"]
    usd = by_ref["Demo Tech Inc"]
    assert usd.fx is not None and usd.fx.value == D(EXPECTED["fx_rate_used"]["rate"])
    assert usd.fx_rate_type == "FBIL_REF" and by_ref["Demo India ETF"].fx is None


def test_quality_travels_with_every_number(report: PortfolioReport) -> None:
    by_name = {h.position.asset.name: h for h in report.holdings}
    # DTECH's newest close is 2026-11-09 and the market was open on 2026-11-10: stale, said in the note
    tech = by_name["Demo Tech Inc"]
    assert tech.value_inr.status is QualityStatus.STALE and "1 days behind" in tech.value_inr.notes[0]
    assert (
        tech.unrealised_inr.status is QualityStatus.STALE
        and tech.unrealised_pct.status is QualityStatus.STALE
    )
    assert report.totals.value_inr.status is QualityStatus.STALE  # one stale input makes the sum stale
    assert report.totals.unvalued == ()
    assert by_name["Demo Growth Equity Fund - Direct Growth"].value_inr.status is QualityStatus.OK
    assert report.holdings[0].value_inr.as_of.tzinfo is not None


def test_totals_pnl_income_and_costs_match_the_independent_derivation(report: PortfolioReport) -> None:
    t = EXPECTED["totals"]
    exact4(report.totals.value_inr.value, t["value_inr"])
    exact4(report.totals.cost_inr.value, t["cost_inr"])
    exact4(report.totals.unrealised_inr.value, t["unrealised_inr"])
    near(report.totals.unrealised_pct.value, t["unrealised_pct"], "1e-9")
    exact4(report.realised.gain_inr.value, EXPECTED["realised_gain_inr"])
    exact4(report.invested_inr.value, EXPECTED["paid_in_inr"])
    inc, exp = report.income, EXPECTED["income_and_costs"]
    exact4(inc.dividends_inr.value, exp["dividends_inr"])
    exact4(inc.reinvested_dividends_inr.value, exp["reinvested_dividends_inr"])
    exact4(inc.fees_inr.value, exp["fees_inr"])
    exact4(inc.taxes_inr.value, exp["taxes_inr"])
    assert inc.interest_inr.value == 0 and inc.stamp_duty_inr.value == 0


def test_returns_name_their_measure_and_match(report: PortfolioReport) -> None:
    xirr_m, twr_m, abs_m = report.returns
    assert (
        "money-weighted" in xirr_m.why and "removes the effect" in twr_m.why and "ignores when" in abs_m.why
    )
    near(xirr_m.result.value, EXPECTED["xirr_portfolio"], "1e-9")
    near(abs_m.result.value, EXPECTED["absolute_return"], "1e-9")
    # the DEMO prices start in Sept 2026 while the ledger starts in 2024: no price on the earlier flow
    # dates, so TWR is missing and says so, instead of being computed from a different instrument or zero
    assert twr_m.result.value is None and twr_m.result.status is QualityStatus.MISSING
    asset_x = {a.asset: a.xirr.result for a in report.asset_returns}
    expected = {
        "Demo Growth Equity Fund - Direct Growth": "AMFI:900001", "Demo Tech Inc": "TICKER:XNAS:DTECH",
        "Demo India ETF": "TICKER:XNSE:DEMOETF", "Demo IPO Ltd": "TICKER:XNSE:DIPO",
        "Demo New Co": "TICKER:XNSE:DNEW", "Demo Stock Co": "TICKER:XNSE:DSTK",
    }  # fmt: skip
    assert set(asset_x) == set(expected)  # Demo Old Co is folded into Demo New Co by the merger
    for name, ref in expected.items():
        near(asset_x[name].value, EXPECTED["xirr_by_asset"][ref], "1e-8")
    assert asset_x["Demo Stock Co"].status is QualityStatus.OK  # fully exited: all flows are known
    # reinvested dividends are unit moves with no cash: flagged as approximate
    assert asset_x["Demo Growth Equity Fund - Direct Growth"].status is QualityStatus.ESTIMATED
    assert asset_x["Demo Tech Inc"].status is QualityStatus.STALE  # ending value is stale


def test_fx_attribution_reconciles_and_names_the_rate_type(report: PortfolioReport) -> None:
    (row,) = report.fx_attribution
    a, e = row.attribution, EXPECTED["fx_attribution_DTECH"]
    assert row.asset == "Demo Tech Inc" and a.valuation_rate_type == "FBIL_REF" and a.reconciles
    near(a.purchase_rate.value, e["purchase_rate"])
    near(a.local_return.value, e["local_return"])
    near(a.fx_return.value, e["fx_return"])
    near(a.interaction.value, e["interaction"])
    near(a.inr_return.value, e["inr_return"])
    exact4(a.local_effect_inr.value, e["local_effect_inr"])
    exact4(a.fx_effect_inr.value, e["fx_effect_inr"])
    exact4(a.interaction_inr.value, e["interaction_inr"])
    # INR return = local + FX + interaction
    assert a.inr_return.value is not None and a.local_return.value is not None
    assert a.fx_return.value is not None and a.interaction.value is not None
    assert abs(a.inr_return.value - (a.local_return.value + a.fx_return.value + a.interaction.value)) < D(
        "1e-25"
    )
    # and the INR amounts add up to the holding's unrealised P&L
    tech = next(h for h in report.holdings if h.position.asset.name == "Demo Tech Inc")
    assert a.gain_inr.value is not None and tech.unrealised_inr.value is not None
    assert abs(a.gain_inr.value - tech.unrealised_inr.value) < D("1e-9")


def test_allocation_and_concentration_match(report: PortfolioReport) -> None:
    for dim, expected in EXPECTED["allocation"].items():
        got = {s.key: s.weight.value for s in report.allocation[dim]}
        assert set(got) == set(expected)
        for key, w in expected.items():
            near(got[key], w)
        assert abs(sum(got.values(), D(0)) - 1) < D("1e-25")  # type: ignore[arg-type]
    c = report.concentration
    near(c.top_n_weight.value, EXPECTED["concentration"]["top3"])
    near(c.hhi.value, EXPECTED["concentration"]["hhi"])
    assert c.top_n == 3 and report.allocation["market_cap"][0].key == "UNKNOWN"


def test_look_through_is_missing_until_fund_holdings_exist(report: PortfolioReport) -> None:
    lt = report.look_through
    assert set(lt.without_data) == {"Demo Growth Equity Fund - Direct Growth", "Demo India ETF"}
    assert all(m.status.severity >= QualityStatus.ESTIMATED.severity for m in lt.exposure.values())


def test_series_statistics_and_benchmark_comparison(report: PortfolioReport) -> None:
    stats = {s.asset.name: s for s in report.stats}
    etf = stats["Demo India ETF"]
    assert etf.volatility.value is not None and etf.volatility.value > 0
    assert etf.drawdown.depth.value is not None and etf.drawdown.depth.value < 0
    assert etf.drawdown.peak is not None and etf.drawdown.trough is not None
    assert stats["Demo Tech Inc"].volatility.value is not None  # 25 closes: enough returns
    pairs = {(p.a, p.b): p.correlation for p in report.correlations}
    assert len(pairs) == 10  # 5 holdings, every pair
    assert ("Demo India ETF", "Demo Stock Co") not in pairs  # Demo Stock Co is fully sold: not a holding
    bench = {b.asset: b.comparison for b in report.benchmarks}
    assert set(bench) == {"Demo Growth Equity Fund - Direct Growth", "Demo India ETF", "Demo IPO Ltd",
                          "Demo New Co", "Demo Tech Inc"}  # fmt: skip
    etf_b = bench["Demo India ETF"]
    assert etf_b.benchmark_id == "NIFTY50_TRI" and etf_b.excess_return.value is not None
    assert etf_b.beta.value is not None and etf_b.correlation.value is not None
    assert (
        bench["Demo Tech Inc"].excess_return.value is None
    )  # S&P 500 series not supplied: missing, not invented


def test_api_returns_every_number_with_status_and_as_of() -> None:
    client = TestClient(api_app)
    assert client.get("/portfolio").status_code == 401  # login required
    assert client.post("/auth/login", json={"username": "demo", "password": PASSWORD}).status_code == 200
    r = client.get("/portfolio", params={"as_of": str(DEMO_TODAY)})
    assert r.status_code == 200
    body = r.json()
    assert body["dataset"] == "DEMO" and body["fx_rate_type"] == "FBIL_REF"
    value = body["totals"]["value_inr"]
    assert set(value) == {"value", "status", "as_of", "sources", "notes"} and value["status"] == "stale"
    assert D(value["value"]).quantize(D("0.0001")) == D(EXPECTED["totals"]["value_inr"])
    assert body["returns"][1]["measure"] == "TWR" and body["returns"][1]["result"]["status"] == "missing"
    assert isinstance(body["returns"][0]["result"]["value"], str)  # Decimal stays a string, never a float
    assert body["holdings"][0]["value_inr"]["as_of"].startswith("2026-")


def test_cli_prints_status_next_to_every_number() -> None:
    out = runner.invoke(cli_app, ["portfolio", "summary", "--date", str(DEMO_TODAY)])
    assert out.exit_code == 0, out.output
    assert "DEMO portfolio on 2026-11-10 (FX rate type: FBIL_REF)" in out.output
    assert "[stale] as of 2026-11-09" in out.output and "TWR" in out.output and "missing" in out.output
    assert "reconciles: True" in out.output and "34,084.06" in out.output
    alloc = runner.invoke(cli_app, ["portfolio", "allocation", "--date", str(DEMO_TODAY)])
    assert alloc.exit_code == 0 and "By asset_class:" in alloc.output and "HHI" in alloc.output
    assert "no holdings data for" in alloc.output
    stats = runner.invoke(cli_app, ["portfolio", "risk-stats", "--date", str(DEMO_TODAY)])
    assert stats.exit_code == 0 and "max drawdown" in stats.output and "vs NIFTY50_TRI" in stats.output


def test_real_dataset_stays_locked_until_the_phase_4_gate_is_approved() -> None:
    from arthashreni.core.gates import gate_approved
    from arthashreni.db.session import RealDataLocked, get_engine

    if gate_approved(4):
        pytest.skip("phase-4.approved exists: REAL is allowed")
    with pytest.raises(RealDataLocked):
        get_engine("real")
