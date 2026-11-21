"""Risk engine (F2): thresholds with rationale, signals, the four levels, vetoes, scenarios and alerts."""

import textwrap
from datetime import date, timedelta
from decimal import Decimal

import pytest

from arthashreni.analytics.series import BenchmarkComparison
from arthashreni.dq.measured import Measured, QualityStatus, missing
from arthashreni.risk.alerts import build_alerts, diff_alerts
from arthashreni.risk.levels import overall_level, vetoes
from arthashreni.risk.scenarios import Exposure, run_scenarios
from arthashreni.risk.signals import (
    Decline,
    Scope,
    Signal,
    band_breach_signals,
    classify_decline,
    correlation_rise_signal,
    emergency_months_signal,
    foreign_share_signal,
    holding_signal,
    index_signals,
    level_of,
    macro_missing,
    make,
    top3_signal,
    usdinr_signal,
)
from arthashreni.risk.thresholds import Level, Threshold, load, parse

D = Decimal
CFG = load()
DAY = date(2026, 11, 10)
STAMP = Measured(D(0), QualityStatus.OK, missing().as_of, ("t",)).as_of


def m(v: str | None, status: QualityStatus = QualityStatus.OK) -> Measured[Decimal]:
    return Measured(None if v is None else D(v), status, STAMP, ("t",))


def series(prices: list[str], start: date = date(2026, 1, 1)) -> list[tuple[date, Measured[Decimal]]]:
    return [(start + timedelta(days=i), m(p)) for i, p in enumerate(prices)]


# ---- thresholds config ---------------------------------------------------------------------------------


def test_every_threshold_has_a_rationale_and_is_honest_about_backtesting() -> None:
    assert len(CFG.thresholds) >= 9
    for t in CFG.thresholds.values():
        assert t.rationale.strip() and t.unit.strip()
        assert t.backtested is False  # nothing is backtested yet (V31); the config must not claim otherwise


def test_thresholds_are_ordered_by_direction() -> None:
    for t in CFG.thresholds.values():
        if t.direction == "above":
            assert t.caution <= t.high <= t.severe
        else:
            assert t.caution >= t.high >= t.severe


def test_config_rules_load() -> None:
    r = CFG.rules
    assert (r.severe_stress_if_severe, r.high_risk_if_severe, r.high_risk_if_high) == (2, 1, 2)
    assert (r.caution_if_high, r.caution_if_caution) == (1, 2)
    assert r.alert_min_level is Level.HIGH_RISK


def toml(sig: str, extra: str = "") -> str:
    return textwrap.dedent(f"""
        [level_rules]
        severe_stress_if_severe = 2
        high_risk_if_severe = 1
        high_risk_if_high = 2
        caution_if_high = 1
        caution_if_caution = 2
        alert_min_level = "CAUTION"
        [decline]
        market_wide_ratio = 0.6
        asset_specific_ratio = 0.3
        min_correlation = 0.5
        [signal.x]
        unit = "u"
        {sig}
        backtested = false
        {extra}
    """)


def test_malformed_thresholds_are_refused() -> None:
    ok = 'direction = "above"\ncaution = 1\nhigh = 2\nsevere = 3\nrationale = "because"'
    assert parse(toml(ok)).thresholds["x"].severe == 3
    for bad in (
        'direction = "above"\ncaution = 3\nhigh = 2\nsevere = 1\nrationale = "r"',  # wrong order
        'direction = "below"\ncaution = 1\nhigh = 2\nsevere = 3\nrationale = "r"',  # wrong order
        'direction = "sideways"\ncaution = 1\nhigh = 2\nsevere = 3\nrationale = "r"',
        'direction = "above"\ncaution = 1\nhigh = 2\nsevere = 3\nrationale = "  "',  # no rationale
    ):
        with pytest.raises(ValueError, match="malformed or has no rationale"):
            parse(toml(bad))


# ---- levels of one signal ------------------------------------------------------------------------------


def th(direction: str) -> Threshold:
    c, h, s = (D(1), D(2), D(3)) if direction == "above" else (D(3), D(2), D(1))
    return Threshold("x", "u", direction, c, h, s, "r", False)


@pytest.mark.parametrize(
    "v,level",
    [("0.99", Level.NORMAL), ("1", Level.CAUTION), ("1.99", Level.CAUTION), ("2", Level.HIGH_RISK),
     ("2.99", Level.HIGH_RISK), ("3", Level.SEVERE_STRESS), ("99", Level.SEVERE_STRESS)],
)  # fmt: skip
def test_above_thresholds_are_inclusive(v: str, level: Level) -> None:
    assert level_of(m(v), th("above")) is level


@pytest.mark.parametrize(
    "v,level",
    [("3.01", Level.NORMAL), ("3", Level.CAUTION), ("2.01", Level.CAUTION), ("2", Level.HIGH_RISK),
     ("1.01", Level.HIGH_RISK), ("1", Level.SEVERE_STRESS), ("0", Level.SEVERE_STRESS)],
)  # fmt: skip
def test_below_thresholds_are_inclusive(v: str, level: Level) -> None:
    assert level_of(m(v), th("below")) is level


def test_no_value_or_quarantined_has_no_level_never_normal() -> None:
    assert level_of(m(None, QualityStatus.MISSING), th("above")) is None
    assert level_of(m("9", QualityStatus.QUARANTINED), th("above")) is None
    assert level_of(m("9", QualityStatus.STALE), th("above")) is Level.SEVERE_STRESS  # stale still informs


# ---- builders ------------------------------------------------------------------------------------------


def test_index_drawdown_signal_is_the_fall_from_the_peak() -> None:
    prices = [str(100 + i) for i in range(30)] + [
        str(129 - i * 3) for i in range(1, 10)
    ]  # peak 129, then -3/day
    sigs = {s.kind: s for s in index_signals(CFG, "NIFTY", series(prices))}
    assert sigs["idx_drawdown"].value.value == D(27) / D(129)
    assert sigs["idx_drawdown"].scope is Scope.MARKET and sigs["idx_drawdown"].id == "idx_drawdown:NIFTY"
    assert sigs["idx_drawdown"].level is Level.HIGH_RISK  # 20.9%
    assert "idx_vol" in sigs


def test_empty_index_series_is_missing_not_normal() -> None:
    s = index_signals(CFG, "NIFTY", [])
    assert all(x.level is None for x in s)


def test_usdinr_signal_is_the_absolute_relative_change() -> None:
    up = usdinr_signal(CFG, m("88"), m("84"))
    down = usdinr_signal(CFG, m("84"), m("86"))
    assert up.value.value is not None and abs(up.value.value - D(4) / D(84)) < D("1e-25")
    assert down.value.value is not None and abs(down.value.value - D(2) / D(86)) < D("1e-25")
    assert up.level is Level.HIGH_RISK and down.level is Level.CAUTION
    assert usdinr_signal(CFG, m(None, QualityStatus.MISSING), m("84")).level is None


def test_macro_signals_are_missing_with_the_reason() -> None:
    sigs = macro_missing()
    assert [s.id for s in sigs] == ["macro:rates", "macro:inflation", "macro:oil"]
    assert all(s.level is None and s.value.status is QualityStatus.MISSING for s in sigs)
    assert "no macro feed" in sigs[0].value.notes[0]


def test_holding_signal_carries_the_asset() -> None:
    prices = ["100"] * 5 + ["60"]
    s = holding_signal(CFG, 7, "Demo Co", series(prices))
    assert s.asset_id == 7 and s.scope is Scope.ASSET and s.value.value == D("0.4")
    assert s.level is Level.SEVERE_STRESS


def test_top3_and_emergency_and_foreign_share() -> None:
    assert top3_signal(CFG, m("0.8")).level is Level.HIGH_RISK
    assert emergency_months_signal(CFG, m("2")).level is Level.HIGH_RISK  # below three months
    assert emergency_months_signal(CFG, m("8")).level is Level.NORMAL
    fx = foreign_share_signal(CFG, {"INR": m("0.45"), "USD": m("0.35"), "EUR": m("0.2")})
    assert fx.value.value == D("0.55") and fx.level is Level.HIGH_RISK
    assert foreign_share_signal(CFG, {}).level is None
    assert foreign_share_signal(CFG, {"INR": m("0.5"), "USD": m(None, QualityStatus.MISSING)}).level is None


def test_foreign_share_takes_the_worst_status() -> None:
    s = foreign_share_signal(CFG, {"INR": m("0.5"), "USD": m("0.5", QualityStatus.STALE)})
    assert s.value.status is QualityStatus.STALE


def test_band_breach_is_the_distance_outside_the_band() -> None:
    weights = {
        ("asset_class", "MF"): m("0.75"),
        ("asset_class", "ETF_IN"): m("0.05"),
        ("asset_class", "GOLD"): m("0.1"),
    }
    bands = {("asset_class", "MF"): (D("0.3"), D("0.6")), ("asset_class", "ETF_IN"): (D("0.1"), D("0.3")),
             ("asset_class", "GOLD"): (D("0"), D("0.2")), ("asset_class", "SILVER"): (D("0"), D("0.1"))}  # fmt: skip
    got = {s.subject: s for s in band_breach_signals(CFG, weights, bands)}
    assert (
        got["asset_class:MF"].value.value == D("0.15") and got["asset_class:MF"].level is Level.SEVERE_STRESS
    )
    assert (
        got["asset_class:ETF_IN"].value.value == D("0.05")
        and got["asset_class:ETF_IN"].level is Level.HIGH_RISK
    )
    assert got["asset_class:GOLD"].value.value == 0 and got["asset_class:GOLD"].level is Level.NORMAL
    assert got["asset_class:SILVER"].value.value == 0  # not held: weight 0, inside a band that starts at 0


def test_band_breach_on_a_missing_weight_has_no_level() -> None:
    s = band_breach_signals(CFG, {("a", "b"): m(None, QualityStatus.MISSING)}, {("a", "b"): (D(0), D(1))})
    assert s[0].level is None


def test_correlation_rise() -> None:
    s = correlation_rise_signal(CFG, [m("0.9"), m("0.8")], [m("0.4"), m("0.5")])
    assert s.value.value == D("0.4") and s.level is Level.HIGH_RISK
    assert correlation_rise_signal(CFG, [], [m("0.4")]).level is None
    assert correlation_rise_signal(CFG, [m("0.9")], []).level is None
    assert correlation_rise_signal(CFG, [m(None, QualityStatus.MISSING)], [m("0.4")]).level is None
    stale = correlation_rise_signal(CFG, [m("0.9", QualityStatus.STALE)], [m("0.4")])
    assert stale.value.status is QualityStatus.STALE


# ---- overall level -------------------------------------------------------------------------------------


def sig(
    i: str, level: Level | None, kind: str = "k", scope: Scope = Scope.MARKET, asset: int | None = None
) -> Signal:
    return Signal(i, kind, scope, i, m("1" if level is not None else None), level, asset)


def lv(*levels: Level | None) -> Level:
    return overall_level([sig(f"s{i}", x) for i, x in enumerate(levels)], CFG.rules).level


def test_level_rules() -> None:
    n, c, h, s = Level.NORMAL, Level.CAUTION, Level.HIGH_RISK, Level.SEVERE_STRESS
    assert lv() is n and lv(n, n, n) is n
    assert lv(c) is n and lv(c, c) is Level.CAUTION and lv(h) is Level.CAUTION
    assert lv(h, h) is Level.HIGH_RISK and lv(s) is Level.HIGH_RISK
    assert lv(s, s) is Level.SEVERE_STRESS and lv(s, h) is Level.HIGH_RISK
    assert lv(h, c) is Level.CAUTION and lv(s, c, c) is Level.HIGH_RISK


def test_missing_signals_do_not_count_and_are_listed() -> None:
    r = overall_level([sig("a", Level.HIGH_RISK), sig("b", None), sig("c", None)], CFG.rules)
    assert (r.available, r.total, r.missing, r.level) == (1, 3, ("b", "c"), Level.CAUTION)
    assert r.drivers == ("a",) and r.at_or_above == {"caution": 1, "high": 1, "severe": 0}


def test_band_breaches_count_once() -> None:
    band = [sig(f"band_breach:x{i}", Level.SEVERE_STRESS, kind="band_breach") for i in range(3)]
    r = overall_level(band, CFG.rules)
    assert r.level is Level.HIGH_RISK  # one root cause, not three severe signals
    assert r.at_or_above["severe"] == 1
    other = overall_level([*band, sig("idx", Level.SEVERE_STRESS)], CFG.rules)
    assert other.level is Level.SEVERE_STRESS  # a different cause adds up
    assert overall_level([sig("band_breach:y", Level.CAUTION, kind="band_breach")], CFG.rules).available == 1


# ---- market-wide vs asset-specific ---------------------------------------------------------------------


def cmp(asset: str | None, bench: str | None, corr: str | None = "0.9") -> BenchmarkComparison:
    return BenchmarkComparison("B", m(asset), m(bench), m("0"), m("1"), m(corr))


def test_decline_classification() -> None:
    assert classify_decline(CFG, cmp("-0.2", "-0.15")).kind == "market-wide"
    assert classify_decline(CFG, cmp("-0.2", "-0.12")).kind == "market-wide"  # exactly 60%
    assert classify_decline(CFG, cmp("-0.2", "-0.06")).kind == "asset-specific"  # exactly 30%
    assert classify_decline(CFG, cmp("-0.2", "0.05")).kind == "asset-specific"
    assert classify_decline(CFG, cmp("-0.2", "-0.09")).kind == "uncertain"  # in between


@pytest.mark.parametrize(
    "c",
    [None, cmp(None, "-0.1"), cmp("-0.2", None), cmp("0.1", "-0.1"), cmp("-0.2", "-0.1", "0.3"), cmp("-0.2", "-0.1", None),
     BenchmarkComparison("B", m("-0.2", QualityStatus.QUARANTINED), m("-0.1"), m("0"), m("1"), m("0.9"))],
)  # fmt: skip
def test_uncertain_is_an_allowed_answer(c: BenchmarkComparison | None) -> None:
    assert classify_decline(CFG, c).kind == "uncertain"


def test_correlation_floor_is_inclusive() -> None:
    assert classify_decline(CFG, cmp("-0.2", "-0.15", "0.5")).kind == "market-wide"


# ---- vetoes (F3.3) -------------------------------------------------------------------------------------


def test_only_severe_asset_specific_declines_veto() -> None:
    signals = [sig("a", Level.SEVERE_STRESS, scope=Scope.ASSET, asset=1), sig("b", Level.SEVERE_STRESS, scope=Scope.ASSET, asset=2),
               sig("c", Level.HIGH_RISK, scope=Scope.ASSET, asset=3), sig("d", Level.SEVERE_STRESS, scope=Scope.ASSET, asset=4),
               sig("e", Level.SEVERE_STRESS, scope=Scope.MARKET)]  # fmt: skip
    declines = {
        1: Decline("asset-specific", "r1"),
        2: Decline("market-wide", "r2"),
        3: Decline("asset-specific", "r3"),
    }
    got = vetoes(signals, declines)
    assert (
        set(got) == {1} and "severe asset-specific decline (a)" in got[1][0]
    )  # 4 has no classification: no veto


# ---- scenarios -----------------------------------------------------------------------------------------


def ex(
    i: int, cls: str, cur: str, value: str | None, sector: str | None = None, category: str | None = None
) -> Exposure:
    return Exposure(
        i, f"A{i}", cls, cur, sector, m(value, QualityStatus.OK if value else QualityStatus.MISSING), category
    )


BOOK = [ex(1, "MF", "INR", "1000"), ex(2, "EQUITY_US", "USD", "2000", "Tech"), ex(3, "GOLD_ETF", "INR", "500"),
        ex(4, "EQUITY_IN", "INR", "1500", "Tech"), ex(5, "EQUITY_IN", "INR", "500", "Banks")]  # fmt: skip


def by_id(results: list) -> dict:  # type: ignore[type-arg]
    return {r.id: r for r in results}


def test_equity_shocks() -> None:
    r = by_id(run_scenarios(BOOK, DAY))
    assert r["equity_-10"].total_inr.value == D(-500)  # 5000 of equity-like value, gold untouched
    assert r["equity_-20"].total_inr.value == D(-1000) and r["equity_-30"].total_inr.value == D(-1500)
    pct = r["equity_-10"].total_pct.value
    assert pct is not None and abs(pct - D(-500) / D(5500)) < D("1e-25")
    assert dict(r["equity_-10"].per_holding)["A3"].value == 0
    assert r["equity_-10"].note == "scenario, not a prediction"


def test_sector_shock_hits_the_largest_sector_only() -> None:
    r = by_id(run_scenarios(BOOK, DAY))["sector_shock"]
    assert "Tech" in r.label and r.total_inr.value == D("-0.30") * D(3500)


def test_currency_shocks_move_only_foreign_holdings() -> None:
    r = by_id(run_scenarios(BOOK, DAY))
    assert r["usdinr_up_10"].total_inr.value == D(200) and r["usdinr_down_10"].total_inr.value == D(-200)


def test_joint_stress_and_commodity_reversal() -> None:
    r = by_id(run_scenarios(BOOK, DAY))
    us = D(2000) * ((D("0.8") * D("1.05")) - 1)
    inr = D("-0.2") * D(3000)  # MF + two Indian equities
    assert r["joint_india_us"].total_inr.value == us + inr
    assert r["commodity_reversal"].total_inr.value == D("-0.15") * 500


def test_unvalued_holdings_are_left_out_and_said() -> None:
    r = by_id(run_scenarios([*BOOK, ex(6, "EQUITY_IN", "INR", None)], DAY))["equity_-10"]
    assert r.total_inr.value == D(-500) and r.total_inr.status is QualityStatus.ESTIMATED
    assert "A6" in r.total_inr.notes[-1] and r.total_pct.status is QualityStatus.ESTIMATED


def test_a_debt_fund_is_not_shocked_like_equity_but_an_unknown_fund_is() -> None:
    book = [
        ex(1, "MF", "INR", "1000", None, "DEBT"),
        ex(2, "MF", "INR", "1000", "Tech", "EQUITY"),
        ex(3, "MF", "INR", "1000"),
    ]
    r = by_id(run_scenarios(book, DAY))
    assert r["equity_-10"].total_inr.value == D(-200)  # the debt fund is untouched; unknown counts as equity
    assert dict(r["equity_-10"].per_holding)["A1"].value == 0
    assert dict(r["equity_-10"].per_holding)["A3"].value == D(-100)
    assert r["joint_india_us"].total_inr.value == D(-400)
    assert dict(r["commodity_reversal"].per_holding)["A1"].value == 0


def test_a_debt_fund_with_a_sector_does_not_make_a_sector_shock() -> None:
    r = by_id(
        run_scenarios([ex(1, "MF", "INR", "1000", "Banks", "DEBT"), ex(2, "EQUITY_IN", "INR", "500")], DAY)
    )
    assert "sector_shock" not in r  # no equity-oriented holding has a sector


def test_no_sector_means_no_sector_scenario() -> None:
    ids = [r.id for r in run_scenarios([ex(1, "MF", "INR", "100")], DAY)]
    assert "sector_shock" not in ids and "equity_-10" in ids


def test_empty_book_gives_missing_percentages_not_errors() -> None:
    r = by_id(run_scenarios([], DAY))["equity_-10"]
    assert r.total_inr.value == 0 and r.total_pct.value is None


# ---- alerts --------------------------------------------------------------------------------------------


def test_alerts_only_for_material_levels_with_every_required_field() -> None:
    signals = [sig("idx", Level.HIGH_RISK), sig("low", Level.CAUTION), sig("gone", None),
               sig("asset", Level.SEVERE_STRESS, scope=Scope.ASSET, asset=1)]  # fmt: skip
    values = {1: m("1000")}
    alerts = build_alerts(
        DAY,
        signals,
        {1: Decline("market-wide", "moves with benchmark")},
        values,
        ("equity_-10",),
        Level.HIGH_RISK,
    )
    assert [a.key for a in alerts] == ["idx:HIGH_RISK", "asset:SEVERE_STRESS"]
    a = alerts[1]
    assert a.assets == ("asset",) and a.classification == "temporary" and a.scenarios == ("equity_-10",)
    assert a.impact_inr is not None and a.impact_inr.value == D(-1000)  # value 1000 x -(signal 1)
    assert (
        "market-wide: moves with benchmark" in a.what
        and a.when == DAY
        and a.evidence == (f"signal:asset:{DAY}",)
    )
    assert (
        "reduce only with clear evidence" in a.responses and "Nothing is executed" in a.if_nothing and a.watch
    )
    assert alerts[0].responses[0] == "continue the plan" and alerts[0].impact_inr is None


def test_asset_specific_declines_stay_uncertain_without_fundamentals() -> None:
    s = [sig("asset", Level.SEVERE_STRESS, scope=Scope.ASSET, asset=1)]
    a = build_alerts(DAY, s, {1: Decline("asset-specific", "x")}, {}, (), Level.HIGH_RISK)[0]
    assert a.classification == "uncertain" and a.impact_inr is None
    b = build_alerts(DAY, s, {}, {1: m("10")}, (), Level.HIGH_RISK)[0]
    assert b.classification == "uncertain"


def test_portfolio_alerts_offer_rebalancing_through_contributions() -> None:
    a = build_alerts(DAY, [sig("p", Level.HIGH_RISK, scope=Scope.PORTFOLIO)], {}, {}, (), Level.HIGH_RISK)[0]
    assert a.responses[0] == "consider rebalancing via contributions"


def test_the_minimum_level_is_configurable() -> None:
    s = [sig("c", Level.CAUTION)]
    assert build_alerts(DAY, s, {}, {}, (), Level.HIGH_RISK) == []
    assert len(build_alerts(DAY, s, {}, {}, (), Level.CAUTION)) == 1


def test_identical_alerts_are_deduplicated_and_unchanged_conditions_stay_silent() -> None:
    alerts = build_alerts(DAY, [sig("idx", Level.HIGH_RISK)], {}, {}, (), Level.HIGH_RISK)
    first = diff_alerts(alerts, set())
    assert [a.key for a in first.new] == ["idx:HIGH_RISK"]
    again = diff_alerts(alerts, {"idx:HIGH_RISK"})
    assert again.new == () and again.unchanged == ("idx:HIGH_RISK",) and again.cleared == ()


def test_an_escalation_is_a_new_alert_and_closes_the_old_one() -> None:
    worse = build_alerts(DAY, [sig("idx", Level.SEVERE_STRESS)], {}, {}, (), Level.HIGH_RISK)
    d = diff_alerts(worse, {"idx:HIGH_RISK"})
    assert [a.key for a in d.new] == ["idx:SEVERE_STRESS"] and d.cleared == ("idx:HIGH_RISK",)


def test_a_cleared_condition_closes() -> None:
    d = diff_alerts([], {"b:HIGH_RISK", "a:HIGH_RISK"})
    assert d.cleared == ("a:HIGH_RISK", "b:HIGH_RISK") and d.new == ()


def test_make_names_the_signal_by_kind_and_subject() -> None:
    s = make(CFG, "top3_weight", Scope.PORTFOLIO, "portfolio", m("0.7"))
    assert s.id == "top3_weight:portfolio" and s.kind == "top3_weight" and s.level is Level.CAUTION
