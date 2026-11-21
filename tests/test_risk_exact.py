"""Exact-output tests for the risk engine: whole objects are compared, so a changed label, scope, subject, source
list, note or level shows up. Complements tests/test_risk.py (behaviour) for the mutation gate (G1.7)."""

import textwrap
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from arthashreni.analytics.series import BenchmarkComparison
from arthashreni.dq.measured import Measured, QualityStatus, day_stamp
from arthashreni.risk.alerts import Alert, build_alerts, diff_alerts
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
    top3_signal,
    usdinr_signal,
)
from arthashreni.risk.thresholds import Config, Level, Rules, Threshold, load, parse

D = Decimal
Q = QualityStatus
CFG = load()
DAY = date(2026, 11, 10)
T1 = datetime(2026, 11, 9, tzinfo=UTC)
T2 = datetime(2026, 11, 8, tzinfo=UTC)


def mm(
    v: str | None,
    status: Q = Q.OK,
    at: datetime = T1,
    src: tuple[str, ...] = ("s",),
    notes: tuple[str, ...] = (),
) -> Measured[Decimal]:
    return Measured(None if v is None else D(v), status, at, src, notes)


# ---- config parsing --------------------------------------------------------------------------------------

RULES = """
[level_rules]
severe_stress_if_severe = 5
high_risk_if_severe = 4
high_risk_if_high = 3
caution_if_high = 2
caution_if_caution = 1
alert_min_level = "CAUTION"
[decline]
market_wide_ratio = 0.7
asset_specific_ratio = 0.2
min_correlation = 0.4
"""


def test_parse_builds_every_field() -> None:
    cfg = parse(
        RULES
        + textwrap.dedent("""
        [signal.a]
        unit = "pct"
        direction = "above"
        caution = 1
        high = 2
        severe = 3
        rationale = "why a"
        backtested = true
        [signal.b]
        unit = "months"
        direction = "below"
        caution = 6
        high = 3
        severe = 1
        rationale = "why b"
        backtested = false
        """)
    )
    assert cfg == Config(
        {
            "a": Threshold("a", "pct", "above", D(1), D(2), D(3), "why a", True),
            "b": Threshold("b", "months", "below", D(6), D(3), D(1), "why b", False),
        },
        Rules(5, 4, 3, 2, 1, Level.CAUTION, D("0.7"), D("0.2"), D("0.4")),
    )


@pytest.mark.parametrize("direction", ["above", "below"])
def test_equal_thresholds_are_allowed_but_other_directions_and_orders_are_not(direction: str) -> None:
    def one(d: str, a: int, b: int, c: int, why: str = "x") -> str:
        return (
            RULES
            + f'[signal.q]\nunit="u"\ndirection="{d}"\ncaution={a}\nhigh={b}\nsevere={c}\nrationale="{why}"\nbackstested=1\nbacktested=false\n'
        )

    ok = (1, 1, 1)
    assert parse(one(direction, *ok)).thresholds["q"].caution == 1  # equal values are a valid (flat) table
    low_first, low_mid = ((1, 1, 2), (1, 2, 2)) if direction == "above" else ((2, 1, 1), (2, 2, 1))
    assert parse(one(direction, *low_first)) and parse(
        one(direction, *low_mid)
    )  # ties at either end are fine
    bad = (2, 1, 3) if direction == "above" else (1, 2, 3)
    for args in ((direction, *bad), ("sideways", 1, 2, 3), (direction, *ok, " ")):
        with pytest.raises(ValueError, match="malformed or has no rationale"):
            parse(one(*args))  # type: ignore[arg-type]


# ---- level_of ---------------------------------------------------------------------------------------------

ABOVE = Threshold("t", "u", "above", D("0.1"), D("0.2"), D("0.3"), "r", False)
BELOW = Threshold("t", "u", "below", D(6), D(3), D(1), "r", False)


@pytest.mark.parametrize(
    ("v", "level"),
    [("0.09", Level.NORMAL), ("0.1", Level.CAUTION), ("0.19", Level.CAUTION), ("0.2", Level.HIGH_RISK),
     ("0.29", Level.HIGH_RISK), ("0.3", Level.SEVERE_STRESS), ("5", Level.SEVERE_STRESS)],
)  # fmt: skip
def test_level_of_above(v: str, level: Level) -> None:
    assert level_of(mm(v), ABOVE) is level


@pytest.mark.parametrize(
    ("v", "level"),
    [("7", Level.NORMAL), ("6", Level.CAUTION), ("4", Level.CAUTION), ("3", Level.HIGH_RISK),
     ("2", Level.HIGH_RISK), ("1", Level.SEVERE_STRESS), ("0", Level.SEVERE_STRESS)],
)  # fmt: skip
def test_level_of_below(v: str, level: Level) -> None:
    assert level_of(mm(v), BELOW) is level


def test_level_of_needs_a_usable_value() -> None:
    assert level_of(mm(None), ABOVE) is None
    assert level_of(mm("0.5", Q.QUARANTINED), ABOVE) is None
    assert level_of(mm("0.5", Q.STALE), ABOVE) is Level.SEVERE_STRESS  # stale still counts, labelled


# ---- signal constructors ----------------------------------------------------------------------------------


def sig(
    id_: str,
    kind: str,
    scope: Scope,
    subject: str,
    value: Measured[Decimal],
    level: Level | None,
    asset: int | None = None,
) -> Signal:
    return Signal(id_, kind, scope, subject, value, level, asset)


def test_macro_signals_are_missing_with_the_stated_reason() -> None:
    got = macro_missing()
    note = "no macro feed (FRED, RBI DBIE and MOSPI adapters are not built)"
    assert [(s.id, s.kind, s.scope, s.subject, s.level, s.asset_id) for s in got] == [
        (f"macro:{n}", "", Scope.MARKET, n, None, None) for n in ("rates", "inflation", "oil")
    ]
    assert all(
        s.value.value is None and s.value.status is Q.MISSING and s.value.notes == (note,) for s in got
    )
    assert macro_missing("custom")[0].value.notes == ("custom",)


def test_usdinr_signal_is_the_absolute_move() -> None:
    s = usdinr_signal(CFG, mm("92", src=("fbil",)), mm("100", src=("fbil",)))
    assert (s.id, s.kind, s.scope, s.subject, s.asset_id) == (
        "usdinr_move:USDINR",
        "usdinr_move",
        Scope.MARKET,
        "USDINR",
        None,
    )
    assert (
        s.value.value == D("0.08")
        and s.value.status is Q.OK
        and s.level is level_of(s.value, CFG.thresholds["usdinr_move"])
    )
    assert usdinr_signal(CFG, mm("108"), mm("100")).value.value == D(
        "0.08"
    )  # a rise and a fall count the same
    assert usdinr_signal(CFG, mm(None), mm("100")).value.value is None


def test_top3_and_emergency_signals() -> None:
    v = mm("0.9", notes=("n",))
    s = top3_signal(CFG, v)
    assert s == sig(
        "top3_weight:portfolio",
        "top3_weight",
        Scope.PORTFOLIO,
        "portfolio",
        v,
        level_of(v, CFG.thresholds["top3_weight"]),
    )
    e = emergency_months_signal(CFG, mm("2"))
    assert (e.id, e.kind, e.scope, e.subject) == (
        "emergency_months:portfolio",
        "emergency_months",
        Scope.PORTFOLIO,
        "portfolio",
    )
    assert e.level is level_of(mm("2"), CFG.thresholds["emergency_months"])


def test_holding_signal_names_the_asset_and_negates_the_drawdown() -> None:
    prices = [(date(2026, 1, 1 + i), mm(p)) for i, p in enumerate(["100", "80"])]
    s = holding_signal(CFG, 7, "Fund A", prices)
    assert (s.id, s.kind, s.scope, s.subject, s.asset_id) == (
        "holding_drawdown:Fund A",
        "holding_drawdown",
        Scope.ASSET,
        "Fund A",
        7,
    )
    assert s.value.value == D("0.2")


def test_index_signals_are_market_scope_per_benchmark() -> None:
    prices = [(date(2026, 1, 1 + i), mm(p)) for i, p in enumerate(["100", "90", "95"])]
    dd, vol = index_signals(CFG, "NIFTY", prices)
    assert (dd.id, dd.kind, dd.scope, dd.subject) == (
        "idx_drawdown:NIFTY",
        "idx_drawdown",
        Scope.MARKET,
        "NIFTY",
    )
    assert (vol.id, vol.kind, vol.scope, vol.subject) == ("idx_vol:NIFTY", "idx_vol", Scope.MARKET, "NIFTY")
    assert dd.value.value == D("0.05")


def test_foreign_share_exact() -> None:
    empty = foreign_share_signal(CFG, {})
    assert (empty.id, empty.scope, empty.subject, empty.level) == (
        "foreign_share:portfolio",
        Scope.PORTFOLIO,
        "portfolio",
        None,
    )
    assert empty.value.notes == ("no valued holdings",)

    part = foreign_share_signal(CFG, {"INR": mm("0.6", at=T1), "USD": mm(None, Q.MISSING, T2)})
    assert part.level is None and part.value.value is None and part.value.as_of == T1  # first weight's as-of
    assert part.value.notes == ("a weight is missing",) and (part.scope, part.subject) == (
        Scope.PORTFOLIO,
        "portfolio",
    )

    w = {
        "INR": mm("0.6", Q.OK, T1, ("a",), ("n1",)),
        "USD": mm("0.3", Q.STALE, T2, ("b",)),
        "EUR": mm("0.1", Q.ESTIMATED, T2, ("c",)),
    }
    got = foreign_share_signal(CFG, w)
    assert got.value == Measured(
        D("0.4"), Q.STALE, T1, ("a",), ("n1",)
    )  # the worst status; first weight's as-of/sources/notes
    assert (got.id, got.kind, got.scope, got.subject, got.asset_id) == (
        "foreign_share:portfolio",
        "foreign_share",
        Scope.PORTFOLIO,
        "portfolio",
        None,
    )
    assert got.level is level_of(got.value, CFG.thresholds["foreign_share"])
    only_inr = foreign_share_signal(CFG, {"INR": mm("1")})
    assert only_inr.value.value == 0 and only_inr.value.status is Q.OK


def test_band_breach_signals_exact() -> None:
    weights = {
        ("asset_class", "MF"): mm("0.7", src=("w",), notes=("n",)),
        ("asset_class", "GOLD"): mm("0.02"),
        ("country", "IN"): mm("0.5"),
    }
    bands = {("asset_class", "MF"): (D("0.3"), D("0.6")), ("asset_class", "GOLD"): (D("0.05"), D("0.2")),
             ("country", "IN"): (D("0.4"), D("0.9")), ("currency", "USD"): (D("0.0"), D("0.2"))}  # fmt: skip
    got = band_breach_signals(CFG, weights, bands)
    assert [s.id for s in got] == [
        "band_breach:asset_class:GOLD",
        "band_breach:asset_class:MF",
        "band_breach:country:IN",
        "band_breach:currency:USD",
    ]
    assert [(s.kind, s.scope, s.subject) for s in got] == [
        ("band_breach", Scope.PORTFOLIO, s.subject) for s in got
    ]
    assert [s.value.value for s in got] == [
        D("0.03"),
        D("0.1"),
        D(0),
        D(0),
    ]  # below low, above high, inside, not held
    assert got[1].value.source_ids == ("w",) and got[1].value.notes == ("n",)
    held = got[3].value  # a banded key that is not held: zero distance, said so
    assert (
        held.status is Q.OK
        and held.source_ids == ("ledger",)
        and held.notes == ("not held",)
        and abs(datetime.now(UTC) - held.as_of) < timedelta(minutes=1)
    )
    assert got[0].level is level_of(got[0].value, CFG.thresholds["band_breach"])


def test_correlation_rise_exact() -> None:
    miss = correlation_rise_signal(CFG, [], [mm("0.5")])
    assert (miss.id, miss.kind, miss.scope, miss.subject, miss.level) == (
        "correlation_rise:portfolio",
        "correlation_rise",
        Scope.PORTFOLIO,
        "portfolio",
        None,
    )
    assert miss.value.notes == ("not enough price history",)
    assert correlation_rise_signal(CFG, [mm("0.5")], []).value.value is None
    assert correlation_rise_signal(CFG, [mm("0.5", Q.QUARANTINED)], [mm("0.5")]).value.value is None
    assert correlation_rise_signal(CFG, [mm("0.5")], [mm(None)]).value.value is None

    short = [mm("0.9", Q.OK, T1, ("a",), ("na",)), mm("0.7", Q.STALE, T1)]
    long = [mm("0.2", Q.OK, T2, ("c",)), mm("0.4", Q.ESTIMATED, T2)]
    got = correlation_rise_signal(CFG, short, long)
    assert got.value == Measured(
        D("0.5"), Q.STALE, T2, ("a",), ("na",)
    )  # (0.8 - 0.3), worst status, oldest as-of, first's sources
    assert (got.id, got.kind, got.scope, got.subject) == (
        "correlation_rise:portfolio",
        "correlation_rise",
        Scope.PORTFOLIO,
        "portfolio",
    )
    assert got.level is level_of(got.value, CFG.thresholds["correlation_rise"])


# ---- classify_decline -------------------------------------------------------------------------------------


def cmp(
    a: str | None, b: str | None, corr: str | None = "0.9", a_status: Q = Q.OK, b_status: Q = Q.OK
) -> BenchmarkComparison:
    return BenchmarkComparison("B", mm(a, a_status), mm(b, b_status), mm("0"), mm("1"), mm(corr))


def test_classify_decline_every_branch_with_its_wording() -> None:
    no_bench = Decline("uncertain", "no usable benchmark comparison")
    assert classify_decline(CFG, None) == no_bench
    assert (
        classify_decline(CFG, cmp("-0.1", "-0.1", a_status=Q.QUARANTINED)) == no_bench
    )  # only the asset unusable
    assert (
        classify_decline(CFG, cmp("-0.1", "-0.1", b_status=Q.QUARANTINED)) == no_bench
    )  # only the benchmark unusable
    assert classify_decline(CFG, cmp("-0.1", None)) == no_bench
    flat = Decline("uncertain", "the asset has not fallen over the compared dates")
    assert (
        classify_decline(CFG, cmp("0.02", "-0.1")) == flat and classify_decline(CFG, cmp("0", "-0.1")) == flat
    )
    weak = Decline("uncertain", "the benchmark is not correlated enough to be a fair yardstick")
    assert classify_decline(CFG, cmp("-0.2", "-0.2", corr=None)) == weak
    r = CFG.rules
    below = str(r.min_correlation - D("0.01"))
    assert classify_decline(CFG, cmp("-0.2", "-0.2", corr=below)) == weak
    assert (
        classify_decline(CFG, cmp("-0.2", "-0.2", corr=str(r.min_correlation))).kind == "market-wide"
    )  # equal is enough
    assert classify_decline(CFG, cmp("-0.2", "-0.2")) == Decline(
        "market-wide", "the benchmark fell 100% as much as the asset"
    )
    assert classify_decline(CFG, cmp("-0.2", "0.04")) == Decline(
        "asset-specific", "the benchmark fell only 0% as much as the asset"
    )
    assert classify_decline(CFG, cmp("-0.2", "-0.02")) == Decline(
        "asset-specific", "the benchmark fell only 10% as much as the asset"
    )
    mid = classify_decline(CFG, cmp("-0.2", "-0.1"))
    assert mid == Decline("uncertain", "the benchmark fell 50% as much as the asset: in between")
    edge = classify_decline(CFG, cmp("-1", str(-r.market_wide_ratio)))
    assert edge.kind == "market-wide"  # the ratio at the limit counts as market-wide
    assert classify_decline(CFG, cmp("-1", str(-r.asset_specific_ratio))).kind == "asset-specific"


# ---- overall level and vetoes ----------------------------------------------------------------------------


def lv(
    id_: str, level: Level | None, kind: str = "k", scope: Scope = Scope.PORTFOLIO, asset: int | None = None
) -> Signal:
    return sig(id_, kind, scope, id_, mm("0.1"), level, asset)


def test_the_worst_band_breach_alone_counts_toward_the_level() -> None:
    rules = CFG.rules
    two = [
        lv("band_breach:a", Level.CAUTION, "band_breach"),
        lv("band_breach:b", Level.HIGH_RISK, "band_breach"),
    ]
    r = overall_level(two, rules)
    assert r.at_or_above == {"caution": 1, "high": 1, "severe": 0} and r.available == 1 and r.total == 2
    assert r.drivers == ("band_breach:b",)  # the lower one is dropped from the count, not from `total`
    tie = overall_level(
        [
            lv("band_breach:a", Level.CAUTION, "band_breach"),
            lv("band_breach:z", Level.CAUTION, "band_breach"),
        ],
        rules,
    )
    assert tie.drivers == ("band_breach:z",)  # equal levels: the later id is kept
    mixed = overall_level(
        [
            lv("band_breach:a", Level.HIGH_RISK, "band_breach"),
            lv("band_breach:b", Level.CAUTION, "band_breach"),
            lv("x", Level.CAUTION),
        ],
        rules,
    )
    assert mixed.drivers == ("band_breach:a", "x") and mixed.available == 2


def test_drivers_include_caution_and_missing_lists_unlevelled() -> None:
    r = overall_level([lv("a", Level.CAUTION), lv("b", Level.NORMAL), lv("c", None)], CFG.rules)
    assert r.drivers == ("a",) and r.missing == ("c",) and r.available == 2 and r.total == 3
    assert r.at_or_above == {"caution": 1, "high": 0, "severe": 0}


def test_a_veto_lists_every_severe_asset_specific_signal_of_the_asset() -> None:
    d = {1: Decline("asset-specific", "diverged")}
    s = [
        lv("s1", Level.SEVERE_STRESS, scope=Scope.ASSET, asset=1),
        lv("s2", Level.SEVERE_STRESS, scope=Scope.ASSET, asset=1),
    ]
    assert vetoes(s, d) == {
        1: ("severe asset-specific decline (s1): diverged", "severe asset-specific decline (s2): diverged")
    }


# ---- alerts -----------------------------------------------------------------------------------------------


def test_alert_fields_exact_for_each_scope() -> None:
    val = mm("0.25", Q.STALE, T1, ("src",))
    asset = Signal("holding_drawdown:A", "holding_drawdown", Scope.ASSET, "A", val, Level.HIGH_RISK, 4)
    market = Signal("idx_drawdown:N", "idx_drawdown", Scope.MARKET, "N", val, Level.HIGH_RISK)
    port = Signal(
        "top3_weight:portfolio", "top3_weight", Scope.PORTFOLIO, "portfolio", val, Level.SEVERE_STRESS
    )
    quiet = Signal("x", "x", Scope.MARKET, "x", val, Level.CAUTION)
    nothing = Signal("y", "y", Scope.MARKET, "y", mm(None), Level.SEVERE_STRESS)
    decl = {4: Decline("asset-specific", "diverged")}
    got = build_alerts(
        DAY, [asset, market, port, quiet, nothing], decl, {4: mm("1000")}, ("sc1", "sc2"), Level.HIGH_RISK
    )
    assert [a.key for a in got] == [
        "holding_drawdown:A:HIGH_RISK",
        "idx_drawdown:N:HIGH_RISK",
        "top3_weight:portfolio:SEVERE_STRESS",
    ]
    a, m_, p = got
    assert (a.level, a.assets, a.classification, a.sources) == (
        Level.HIGH_RISK,
        ("A",),
        "uncertain",
        ("src",),
    )
    assert a.what == "holding_drawdown:A is 25.00%: level HIGH_RISK (asset-specific: diverged)"
    assert a.impact_inr is not None and a.impact_inr.value == D("-250") and a.impact_inr.status is Q.STALE
    assert (
        a.evidence == ("signal:holding_drawdown:A:2026-11-10",)
        and a.when == DAY
        and a.scenarios == ("sc1", "sc2")
    )
    assert a.watch == "whether holding_drawdown:A moves to a different level at the next daily check"
    assert (
        a.if_nothing
        == "Nothing is executed automatically; the plan and any SIP continue unchanged until you decide."
    )
    assert a.responses == ("review thesis", "hold cash", "reduce only with clear evidence", "no action")
    assert (m_.assets, m_.impact_inr, m_.classification, m_.level, m_.what) == (
        (),
        None,
        "uncertain",
        Level.HIGH_RISK,
        "idx_drawdown:N is 25.00%: level HIGH_RISK",
    )
    assert m_.responses == (
        "continue the plan",
        "consider rebalancing via contributions",
        "accumulate only if justified",
        "no action",
    )
    assert (p.assets, p.level, p.sources, p.responses) == (
        (),
        Level.SEVERE_STRESS,
        ("src",),
        ("consider rebalancing via contributions", "review thesis", "hold cash", "no action"),
    )
    temp = build_alerts(DAY, [asset], {4: Decline("market-wide", "r")}, {}, (), Level.CAUTION)[0]
    assert temp.classification == "temporary" and temp.impact_inr is None  # no value for the asset: no impact


def test_diff_alerts_keeps_unchanged_and_cleared_apart() -> None:
    def al(key: str) -> Alert:
        return Alert(key, Level.CAUTION, "w", DAY, (), (), (), None, "uncertain", (), (), "", "")

    d = diff_alerts([al("a"), al("b")], {"b", "c"})
    assert [x.key for x in d.new] == ["a"] and d.unchanged == ("b",) and d.cleared == ("c",)


# ---- scenarios --------------------------------------------------------------------------------------------


def ex(
    i: int,
    cls: str,
    cur: str,
    value: str | None,
    sector: str | None = None,
    cat: str | None = None,
    status: Q = Q.OK,
) -> Exposure:
    return Exposure(i, f"A{i}", cls, cur, sector, mm(value, status if value else Q.MISSING, T1), cat)


def test_scenario_ids_labels_and_descriptions_are_exact() -> None:
    book = [ex(1, "EQUITY_IN", "INR", "1000", "Banks")]
    got = run_scenarios(book, DAY)
    assert [(s.id, s.label, s.description) for s in got] == [
        ("equity_-10", "Equity -10%", "All equity-like holdings fall 10%."),
        ("equity_-20", "Equity -20%", "All equity-like holdings fall 20%."),
        ("equity_-30", "Equity -30%", "All equity-like holdings fall 30%."),
        ("sector_shock", "Sector shock: Banks -30%", "The largest held sector (Banks) falls 30%."),
        (
            "usdinr_up_10",
            "USD/INR +10%",
            "The rupee weakens 10% against the dollar; only foreign holdings move.",
        ),
        (
            "usdinr_down_10",
            "USD/INR -10%",
            "The rupee strengthens 10% against the dollar; only foreign holdings move.",
        ),
        (
            "joint_india_us",
            "Joint India + US stress",
            "Indian and US equity both fall 20% (US in dollars) while the rupee weakens 5%.",
        ),
        ("commodity_reversal", "Commodity reversal", got[-1].description),
    ]
    assert set(dict(got[0].per_holding)["A1"].source_ids) == {"s", "scenario"}  # the shock is a named source


def test_the_largest_sector_is_the_sum_of_its_holdings_with_a_name_tie_break_last() -> None:
    book = [ex(1, "EQUITY_IN", "INR", "600", "Tech"), ex(2, "EQUITY_IN", "INR", "600", "Tech"), ex(3, "EQUITY_IN", "INR", "1201", "Banks"),
            ex(4, "MF", "INR", "5000", "Tech", "DEBT")]  # fmt: skip
    s = {r.id: r for r in run_scenarios(book, DAY)}["sector_shock"]
    assert s.label == "Sector shock: Banks -30%"  # 1201 beats 600 + 600; the debt fund is not counted
    assert dict(s.per_holding) == {n: v for n, v in s.per_holding} and dict(s.per_holding)["A3"].value == D(
        "-360.3"
    )
    assert dict(s.per_holding)["A1"].value == 0
    tie = {
        r.id: r
        for r in run_scenarios(
            [ex(1, "EQUITY_IN", "INR", "100", "Aa"), ex(2, "EQUITY_IN", "INR", "100", "Zz")], DAY
        )
    }["sector_shock"]
    assert tie.label == "Sector shock: Zz -30%"  # equal sums: the later name
    debt_in_top = {
        r.id: r
        for r in run_scenarios(
            [ex(1, "EQUITY_IN", "INR", "100", "Tech"), ex(2, "MF", "INR", "100", "Tech", "DEBT")], DAY
        )
    }["sector_shock"]
    assert dict(debt_in_top.per_holding)["A2"].value == 0 and dict(debt_in_top.per_holding)["A1"].value == D(
        "-30"
    )


def test_unvalued_holdings_are_listed_and_estimate_the_totals() -> None:
    book = [
        ex(1, "EQUITY_IN", "INR", "1000"),
        ex(2, "EQUITY_IN", "INR", None),
        ex(3, "EQUITY_US", "USD", None),
    ]
    r = run_scenarios(book, DAY)[0]
    note = "2 holding(s) have no usable value and are left out: A2, A3"
    assert r.total_inr.status is Q.ESTIMATED and note in r.total_inr.notes and note in r.total_pct.notes
    assert (
        r.total_pct.status is Q.ESTIMATED
        and r.total_inr.value == D(-100)
        and [n for n, _ in r.per_holding] == ["A1"]
    )


def test_an_empty_book_is_stamped_with_the_day() -> None:
    r = run_scenarios([], DAY)[0]
    assert r.total_inr.as_of == day_stamp(DAY) and r.total_inr.value == 0 and r.total_pct.value is None
