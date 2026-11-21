"""IPS schema, personal-context hard rules, cost rule, IPO eligibility and the advisory score."""

import textwrap
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from arthashreni.policy.context import Certainty, ContextField, Verdict, liquidity
from arthashreni.policy.cost import (
    CostSchedule,
    CostVerdict,
    all_in_cost,
    break_even_amount,
    check_cost,
    load_costs,
)
from arthashreni.policy.ipo import ipo_verdict
from arthashreni.policy.ips import IPS, AdvisoryWeights, Band, ips_from_json
from arthashreni.policy.score import advisory_score

D = Decimal
TODAY = date(2026, 11, 12)


def band(lo: str, hi: str) -> Band:
    return Band(low=D(lo), high=D(hi))


def ips(**kw: object) -> IPS:
    base: dict[str, object] = {"asset_class_bands": {"MF": band("0.4", "0.7"), "GOLD_ETF": band("0", "0.4")}}
    return IPS(**{**base, **kw})  # type: ignore[arg-type]


# ---- IPS -----------------------------------------------------------------------------------------------


def test_band_must_be_ordered_and_a_fraction() -> None:
    with pytest.raises(ValidationError, match="above band high"):
        band("0.5", "0.4")
    with pytest.raises(ValidationError):
        band("0", "1.5")
    assert band("0.2", "0.6").mid == D("0.4")


def test_bands_must_be_able_to_add_up_to_everything() -> None:
    with pytest.raises(ValidationError, match="asset class bands cannot add up"):
        ips(asset_class_bands={"MF": band("0.6", "0.7"), "ETF_IN": band("0.5", "0.6")})  # lows sum above 100%
    with pytest.raises(ValidationError, match="cannot add up"):
        ips(
            asset_class_bands={"MF": band("0.1", "0.4"), "ETF_IN": band("0.1", "0.4")}
        )  # highs sum below 100%
    with pytest.raises(ValidationError, match="country bands"):
        ips(country_bands={"IN": band("0.1", "0.4")})
    with pytest.raises(ValidationError, match="currency bands"):
        ips(currency_bands={"INR": band("0.1", "0.4")})
    with pytest.raises(ValidationError, match="needs asset class bands"):
        ips(asset_class_bands={})


def test_advisory_weights_default_to_the_documented_hypothesis_and_must_total_100() -> None:
    w = AdvisoryWeights()
    assert (w.market, w.quality, w.fit, w.valuation, w.context) == (25, 25, 20, 15, 15)
    with pytest.raises(ValidationError, match="add up to 100"):
        AdvisoryWeights(market=D(30))


def test_defaults_are_the_conservative_ones() -> None:
    p = ips()
    assert p.leftover_rule == "RESERVE_NO_INFLATE" and p.sale_proceeds_to == "RESERVE"
    assert p.cost_threshold_pct == D("0.01") and p.rebalance_breach_margin == D("0.05")
    assert p.cooldown_months == 3 and p.max_holding is None


def test_ips_round_trips_through_json() -> None:
    p = ips(max_holding=D("0.3"))
    assert ips_from_json(p.model_dump(mode="json")) == p


# ---- personal context (B6) -----------------------------------------------------------------------------


def known(v: object, c: Certainty = Certainty.KNOWN) -> ContextField:
    return ContextField(value=v, certainty=c)


def ctx(
    fund: object = 600000, spend: object = 50000, stable: object = True, planned: object = None
) -> dict[str, ContextField]:
    out = {
        "emergency_fund_inr": known(fund),
        "essential_expenses_inr": known(spend),
        "income_stable": known(stable),
    }
    if planned is not None:
        out["planned_expenses"] = known(planned)
    return out


def test_emergency_fund_at_the_minimum_is_ok() -> None:
    liq = liquidity(ctx(fund=300000), ips(emergency_fund_min_months=D(6)), TODAY)
    assert liq.emergency is Verdict.OK and liq.emergency_shortfall_inr == 0


def test_emergency_fund_below_the_minimum_fails_with_the_shortfall() -> None:
    liq = liquidity(ctx(fund=200000), ips(emergency_fund_min_months=D(6)), TODAY)
    assert liq.emergency is Verdict.FAIL and liq.emergency_shortfall_inr == 100000
    assert any("below 6 months" in n for n in liq.notes)


def test_unknown_emergency_fund_is_unknown_not_ok() -> None:
    liq = liquidity({"essential_expenses_inr": known(50000)}, ips(), TODAY)
    assert liq.emergency is Verdict.UNKNOWN
    liq = liquidity({"emergency_fund_inr": ContextField(value=1, certainty=Certainty.UNKNOWN),
                     "essential_expenses_inr": known(1)}, ips(), TODAY)  # fmt: skip
    assert liq.emergency is Verdict.UNKNOWN


def test_assumed_fields_are_listed_as_assumptions() -> None:
    c = ctx()
    c["emergency_fund_inr"] = known(600000, Certainty.ASSUMED)
    assert liquidity(c, ips(), TODAY).assumptions == ("emergency_fund_inr is an assumption",)


def test_planned_expense_inside_the_window_blocks_risk_money() -> None:
    soon = [{"date": "2027-03-01", "amount_inr": 500000}]
    liq = liquidity(ctx(planned=soon), ips(planned_expense_window_days=180), TODAY)
    assert liq.expense_window is Verdict.FAIL
    assert (
        liquidity(ctx(planned=soon), ips(planned_expense_window_days=90), TODAY).expense_window is Verdict.OK
    )


def test_expense_window_edges() -> None:
    on_edge = [{"date": "2026-11-22", "amount_inr": 1}]
    assert (
        liquidity(ctx(planned=on_edge), ips(planned_expense_window_days=10), TODAY).expense_window
        is Verdict.FAIL
    )
    after = [{"date": "2026-11-23", "amount_inr": 1}]
    assert (
        liquidity(ctx(planned=after), ips(planned_expense_window_days=10), TODAY).expense_window is Verdict.OK
    )
    today_item = [{"date": "2026-11-12", "amount_inr": 1}]
    assert (
        liquidity(ctx(planned=today_item), ips(planned_expense_window_days=0), TODAY).expense_window
        is Verdict.FAIL
    )
    past = [{"date": "2026-11-11", "amount_inr": 1}]
    assert liquidity(ctx(planned=past), ips(), TODAY).expense_window is Verdict.OK


def test_unknown_planned_expenses_are_not_applied_but_said() -> None:
    liq = liquidity(ctx(), ips(), TODAY)
    assert liq.expense_window is Verdict.UNKNOWN
    assert any("planned large expenses unknown" in n for n in liq.notes)


def test_income_must_be_confirmed_stable() -> None:
    assert not liquidity(ctx(stable=True), ips(), TODAY).income_unstable
    assert liquidity(ctx(stable=False), ips(), TODAY).income_unstable
    unknown = {k: v for k, v in ctx().items() if k != "income_stable"}
    assert liquidity(unknown, ips(), TODAY).income_unstable  # unknown is treated as unstable


# ---- cost rule (B4) ------------------------------------------------------------------------------------


def sched(**kw: object) -> CostSchedule:
    return CostSchedule(platform="P", asset_class="MF", **kw)  # type: ignore[arg-type]


def test_all_in_cost_adds_every_component_with_gst_on_brokerage_and_transfer() -> None:
    s = sched(brokerage_pct=D("0.001"), brokerage_flat_inr=D(10), fx_markup_pct=D("0.005"),
              transfer_fee_flat_inr=D(20), stamp_duty_pct=D("0.0001"), gst_pct=D("0.18"))  # fmt: skip
    # brokerage 10 + 1.0 = 11; gst 0.18 * (11 + 20) = 5.58; fx 5; transfer 20; stamp 0.1
    assert all_in_cost(D(1000), s) == D("41.68")


def test_missing_schedule_is_unknown_never_a_guess() -> None:
    c = check_cost(D(1000), None, D("0.01"))
    assert c.verdict is CostVerdict.UNKNOWN and c.cost_inr is None and c.pct is None


def test_below_minimum_ticket() -> None:
    c = check_cost(D(99), sched(min_ticket_inr=D(100)), D("0.01"))
    assert c.verdict is CostVerdict.BELOW_MIN_TICKET
    assert check_cost(D(100), sched(min_ticket_inr=D(100)), D("0.01")).verdict is CostVerdict.OK


def test_threshold_is_exclusive_of_equal() -> None:
    s = sched(stamp_duty_pct=D("0.01"))
    assert check_cost(D(1000), s, D("0.01")).verdict is CostVerdict.OK  # exactly 1%
    assert check_cost(D(1000), s, D("0.0099")).verdict is CostVerdict.TOO_COSTLY


def test_too_costly_says_where_it_becomes_worthwhile() -> None:
    c = check_cost(D(500), sched(brokerage_flat_inr=D(20)), D("0.01"))
    assert c.verdict is CostVerdict.TOO_COSTLY and c.pct == D("0.04")
    assert "accumulate to at least 2000.00" in c.note


def test_percentage_costs_alone_over_the_limit_cannot_be_fixed_by_waiting() -> None:
    c = check_cost(D(10000), sched(fx_markup_pct=D("0.02")), D("0.01"))
    assert c.verdict is CostVerdict.TOO_COSTLY and "percentage costs alone" in c.note


def test_break_even_amount() -> None:
    assert break_even_amount(sched(brokerage_flat_inr=D(20)), D("0.01")) == 2000
    assert break_even_amount(sched(brokerage_flat_inr=D(20), fx_markup_pct=D("0.005")), D("0.01")) == 4000
    assert break_even_amount(sched(brokerage_flat_inr=D(10), gst_pct=D("0.18")), D("0.01")) == D("1180")
    assert break_even_amount(sched(stamp_duty_pct=D("0.001")), D("0.01")) == 0
    assert break_even_amount(sched(fx_markup_pct=D("0.01")), D("0.01")) is None


def test_load_costs(tmp_path: Path) -> None:
    f = tmp_path / "costs.toml"
    f.write_text(
        textwrap.dedent("""
        [[schedule]]
        platform = "A"
        asset_class = "MF"
        brokerage_flat_inr = 5
        [[schedule]]
        platform = "B"
        asset_class = "ETF_IN"
        min_ticket_inr = 100
    """)
    )
    book = load_costs(f)
    assert set(book) == {("A", "MF"), ("B", "ETF_IN")} and book[("A", "MF")].brokerage_flat_inr == 5
    assert load_costs(tmp_path / "missing.toml") == {}


def test_negative_fees_are_refused() -> None:
    with pytest.raises(ValidationError):
        sched(brokerage_pct=D("-0.1"))


# ---- IPO (B3) ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("board", ["SME", "SME_EMERGE", "", "mainboard "])
def test_only_the_mainboard_can_apply(board: str) -> None:
    v = ipo_verdict(board, D(14800), "ALLOWED", D(10**9))
    assert not v.can_apply and "excluded" in v.reasons[0]


def test_minimum_lot_comes_from_the_rhp_never_a_default() -> None:
    v = ipo_verdict("MAINBOARD", None, "ALLOWED", D(10**9))
    assert not v.can_apply and "RHP" in v.reasons[0]


def test_reserve_must_cover_the_minimum_application() -> None:
    assert ipo_verdict("MAINBOARD", D(14800), "ALLOWED", D(14800)).can_apply
    short = ipo_verdict("MAINBOARD", D(14800), "ALLOWED", D("14799.99"))
    assert not short.can_apply and "does not cover" in short.reasons[0]


def test_research_classification_must_allow_it() -> None:
    for cls in ("UNRATED", "NOT_ALLOWED"):
        v = ipo_verdict("MAINBOARD", D(14800), cls, D(20000))
        assert not v.can_apply and cls in v.reasons[0]
    both = ipo_verdict("MAINBOARD", D(14800), "UNRATED", D(1))
    assert len(both.reasons) == 2


# ---- advisory score ------------------------------------------------------------------------------------


def test_score_is_a_weighted_average_of_the_components_that_have_data() -> None:
    s = advisory_score({"market": D(1), "fit": D(0)}, AdvisoryWeights())
    assert s.total == D(25) / D(45)  # weights 25 and 20 only
    assert s.components["quality"] is None and "context, quality, valuation had no data" in s.note


def test_full_data_has_no_missing_note() -> None:
    full = {k: D("0.5") for k in ("market", "quality", "fit", "valuation", "context")}
    s = advisory_score(full, AdvisoryWeights())
    assert s.total == D("0.5") and s.note == "advisory"


def test_no_data_gives_no_score_not_a_default() -> None:
    s = advisory_score({"market": None}, AdvisoryWeights())
    assert s.total is None and s.note == "no score component has data"


def test_weights_can_be_changed() -> None:
    w = AdvisoryWeights(market=D(100), quality=D(0), fit=D(0), valuation=D(0), context=D(0))
    assert advisory_score({"market": D("0.3"), "fit": D(1)}, w).total == D("0.3")


def test_score_inputs_are_validated() -> None:
    with pytest.raises(ValueError, match="unknown score components"):
        advisory_score({"vibes": D(1)}, AdvisoryWeights())
    with pytest.raises(ValueError, match="between 0 and 1"):
        advisory_score({"market": D("1.1")}, AdvisoryWeights())
    with pytest.raises(ValueError, match="between 0 and 1"):
        advisory_score({"market": D("-0.1")}, AdvisoryWeights())


# ---- exact outputs (the reasons are shown to the investor and kept in the audit trail) --------------------


def test_cost_messages_and_values_are_exact(tmp_path: Path) -> None:
    f = tmp_path / "none.toml"
    f.write_text("# no schedules\n")
    assert load_costs(f) == {}
    for bad in (D(0), D(-1)):
        with pytest.raises(ValueError, match="^an investment amount is positive$"):
            check_cost(bad, sched(), D("0.01"))
    assert check_cost(D(5), None, D("0.01")).note == "no cost schedule for this platform and instrument"
    assert check_cost(D(5), sched(min_ticket_inr=D(10)), D("0.01")).note == "below the minimum ticket 10"
    ok = check_cost(D(1000), sched(brokerage_flat_inr=D(5)), D("0.01"))
    assert (ok.cost_inr, ok.pct, ok.note) == (D(5), D("0.005"), "all-in cost 0.50%")
    costly = check_cost(D(1000), sched(brokerage_flat_inr=D(50)), D("0.01"))
    assert costly.cost_inr == 50 and costly.pct == D("0.05")
    assert costly.note == "all-in cost 5.00% exceeds 1.00%; accumulate to at least 5000.00"
    assert check_cost(D(1000), sched(fx_markup_pct=D("0.02")), D("0.01")).note == (
        "all-in cost 2.00% exceeds 1.00%; percentage costs alone exceed the limit"
    )


def test_break_even_uses_every_cost_component() -> None:
    assert break_even_amount(sched(brokerage_flat_inr=D(10), transfer_fee_flat_inr=D(5)), D("0.01")) == 1500
    assert break_even_amount(sched(brokerage_flat_inr=D(8), stamp_duty_pct=D("0.002")), D("0.01")) == 1000
    s = sched(brokerage_flat_inr=D(10), brokerage_pct=D("0.001"), gst_pct=D("0.1"), fx_markup_pct=D("0.001"))
    # flat 10 * 1.1 = 11; variable 0.001 * 1.1 + 0.001 = 0.0021; 11 / (0.01 - 0.0021)
    assert break_even_amount(s, D("0.01")) == D(11) / D("0.0079")


def test_liquidity_outputs_are_exact() -> None:
    unknown = liquidity({}, ips(), TODAY)
    assert unknown.emergency_shortfall_inr == 0
    assert unknown.notes[0] == "emergency fund or essential expenses unknown: liquidity cannot be confirmed"
    assert "planned large expenses unknown: not applied" in unknown.notes
    assert "income not confirmed stable: the risk level of the mix may not rise" in unknown.notes
    ok = liquidity(ctx(fund=300000, planned=[]), ips(emergency_fund_min_months=D(6)), TODAY)
    assert ok.notes == () and ok.emergency_shortfall_inr == 0
    one_short = liquidity(ctx(fund=299999), ips(emergency_fund_min_months=D(6)), TODAY)
    assert one_short.emergency is Verdict.FAIL and one_short.emergency_shortfall_inr == 1
    assert one_short.notes[0] == "emergency fund is 1.00 below 6 months of expenses"


def test_ipo_and_score_outputs_are_exact() -> None:
    sme = ipo_verdict("SME", D(1), "ALLOWED", D(10))
    assert sme.can_apply is False and sme.reasons == ("SME and other non-mainboard IPOs are excluded",)
    nolot = ipo_verdict("MAINBOARD", None, "ALLOWED", D(10))
    assert nolot.can_apply is False and nolot.reasons == ("minimum lot value is not read from the RHP yet",)
    none = advisory_score({"market": None}, AdvisoryWeights())
    assert none.components == {
        "market": None,
        "quality": None,
        "fit": None,
        "valuation": None,
        "context": None,
    }
    some = advisory_score({"market": D("0.5"), "fit": D(1)}, AdvisoryWeights())
    assert some.components == {
        "market": D("0.5"),
        "quality": None,
        "fit": D(1),
        "valuation": None,
        "context": None,
    }
    assert some.total == (D(25) * D("0.5") + D(20)) / D(45)
    with pytest.raises(ValueError, match="^score components are between 0 and 1$"):
        advisory_score({"market": D(2)}, AdvisoryWeights())
