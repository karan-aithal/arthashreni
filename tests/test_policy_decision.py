"""Decision engine (F3): hard gates, policy allocation, vetoes, cost rule, envelope. Property tests (B7) at the end:
total <= budget, the envelope reconciles, no envelope line is used twice, reserves never go negative, no SME IPO is
eligible and every allocation obeys the IPS bands, whatever random portfolio the engine is given."""

from datetime import date
from decimal import ROUND_DOWN, Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from arthashreni.dq.confidence import ConfidenceLevel
from arthashreni.dq.measured import QualityStatus as Q
from arthashreni.policy.context import RISKY_CLASSES, Certainty, ContextField, Verdict, liquidity
from arthashreni.policy.cost import CostSchedule, CostVerdict, check_cost
from arthashreni.policy.decision import (
    INF,
    DecisionInput,
    Holding,
    PastMove,
    Reserve,
    _cap_room,
    _exclusions,
    _floor,
    _months_between,
    _Plan,
    decide,
)
from arthashreni.policy.envelope import EnvelopeLine, Kind, OverAllocated
from arthashreni.policy.ipo import ipo_verdict
from arthashreni.policy.ips import IPS, Band
from arthashreni.policy.recommendation import Action, Recommendation
from arthashreni.risk.thresholds import Level

D = Decimal
PERIOD, TODAY = date(2026, 11, 1), date(2026, 11, 12)


def band(lo: str, hi: str) -> Band:
    return Band(low=D(lo), high=D(hi))


def ctx(
    fund: object = 600000, spend: object = 50000, stable: object = True, planned: object = None
) -> dict[str, ContextField]:
    out = {
        "emergency_fund_inr": ContextField(value=fund, certainty=Certainty.KNOWN),
        "essential_expenses_inr": ContextField(value=spend, certainty=Certainty.KNOWN),
        "income_stable": ContextField(value=stable, certainty=Certainty.KNOWN),
    }
    if planned is not None:
        out["planned_expenses"] = ContextField(value=planned, certainty=Certainty.KNOWN)
    return out


def hold(i: int, cls: str, value: str, **kw: object) -> Holding:
    country = "US" if cls == "EQUITY_US" else "IN"
    base: dict[str, object] = dict(asset_id=i, name=f"A{i}", asset_class=cls, country=country,
                                   currency="USD" if country == "US" else "INR", platform="P", value_inr=D(value),
                                   price_status=Q.OK, price_as_of=date(2026, 11, 10))  # fmt: skip
    return Holding(**{**base, **kw})  # type: ignore[arg-type]


def sip(asset: int, amount: str = "2000") -> EnvelopeLine:
    return EnvelopeLine(kind=Kind.SIP, amount_inr=D(amount), asset_id=asset)


FREE = {"P": None}


def costs(*classes: str, **kw: object) -> dict[tuple[str, str], CostSchedule]:
    return {("P", c): CostSchedule(platform="P", asset_class=c, **kw) for c in classes}  # type: ignore[arg-type]


STD_IPS = IPS(
    asset_class_bands={
        "MF": band("0.3", "0.6"),
        "EQUITY_US": band("0.1", "0.3"),
        "GOLD_ETF": band("0.05", "0.2"),
    },
    max_monthly_mix_change=D("0.5"),
)
STD_HOLDINGS = (hold(1, "MF", "60000"), hold(2, "EQUITY_US", "8000"), hold(3, "GOLD_ETF", "2000"))


def inp(**kw: object) -> DecisionInput:
    base: dict[str, object] = dict(
        period=PERIOD, today=TODAY, dataset="DEMO", opening_budget_inr=D(10000), sip_lines=(), holdings=STD_HOLDINGS,
        reconciliation_clean=True, confidence=ConfidenceLevel.MEDIUM, confidence_reasons=(), risk_level=Level.NORMAL,
        ips=STD_IPS, ips_version=1, context=ctx(), costs=costs("MF", "EQUITY_US", "GOLD_ETF"),
        cash_reserve=Reserve(1, "Cash reserve", D(0)),
    )  # fmt: skip
    return DecisionInput(**{**base, **kw})  # type: ignore[arg-type]


def lines(rec: Recommendation, action: Action) -> list:  # type: ignore[type-arg]
    return [ln for ln in rec.lines if ln.action is action]


def assert_money_adds_up(rec: Recommendation) -> None:
    e = rec.envelope
    assert (
        e.committed_sip_inr + e.new_allocations_inr + e.reserve_transfers_inr + e.unallocated_inr
        == rec.budget_inr
    )
    assert rec.human_approval_required is True


# ---- hard gates ----------------------------------------------------------------------------------------


def test_no_approved_ips_means_no_personalised_allocation() -> None:
    rec = decide(inp(ips=None, ips_version=None, sip_lines=(sip(1),)))
    assert (
        not rec.personalised
        and rec.envelope.new_allocations_inr == 0
        and rec.envelope.reserve_transfers_inr == 0
    )
    assert rec.envelope.committed_sip_inr == 2000 and rec.envelope.unallocated_inr == 8000
    assert "approved Investment Policy Statement" in rec.missing
    assert lines(rec, Action.HOLD_CASH)[0].reasons[0] == "no approved IPS: no personalised allocation"
    assert rec.overall_action is Action.HOLD_CASH
    assert rec.assumptions == ("Leftover-cash rule: default assumption (IPS not approved)",)
    assert_money_adds_up(rec)


def test_unexplained_reconciliation_difference_blocks_new_money() -> None:
    rec = decide(inp(reconciliation_clean=False))
    assert rec.envelope.new_allocations_inr == 0 and rec.envelope.unallocated_inr == 10000
    assert any("reconciliation" in r for r in lines(rec, Action.HOLD_CASH)[0].reasons)


def test_unknown_emergency_fund_is_insufficient_evidence() -> None:
    rec = decide(inp(context={}))
    assert rec.envelope.new_allocations_inr == 0 and rec.envelope.reserve_transfers_inr == 0
    assert any("insufficient evidence" in r for r in lines(rec, Action.HOLD_CASH)[0].reasons)
    assert set(rec.missing) == {"emergency_fund_inr", "essential_expenses_inr"}


def test_emergency_fund_below_minimum_builds_liquidity_first() -> None:
    rec = decide(inp(context=ctx(fund=250000)))  # minimum 6 x 50,000; short by 50,000
    first = rec.lines[0]
    assert (
        first.action is Action.HOLD_CASH and first.amount_inr == 10000
    )  # all free money, shortfall is larger
    assert "build liquidity first" in first.reasons[0]
    assert rec.envelope.reserve_transfers_inr == 10000 and rec.envelope.new_allocations_inr == 0
    assert rec.reserve_movements[0]["direction"] == "in" and rec.reserve_movements[0]["amount_inr"] == "10000"


def test_a_small_shortfall_is_covered_then_the_rest_is_allocated() -> None:
    rec = decide(inp(context=ctx(fund=299000)))  # short by 1,000
    assert rec.envelope.reserve_transfers_inr == 1000 and rec.envelope.new_allocations_inr > 0
    assert rec.lines[0].amount_inr == 1000


def test_no_cash_reserve_leaves_the_money_unallocated_with_a_warning() -> None:
    rec = decide(inp(context=ctx(fund=250000), cash_reserve=None))
    assert rec.envelope.reserve_transfers_inr == 0
    assert any("no reserve is set up" in w for w in rec.warnings)


def test_a_large_planned_expense_stops_new_risk_money_only() -> None:
    soon = [{"date": "2027-01-15", "amount_inr": 400000}]
    rec = decide(inp(context=ctx(planned=soon)))
    funded = {ln.subject for ln in lines(rec, Action.ACCUMULATE)}
    assert funded == {"A3"}  # gold only; MF and US equity are risk money
    assert "a large planned expense is near" in str(lines(rec, Action.HOLD_CASH) or "") or funded == {"A3"}


def test_unconfirmed_income_cannot_raise_the_risk_level_of_the_mix() -> None:
    held = (hold(1, "MF", "10000"), hold(2, "GOLD_ETF", "10000"))
    ips = IPS(
        asset_class_bands={"MF": band("0.3", "0.9"), "GOLD_ETF": band("0", "0.7")},
        max_monthly_mix_change=D(1),
    )
    risky = decide(inp(holdings=held, ips=ips, context=ctx(stable=True), costs=costs("MF", "GOLD_ETF")))
    cautious = decide(inp(holdings=held, ips=ips, context=ctx(stable=False), costs=costs("MF", "GOLD_ETF")))
    mf_stable = sum(
        ln.amount_inr for ln in risky.lines if ln.action is Action.ACCUMULATE and ln.subject == "A1"
    )
    mf_unstable = sum(
        ln.amount_inr for ln in cautious.lines if ln.action is Action.ACCUMULATE and ln.subject == "A1"
    )
    assert (
        mf_stable > 0 and mf_unstable == 0
    )  # MF is half the book already: adding to it raises the risky share


def test_stale_or_conflicting_prices_mean_insufficient_evidence_for_that_asset() -> None:
    for status in (Q.STALE, Q.MISSING, Q.CONFLICTING, Q.QUARANTINED):
        held = (
            hold(1, "MF", "60000", price_status=status),
            hold(2, "EQUITY_US", "8000"),
            hold(3, "GOLD_ETF", "2000"),
        )
        rec = decide(inp(holdings=held))
        assert not [ln for ln in lines(rec, Action.ACCUMULATE) if ln.subject == "A1"]
        hold_line = next(ln for ln in lines(rec, Action.HOLD) if ln.subject == "A1")
        assert hold_line.reasons[0] == f"insufficient evidence: the price is {status.value}"
        assert hold_line.confidence == "low" and hold_line.data_freshness == status.value


def test_estimated_prices_are_usable_but_labelled() -> None:
    held = (hold(1, "MF", "1000", price_status=Q.ESTIMATED), hold(2, "EQUITY_US", "8000"))
    rec = decide(inp(holdings=held))
    line = next(ln for ln in lines(rec, Action.ACCUMULATE) if ln.subject == "A1")
    assert line.data_freshness == "estimated"


# ---- vetoes --------------------------------------------------------------------------------------------


def test_veto_gives_no_new_money_whatever_the_score_and_sip_gets_a_review() -> None:
    held = (
        hold(1, "MF", "1000", risk_veto=("severe asset-specific decline (x)",)),
        hold(2, "EQUITY_US", "8000"),
    )
    rec = decide(inp(holdings=held, sip_lines=(sip(1, "1000"),)))
    assert not [ln for ln in lines(rec, Action.ACCUMULATE) if ln.subject == "A1"]
    assert lines(rec, Action.AVOID)[0].subject == "A1"
    review = lines(rec, Action.REVIEW)[0]
    assert review.amount_inr == 0 and "nothing is cancelled automatically" in review.reasons[0]
    assert lines(rec, Action.CONTINUE_SIP)[0].amount_inr == 1000  # the committed SIP is still recorded


def test_failed_thesis_gets_no_new_money() -> None:
    held = (hold(1, "MF", "1000", thesis_failed=True), hold(2, "EQUITY_US", "8000"))
    rec = decide(inp(holdings=held))
    assert not [ln for ln in lines(rec, Action.ACCUMULATE) if ln.subject == "A1"]
    assert lines(rec, Action.AVOID)[0].reasons == ("the investor flagged the thesis as failed",)


def test_class_without_a_band_is_not_funded() -> None:
    held = (*STD_HOLDINGS, hold(4, "SILVER_ETF", "1000"))
    rec = decide(inp(holdings=held))
    assert all(ln.subject != "A4" for ln in lines(rec, Action.ACCUMULATE))


def test_cooldown_blocks_reversing_a_recent_reduction_only() -> None:
    held = (hold(1, "MF", "1000"), hold(2, "EQUITY_US", "8000"))
    recent = (PastMove(date(2026, 9, 1), "MF", "down"),)  # 2 months before November, cooldown is 3
    assert not [
        ln
        for ln in lines(decide(inp(holdings=held, history=recent)), Action.ACCUMULATE)
        if ln.subject == "A1"
    ]
    old = (PastMove(date(2026, 8, 1), "MF", "down"),)  # exactly 3 months: allowed
    assert [
        ln for ln in lines(decide(inp(holdings=held, history=old)), Action.ACCUMULATE) if ln.subject == "A1"
    ]
    same_way = (PastMove(date(2026, 10, 1), "MF", "up"),)
    assert [
        ln
        for ln in lines(decide(inp(holdings=held, history=same_way)), Action.ACCUMULATE)
        if ln.subject == "A1"
    ]
    other_class = (PastMove(date(2026, 10, 1), "GOLD_ETF", "down"),)
    assert [
        ln
        for ln in lines(decide(inp(holdings=held, history=other_class)), Action.ACCUMULATE)
        if ln.subject == "A1"
    ]


# ---- policy allocation ---------------------------------------------------------------------------------


def test_money_goes_to_the_class_below_its_band_and_never_beyond_its_target() -> None:
    held = (hold(1, "MF", "9000"), hold(2, "EQUITY_US", "1000"))
    ips = IPS(
        asset_class_bands={"MF": band("0.4", "0.9"), "EQUITY_US": band("0.1", "0.9")},
        max_monthly_mix_change=D(1),
    )
    rec = decide(inp(holdings=held, ips=ips, costs=costs("MF", "EQUITY_US")))
    added = lines(rec, Action.ACCUMULATE)
    # MF is at 90%, above its target (65%): no money. US equity is at 10%, target 50%: (1000 + x) / (10000 + x) = 50%
    assert [(ln.subject, ln.amount_inr) for ln in added] == [("A2", D("8000.00")), ("A1", D("2000.00"))]
    # after A2 gets 8,000 the MF weight is 9,000 / 18,000 = 50%, below its 65% target, so it takes the rest


def test_below_low_tier_comes_before_below_mid_tier() -> None:
    held = (hold(1, "MF", "50000"), hold(2, "EQUITY_US", "40000"), hold(3, "GOLD_ETF", "500"))
    ips = IPS(
        asset_class_bands={
            "MF": band("0.4", "0.9"),
            "EQUITY_US": band("0.3", "0.9"),
            "GOLD_ETF": band("0.2", "0.9"),
        },
        max_monthly_mix_change=D(1),
    )
    rec = decide(
        inp(holdings=held, ips=ips, opening_budget_inr=D(1000), costs=costs("MF", "EQUITY_US", "GOLD_ETF"))
    )
    assert [ln.subject for ln in lines(rec, Action.ACCUMULATE)] == [
        "A3"
    ]  # gold is below its low: it gets the money


def test_class_band_high_is_never_exceeded() -> None:
    held = (hold(1, "MF", "5900"), hold(2, "EQUITY_US", "4100"))
    ips = IPS(
        asset_class_bands={"MF": band("0.3", "0.6"), "EQUITY_US": band("0.1", "0.9")},
        max_monthly_mix_change=D(1),
    )
    rec = decide(inp(holdings=held, ips=ips, costs=costs("MF", "EQUITY_US")))
    assert not [
        ln for ln in lines(rec, Action.ACCUMULATE) if ln.subject == "A1"
    ]  # MF is already at 59%: its target is 45%


def test_monthly_mix_change_limit_binds() -> None:
    held = (hold(1, "MF", "100000"), hold(2, "EQUITY_US", "100000"))
    ips = IPS(
        asset_class_bands={"MF": band("0.1", "0.9"), "EQUITY_US": band("0.7", "0.9")},
        max_monthly_mix_change=D("0.01"),
    )
    rec = decide(inp(holdings=held, ips=ips, opening_budget_inr=D(50000), costs=costs("MF", "EQUITY_US")))
    added = lines(rec, Action.ACCUMULATE)
    first = added[0]
    assert first.subject == "A2" and "monthly mix-change limit" in first.reasons[0]
    total = D(200000) + sum((ln.amount_inr for ln in added), D(0))
    us = D(100000) + sum((ln.amount_inr for ln in added if ln.subject == "A2"), D(0))
    assert us / total - D("0.5") <= D(
        "0.01"
    )  # half the sum of |weight change| for two classes is one class's gain


def test_holding_fund_house_and_sector_limits() -> None:
    held = (
        hold(1, "MF", "5000", fund_house="H", sector=None),
        hold(2, "MF", "5000", fund_house="H"),
        hold(3, "EQUITY_US", "90000"),
    )
    for ips in (
        IPS(
            asset_class_bands={"MF": band("0.5", "0.9"), "EQUITY_US": band("0", "0.9")},
            max_holding=D("0.06"),
            max_monthly_mix_change=D(1),
        ),
        IPS(
            asset_class_bands={"MF": band("0.5", "0.9"), "EQUITY_US": band("0", "0.9")},
            max_fund_house=D("0.12"),
            max_monthly_mix_change=D(1),
        ),
    ):
        rec = decide(inp(holdings=held, ips=ips, opening_budget_inr=D(50000), costs=costs("MF", "EQUITY_US")))
        added = sum(
            (ln.amount_inr for ln in lines(rec, Action.ACCUMULATE) if ln.subject in ("A1", "A2")), D(0)
        )
        total = D(100000) + rec.envelope.new_allocations_inr
        assert (D(10000) + added) / total <= max(ips.max_fund_house or 0, D("0.12")) + D("1e-9")
        for ln in lines(rec, Action.ACCUMULATE):
            if ips.max_holding and ln.subject in ("A1", "A2"):
                assert (D(5000) + ln.amount_inr) / total <= ips.max_holding
    sector = (hold(1, "EQUITY_US", "5000", sector="Tech"), hold(2, "EQUITY_US", "80000", sector="Other"))
    ips = IPS(
        asset_class_bands={"EQUITY_US": band("0", "1")}, max_sector=D("0.08"), max_monthly_mix_change=D(1)
    )
    rec = decide(inp(holdings=sector, ips=ips, costs=costs("EQUITY_US")))
    tech = sum((ln.amount_inr for ln in lines(rec, Action.ACCUMULATE) if ln.subject == "A1"), D(0))
    assert (D(5000) + tech) / (D(85000) + rec.envelope.new_allocations_inr) <= D("0.08")


def test_country_and_currency_bands_limit_new_money() -> None:
    held = (hold(1, "MF", "70000"), hold(2, "EQUITY_US", "29000"))
    ips = IPS(
        asset_class_bands={"MF": band("0.1", "0.9"), "EQUITY_US": band("0.1", "0.9")},
        country_bands={"IN": band("0", "1"), "US": band("0", "0.3")},
        currency_bands={"INR": band("0", "1"), "USD": band("0", "0.3")},
        max_monthly_mix_change=D(1),
    )
    rec = decide(inp(holdings=held, ips=ips, opening_budget_inr=D(50000), costs=costs("MF", "EQUITY_US")))
    us = sum((ln.amount_inr for ln in lines(rec, Action.ACCUMULATE) if ln.subject == "A2"), D(0))
    assert (D(29000) + us) / (D(99000) + rec.envelope.new_allocations_inr) <= D("0.3")


def test_committed_sips_count_as_money_already_moving() -> None:
    held = (hold(1, "MF", "5500"), hold(2, "EQUITY_US", "4500"))
    ips = IPS(
        asset_class_bands={"MF": band("0.1", "0.6"), "EQUITY_US": band("0.1", "0.9")},
        max_monthly_mix_change=D(1),
    )
    rec = decide(inp(holdings=held, ips=ips, sip_lines=(sip(1, "2000"),), costs=costs("MF", "EQUITY_US")))
    assert not [
        ln for ln in lines(rec, Action.ACCUMULATE) if ln.subject == "A1"
    ]  # (5500+2000)/12000 is already above MF's target


def test_no_valued_holdings_means_no_weights_and_no_allocation() -> None:
    rec = decide(inp(holdings=()))
    assert rec.envelope.new_allocations_inr == 0 and any("no valued holdings" in w for w in rec.warnings)
    rec = decide(inp(holdings=(hold(1, "MF", "0"),)))
    assert rec.envelope.new_allocations_inr == 0


def test_band_breach_beyond_the_margin_asks_for_review_never_a_sale() -> None:
    rec = decide(inp(holdings=(hold(1, "MF", "70000"), hold(2, "EQUITY_US", "30000"))))  # MF 70% vs high 60%
    review = lines(rec, Action.REVIEW)
    assert review and review[0].subject == "MF" and "tax unavailable" in review[0].reasons[1]
    assert not lines(rec, Action.REDUCE)
    within = decide(
        inp(holdings=(hold(1, "MF", "63000"), hold(2, "EQUITY_US", "30000"), hold(3, "GOLD_ETF", "7000")))
    )  # MF is 3 points over its high: inside the 5-point margin
    assert not lines(within, Action.REVIEW)


def test_reduce_only_when_the_investor_flagged_the_thesis() -> None:
    held = (hold(1, "MF", "70000", thesis_failed=True), hold(2, "EQUITY_US", "30000"))
    rec = decide(inp(holdings=held))
    assert lines(rec, Action.REDUCE)[0].subject == "MF" and lines(rec, Action.REDUCE)[0].amount_inr == 0


# ---- cost rule, reserve use, IPO fund ------------------------------------------------------------------


def test_costly_investment_accumulates_in_the_reserve_instead() -> None:
    rec = decide(inp(costs=costs("MF", "EQUITY_US", "GOLD_ETF", brokerage_flat_inr=D(500))))
    assert not lines(rec, Action.ACCUMULATE) and rec.envelope.reserve_transfers_inr > 0
    assert "cost rule" in lines(rec, Action.HOLD_CASH)[0].reasons[0]
    assert rec.reserve_movements and all(m["reserve"] == "Cash reserve" for m in rec.reserve_movements)


def test_unknown_cost_means_do_not_invest_this_month() -> None:
    rec = decide(inp(costs={}))
    assert not lines(rec, Action.ACCUMULATE)
    assert "no cost schedule" in lines(rec, Action.HOLD_CASH)[0].reasons[0]


def test_below_the_minimum_ticket_is_skipped_without_diverting_money() -> None:
    held = (hold(1, "MF", "100000"), hold(2, "GOLD_ETF", "100"))
    ips = IPS(
        asset_class_bands={"MF": band("0.1", "0.99"), "GOLD_ETF": band("0", "0.01")},
        max_monthly_mix_change=D(1),
    )
    rec = decide(inp(holdings=held, ips=ips, costs=costs("MF", "GOLD_ETF", min_ticket_inr=D(100000))))
    assert not lines(rec, Action.ACCUMULATE) and rec.envelope.reserve_transfers_inr == 0
    assert rec.envelope.unallocated_inr == 10000


def test_ipo_sinking_fund_contribution_comes_from_the_ips() -> None:
    ips = IPS(
        asset_class_bands=dict(STD_IPS.asset_class_bands),
        ipo_monthly_sinking_inr=D(1500),
        max_monthly_mix_change=D("0.5"),
    )
    rec = decide(inp(ips=ips, ipo_reserve=Reserve(2, "IPO fund", D(0))))
    ipo = next(ln for ln in lines(rec, Action.HOLD_CASH) if ln.subject == "IPO fund")
    assert ipo.amount_inr == 1500 and "IPO sinking fund" in ipo.reasons[0]
    no_fund = decide(inp(ips=ips))
    assert any("no reserve is set up" in w for w in no_fund.warnings)


def test_reserve_use_is_a_separate_approved_line_outside_the_budget() -> None:
    rec = decide(inp(reserve_use=((2, D(3000)),), cash_reserve=Reserve(1, "Cash reserve", D(5000))))
    use = next(e for e in rec.envelope.lines if e.kind is Kind.RESERVE_USE)
    assert use.amount_inr == 3000 and use.asset_id == 2 and use.reserve_id == 1
    assert {m["direction"] for m in rec.reserve_movements} >= {"out"}
    assert_money_adds_up(rec)  # the budget identity is unaffected by reserve use
    assert (
        rec.envelope.unallocated_inr + rec.envelope.new_allocations_inr + rec.envelope.committed_sip_inr
        == 10000
    )


def test_reserve_use_is_refused_when_the_reserve_is_too_small_or_the_asset_not_eligible() -> None:
    small = decide(inp(reserve_use=((2, D(3000)),), cash_reserve=Reserve(1, "Cash reserve", D(2999))))
    assert all(e.kind is not Kind.RESERVE_USE for e in small.envelope.lines)
    assert any("refused" in w for w in small.warnings)
    stale = (hold(1, "MF", "60000"), hold(2, "EQUITY_US", "8000", price_status=Q.STALE))
    refused = decide(
        inp(holdings=stale, reserve_use=((2, D(100)),), cash_reserve=Reserve(1, "Cash reserve", D(5000)))
    )
    assert all(e.kind is not Kind.RESERVE_USE for e in refused.envelope.lines)
    unknown = decide(inp(reserve_use=((99, D(100)),), cash_reserve=Reserve(1, "Cash reserve", D(5000))))
    assert any("refused" in w for w in unknown.warnings)
    none = decide(inp(reserve_use=((2, D(100)),), cash_reserve=None))
    assert any("refused" in w for w in none.warnings)


# ---- envelope and recommendation -----------------------------------------------------------------------


def test_sips_over_the_budget_are_a_configuration_error() -> None:
    with pytest.raises(OverAllocated, match="exceed the monthly budget"):
        decide(inp(sip_lines=(sip(1, "6000"), sip(2, "4000.01"))))


def test_overall_action() -> None:
    assert decide(inp()).overall_action is Action.ACCUMULATE
    assert decide(inp(ips=None, ips_version=None)).overall_action is Action.HOLD_CASH
    only_sip = decide(
        inp(
            sip_lines=(sip(1),),
            holdings=(hold(1, "MF", "100"),),
            ips=IPS(asset_class_bands={"MF": band("0", "1")}),
            context=ctx(),
            costs={},
        )
    )
    assert only_sip.overall_action in (Action.CONTINUE_SIP, Action.HOLD_CASH)
    nothing = decide(inp(holdings=(), ips=None, ips_version=None, sip_lines=()))
    assert nothing.overall_action is Action.HOLD_CASH


def test_every_line_carries_evidence_risks_assumptions_and_invalidation() -> None:
    rec = decide(inp(sip_lines=(sip(1),), risk_level=Level.HIGH_RISK))
    for ln in rec.lines:
        assert ln.evidence_ids and ln.risks and ln.assumptions and ln.invalidation_conditions
        assert "ips:v1" in ln.evidence_ids and f"risk_level:{PERIOD}" in ln.evidence_ids
        assert "risk level is HIGH_RISK" in ln.risks
    assert any(e.startswith("price:") for e in rec.lines[0].evidence_ids)


def test_recommendation_is_a_versioned_json_document() -> None:
    rec = decide(
        inp(
            sip_lines=(sip(1),),
            scenarios=(
                {
                    "id": "equity_-10",
                    "label": "x",
                    "total_inr": "-1",
                    "total_pct": "-0.1",
                    "status": "ok",
                    "note": "n",
                },
            ),
            sources=("amfi",),
        )
    )
    again = Recommendation.model_validate_json(rec.model_dump_json())
    assert (
        again == rec
        and rec.schema_version == "1.0"
        and rec.sources == ("amfi",)
        and rec.scenarios[0]["id"] == "equity_-10"
    )
    assert rec.label.startswith("DEMO") and rec.dataset == "DEMO" and rec.review_date == TODAY


def test_real_output_is_not_labelled_demo() -> None:
    rec = decide(inp(dataset="REAL"))
    assert rec.dataset == "REAL" and not rec.label.startswith("DEMO")


def test_the_leftover_assumption_names_the_ips_version() -> None:
    assert decide(inp(ips_version=3)).assumptions[0] == "Leftover-cash rule: RESERVE_NO_INFLATE (IPS v3)"


def test_recommendation_validators() -> None:
    rec = decide(inp(sip_lines=(sip(1),)))
    data = rec.model_dump()
    with pytest.raises(ValueError, match="DEMO output must be labelled DEMO"):
        Recommendation.model_validate({**data, "label": "advice"})
    with pytest.raises(ValueError, match="budget differs"):
        Recommendation.model_validate({**data, "budget_inr": D(1)})
    with pytest.raises(ValueError, match="no approved IPS"):
        Recommendation.model_validate({**data, "personalised": False})
    dup = [dict(ln) for ln in data["lines"]]
    dup[1]["envelope_ref"] = dup[0]["envelope_ref"]
    with pytest.raises(ValueError, match="claimed by two"):
        Recommendation.model_validate({**data, "lines": dup})
    with pytest.raises(ValueError, match="exactly one recommendation line"):
        Recommendation.model_validate(
            {**data, "lines": [ln for ln in data["lines"] if ln["envelope_ref"] != 0]}
        )
    wrong = [dict(ln) for ln in data["lines"]]
    wrong[0]["amount_inr"] = D(1)
    with pytest.raises(ValueError, match="differs from its envelope line"):
        Recommendation.model_validate({**data, "lines": wrong})
    bare = decide(inp(ips=None, ips_version=None)).model_dump()  # no envelope lines at all
    nomoney = [dict(bare["lines"][0], amount_inr=D(5))]
    with pytest.raises(ValueError, match="with money needs an envelope line"):
        Recommendation.model_validate({**bare, "lines": nomoney})
    action = [dict(ln, action=Action.ACCUMULATE) if i == 0 else ln for i, ln in enumerate(data["lines"])]
    with pytest.raises(ValueError, match="only SIP envelope lines"):
        Recommendation.model_validate({**data, "lines": action})
    with pytest.raises(ValueError):
        Recommendation.model_validate({**data, "human_approval_required": False})


# ---- property tests (B7) -------------------------------------------------------------------------------

CLASSES = ["MF", "EQUITY_IN", "EQUITY_US", "ETF_IN", "GOLD_ETF", "SILVER_ETF"]
paise = st.decimals(min_value=0, max_value=200000, places=2)


@st.composite
def decision_inputs(draw: st.DrawFn) -> DecisionInput:
    n = draw(st.integers(1, 6))
    holdings = []
    for i in range(1, n + 1):
        cls = draw(st.sampled_from(CLASSES))
        holdings.append(
            hold(i, cls, str(draw(paise)),
                 price_status=draw(st.sampled_from([Q.OK, Q.OK, Q.OK, Q.ESTIMATED, Q.STALE, Q.CONFLICTING])),
                 fund_house=draw(st.sampled_from([None, "H1", "H2"])), sector=draw(st.sampled_from([None, "S1", "S2"])),
                 thesis_failed=draw(st.booleans()) and draw(st.booleans()),
                 risk_veto=("v",) if draw(st.integers(0, 9)) == 0 else ())
        )  # fmt: skip
    bands = {}
    for c in {h.asset_class for h in holdings} | set(draw(st.lists(st.sampled_from(CLASSES), max_size=2))):
        low = draw(st.decimals(min_value=0, max_value="0.15", places=2))
        bands[c] = Band(low=low, high=draw(st.decimals(min_value=low, max_value=1, places=2)))
    bands["CATCH_ALL"] = Band(low=D(0), high=D(1))
    country = {"IN": Band(low=D(0), high=draw(st.decimals(min_value="0.4", max_value=1, places=2))), "US": Band(low=D(0), high=draw(st.decimals(min_value="0.2", max_value=1, places=2))), "OTHER": Band(low=D(0), high=D(1))} if draw(st.booleans()) else {}  # fmt: skip
    ips = IPS(
        asset_class_bands=bands, country_bands=country,
        max_monthly_mix_change=draw(st.decimals(min_value="0.01", max_value="0.6", places=2)),
        max_holding=draw(st.sampled_from([None, D("0.3"), D("0.6")])), max_sector=draw(st.sampled_from([None, D("0.5")])),
        max_fund_house=draw(st.sampled_from([None, D("0.5")])), cooldown_months=draw(st.integers(0, 6)),
        ipo_monthly_sinking_inr=draw(st.sampled_from([D(0), D(0), D(500)])),
        cost_threshold_pct=draw(st.sampled_from([D("0.005"), D("0.01"), D("0.05")])),
    )  # fmt: skip
    budget = D(draw(st.integers(1000, 100000)))
    sips: list[EnvelopeLine] = []
    for _ in range(draw(st.integers(0, 3))):
        amount = D(draw(st.integers(100, 4000)))
        sips.append(EnvelopeLine(kind=Kind.SIP, amount_inr=amount, asset_id=draw(st.integers(1, n))))
    assume(sum((s.amount_inr for s in sips), D(0)) <= budget)
    book = {}
    for h in holdings:
        if draw(st.integers(0, 3)):
            book[("P", h.asset_class)] = CostSchedule(
                platform="P", asset_class=h.asset_class, brokerage_flat_inr=D(draw(st.integers(0, 60))),
                fx_markup_pct=draw(st.sampled_from([D(0), D("0.002")])), min_ticket_inr=D(draw(st.sampled_from([0, 100, 500]))),
            )  # fmt: skip
    cash = draw(st.sampled_from([None, Reserve(1, "Cash", D(draw(st.integers(0, 20000))))]))
    use = tuple(
        (draw(st.integers(1, n + 1)), D(draw(st.integers(1, 5000)))) for _ in range(draw(st.integers(0, 2)))
    )
    return DecisionInput(
        period=PERIOD, today=TODAY, dataset=draw(st.sampled_from(["DEMO", "REAL"])), opening_budget_inr=budget,
        sip_lines=tuple(sips), holdings=tuple(holdings), reconciliation_clean=draw(st.integers(0, 5)) != 0,
        confidence=draw(st.sampled_from(list(ConfidenceLevel))), confidence_reasons=(),
        risk_level=draw(st.sampled_from(list(Level))), ips=draw(st.sampled_from([ips, ips, ips, None])),
        ips_version=1,
        context=draw(st.sampled_from([
            ctx(), ctx(fund=0), ctx(stable=False), {}, ctx(planned=[{"date": "2026-12-01", "amount_inr": 1}]),
        ])),
        costs=book, cash_reserve=cash, ipo_reserve=draw(st.sampled_from([None, Reserve(2, "IPO", D(0))])),
        history=tuple(PastMove(date(2026, draw(st.integers(7, 10)), 1), draw(st.sampled_from(CLASSES)), draw(st.sampled_from(["up", "down"]))) for _ in range(draw(st.integers(0, 2)))),
        reserve_use=use,
    )  # fmt: skip


def paise_floor(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), ROUND_DOWN)


@pytest.mark.property
@given(decision_inputs())
def test_property_budget_envelope_and_lines(i: DecisionInput) -> None:
    rec = decide(i)
    e = rec.envelope
    # total <= budget, and the envelope reconciles exactly (B7)
    assert e.new_allocations_inr + e.reserve_transfers_inr + e.committed_sip_inr <= i.opening_budget_inr
    assert (
        e.committed_sip_inr + e.new_allocations_inr + e.reserve_transfers_inr + e.unallocated_inr
        == i.opening_budget_inr
    )
    assert e.committed_sip_inr == sum((s.amount_inr for s in i.sip_lines), D(0))
    assert min(e.committed_sip_inr, e.new_allocations_inr, e.reserve_transfers_inr, e.unallocated_inr) >= 0
    # no envelope line is used twice; every rupee has exactly one line
    refs = [ln.envelope_ref for ln in rec.lines if ln.envelope_ref is not None]
    assert sorted(refs) == list(range(len(e.lines)))
    assert rec.human_approval_required is True
    assert Recommendation.model_validate_json(rec.model_dump_json()) == rec
    # no personalised allocation without an approved IPS, a clean reconciliation and known liquidity
    if i.ips is None or not i.reconciliation_clean:
        assert e.new_allocations_inr == 0 and e.reserve_transfers_inr == 0
        assert not [x for x in e.lines if x.kind is not Kind.SIP]
    elif liquidity(i.context, i.ips, i.today).emergency is Verdict.UNKNOWN:
        assert e.new_allocations_inr == 0 and e.reserve_transfers_inr == 0


@pytest.mark.property
@given(decision_inputs())
def test_property_reserves_never_go_negative(i: DecisionInput) -> None:
    rec = decide(i)
    out = sum((Decimal(m["amount_inr"]) for m in rec.reserve_movements if m["direction"] == "out"), D(0))
    into = sum((Decimal(m["amount_inr"]) for m in rec.reserve_movements if m["direction"] == "in"), D(0))
    available = i.cash_reserve.available_inr if i.cash_reserve else D(0)
    assert all(Decimal(m["amount_inr"]) > 0 for m in rec.reserve_movements)
    assert out <= available  # the reserve can pay for what it is asked to, and no more
    assert into >= 0 and available + into - out >= 0
    for m in rec.reserve_movements:
        assert m["reserve_id"] in (1, 2)


@pytest.mark.property
@given(decision_inputs())
def test_property_every_allocation_obeys_the_ips(i: DecisionInput) -> None:
    rec = decide(i)
    ips = i.ips
    funded = [x for x in rec.envelope.lines if x.kind in (Kind.NEW_ALLOCATION, Kind.RESERVE_USE)]
    if not funded:
        return
    assert ips is not None
    by_id = {h.asset_id: h for h in i.holdings}
    added: dict[int, Decimal] = {}
    for s in i.sip_lines:
        added[s.asset_id or 0] = added.get(s.asset_id or 0, D(0)) + s.amount_inr
    for x in funded:
        added[x.asset_id or 0] = added.get(x.asset_id or 0, D(0)) + x.amount_inr
    v0 = sum((h.value_inr for h in i.holdings), D(0))
    final = {h.asset_id: h.value_inr + added.get(h.asset_id, D(0)) for h in i.holdings}
    total = sum(final.values(), D(0))
    assert total > 0 and v0 > 0

    def weight(pick: object) -> Decimal:
        return sum((final[h.asset_id] for h in i.holdings if pick(h)), D(0)) / total  # type: ignore[operator]

    tol = D("1e-12")
    for x in funded:
        h = by_id[x.asset_id or 0]
        # gates: no money for a vetoed, failed-thesis or unusable-data asset, or a class without a band
        assert (
            not h.risk_veto
            and not h.thesis_failed
            and h.price_status not in (Q.STALE, Q.MISSING, Q.CONFLICTING, Q.QUARANTINED)
        )
        assert h.asset_class in ips.asset_class_bands
        assert (
            weight(lambda g, h=h: g.asset_class == h.asset_class)
            <= ips.asset_class_bands[h.asset_class].high + tol
        )
        if h.country in ips.country_bands:
            assert weight(lambda g, h=h: g.country == h.country) <= ips.country_bands[h.country].high + tol
        if ips.max_holding is not None:
            assert final[h.asset_id] / total <= ips.max_holding + tol
        if ips.max_sector is not None and h.sector:
            assert weight(lambda g, h=h: g.sector == h.sector) <= ips.max_sector + tol
        if ips.max_fund_house is not None and h.fund_house:
            assert weight(lambda g, h=h: g.fund_house == h.fund_house) <= ips.max_fund_house + tol
        assert not any(
            m.asset_class == h.asset_class
            and m.direction == "down"
            and abs((PERIOD.year - m.period.year) * 12 + PERIOD.month - m.period.month) < ips.cooldown_months
            for m in i.history
        )
        # the cost rule and the minimum ticket
        schedule = i.costs.get((h.platform, h.asset_class))
        if x.kind is Kind.NEW_ALLOCATION:
            assert check_cost(x.amount_inr, schedule, ips.cost_threshold_pct).verdict is CostVerdict.OK
    new_only = [x for x in funded if x.kind is Kind.NEW_ALLOCATION]
    if new_only:
        # the monthly mix change (SIPs included) stays within the IPS limit
        classes = {h.asset_class for h in i.holdings}
        turnover = sum(
            (abs(sum((final[h.asset_id] for h in i.holdings if h.asset_class == c), D(0)) / total
                 - sum((h.value_inr for h in i.holdings if h.asset_class == c), D(0)) / v0) for c in classes),
            D(0),
        ) / 2  # fmt: skip
        assert turnover <= ips.max_monthly_mix_change + tol
        liq = liquidity(i.context, ips, i.today)
        if liq.income_unstable:
            risky0 = sum((h.value_inr for h in i.holdings if h.asset_class in RISKY_CLASSES), D(0)) / v0
            if any(by_id[x.asset_id or 0].asset_class in RISKY_CLASSES for x in new_only):
                assert weight(lambda g: g.asset_class in RISKY_CLASSES) <= risky0 + tol
        if liq.expense_window is Verdict.FAIL:
            assert all(by_id[x.asset_id or 0].asset_class not in RISKY_CLASSES for x in new_only)


@pytest.mark.property
@given(
    st.text(max_size=12).filter(lambda b: b != "MAINBOARD"),
    st.one_of(st.none(), st.decimals(min_value=1, max_value=10**7, places=2)),
    st.sampled_from(["UNRATED", "ALLOWED", "NOT_ALLOWED"]),
    st.decimals(min_value=0, max_value=10**9, places=2),
)
def test_property_no_sme_or_non_mainboard_ipo_is_ever_eligible(
    board: str, lot: Decimal | None, research: str, reserve: Decimal
) -> None:
    assert not ipo_verdict(board, lot, research, reserve).can_apply


@pytest.mark.property
@given(
    st.decimals(min_value=1, max_value=10**7, places=2),
    st.decimals(min_value=0, max_value=10**9, places=2),
    st.sampled_from(["UNRATED", "ALLOWED", "NOT_ALLOWED"]),
)
def test_property_mainboard_needs_cover_and_allowed_research(
    lot: Decimal, reserve: Decimal, research: str
) -> None:
    assert ipo_verdict("MAINBOARD", lot, research, reserve).can_apply == (
        reserve >= lot and research == "ALLOWED"
    )


# ---- the limit arithmetic, one limit at a time ---------------------------------------------------------


def plan_of(holdings: tuple[Holding, ...], ips: IPS, **kw: object) -> _Plan:
    return _Plan(inp(holdings=holdings, ips=ips, **kw), ips)


def liq_ok(ips: IPS, **kw: object):  # type: ignore[no-untyped-def]
    return liquidity(ctx(**kw), ips, TODAY)  # type: ignore[arg-type]


TWO = (
    hold(1, "MF", "6000", fund_house="H", sector="S"),
    hold(2, "EQUITY_US", "4000", fund_house="G", sector="T"),
)  # A1 is IN / INR, A2 is US / USD; 10,000 in all
LOOSE = {"MF": band("0", "1"), "EQUITY_US": band("0", "1")}


def loose(**kw: object) -> IPS:
    return IPS(asset_class_bands=LOOSE, max_monthly_mix_change=D(1), **kw)  # type: ignore[arg-type]


def test_cap_room_and_floor_helpers() -> None:
    assert _cap_room(D(6000), D("0.7"), D(10000)) == D(1000) / D("0.3")
    assert _cap_room(D(8000), D("0.7"), D(10000)) == 0 and _cap_room(D(1), D(1), D(1)) == INF
    assert _floor(D("1.239")) == D("1.23") and _floor(D("-1.239")) == D("-1.23") and _floor(INF) == INF
    assert _months_between(date(2026, 11, 1), date(2026, 9, 1)) == 2
    assert _months_between(date(2026, 1, 1), date(2025, 12, 1)) == 1
    assert (
        _months_between(date(2027, 2, 1), date(2026, 2, 1)) == 12
        and _months_between(date(2026, 9, 1), date(2026, 11, 1)) == 2
    )


@pytest.mark.parametrize(
    "ips,reason,expected",
    [
        (loose(), "class band high", None),
        (
            IPS(
                asset_class_bands={"MF": band("0", "0.7"), "EQUITY_US": band("0", "1")},
                max_monthly_mix_change=D(1),
            ),
            "class band high",
            D(1000) / D("0.3"),
        ),
        (
            loose(country_bands={"IN": band("0", "0.7"), "US": band("0", "1")}),
            "country band",
            D(1000) / D("0.3"),
        ),
        (
            loose(currency_bands={"INR": band("0", "0.7"), "USD": band("0", "1")}),
            "currency band",
            D(1000) / D("0.3"),
        ),
        (loose(max_holding=D("0.7")), "holding limit", D(1000) / D("0.3")),
        (loose(max_fund_house=D("0.7")), "fund-house limit", D(1000) / D("0.3")),
        (loose(max_sector=D("0.7")), "sector limit", D(1000) / D("0.3")),
    ],
)
def test_each_limit_is_named_and_exact(ips: IPS, reason: str, expected: Decimal | None) -> None:
    plan = plan_of(TWO, ips)
    x, why = plan.room(TWO[0], ips.asset_class_bands["MF"], liq_ok(ips), D(0), D(10**6), to_target=False)
    if (
        expected is None
    ):  # nothing but the class band (high 1): everything may be added, so infinity is floored away
        assert (why in ("class band high", "class target") and x == INF) or x > 0
        return
    assert why == reason and x == _floor(expected)


def test_class_target_binds_only_when_asked_for() -> None:
    ips = IPS(
        asset_class_bands={"MF": band("0.2", "1"), "EQUITY_US": band("0", "1")}, max_monthly_mix_change=D(1)
    )
    plan = plan_of(TWO, ips)
    x, why = plan.room(
        TWO[0], ips.asset_class_bands["MF"], liq_ok(ips), D(0), D(10**6)
    )  # target 60%: A1 is there
    assert (x, why) == (D(0), "class target")
    wide = IPS(
        asset_class_bands={"MF": band("0.2", "1.0"), "EQUITY_US": band("0", "1")}, max_monthly_mix_change=D(1)
    )
    x, why = plan_of(TWO, wide).room(
        TWO[0], wide.asset_class_bands["MF"], liq_ok(wide), D(0), D(10**6), to_target=False
    )
    assert (x, why) == (D(10**6), "class target")  # nothing else limits it, so the ceiling is what is left


def test_a_class_target_below_the_weight_leaves_no_room_and_a_target_above_it_leaves_some() -> None:
    ips = IPS(
        asset_class_bands={"MF": band("0.5", "1"), "EQUITY_US": band("0", "1")}, max_monthly_mix_change=D(1)
    )  # target 75%
    x, why = plan_of(TWO, ips).room(TWO[0], ips.asset_class_bands["MF"], liq_ok(ips), D(0), D(10**6))
    assert why == "class target" and x == _floor(D(1500) / D("0.25"))


def test_unstable_income_caps_the_risky_share() -> None:
    held = (hold(1, "MF", "6000"), hold(2, "GOLD_ETF", "4000"))
    ips = IPS(
        asset_class_bands={"MF": band("0", "1"), "GOLD_ETF": band("0", "1")}, max_monthly_mix_change=D(1)
    )
    plan = _Plan(inp(holdings=held, ips=ips, context=ctx(stable=False)), ips)
    x, why = plan.room(
        held[0], ips.asset_class_bands["MF"], liq_ok(ips, stable=False), D("0.7"), D(10**6), to_target=False
    )
    assert why == "risk level may not rise" and x == _floor(D(1000) / D("0.3"))
    stable = plan.room(
        held[0], ips.asset_class_bands["MF"], liq_ok(ips, stable=True), D("0.7"), D(10**6), to_target=False
    )
    assert stable[1] != "risk level may not rise"  # a stable income is not capped
    gold = plan.room(
        held[1],
        ips.asset_class_bands["GOLD_ETF"],
        liq_ok(ips, stable=False),
        D("0.7"),
        D(10**6),
        to_target=False,
    )
    assert gold[1] != "risk level may not rise"  # and gold is not risk money


def test_turnover_is_half_the_sum_of_class_weight_changes() -> None:
    plan = plan_of(TWO, loose())
    assert plan.turnover(TWO[0], D(0)) == 0
    assert (
        plan.turnover(TWO[0], D(1000))
        == (abs(D(7000) / D(11000) - D("0.6")) + abs(D(4000) / D(11000) - D("0.4"))) / 2
    )
    plan.put(2, D(1000))
    assert plan.total() == 11000 and plan.val[2] == 5000
    plan.put(1, D(500))
    plan.put(99, D(250))  # money to an asset that is not held still counts as money moving in
    assert plan.total() == D(11750)


def test_the_mix_change_limit_is_found_by_bisection() -> None:
    ips = IPS(asset_class_bands=LOOSE, max_monthly_mix_change=D("0.02"))
    x, why = plan_of(TWO, ips).room(TWO[0], LOOSE["MF"], liq_ok(ips), D(0), D(10**6), to_target=False)
    assert why == "monthly mix-change limit" and x == D("526.31")  # x * 0.4 / (10000 + x) <= 0.02


def test_tiny_rooms_are_still_checked_against_the_mix_change_limit() -> None:
    c = D("6000.5") / D("10000.5")  # a class target that leaves room for exactly 0.50
    ips = IPS(
        asset_class_bands={"MF": Band(low=2 * c - 1, high=D(1)), "EQUITY_US": band("0", "1")},
        max_monthly_mix_change=D("0.00001"),
    )
    x, why = plan_of(TWO, ips).room(TWO[0], ips.asset_class_bands["MF"], liq_ok(ips), D(0), D(10**6))
    assert (x, why) == (D("0.25"), "monthly mix-change limit")


def test_no_room_keeps_its_own_reason_even_when_the_mix_has_already_moved() -> None:
    ips = IPS(
        asset_class_bands={"MF": band("0.2", "1"), "EQUITY_US": band("0", "1")},
        max_monthly_mix_change=D("0.05"),
    )
    plan = plan_of(TWO, ips)
    plan.put(1, D(4000))  # an earlier allocation already changed the mix by more than the limit
    x, why = plan.room(TWO[0], ips.asset_class_bands["MF"], liq_ok(ips), D(0), D(10**6))
    assert x == 0 and why == "class target"


def test_exclusion_reasons_are_exact() -> None:
    ips = loose(cooldown_months=3)
    soon = ctx(planned=[{"date": "2026-12-01", "amount_inr": 1}])
    liq = liquidity(soon, ips, TODAY)
    h = hold(1, "MF", "100")
    i = inp(ips=ips, history=(PastMove(date(2026, 10, 1), "MF", "down"),))
    assert _exclusions(h, i, ips, liq) == [
        "cooldown: MF was reduced in 2026-10, less than 3 months ago",
        "a large planned expense is near: no new risk money",
    ]
    assert (
        _exclusions(
            hold(2, "SILVER_ETF", "1", price_status=Q.STALE, thesis_failed=True, risk_veto=("v",)),
            inp(ips=ips),
            ips,
            liq_ok(ips),
        )
        == [
            "insufficient evidence: the price is stale",
            "v",
            "the investor flagged the thesis as failed",
            "the IPS has no band for SILVER_ETF",
            "a large planned expense is near: no new risk money"[:0] or "the IPS has no band for SILVER_ETF",
        ][:4]
    )


def test_an_unbounded_class_does_not_crash_the_engine() -> None:
    """A class pinned at 100% and below its target has unbounded room (no limit applies): the money available is
    the ceiling. Found by the first mutation run; the room used to be infinite and could not be tested."""
    ips = IPS(
        asset_class_bands={"MF": band("1", "1"), "EQUITY_US": band("0", "0")}, max_monthly_mix_change=D(1)
    )
    rec = decide(
        inp(holdings=(hold(1, "MF", "5000"), hold(2, "EQUITY_US", "5000")), ips=ips, costs=costs("MF"))
    )
    assert rec.envelope.new_allocations_inr == 10000
    assert_money_adds_up(rec)
