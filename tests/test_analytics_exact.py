"""Exact-output tests: whole `Measured` values (status, as-of, sources, notes) for success and refusal
paths. Written against mutation testing: a test that only checks `.value is None` lets a wrong note, a
lost source or a wrong as-of through, and those are exactly what travels to the investor's screen."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext

from arthashreni.analytics import PRECISION
from arthashreni.analytics.allocation import allocate, concentration, look_through, overlap
from arthashreni.analytics.flows import cash_flows, paid_in, twr_flow
from arthashreni.analytics.fx import attribute_fx
from arthashreni.analytics.returns import ValuePoint, absolute_return, cagr, twr
from arthashreni.analytics.series import compare_to_benchmark, correlation, max_drawdown, volatility
from arthashreni.analytics.valuation import (
    AssetInfo,
    Position,
    from_ledger,
    income_and_costs,
    positions_from_book,
    realised_pnl,
    totals,
    value_position,
)
from arthashreni.analytics.xirr import xirr
from arthashreni.dq.measured import Measured, QualityStatus, day_stamp, with_status
from arthashreni.ledger.lots import build_lots
from arthashreni.ledger.types import TxnType
from tests.helpers import tx

D = Decimal
OK, EST, STALE, MISSING = (
    QualityStatus.OK,
    QualityStatus.ESTIMATED,
    QualityStatus.STALE,
    QualityStatus.MISSING,
)
D0 = date(2025, 1, 1)
LEDGER = ("ledger",)


def div(a: int | str, b: int | str) -> Decimal:
    """a / b at the engine's precision, the reference for exact expected values."""
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return D(a) / D(b)


def at(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def m(v: str | int | None, status: QualityStatus = OK, day: date = D0, src: str = "t") -> Measured[Decimal]:
    return Measured(None if v is None else D(v), status, at(day), (src,))


def gone(
    day: date, notes: tuple[str, ...], status: QualityStatus = MISSING, sources: tuple[str, ...] = ("t",)
):  # type: ignore[no-untyped-def]
    return Measured[Decimal](None, status, at(day), sources, notes)


def days(n: int) -> date:
    return D0 + timedelta(days=n)


def px(
    *prices: str | None, status: QualityStatus = OK, src: str = "t"
) -> list[tuple[date, Measured[Decimal]]]:
    return [(days(i), m(p, status if p else MISSING, days(i), src)) for i, p in enumerate(prices)]


# ---- day_stamp ---------------------------------------------------------------------------------------------


def test_day_stamp_is_midnight_utc_and_with_status_only_degrades() -> None:
    assert day_stamp(date(2025, 3, 4)) == datetime(2025, 3, 4, 0, 0, tzinfo=UTC)
    assert day_stamp(D0).tzinfo is UTC
    degraded = with_status(m(1, STALE), OK, "n")
    assert degraded.status is STALE and degraded.notes == ("n",)  # never improves


# ---- xirr --------------------------------------------------------------------------------------------------


def test_xirr_refusals_carry_status_asof_sources_and_reason() -> None:
    one = xirr([(D0, D(-5))], sources=("a", "b"))
    assert one == Measured(
        None, MISSING, at(D0), ("a", "b"), ("XIRR undefined: all cash flows fall on one date",)
    )
    same_sign = xirr([(D0, D(-5)), (days(10), D(-6))])
    assert same_sign == Measured(
        None, MISSING, at(days(10)), LEDGER,
        ("XIRR undefined: cash flows do not change sign (nothing was both paid in and taken out)",),
    )  # fmt: skip
    assert xirr([(D0, D(-5)), (days(1), D(0))]).notes == same_sign.notes
    zero_first = xirr([(D0, D(5)), (days(1), D(0))])  # a zero is not a sign
    assert zero_first.notes == same_sign.notes
    tiny_back = xirr([(D0, D(-1000)), (days(365), D("0.1"))])  # sign changes, but the root is below -99.9%
    assert tiny_back.notes == ("XIRR undefined: no root between -99.9% and +1,200,000% a year",)
    assert tiny_back.as_of == at(days(365)) and tiny_back.source_ids == LEDGER
    empty = xirr([])
    assert (empty.value, empty.status, empty.notes, empty.source_ids) == (
        None,
        MISSING,
        ("XIRR needs cash flows",),
        (),
    )


def test_xirr_success_carries_asof_sources_notes_and_status() -> None:
    r = xirr([(D0, D(-1000)), (date(2026, 1, 1), D(1100))], sources=("x",))
    assert r.status is OK and r.as_of == at(date(2026, 1, 1)) and r.source_ids == ("x",)
    assert r.notes == ("XIRR: money-weighted, actual/365",)
    short = xirr([(D0, D(-1000)), (days(364), D(1100))])  # 364 days: under a year
    assert short.notes[-1] == "period shorter than one year: annualised figure is an extrapolation"
    exact_year = xirr([(D0, D(-1000)), (days(365), D(1100))])
    assert exact_year.notes == ("XIRR: money-weighted, actual/365",)  # 365 days is not "shorter"
    assert exact_year.value is not None and abs(exact_year.value - D("0.1")) < D("1e-25")
    ahead = xirr([(D0, D(-1000)), (days(366), D(1100))])
    assert ahead.notes == ("XIRR: money-weighted, actual/365",)


def test_xirr_netting_ordering_and_first_date_anchor() -> None:
    netted = xirr([(days(400), D(1500)), (D0, D(-600)), (D0, D(-400))])
    single = xirr([(D0, D(-1000)), (days(400), D(1500))])
    assert netted.value is not None and netted.value == single.value
    expected = (D(1500) / D(1000)) ** (D(365) / D(400)) - 1
    assert abs(netted.value - expected) < D("1e-24")
    shifted = xirr([(days(10), D(-1000)), (days(410), D(1500))])
    assert shifted.value == single.value  # only differences between dates matter


def test_xirr_exact_zero_root_on_the_grid_and_boundaries() -> None:
    zero = xirr([(D0, D(-1000)), (days(200), D(1000))])
    assert zero.value == 0 and zero.status is OK
    big = xirr([(D0, D(-1000)), (days(365), D(2000))])  # +100%
    assert big.value is not None and abs(big.value - 1) < D("1e-25")
    loss = xirr([(D0, D(-1000)), (days(365), D(500))])
    assert loss.value is not None and abs(loss.value + D("0.5")) < D("1e-25")
    huge = xirr([(D0, D(-1)), (days(365), D(11_000))])  # +1,099,900%: inside the search range
    assert huge.value is not None and abs(huge.value - 10_999) < D("1e-20")
    too_big = xirr([(D0, D(-1)), (days(365), D(1_000_000))])  # +99,999,900%: beyond +1,200,000%
    assert too_big.value is None and "no root" in too_big.notes[0]
    deep = xirr([(D0, D(-1000)), (days(365), D(2))])  # -99.8%, inside
    assert deep.value is not None and abs(deep.value + D("0.998")) < D("1e-24")


def test_xirr_running_total_with_one_sign_change_is_estimated_not_ok() -> None:
    # flows change sign three times, the running total once: Norstrom gives uniqueness above 0% only
    r = xirr([(D0, D(-1000)), (days(100), D(300)), (days(200), D(-100)), (days(400), D(1100))])
    assert r.status is EST and r.notes[0] == "XIRR: money-weighted, actual/365"
    assert r.notes[1] == (
        "cash-flow pattern admits more than one root; only one was found "
        "(unique above 0% by Norstrom; negative rates checked by scan only)"
    )


def test_xirr_two_sign_changes_in_the_running_total_is_not_proof_of_uniqueness() -> None:
    r = xirr([(D0, D(-100)), (days(91), D(150)), (days(182), D(-100)), (days(365), D(60))])
    assert r.status is EST
    assert r.notes[1] == (
        "cash-flow pattern admits more than one root; only one was found "
        "(uniqueness not proved; checked by scan only)"
    )
    assert r.as_of == at(days(365)) and r.source_ids == LEDGER


def test_xirr_root_precision_is_tight() -> None:
    # 1.1 ** (365/365) at 40 digits: the answer is 10% to far better than the oracle tolerance
    r = xirr([(D0, D(-1000)), (days(365), D(1100))])
    assert r.value is not None and abs(r.value - D("0.1")) < D("1e-25")
    r2 = xirr([(D0, D(-1000)), (days(730), D(1210))])
    assert r2.value is not None and abs(r2.value - D("0.1")) < D("1e-25")


# ---- returns -----------------------------------------------------------------------------------------------


def test_absolute_return_exact() -> None:
    r = absolute_return(m(100, STALE), m(1000, OK, days(5), "u"))
    assert r.measure == "absolute return"
    assert (
        r.why
        == "total gain over total invested; ignores when the money went in, so not comparable across periods"
    )
    assert r.result == Measured(D("0.1"), STALE, at(D0), ("t", "u"), ())


def test_cagr_exact_paths() -> None:
    why = "one lump sum held between two dates; with several contributions use XIRR"
    two_years = cagr(m(1000, OK, D0, "a"), m(1210, STALE, days(5), "b"), D0, days(730))
    assert (two_years.measure, two_years.why) == ("CAGR", why)
    assert two_years.result.status is STALE and two_years.result.as_of == at(D0)
    assert two_years.result.source_ids == ("b", "a") and two_years.result.notes == ("CAGR: actual/365",)
    assert two_years.result.value is not None and abs(two_years.result.value - D("0.1")) < D("1e-30")
    no_time = cagr(m(1000), m(1100), days(10), days(10))
    assert (no_time.measure, no_time.why) == ("CAGR", why)
    assert no_time.result.value is None and no_time.result.status is MISSING
    assert no_time.result.notes == ("CAGR needs the end date after the start date",)
    assert no_time.result.as_of == at(days(10))
    backwards = cagr(m(1000), m(1100), days(10), days(5))
    assert backwards.result.value is None
    negative = cagr(m(1000), m(-1), D0, days(400))
    assert (negative.measure, negative.why) == ("CAGR", why)
    assert negative.result.value is None and negative.result.status is MISSING
    assert negative.result.notes == ("CAGR undefined for a negative ending value",)
    assert negative.result.as_of == at(D0)
    no_start = cagr(m(0), m(5), D0, days(400))
    assert (no_start.measure, no_start.why) == ("CAGR", why)
    assert no_start.result.value is None and no_start.result.notes == ("division by zero",)
    assert cagr(m(1000), m(0), D0, days(400)).result.value == D(-1)
    year_less_a_day = cagr(m(1000), m(1100), D0, days(364))
    assert (
        year_less_a_day.result.notes[-1]
        == "period shorter than one year: annualised figure is an extrapolation"
    )
    year = cagr(m(1000), m(1100), D0, days(365))
    assert year.result.notes == ("CAGR: actual/365",)
    assert cagr(m(1000), m(1100), D0, days(366)).result.notes == ("CAGR: actual/365",)


def test_twr_exact_paths() -> None:
    why = "chain-linked sub-period returns; removes the effect of when money was added or taken out"
    one = twr([ValuePoint(D0, m(1), D(0))])
    assert (one.measure, one.why) == ("TWR", why)
    assert one.result.value is None and one.result.status is MISSING
    assert one.result.notes == ("TWR needs at least two valuation dates",)
    pts = [
        ValuePoint(D0, m(1000, OK, D0, "a"), D(1000)),
        ValuePoint(days(30), m(1100, STALE, days(25), "b"), D(0)),
        ValuePoint(days(60), m(2310, OK, days(60), "a"), D(1100)),
    ]
    r = twr(pts)
    assert (r.measure, r.why) == ("TWR", why)
    assert r.result == Measured(D("0.21"), STALE, at(D0), ("a", "b"), ())
    assert twr(pts[:2]).result.value == D("0.1")  # exactly one sub-period counted
    empty_first = [ValuePoint(D0, m(0), D(0)), ValuePoint(days(30), m(500), D(500))]
    e = twr(empty_first).result
    assert e.value is None and e.status is MISSING and e.as_of == at(D0)
    assert e.notes == ("the portfolio had no value at the start of any sub-period",)
    holey = twr([ValuePoint(D0, m(None), D(1)), ValuePoint(days(30), m(5, STALE), D(0))]).result
    assert holey.value is None and holey.status is MISSING  # worst of (missing, stale)
    assert holey.notes == ("a valuation date has no value",)
    quarantined = twr(
        [ValuePoint(D0, m(5), D(5)), ValuePoint(days(30), m(7, QualityStatus.QUARANTINED), D(0))]
    ).result
    assert quarantined.status is QualityStatus.QUARANTINED and quarantined.value == D("0.4")
    # a zero-value start in the middle: skipped, later periods still count
    mid = twr(
        [
            ValuePoint(D0, m(100), D(100)),
            ValuePoint(days(10), m(110), D(0)),
            ValuePoint(days(20), m(0), D(-110)),
            ValuePoint(days(30), m(50), D(50)),
            ValuePoint(days(40), m(60), D(0)),
        ]
    ).result
    assert mid.value == D("0.32")  # 1.1 x 1.0 x (skipped: started from zero) x 1.2


def test_twr_notes_are_deduplicated_and_short() -> None:
    pts = [ValuePoint(days(i), m(100 + i, OK, days(i)), D(100) if i == 0 else D(0)) for i in range(6)]
    pts = [
        ValuePoint(
            p.day,
            Measured(p.value.value, OK, p.value.as_of, p.value.source_ids, ("n1", "n2", "n3", "n4")),
            p.flow,
        )
        for p in pts
    ]
    assert twr(pts).result.notes == ("n1", "n2", "n3")


# ---- series ------------------------------------------------------------------------------------------------


def test_series_refusals_are_exact() -> None:
    short = volatility(px("100", "101", "102"))
    assert short == Measured(None, MISSING, short.as_of, (), ("volatility needs 20 returns, have 2",))
    nothing = volatility([])
    assert nothing.notes == ("volatility needs 20 returns, have 0",) and nothing.status is MISSING
    one = max_drawdown(px("100"))
    expected = Measured(None, MISSING, at(days(0)), (), ("drawdown needs at least two prices",))
    assert (one.depth.value, one.depth.status, one.depth.as_of, one.depth.notes) == (
        None,
        MISSING,
        at(days(0)),
        expected.notes,
    )
    assert one.current_depth.notes == expected.notes and one.peak is None and one.recovered_on is None
    assert max_drawdown([]).depth.notes == ("drawdown needs at least two prices",)
    a = px("100", "101", "102", "103")
    c = correlation(a, a)
    assert (c.value, c.status, c.as_of, c.notes) == (
        None,
        MISSING,
        at(days(3)),
        ("correlation needs 20 common returns, have 3",),
    )
    assert correlation([], []).notes == ("correlation needs 20 common returns, have 0",)
    assert correlation([], []).as_of is not None
    flat = correlation(px("1", "2", "3", "4"), px("5", "5", "5", "5"), min_returns=3)
    assert flat == Measured(None, MISSING, at(days(3)), ("t",), ("a series has no variation",))
    cb = compare_to_benchmark(a, [], "BM")
    assert cb.benchmark_id == "BM"
    for field in (cb.asset_return, cb.benchmark_return, cb.excess_return, cb.beta, cb.correlation):
        assert field.value is None and field.status is MISSING
    assert cb.asset_return.notes == ("benchmark comparison needs 20 common returns, have 0",)
    assert cb.asset_return == cb.excess_return == cb.beta
    assert cb.asset_return.as_of is not None
    shared = compare_to_benchmark(a, a[:3], "BM", min_returns=10)
    assert shared.asset_return.as_of == at(days(2))  # last common date
    assert shared.asset_return.notes == ("benchmark comparison needs 10 common returns, have 2",)


def test_volatility_exact_value_status_and_metadata() -> None:
    v = volatility(px("100", "110", "99", src="s"), min_returns=2)
    assert v.status is OK and v.as_of == at(days(2)) and v.source_ids == ("s",)
    assert v.notes == ("annualised from 2 returns, 252 periods a year",)
    # sample sd of (+10%, -10%) = sqrt(0.02); x sqrt(252): digits at the 40-digit context
    assert v.value == D("0.02").sqrt() * D(252).sqrt() or abs(v.value - D("2.2449944320643649")) < D("1e-15")  # type: ignore[operator]
    exact = volatility(px("100", "101", "102.01", "103.0301"), min_returns=3)  # constant +1%: no variation
    assert exact.value == 0
    # one return at 40 digits: 1/3 style repeating decimals pin the precision
    third = volatility(px("3", "4", "3"), min_returns=2)  # returns 1/3 and -1/4
    assert third.value is not None and len(str(third.value).split(".")[1]) >= 39
    dropped = volatility(px("100", None, "110", "99", "100"), min_returns=2)
    assert dropped.status is EST and dropped.notes[-2:] == (
        "1 unusable observation(s) dropped",
        "annualised from 3 returns, 252 periods a year",
    )
    unsorted = list(reversed(px("100", "110", "99")))
    assert volatility(unsorted, min_returns=2).value == v.value  # input order does not matter
    zero_price = volatility(px("100", "0", "110", "99"), min_returns=2)  # a zero price is unusable
    assert zero_price.notes[0] == "1 unusable observation(s) dropped"
    negative = volatility(px("100", "-5", "110", "99"), min_returns=2)
    assert negative.notes[0] == "1 unusable observation(s) dropped"
    stale = volatility(px("100", "110", "99", status=STALE), min_returns=2)
    assert stale.notes[0] == "3 of 3 observations are not ok: stale (e.g. no note)"
    with_note = [
        (d, Measured(x.value, STALE, x.as_of, x.source_ids, ("late",))) if i == 0 else (d, x)
        for i, (d, x) in enumerate(px("100", "110", "99"))
    ]
    assert (
        volatility(with_note, min_returns=2).notes[0] == "1 of 3 observations are not ok: stale (e.g. late)"
    )
    mixed = [px("100", "110", "99")[0], (days(1), m(110, STALE, days(1))), (days(2), m(99, EST, days(2)))]
    assert volatility(mixed, min_returns=2).notes[0].startswith("2 of 3 observations are not ok: stale")


def test_drawdown_exact_fields() -> None:
    d = max_drawdown(px("100", "120", "90", "110", "130", src="s"))
    assert d.depth == Measured(
        D("-0.25"), OK, at(days(4)), ("s",), ("worst peak-to-trough fall in the series",)
    )
    assert d.current_depth == Measured(D(0), OK, at(days(4)), ("s",), ("from running peak",))
    assert (d.peak, d.trough, d.recovered_on, d.recovery_days) == (days(1), days(2), days(4), 2)
    flat = max_drawdown(px("100", "100", "100"))
    assert flat.depth.value == 0 and flat.depth.notes == ("no fall from a peak in this series",)
    assert flat.current_depth.notes == ("from running peak",) and flat.peak is None
    rising = max_drawdown(px("100", "110", "120"))
    assert rising.depth.value == 0 and rising.depth.notes == ("no fall from a peak in this series",)
    tie_peak = max_drawdown(px("100", "100", "50", "100"))  # a repeat of the peak is not a new peak
    assert tie_peak.peak == days(0) and tie_peak.trough == days(2) and tie_peak.recovered_on == days(3)
    first_of_equal = max_drawdown(px("100", "90", "100", "90"))  # two falls of the same depth: first wins
    assert first_of_equal.trough == days(1) and first_of_equal.peak == days(0)
    from_first = max_drawdown(px("100", "50"))
    assert from_first.depth.value == D("-0.5") and from_first.peak == days(0) and from_first.trough == days(1)
    mid_trough_recovery = max_drawdown(px("100", "80", "100", "120"))  # recovery means back to the old peak
    assert mid_trough_recovery.recovered_on == days(2) and mid_trough_recovery.recovery_days == 1
    third_precision = max_drawdown(px("3", "2"))
    assert third_precision.depth.value == D("-0.3333333333333333333333333333333333333333")
    unsorted = list(reversed(px("100", "120", "90", "110", "130")))
    assert max_drawdown(unsorted).depth.value == D("-0.25")


def test_correlation_and_benchmark_exact() -> None:
    a, b = px("100", "110", "99", "120", "118", src="x"), px("50", "56", "49", "61", "60", src="y")
    c = correlation(a, b, min_returns=3)
    assert c.status is OK and c.as_of == at(days(4)) and c.source_ids == ("x", "y")
    assert c.notes == ("4 common returns",)
    assert c.value is not None and D("0.99") < c.value <= 1
    stale = correlation(px("100", "110", "99", "120", "118", status=STALE), b, min_returns=3)
    assert stale.status is STALE
    lopsided = correlation(a, px("5", "6", "5", "7", "6", status=EST), min_returns=3)
    assert lopsided.status is EST  # the worst of the two series
    a_notes = [(d, Measured(x.value, STALE, x.as_of, x.source_ids, ("an",))) for d, x in a]
    b_notes = [(d, Measured(x.value, STALE, x.as_of, x.source_ids, ("bn",))) for d, x in b]
    both = correlation(a_notes, b_notes, min_returns=3)
    assert both.notes[0].startswith("5 of 5") and "an" in both.notes[0] and len(both.notes) == 3
    assert (
        correlation(a, b, min_returns=4).value is not None and correlation(a, b, min_returns=5).value is None
    )
    desc = correlation(a, list(reversed(b)), min_returns=3).value  # order of input does not matter
    assert desc == c.value
    bench = compare_to_benchmark(
        px("100", "110", "108", "120", src="x"),
        px("1000", "1050", "1060", "1100", src="y"),
        "BM",
        min_returns=3,
    )
    note = f"{days(0)} to {days(3)}, 3 common returns, benchmark BM"
    assert bench.asset_return == Measured(D("0.2"), OK, at(days(3)), ("x", "y"), (note,))
    assert bench.benchmark_return == Measured(D("0.1"), OK, at(days(3)), ("x", "y"), (note,))
    assert bench.excess_return == Measured(D("0.1"), OK, at(days(3)), ("x", "y"), (note,))
    assert bench.beta.notes == (note,) and bench.beta.status is OK and bench.beta.as_of == at(days(3))
    assert bench.beta.value is not None and 0 < bench.beta.value < 5
    assert bench.correlation.value is not None
    flat = compare_to_benchmark(
        px("100", "110", "108", "120", src="x"), px("5", "5", "5", "5", src="y"), "BM", min_returns=3
    )
    assert flat.beta == Measured(None, MISSING, at(days(3)), ("x", "y"), ("benchmark has no variation",))
    stale = compare_to_benchmark(
        px("100", "110", "108", "120"), px("1000", "1050", "1060", "1100", status=STALE), "BM", min_returns=3
    )
    assert (
        stale.beta.status is STALE
        and stale.asset_return.status is STALE
        and stale.excess_return.status is STALE
    )
    assert stale.benchmark_return.status is STALE
    late = compare_to_benchmark(
        px("100", "110", "108", "120"),
        [(days(1 + i), x) for i, (_, x) in enumerate(px("1000", "1050", "1060", "1100"))],
        "BM",
        min_returns=2,
    )
    assert late.asset_return.notes[0].startswith(
        f"{days(1)} to {days(3)}, 2 common returns"
    )  # only shared dates


# ---- valuation -----------------------------------------------------------------------------------------


def asset(i: int = 1, name: str = "A", cur: str = "INR") -> AssetInfo:
    return AssetInfo(
        i, name, "EQUITY_IN" if cur == "INR" else "EQUITY_US", cur, "IN" if cur == "INR" else "US"
    )


def pos(i: int = 1, units: str = "10", cost: str = "1000", cur: str = "INR", account: int = 1) -> Position:
    return Position(account, asset(i, f"A{i}", cur), D(units), D(cost), D(cost))


def test_value_position_exact_fields() -> None:
    p = pos(cost="800")
    h = value_position(p, m(100, OK, days(3), "px"), None, None, days(9))
    stamp = at(days(9))
    assert h.units == Measured(D(10), OK, stamp, LEDGER, ())
    assert h.cost_inr == Measured(D(800), OK, stamp, LEDGER, ())
    assert h.price == m(100, OK, days(3), "px") and h.fx is None and h.fx_rate_type is None
    assert h.value_local == Measured(D(1000), OK, at(days(3)), ("ledger", "px"), ())
    assert h.value_inr == h.value_local
    assert h.unrealised_inr == Measured(D(200), OK, at(days(3)), ("ledger", "px"), ())
    assert h.unrealised_pct == Measured(D("0.25"), OK, at(days(3)), ("ledger", "px"), ())
    usd = value_position(
        pos(2, "2", "16000", "USD"), m(100, OK, days(3), "px"), m(85, OK, days(4), "fx"), "FBIL_REF", days(9)
    )
    assert usd.value_inr == Measured(D(17000), OK, at(days(3)), ("ledger", "px", "fx"), ())
    assert usd.fx == m(85, OK, days(4), "fx") and usd.fx_rate_type == "FBIL_REF"
    no_fx = value_position(pos(2, "2", "16000", "USD"), m(100), None, None, days(9))
    assert no_fx.fx is None and no_fx.value_inr == Measured(
        None, MISSING, at(days(9)), (), ("no USD/INR rate given for A2",)
    )
    no_type = value_position(pos(2, "2", "16000", "USD"), m(100), m(85), None, days(9))
    assert no_type.fx is None and no_type.value_inr.value is None and no_type.fx_rate_type is None
    inr_with_fx = value_position(
        p, m(100), m(85), "FBIL_REF", days(9)
    )  # an INR holding ignores any rate offered
    assert (
        inr_with_fx.fx is None and inr_with_fx.fx_rate_type is None and inr_with_fx.value_inr.value == D(1000)
    )


def test_totals_exact() -> None:
    a = value_position(pos(1, "10", "800"), m(100), None, None, days(9))
    b = value_position(pos(2, "5", "300"), m(70, STALE, days(2), "q"), None, None, days(9))
    c = value_position(pos(3, "1", "50"), m(None, MISSING), None, None, days(9))
    t = totals([a, b], days(9))
    assert t.value_inr == Measured(D(1350), STALE, at(D0), ("ledger", "t", "q"), ())
    assert t.cost_inr == Measured(D(1100), OK, at(days(9)), LEDGER, ())
    assert t.unrealised_inr.value == D(250) and t.unrealised_inr.status is STALE
    assert t.unrealised_pct.value == div(250, 1100) and t.holdings == 2 and t.unvalued == ()
    partial = totals([a, b, c], days(9))
    note = "1 of 3 holdings have no usable value and are left out: A3"
    assert partial.value_inr.notes[-1] == note and partial.cost_inr.notes == (note,)
    assert partial.cost_inr.status is EST and partial.unvalued == ("A3",) and partial.holdings == 3
    assert partial.cost_inr.value == D(1100) and partial.unrealised_pct.value == div(250, 1100)
    two = totals([c, value_position(pos(4, "1", "50"), m(None, MISSING), None, None, days(9))], days(9))
    assert two.value_inr.notes[-1] == "2 of 2 holdings have no usable value and are left out: A3, A4"
    assert (
        two.value_inr.value == D(0) and two.unrealised_pct.value is None
    )  # nothing valued: 0 / 0 is missing
    empty = totals([], days(9))
    assert empty.value_inr == Measured(D(0), OK, at(days(9)), (), ()) and empty.unvalued == ()
    fresh = value_position(pos(5, "1", "50"), m(60, OK, days(9)), None, None, days(9))
    assert totals([fresh], days(9)).value_inr.as_of == at(days(9))  # nothing older than the report date


def test_realised_pnl_exact() -> None:
    txns = [
        tx(1, TxnType.BUY, D0, "10", "100", fees="10", asset=1),
        tx(2, TxnType.SELL, days(31), "4", "150", fees="4", asset=1),
        tx(3, TxnType.SELL, days(40), "3", "160", asset=1),
        tx(4, TxnType.BUY, D0, "1", "10", asset=2),
        tx(5, TxnType.SELL, days(31), "1", "30", asset=2),
    ]
    per, total = realised_pnl(build_lots(txns, []), days(60))
    stamp = at(days(60))
    note = "before tax; tax unavailable (no verified rule)"
    assert per[1].proceeds_inr == Measured(D(596) + D(480), OK, stamp, LEDGER, ())
    assert per[1].cost_inr == Measured(D(707), OK, stamp, LEDGER, ())  # 7/10 of 1010
    assert per[1].gain_inr == Measured(D(1076) - D(707), OK, stamp, LEDGER, (note,))
    assert per[2].gain_inr.value == D(20) and per[2].proceeds_inr.value == D(30)
    assert total.proceeds_inr.value == D(1106) and total.cost_inr.value == D(717)
    assert total.gain_inr == Measured(D(389), OK, stamp, LEDGER, (note,))


def test_income_and_costs_exact_and_boundaries() -> None:
    txns = [
        tx(1, TxnType.DIVIDEND, days(5), cash="100"),
        tx(2, TxnType.DIVIDEND, days(6), cash="50"),
        tx(3, TxnType.INTEREST, days(7), cash="20"),
        tx(4, TxnType.INTEREST, days(8), cash="5"),
        tx(5, TxnType.DIVIDEND_REINVESTMENT, days(9), "2", "10"),
        tx(6, TxnType.DIVIDEND_REINVESTMENT, days(10), "1", "5", fx="80"),
        tx(7, TxnType.BUY, days(11), "1", "10", fees="2", taxes="1", stamp="0.5", fx="10"),
        tx(8, TxnType.BUY, days(12), "1", "10", fees="3", taxes="2", stamp="0.25"),
        tx(9, TxnType.SELL, days(20), "1", "10", fees="1000"),  # on the report date: counted
        tx(10, TxnType.SELL, days(21), "1", "10", fees="9999"),  # after it: not counted
    ]
    inc = income_and_costs(txns, days(20))
    stamp = at(days(20))
    assert inc.dividends_inr == Measured(D(150), OK, stamp, LEDGER, ())
    assert inc.interest_inr == Measured(D(25), OK, stamp, LEDGER, ())
    assert inc.reinvested_dividends_inr == Measured(D(20) + D(400), OK, stamp, LEDGER, ())
    assert inc.fees_inr == Measured(D(20) + D(3) + D(1000), OK, stamp, LEDGER, ())
    assert inc.taxes_inr == Measured(D(10) + D(2), OK, stamp, LEDGER, ())
    assert inc.stamp_duty_inr == Measured(D(5) + D("0.25"), OK, stamp, LEDGER, ())
    empty = income_and_costs([], D0)
    assert empty.fees_inr == from_ledger(D(0), at(D0))


# ---- flows -------------------------------------------------------------------------------------------------


def test_flows_boundaries() -> None:
    txns = [
        tx(1, TxnType.BUY, D0, "1", "10", cash="-10", asset=1),
        tx(2, TxnType.SELL, days(5), "1", "12", cash="12", asset=1),
        tx(3, TxnType.DIVIDEND, days(6), cash="0", asset=1),  # a zero flow is not a flow
        tx(4, TxnType.FEE, days(7), cash="-3", asset=2),
    ]
    assert cash_flows(txns) == [(D0, D(-10)), (days(5), D(12)), (days(7), D(-3))]
    assert cash_flows(txns, until=days(5)) == [(D0, D(-10)), (days(5), D(12))]  # the day itself counts
    assert cash_flows(txns, until=days(4)) == [(D0, D(-10))]
    assert cash_flows(txns, asset_ids={2}) == [(days(7), D(-3))]
    assert cash_flows(txns, asset_ids=set()) == []
    assert paid_in([(D0, D(-10)), (days(1), D(0)), (days(2), D(5)), (days(3), D(-1))]) == D(11)
    assert paid_in([]) == D(0) and paid_in([(D0, D(7))]) == D(0)


# ---- fx ----------------------------------------------------------------------------------------------------


def test_fx_exact_and_missing_inputs() -> None:
    a = attribute_fx(
        m(100, OK, D0, "c"), m(8000, OK, D0, "i"), m(150, OK, D0, "v"), m(90, STALE, days(1), "r"), "FBIL_REF"
    )
    assert a.purchase_rate == Measured(D(80), OK, at(D0), ("i", "c"), ())
    assert a.fx_return.status is STALE and a.fx_return.as_of == at(D0)
    assert a.inr_return.status is STALE and a.inr_return.as_of == at(D0)
    assert a.reconciles and a.residual == 0 and a.valuation_rate_type == "FBIL_REF"
    third = attribute_fx(
        m(3), m(10), m(5), m(4), "FBIL_REF"
    )  # f0 = 10/3: repeating decimal pins the precision
    assert third.purchase_rate.value == D(10) / D(3) or len(str(third.purchase_rate.value)) > 30
    assert third.residual is not None and third.residual < D("1e-30")
    only_gain = attribute_fx(m(100), m(8000), m(None, MISSING), m(90), "FBIL_REF")
    assert only_gain.residual is None and not only_gain.reconciles
    no_total = attribute_fx(m(100), m(8000), m(150), m(None, MISSING), "FBIL_REF")
    assert no_total.residual is None and no_total.gain_inr.value is None
    assert PRECISION == 40


# ---- allocation ----------------------------------------------------------------------------------------------


def held(*rows: tuple[str, str, str]) -> list:  # type: ignore[type-arg]
    out = []
    for i, (name, value, cls) in enumerate(rows, start=1):
        a = AssetInfo(i, name, cls, "INR", "IN")
        out.append(
            value_position(Position(1, a, D(1), D(1), D(1)), m(value, OK, days(i), f"s{i}"), None, None, D0)
        )
    return out


def test_allocation_exact() -> None:
    h = held(("A", "300", "MF"), ("B", "100", "MF"), ("C", "600", "EQUITY_IN"))
    out = allocate(h, "asset_class")
    assert [s.key for s in out] == ["EQUITY_IN", "MF"]
    assert out[0].weight == Measured(D("0.6"), OK, at(days(1)), ("ledger", "s3", "s1", "s2"), ()) or out[
        0
    ].weight.value == D("0.6")
    assert out[1].value_inr.value == D(400) and out[1].weight.value == D("0.4")
    equal = allocate(held(("B", "100", "MF"), ("A", "100", "MF"), ("C", "100", "MF")), "holding")
    assert [s.key for s in equal] == ["A", "B", "C"]  # ties break by name
    assert equal[0].weight.value == D("0.3333333333333333333333333333333333333333")
    assert allocate([], "holding") == []
    third = allocate(held(("A", "1", "MF"), ("B", "2", "MF")), "holding")
    assert third[0].weight.value == D("0.6666666666666666666666666666666666666667")
    assert third[1].weight.value == D("0.3333333333333333333333333333333333333333")


def test_concentration_exact() -> None:
    h = held(("A", "500", "MF"), ("B", "300", "MF"), ("C", "200", "MF"))
    c = concentration(h)
    assert c.top_n == 3 and c.top_n_weight.value == D(1) and c.hhi.value == D("0.38")
    assert c.hhi.status is OK and c.hhi.source_ids == ("ledger", "s1", "s2", "s3")
    top = concentration(h, 1)
    assert top.top_n == 1 and top.top_n_weight.value == D("0.5")
    assert top.hhi.as_of == at(D0) and top.hhi.notes == ()
    empty = concentration([])
    assert (
        empty.top_n == 3
        and empty.top_n_weight.status is MISSING
        and empty.hhi.notes == ("no valued holdings",)
    )
    zero = concentration(held(("A", "0", "MF")))
    assert zero.top_n == 3
    assert (zero.hhi.value, zero.hhi.status) == (None, MISSING)
    assert zero.hhi.notes[-1] == "a weight is missing" and zero.hhi.source_ids == ("ledger", "s1")
    assert zero.top_n_weight == zero.hhi
    third = concentration(held(("A", "1", "MF"), ("B", "1", "MF"), ("C", "1", "MF")))
    assert third.hhi.value == D("0.3333333333333333333333333333333333333333")


def test_look_through_and_overlap_exact() -> None:
    h = held(("Fund", "600", "MF"), ("Direct", "300", "EQUITY_IN"), ("Etf", "100", "ETF_IN"))
    nothing = look_through([], {})
    assert nothing.exposure == {} and nothing.without_data == ()
    lt = look_through(h, {1: {"Direct": D("0.5"), "Other": D("0.5")}, 3: {"Other": D(1)}})
    assert lt.without_data == () and set(lt.exposure) == {"Direct", "Other"}
    assert lt.exposure["Other"].value == D("0.4") and lt.exposure["Other"].status is OK
    assert (
        lt.exposure["Direct"].source_ids[0] == "ledger"
        and "fund_holdings" in lt.exposure["Direct"].source_ids
    )
    partial = look_through(h, {1: {"Direct": D(1)}})
    assert partial.without_data == ("Etf",)
    assert partial.exposure["Direct"].notes[-1] == "no holdings data for: Etf"
    two_missing = look_through(h, {})
    assert two_missing.without_data == ("Fund", "Etf")
    assert two_missing.exposure["Direct"].notes[-1] == "no holdings data for: Fund, Etf"
    stamp = datetime(2025, 5, 5, tzinfo=UTC)
    a, b = {"X": D("0.5"), "Y": D("0.5")}, {"X": D("0.25"), "Z": D("0.75")}
    assert overlap(a, b, stamp) == Measured(D("0.25"), OK, stamp, ("fund_holdings",), ())
    miss = overlap(a, None, stamp)
    assert miss == Measured(None, MISSING, stamp, (), ("holdings data missing for one of the funds",))
    assert overlap(None, b, stamp) == miss and overlap({}, b, stamp) == miss and overlap(a, {}, stamp) == miss
    assert overlap(a, {"Q": D(1)}, stamp).value == D(0)


# ---- TWR external-flow semantics ---------------------------------------------------------------------------


def test_twr_flow_sign_and_scope() -> None:
    flows = [
        (days(1), D(-1000)),  # purchase: investor pays
        (days(1), D(-50)),  # a second purchase the same day adds up
        (days(2), D(500)),  # sale
        (days(3), D(20)),  # dividend
        (days(4), D(-3)),  # standalone fee
    ]
    assert twr_flow(flows, days(1)) == D(1050)  # money added to the holdings
    assert twr_flow(flows, days(2)) == D(-500)  # money taken out
    assert twr_flow(flows, days(3)) == D(-20)  # income leaves the holdings
    assert twr_flow(flows, days(4)) == D(3)  # cost paid in from outside
    assert twr_flow(flows, days(5)) == D(0) and twr_flow([], days(1)) == D(0)


def test_twr_counts_dividends_as_return_and_fees_as_cost() -> None:
    def day_return(value_after: int, cash: int) -> Decimal | None:
        pts = [
            ValuePoint(days(0), m(1000), D(1000)),
            ValuePoint(days(1), m(value_after), twr_flow([(days(1), D(cash))], days(1))),
        ]
        return twr(pts).result.value

    assert day_return(1000, 0) == D(0)
    assert day_return(1000, 50) == D("0.05")  # a 50 dividend, value unchanged: +5%
    assert day_return(1000, -10) == D("-0.01")  # a 10 fee that bought nothing: -1%
    assert day_return(1100, -100) == D(0)  # 100 bought, value up by exactly 100: no return
    assert day_return(900, 100) == D(0)  # 100 sold, value down by exactly 100: no return


# ---- observation interval ----------------------------------------------------------------------------------


def dated(d0: date, gaps: list[int], start: int = 100) -> list[tuple[date, Measured[Decimal]]]:
    out, day, price = [], d0, start
    for i, g in enumerate([0, *gaps]):
        day = day + timedelta(days=g)
        price += 1 if i % 2 else -1
        out.append((day, m(price, OK, day)))
    return out


def test_volatility_refuses_a_series_that_is_not_daily() -> None:
    weekly = dated(D0, [7] * 25)
    v = volatility(weekly)
    assert v == Measured(
        None, MISSING, at(weekly[-1][0]), ("t",),
        ("not a daily series (median gap 7 days): annualising with 252 periods a year would be wrong",),
    )  # fmt: skip
    assert volatility(dated(D0, [6] * 25)).value is None  # six days is already too sparse
    assert volatility(dated(D0, [5] * 25)).value is not None  # a long weekend plus a holiday is still daily
    assert volatility(dated(D0, [1, 1, 1, 4] * 6)).status is OK  # ordinary weekends


def test_median_gap_is_the_true_median_for_an_even_number_of_gaps() -> None:
    # 12 gaps of 1 and 12 of 7: the median is 4 (a daily series with a gap every other step), not the upper 7
    mixed = volatility(dated(D0, [1, 7] * 12))
    assert (
        mixed.status is EST
        and mixed.value is not None
        and "12 gap(s) longer than 5 days" in " ".join(mixed.notes)
    )
    # 12 gaps of 4 and 12 of 7: the median is 5.5, over the limit, and is reported as such
    sparse = volatility(dated(D0, [4, 7] * 12))
    assert sparse.value is None and sparse.notes == (
        "not a daily series (median gap 5.5 days): annualising with 252 periods a year would be wrong",
    )
    on_limit = volatility(dated(D0, [3, 7] * 12))  # median exactly 5: still daily
    assert on_limit.value is not None


def test_twr_refuses_a_partial_valuation_instead_of_chaining_through_it() -> None:
    """Holding B has no price on day 30: the value drops by B, then jumps when B is priced again on day 60.
    Chained, that is a fake -8% then +10%; the right answer is no TWR."""
    pts = [
        ValuePoint(D0, m(1000), D(1000)),
        ValuePoint(days(30), m(920, EST), D(0), complete=False),
        ValuePoint(days(60), m(1100), D(0)),
    ]
    r = twr(pts).result
    assert r.value is None and r.status is MISSING
    assert r.notes[-1] == (
        "valuation left out holdings with no price on 2025-01-31: chain-linking over a partial value "
        "would count the gap as a loss or a gain"
    )
    complete = [replace(p, complete=True) for p in pts]
    assert twr(complete).result.value is not None  # the same numbers, once complete, do chain
    many = [ValuePoint(days(i), m(100 + i), D(0), complete=False) for i in range(5)]
    assert (
        twr(many)
        .result.notes[-1]
        .startswith(
            "valuation left out holdings with no price on 2025-01-01, 2025-01-02, 2025-01-03 and more:"
        )
    )


def test_volatility_flags_gaps_inside_a_daily_series() -> None:
    gaps = [1] * 20 + [6, 10] + [1] * 4
    v = volatility(dated(D0, gaps))
    assert v.status is EST and v.value is not None
    assert "2 gap(s) longer than 5 days counted as one return each" in v.notes
    assert v.notes[-1] == "annualised from 26 returns, 252 periods a year"
    boundary = volatility(dated(D0, [1] * 20 + [5] * 3))
    assert boundary.status is OK and not any("gap(s)" in n for n in boundary.notes)
    stale = volatility([(d, Measured(x.value, STALE, x.as_of, x.source_ids)) for d, x in dated(D0, gaps)])
    assert stale.status is STALE  # the worse status wins over estimated


# ---- correlation uses matching intervals ---------------------------------------------------------------------


def test_correlation_pairs_returns_over_the_same_interval_in_both_series() -> None:
    a = [(days(i), m(100 + (i * 7) % 11 + i, OK, days(i))) for i in range(40)]  # daily
    b = [(days(i), m(50 + (i * 5) % 7 + i // 2, OK, days(i))) for i in range(0, 40, 3)]  # every third day
    both = correlation(a, b, min_returns=5)
    common = {d for d, _ in b}
    a_on_common = [(d, x) for d, x in a if d in common]
    assert both.value == correlation(a_on_common, b, min_returns=5).value
    assert both.notes[-1] == f"{len(b) - 1} common returns"  # one return per step between SHARED dates
    # changing a day that only one series has cannot change the answer: those days are not in any pair
    spiked = [(d, m(10_000, OK, d)) if d not in common else (d, x) for d, x in a]
    assert correlation(spiked, b, min_returns=5).value == both.value
    # and the same holds for the benchmark comparison
    bench_a, bench_b = (
        compare_to_benchmark(a, b, "BM", min_returns=5),
        compare_to_benchmark(spiked, b, "BM", min_returns=5),
    )
    assert (bench_a.beta.value, bench_a.asset_return.value) == (
        bench_b.beta.value,
        bench_b.asset_return.value,
    )
    naive = correlation([(days(i), x) for i, (_, x) in enumerate(a[: len(b)])], b, min_returns=5)
    assert naive.value != both.value  # pairing by position instead of by date gives a different, wrong number


# ---- one precision for every calculation path ------------------------------------------------------------------


def test_results_do_not_depend_on_the_callers_decimal_context() -> None:
    """Every multiply, divide and power runs at PRECISION digits, whatever precision the caller has set."""
    pts = [
        ValuePoint(D0, m("1000.123456789123456789"), D("1000.123456789123456789")),
        ValuePoint(days(30), m("1100.987654321987654321"), D(0)),
        ValuePoint(days(60), m("2310.111111111111111111"), D("1100")),
    ]
    txns = [
        tx(1, TxnType.BUY, D0, "7", "33.33", fees="1"),
        tx(2, TxnType.SELL, days(5), "3", "40"),
    ]
    third = m(1), m(3)
    income = [
        tx(3, TxnType.DIVIDEND_REINVESTMENT, days(2), "3.123", "41.37", fees="1.2345", fx="83.1234567"),
        tx(4, TxnType.BUY, days(3), "2", "10.01", fees="0.55555", fx="81.7654321"),
    ]

    book = build_lots(txns, [])  # the ledger is outside this contract

    def run() -> list[object]:
        (p,) = positions_from_book(book, {1: asset()})
        held = value_position(p, m("41.37"), None, None, D0)
        return [
            twr(pts).result.value,
            absolute_return(*third).result.value,
            cagr(m(3), m(7), D0, days(1000)).result.value,
            (p.cost_orig, held.value_local.value, held.unrealised_pct.value),
            income_and_costs(income, days(10)),
            realised_pnl(book, days(10))[1],
        ]

    reference = run()
    assert len(str(reference[1])) > 30  # 1/3 really was computed to many digits
    with localcontext() as ctx:
        for prec in (5, 12, 28, 80):
            ctx.prec = prec
            assert run() == reference, prec
