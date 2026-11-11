"""Pure calculation engine (Phase 4, F1): hand-computed expectations, status propagation, refusals."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from arthashreni.analytics import PRECISION
from arthashreni.analytics.allocation import allocate, concentration, look_through, overlap
from arthashreni.analytics.benchmarks import BENCHMARKS, benchmark_for
from arthashreni.analytics.flows import cash_flows, has_internal_moves, paid_in
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
from arthashreni.dq.measured import Measured, QualityStatus, m_sum
from arthashreni.ledger.lots import build_lots
from arthashreni.ledger.types import ActionType, TxnType
from tests.helpers import ca, tx

D = Decimal
OK, STALE, EST = QualityStatus.OK, QualityStatus.STALE, QualityStatus.ESTIMATED
D0 = date(2025, 1, 1)
STAMP = datetime(2025, 6, 30, tzinfo=UTC)


def m(v: str | int | None, status: QualityStatus = OK, day: date = D0, *notes: str) -> Measured[Decimal]:
    return Measured(
        None if v is None else D(v), status, datetime.combine(day, datetime.min.time(), UTC), ("t",), notes
    )


def asset(i: int = 1, name: str = "A", cls: str = "EQUITY_IN", cur: str = "INR", **kw: str) -> AssetInfo:
    return AssetInfo(i, name, cls, cur, "IN" if cur == "INR" else "US", kw.get("sector"), kw.get("cap"))


# ---- returns -----------------------------------------------------------------------------------------


def test_absolute_return_names_itself_and_divides() -> None:
    r = absolute_return(m(100), m(1000))
    assert r.measure == "absolute return" and "ignores when the money went in" in r.why
    assert r.result.value == D("0.1")
    assert absolute_return(m(100), m(0)).result.value is None  # division by zero is missing, not an error


def test_cagr_two_years_is_the_square_root() -> None:
    r = cagr(m(1000), m(1210), date(2025, 1, 1), date(2027, 1, 1))  # exactly 730 days
    assert r.measure == "CAGR" and r.result.value is not None
    assert abs(r.result.value - D("0.1")) < D("1e-30") and r.result.status is OK


def test_cagr_refusals_notes_and_status() -> None:
    assert cagr(m(1000), m(1100), D0, D0).result.value is None  # no time passed
    assert cagr(m(1000), m(-5), D0, date(2026, 1, 1)).result.value is None  # negative ending value
    assert cagr(m(0), m(5), D0, date(2026, 1, 1)).result.value is None  # no start value
    short = cagr(m(1000), m(1010), D0, D0 + timedelta(days=30)).result
    assert short.value is not None and any("shorter than one year" in n for n in short.notes)
    stale = cagr(m(1000), m(1100, STALE), D0, date(2026, 1, 1)).result
    assert stale.status is STALE and stale.value == pytest.approx(D("0.1"))
    assert cagr(m(1000), m(0), D0, date(2026, 1, 1)).result.value == D(-1)  # wiped out


def test_twr_removes_the_effect_of_when_money_went_in() -> None:
    pts = [
        ValuePoint(date(2025, 1, 1), m(1000), D(1000)),  # first contribution
        ValuePoint(date(2025, 2, 1), m(1100), D(0)),  # +10%
        ValuePoint(date(2025, 3, 1), m(2310), D(1100)),  # added 1100, then +10% on 1100
    ]
    r = twr(pts)
    assert r.measure == "TWR" and "removes the effect" in r.why
    assert r.result.value == pytest.approx(D("0.21"))  # 1.1 * 1.1 - 1
    assert twr(list(reversed(pts))).result.value == r.result.value  # order of input does not matter


def test_twr_skips_a_period_that_starts_empty_and_refuses_when_nothing_can_be_measured() -> None:
    empty_start = [ValuePoint(date(2025, 1, 1), m(0), D(0)), ValuePoint(date(2025, 2, 1), m(500), D(500))]
    assert twr(empty_start).result.value is None  # nothing was invested at the start of any period
    assert twr([ValuePoint(D0, m(1), D(0))]).result.value is None  # needs two dates
    gap = [ValuePoint(D0, m(None), D(1)), ValuePoint(date(2025, 2, 1), m(5), D(0))]
    r = twr(gap).result
    assert r.value is None and r.status is QualityStatus.MISSING


def test_twr_inherits_the_worst_status_and_oldest_as_of() -> None:
    pts = [
        ValuePoint(D0, m(100), D(100)),
        ValuePoint(date(2025, 2, 1), m(110, STALE, date(2025, 1, 30)), D(0)),
    ]
    r = twr(pts).result
    assert r.status is STALE and r.as_of == datetime(2025, 1, 1, tzinfo=UTC) and r.source_ids == ("t",)


# ---- flows ---------------------------------------------------------------------------------------------


def _ledger() -> list:  # type: ignore[type-arg]
    return [
        tx(1, TxnType.BUY, D0, "10", "100", cash="-1000"),
        tx(2, TxnType.DIVIDEND, date(2025, 3, 1), cash="20"),
        tx(3, TxnType.DEPOSIT, date(2025, 3, 2), asset=None, cash="5000"),  # bank to broker: not a result
        tx(4, TxnType.SELL, date(2025, 4, 1), "4", "120", cash="480", asset=2),
        tx(5, TxnType.DIVIDEND_REINVESTMENT, date(2025, 5, 1), "1", "100"),
        tx(6, TxnType.FEE, date(2025, 6, 1), asset=None, cash="-7"),
    ]


def test_cash_flows_count_only_investment_results() -> None:
    txns = _ledger()
    assert cash_flows(txns) == [
        (D0, D(-1000)), (date(2025, 3, 1), D(20)), (date(2025, 4, 1), D(480)), (date(2025, 6, 1), D(-7)),
    ]  # fmt: skip
    assert cash_flows(txns, until=date(2025, 3, 31)) == [(D0, D(-1000)), (date(2025, 3, 1), D(20))]
    assert cash_flows(txns, asset_ids={1}) == [(D0, D(-1000)), (date(2025, 3, 1), D(20))]
    assert cash_flows(txns, asset_ids={1, 2})[2] == (date(2025, 4, 1), D(480))
    assert paid_in(cash_flows(txns)) == D(1007)


def test_internal_unit_moves_are_detected_per_asset() -> None:
    txns = _ledger()
    assert has_internal_moves(txns) and has_internal_moves(txns, {1})
    assert not has_internal_moves(txns, {2}) and not has_internal_moves([])


def test_ipo_block_and_refund_net_to_the_allotment_cost() -> None:
    txns = [
        tx(1, TxnType.IPO_APPLICATION, D0, "100", "150"),
        tx(2, TxnType.IPO_BLOCK, D0, cash="-15000"),
        tx(3, TxnType.IPO_ALLOTMENT, date(2025, 1, 8), "40", "150"),
        tx(4, TxnType.IPO_REFUND, date(2025, 1, 8), cash="9000"),
    ]
    assert sum((c for _, c in cash_flows(txns)), D(0)) == D(-6000)


# ---- valuation -----------------------------------------------------------------------------------------


def div(a: int | str, b: int | str) -> Decimal:
    """a / b at the engine's precision, the reference for exact expected values."""
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return D(a) / D(b)


def _positions() -> tuple[list[Position], dict[int, AssetInfo]]:
    txns = [
        tx(1, TxnType.BUY, D0, "10", "100", fees="5", asset=1),
        tx(2, TxnType.BUY, D0, "2", "50", asset=2, fx="80"),
        tx(3, TxnType.SELL, date(2025, 2, 1), "4", "120", asset=1, fees="2"),
        tx(4, TxnType.BUY, D0, "3", "10", asset=3),
        tx(5, TxnType.SELL, date(2025, 2, 2), "3", "20", asset=3),
    ]
    assets = {1: asset(1, "Alpha"), 2: asset(2, "Beta", cur="USD", cls="EQUITY_US"), 3: asset(3, "Gone")}
    return positions_from_book(build_lots(txns, []), assets), assets


def test_positions_from_book_keep_cost_in_both_currencies_and_drop_exited_holdings() -> None:
    pos, _ = _positions()
    assert [p.asset.name for p in pos] == ["Alpha", "Beta"]
    alpha, beta = pos
    assert alpha.units == D(6) and alpha.cost_inr == D("603.0000") and alpha.cost_orig == D("603.0000")
    assert beta.units == D(2) and beta.cost_orig == D(100) and beta.cost_inr == D(8000)


def test_bonus_lot_has_no_cost_and_no_division_by_zero() -> None:
    txns = [tx(1, TxnType.BUY, D0, "10", "100")]
    book = build_lots(txns, [ca(1, ActionType.BONUS, date(2025, 2, 1), "1", "1")])
    (p,) = positions_from_book(book, {1: asset()})
    assert p.units == D(20) and p.cost_inr == D(1000) and p.cost_orig == D(1000)


def test_value_position_inr_and_usd() -> None:
    alpha, beta = _positions()[0]
    a = value_position(alpha, m(110), None, None, date(2025, 6, 30))
    assert a.value_inr.value == D(660) and a.unrealised_inr.value == D(57)
    assert a.unrealised_pct.value == div(57, 603) and a.fx is None and a.fx_rate_type is None
    b = value_position(beta, m(60), m(85), "FBIL_REF", date(2025, 6, 30))
    assert b.value_local.value == D(120) and b.value_inr.value == D(10200)
    assert b.unrealised_inr.value == D(2200) and b.fx_rate_type == "FBIL_REF"


def test_value_position_status_travels_and_missing_inputs_show_as_missing() -> None:
    alpha, beta = _positions()[0]
    stale = value_position(alpha, m(110, STALE), None, None, D0)
    assert stale.value_inr.status is STALE and stale.unrealised_inr.status is STALE
    no_fx = value_position(beta, m(60), None, None, D0)
    assert no_fx.value_inr.value is None and no_fx.value_inr.status is QualityStatus.MISSING
    assert any("no USD/INR rate" in n for n in no_fx.value_inr.notes)
    no_type = value_position(beta, m(60), m(85), None, D0)  # a rate with no named type is refused
    assert no_type.value_inr.value is None
    no_price = value_position(alpha, m(None, QualityStatus.MISSING), None, None, D0)
    assert no_price.value_inr.value is None


def test_totals_list_unvalued_holdings_and_mark_the_sums_estimated() -> None:
    alpha, beta = _positions()[0]
    good = value_position(alpha, m(110), None, None, D0)
    bad = value_position(beta, m(None, QualityStatus.MISSING), m(85), "FBIL_REF", D0)
    t = totals([good, bad], D0)
    assert t.value_inr.value == D(660) and t.cost_inr.value == D(603) and t.unrealised_inr.value == D(57)
    assert t.value_inr.status is EST and t.unvalued == ("Beta",) and t.holdings == 2
    assert "1 of 2 holdings have no usable value" in t.value_inr.notes[-1]
    clean = totals([good], D0)
    assert clean.value_inr.status is OK and clean.unvalued == ()
    quarantined = value_position(alpha, m(110, QualityStatus.QUARANTINED), None, None, D0)
    assert totals([quarantined], D0).unvalued == ("Alpha",)  # quarantined values never enter a sum


def test_realised_pnl_per_asset_and_total() -> None:
    txns = [
        tx(1, TxnType.BUY, D0, "10", "100", fees="10", asset=1),
        tx(2, TxnType.SELL, date(2025, 2, 1), "4", "150", fees="4", asset=1),
        tx(3, TxnType.BUY, D0, "1", "10", asset=2),
        tx(4, TxnType.SELL, date(2025, 2, 1), "1", "30", asset=2),
    ]
    per, total = realised_pnl(build_lots(txns, []), date(2025, 3, 1))
    # asset 1: cost 4/10 of 1010 = 404; proceeds 4*150-4 = 596; gain 192
    assert per[1].cost_inr.value == D(404) and per[1].proceeds_inr.value == D(596)
    assert per[1].gain_inr.value == D(192) and "tax unavailable" in per[1].gain_inr.notes[0]
    assert per[2].gain_inr.value == D(20)
    assert total.gain_inr.value == D(212) and total.cost_inr.value == D(414)
    assert realised_pnl(build_lots([], []), D0)[1].gain_inr.value == D(0)


def test_income_and_costs_convert_at_the_transaction_fx_and_never_double_count() -> None:
    txns = [
        tx(1, TxnType.BUY, D0, "2", "100", fees="0.5", stamp="0.1", taxes="0.2", fx="80", asset=2),
        tx(2, TxnType.DIVIDEND, date(2025, 3, 1), cash="170", fx="85", asset=2, taxes="0.52"),
        tx(3, TxnType.INTEREST, date(2025, 3, 2), cash="30"),
        tx(4, TxnType.DIVIDEND_REINVESTMENT, date(2025, 3, 3), "0.2", "62"),
        tx(5, TxnType.FEE, date(2025, 3, 4), cash="-11", fees="11"),  # states its amount as net cash only
        tx(6, TxnType.TAX, date(2025, 3, 5), cash="-9", taxes="9"),
        tx(7, TxnType.SELL, date(2025, 9, 1), "1", "10", fees="99"),  # after the report date: ignored
        tx(8, TxnType.DEPOSIT, date(2025, 3, 6), cash="500", fees="1"),
    ]
    inc = income_and_costs(txns, date(2025, 6, 30))
    assert inc.dividends_inr.value == D(170) and inc.interest_inr.value == D(30)
    assert inc.reinvested_dividends_inr.value == D("12.4")
    assert inc.fees_inr.value == D("0.5") * 80 + 11 + 1  # trade fee at 80, FEE row net cash, deposit fee at 1
    assert inc.taxes_inr.value == D("0.2") * 80 + D("0.52") * 85 + 9
    assert inc.stamp_duty_inr.value == D("0.1") * 80
    assert inc.fees_inr.status is OK and inc.fees_inr.source_ids == ("ledger",)


def test_from_ledger_is_ok_and_names_the_ledger() -> None:
    r = from_ledger(D(5), STAMP, "why")
    assert r.status is OK and r.source_ids == ("ledger",) and r.notes == ("why",) and r.as_of == STAMP


# ---- FX attribution ------------------------------------------------------------------------------------


def test_fx_attribution_hand_example() -> None:
    a = attribute_fx(m(100), m(8000), m(150), m(90), "FBIL_REF")
    assert a.valuation_rate_type == "FBIL_REF" and a.purchase_rate.value == D(80)
    assert a.local_return.value == D("0.5") and a.fx_return.value == D("0.125")
    assert a.interaction.value == D("0.0625") and a.inr_return.value == D("0.6875")
    assert a.local_effect_inr.value == D(4000) and a.fx_effect_inr.value == D(1000)
    assert a.interaction_inr.value == D(500) and a.gain_inr.value == D(5500)
    assert a.residual == D(0) and a.reconciles


def test_fx_attribution_loss_and_missing_inputs() -> None:
    down = attribute_fx(m(100), m(8000), m(80), m(75), "SBI_TT_BUY")
    assert down.gain_inr.value == D(-2000) and down.reconciles  # 80*75 - 8000
    gone = attribute_fx(m(100), m(8000), m(None, QualityStatus.MISSING), m(90), "FBIL_REF")
    assert gone.residual is None and not gone.reconciles and gone.inr_return.value is None
    est = attribute_fx(m(100), m(8000), m(150), m(90, EST), "SBI_TT_BUY")
    assert est.fx_return.status is EST and est.inr_return.status is EST


# ---- allocation ----------------------------------------------------------------------------------------


def _held(*rows: tuple[str, str, str, str | None, str, str | None]):  # type: ignore[no-untyped-def]
    """(name, class, currency, sector, value, cap) -> HoldingValue list, INR-valued."""
    out = []
    for i, (name, cls, cur, sector, value, cap) in enumerate(rows, start=1):
        a = AssetInfo(i, name, cls, cur, "IN" if cur == "INR" else "US", sector, cap)
        pos = Position(1, a, D(1), D(1), D(1))
        out.append(
            value_position(
                pos, m(value), None if cur == "INR" else m(1), None if cur == "INR" else "FBIL_REF", D0
            )
        )
    return out


def test_allocation_weights_sum_to_one_and_sort_largest_first() -> None:
    h = _held(
        ("A", "MF", "INR", None, "500", None),
        ("B", "EQUITY_US", "USD", "Tech", "300", "LARGE"),
        ("C", "MF", "INR", "Fin", "200", "MID"),
    )
    by_class = allocate(h, "asset_class")
    assert [(s.key, s.weight.value) for s in by_class] == [("MF", D("0.7")), ("EQUITY_US", D("0.3"))]
    assert sum((s.weight.value or 0 for s in allocate(h, "holding")), D(0)) == D(1)
    assert [(s.key, s.weight.value) for s in allocate(h, "country")] == [("IN", D("0.7")), ("US", D("0.3"))]
    assert [s.key for s in allocate(h, "currency")] == ["INR", "USD"]
    assert {s.key for s in allocate(h, "sector")} == {"UNKNOWN", "Tech", "Fin"}
    assert {s.key for s in allocate(h, "market_cap")} == {"UNKNOWN", "LARGE", "MID"}
    assert by_class[0].value_inr.value == D(700)


def test_allocation_with_an_unvalued_holding_is_estimated_not_silently_shorter() -> None:
    h = _held(("A", "MF", "INR", None, "600", None), ("B", "MF", "INR", None, "400", None))
    pos = Position(1, AssetInfo(9, "Z", "MF", "INR", "IN"), D(1), D(1), D(1))
    h.append(value_position(pos, m(None, QualityStatus.MISSING), None, None, D0))
    s = allocate(h, "asset_class")[0]
    assert s.weight.value == D(1) and s.weight.status is EST
    assert "1 of 3 holdings have no usable value" in s.weight.notes[-1]
    assert allocate([], "holding") == [] and allocate(h[-1:], "holding") == []


def test_concentration_top_n_and_hhi() -> None:
    h = _held(
        ("A", "MF", "INR", None, "500", None), ("B", "MF", "INR", None, "300", None),
        ("C", "MF", "INR", None, "200", None),
    )  # fmt: skip
    c = concentration(h, n=2)
    assert c.top_n == 2 and c.top_n_weight.value == D("0.8") and c.hhi.value == D("0.38")
    assert concentration(_held(("A", "MF", "INR", None, "5", None))).hhi.value == D(1)
    none = concentration([])
    assert none.top_n_weight.value is None and none.hhi.value is None


def test_concentration_inherits_status() -> None:
    h = _held(("A", "MF", "INR", None, "500", None))
    stale = value_position(h[0].position, m(500, STALE), None, None, D0)
    assert concentration([stale]).hhi.status is STALE


def test_look_through_uses_disclosed_holdings_and_lists_funds_without_data() -> None:
    h = _held(
        ("FundA", "MF", "INR", None, "600", None),
        ("Direct", "EQUITY_IN", "INR", "Fin", "300", None),
        ("FundB", "ETF_IN", "INR", None, "100", None),
    )
    lt = look_through(h, {1: {"Direct": D("0.5"), "Other": D("0.5")}, 3: {"Other": D("1")}})
    assert lt.without_data == ()
    assert lt.exposure["Direct"].value == D("0.6")  # 300 direct + 0.5 * 600 through FundA, of 1000
    assert lt.exposure["Other"].value == D("0.4") and lt.exposure["Direct"].status is OK
    partial = look_through(h, {1: {"Direct": D("1")}})
    assert partial.without_data == ("FundB",) and partial.exposure["Direct"].status is EST
    assert "no holdings data for: FundB" in partial.exposure["Direct"].notes[-1]
    assert look_through([], {}).exposure == {}
    assert look_through(h, {}).without_data == ("FundA", "FundB")


def test_overlap_is_sum_of_smaller_weights_and_missing_without_data() -> None:
    a = {"X": D("0.5"), "Y": D("0.3"), "Z": D("0.2")}
    b = {"X": D("0.2"), "Y": D("0.6"), "W": D("0.2")}
    r = overlap(a, b, STAMP)
    assert r.value == D("0.5") and r.status is OK and r.as_of == STAMP
    assert overlap(a, a, STAMP).value == D(1) and overlap(a, {"Q": D(1)}, STAMP).value == D(0)
    assert overlap(a, None, STAMP).value is None and overlap({}, b, STAMP).status is QualityStatus.MISSING


# ---- series ----------------------------------------------------------------------------------------------


def series(
    prices: list[str | None], start: date = D0, status: QualityStatus = OK
) -> list[tuple[date, Measured[Decimal]]]:
    return [
        (start + timedelta(days=i), m(p, status if p else QualityStatus.MISSING, start + timedelta(days=i)))
        for i, p in enumerate(prices)
    ]


def test_volatility_hand_example() -> None:
    v = volatility(series(["100", "110", "99"]), min_returns=2)  # returns +10%, -10%
    assert v.value is not None
    assert abs(v.value - D("0.02").sqrt() * D(252).sqrt()) < D("1e-25")
    assert v.status is OK and v.as_of == datetime(2025, 1, 3, tzinfo=UTC) and "252 periods" in v.notes[-1]


def test_volatility_needs_enough_returns_and_labels_dropped_points() -> None:
    short = volatility(series(["100", "101", "102"]))
    assert short.value is None and "needs 20 returns, have 2" in short.notes[0]
    assert volatility([]).value is None
    flat = volatility(series(["100"] * 5), min_returns=2)
    assert flat.value == D(0)
    holes = volatility(series(["100", "110", None, "99"]), min_returns=2)
    assert (
        holes.value is not None
        and holes.status is EST
        and "1 unusable observation(s) dropped" in holes.notes[-2]
    )
    q = [
        (d, Measured(x.value, QualityStatus.QUARANTINED, x.as_of, x.source_ids))
        for d, x in series(["1", "2"])
    ]
    assert volatility(q, min_returns=1).value is None  # quarantined points never feed a statistic
    stale = volatility(series(["100", "110", "99"], status=STALE), min_returns=2)
    assert stale.status is STALE and "3 of 3 observations are not ok" in stale.notes[0]


def test_max_drawdown_depth_dates_and_recovery() -> None:
    d = max_drawdown(series(["100", "120", "90", "110", "130"]))
    assert d.depth.value == D("-0.25") and d.peak == date(2025, 1, 2) and d.trough == date(2025, 1, 3)
    assert d.recovered_on == date(2025, 1, 5) and d.recovery_days == 2 and d.current_depth.value == D(0)
    assert d.depth.as_of == datetime(2025, 1, 5, tzinfo=UTC)


def test_max_drawdown_still_under_water_and_no_fall() -> None:
    d = max_drawdown(series(["100", "120", "90", "100"]))
    assert d.depth.value == D("-0.25") and d.recovered_on is None and d.recovery_days is None
    assert d.current_depth.value is not None and abs(d.current_depth.value + D(1) / 6) < D("1e-25")
    up = max_drawdown(series(["100", "101", "105"]))
    assert up.depth.value == D(0) and up.peak is None and up.trough is None and up.current_depth.value == D(0)
    lone = max_drawdown(series(["100"]))
    assert lone.depth.value is None and lone.current_depth.value is None
    assert max_drawdown([]).depth.status is QualityStatus.MISSING


def test_drawdown_deepest_fall_wins_over_the_first_one() -> None:
    d = max_drawdown(series(["100", "90", "100", "130", "78", "130"]))  # -10% first, -40% later
    assert d.depth.value == D("-0.4") and d.peak == date(2025, 1, 4) and d.recovered_on == date(2025, 1, 6)


def test_correlation_perfect_inverse_and_degenerate_cases() -> None:
    a = series(["100", "110", "99", "120", "118"])
    twice = [(d, m(x.value * 2 if x.value else None)) for d, x in a]
    assert correlation(a, twice, min_returns=3).value == pytest.approx(D(1), abs=D("1e-30"))
    inv = series(["100", "90", "101", "84", "86"])  # moves against `a` every day
    assert correlation(a, inv, min_returns=3).value is not None
    assert correlation(a, series(["5"] * 5), min_returns=3).value is None  # no variation
    assert "no variation" in correlation(a, series(["5"] * 5), min_returns=3).notes[-1]
    assert correlation(a, a[:3], min_returns=3).value is None  # too few common dates
    assert correlation([], [], min_returns=1).value is None


def test_correlation_aligns_on_common_dates_only() -> None:
    a = series(["100", "110", "99", "120", "118", "119"])
    b = series(["50", "55", "49.5", "60", "59"], start=D0)  # one day shorter
    c = correlation(a, b, min_returns=3)
    assert c.value == pytest.approx(D(1), abs=D("1e-30")) and "4 common returns" in c.notes[-1]


def test_compare_to_benchmark_hand_example() -> None:
    asset_s = series(["100", "110", "108", "120"])
    bench = series(["1000", "1050", "1060", "1100"])
    r = compare_to_benchmark(asset_s, bench, "BM", min_returns=3)
    assert r.benchmark_id == "BM"
    assert r.asset_return.value == pytest.approx(D("0.2"), abs=D("1e-30"))
    assert r.benchmark_return.value == pytest.approx(D("0.1"), abs=D("1e-30"))
    assert r.excess_return.value == pytest.approx(D("0.1"), abs=D("1e-30"))
    # beta = cov(asset, bench) / var(bench) on the three daily returns
    ra = [D(110) / 100 - 1, D(108) / 110 - 1, D(120) / 108 - 1]
    rb = [D(1050) / 1000 - 1, D(1060) / 1050 - 1, D(1100) / 1060 - 1]
    ma, mb = sum(ra) / 3, sum(rb) / 3
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True)) / 2
    var = sum((y - mb) ** 2 for y in rb) / 2
    assert r.beta.value == pytest.approx(cov / var, abs=D("1e-25"))
    assert r.correlation.value is not None and 0 < r.correlation.value <= 1
    assert "BM" in r.asset_return.notes[-1] and r.excess_return.status is OK


def test_compare_to_benchmark_refuses_when_data_is_missing_or_flat() -> None:
    asset_s = series(["100", "110", "121", "133.1"])
    none = compare_to_benchmark(asset_s, [], "BM")
    assert none.excess_return.value is None and none.beta.status is QualityStatus.MISSING
    flat = compare_to_benchmark(asset_s, series(["10"] * 4), "BM", min_returns=3)
    assert flat.beta.value is None and "no variation" in flat.beta.notes[-1]
    assert flat.excess_return.value is not None  # returns still compare; only beta is undefined
    stale = compare_to_benchmark(
        asset_s, series(["1000", "1050", "1060", "1100"], status=STALE), "BM", min_returns=3
    )
    assert stale.excess_return.status is STALE


# ---- benchmark registry ------------------------------------------------------------------------------


def test_benchmarks_are_documented_and_map_asset_classes() -> None:
    assert len({b.id for b in BENCHMARKS}) == len(BENCHMARKS)
    assert all(b.rationale and b.ticker and b.name for b in BENCHMARKS)
    assert benchmark_for("MF") is not None and benchmark_for("MF").id == "NIFTY50_TRI"  # type: ignore[union-attr]
    assert benchmark_for("EQUITY_US").id == "SP500_TRI"  # type: ignore[union-attr]
    assert benchmark_for("GOLD_ETF").id == "GOLD_INR" and benchmark_for("SILVER_ETF").id == "SILVER_INR"  # type: ignore[union-attr]
    assert benchmark_for("DIGITAL_GOLD") is None and benchmark_for("CASH") is None


def test_m_sum_helper_used_by_totals_keeps_status() -> None:
    s = m_sum([m(1), m(2, STALE)], STAMP)
    assert s.value == D(3) and s.status is STALE
