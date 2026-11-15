"""Budget envelope (B1): the validator mirrors the database CHECKs; SIPs are pre-committed."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from arthashreni.policy.envelope import (
    Envelope,
    EnvelopeLine,
    Kind,
    Mandate,
    OverAllocated,
    active_in,
    build_envelope,
    is_paise,
    month_end,
    sip_lines,
)

D = Decimal
P = date(2026, 11, 1)


def line(kind: Kind, amount: str, **kw: int) -> EnvelopeLine:
    return EnvelopeLine(kind=kind, amount_inr=D(amount), **kw)


def sip(amount: str = "2000", asset: int = 1) -> EnvelopeLine:
    return line(Kind.SIP, amount, asset_id=asset)


def test_build_envelope_reconciles_and_leaves_the_rest_unallocated() -> None:
    env = build_envelope(P, D(10000), [sip("2000"), line(Kind.NEW_ALLOCATION, "3000.50", asset_id=2),
                                       line(Kind.RESERVE_TRANSFER, "500", reserve_id=1)])  # fmt: skip
    assert (env.committed_sip_inr, env.new_allocations_inr, env.reserve_transfers_inr) == (
        2000,
        D("3000.50"),
        500,
    )
    assert env.unallocated_inr == D("4499.50")
    assert (
        env.committed_sip_inr + env.new_allocations_inr + env.reserve_transfers_inr + env.unallocated_inr
        == 10000
    )


def test_over_allocation_is_an_error_not_a_warning() -> None:
    with pytest.raises(OverAllocated):
        build_envelope(P, D(10000), [sip("6000"), line(Kind.NEW_ALLOCATION, "4000.01", asset_id=2)])


def test_exactly_the_budget_is_allowed() -> None:
    env = build_envelope(P, D(10000), [sip("6000"), line(Kind.NEW_ALLOCATION, "4000", asset_id=2)])
    assert env.unallocated_inr == 0


def test_an_envelope_that_does_not_add_up_cannot_be_built_directly() -> None:
    with pytest.raises(ValidationError, match="reconcile"):
        Envelope(period=P, opening_budget_inr=D(100), committed_sip_inr=D(10), new_allocations_inr=D(0),
                 reserve_transfers_inr=D(0), unallocated_inr=D(80), lines=(sip("10"),))  # fmt: skip


def test_negative_component_is_over_allocation() -> None:
    with pytest.raises(ValidationError, match="negative"):
        Envelope(period=P, opening_budget_inr=D(100), committed_sip_inr=D(120), new_allocations_inr=D(0),
                 reserve_transfers_inr=D(0), unallocated_inr=D(-20), lines=(sip("120"),))  # fmt: skip


def test_line_sums_must_match_the_totals() -> None:
    with pytest.raises(ValidationError, match="line sums"):
        Envelope(period=P, opening_budget_inr=D(100), committed_sip_inr=D(10), new_allocations_inr=D(0),
                 reserve_transfers_inr=D(0), unallocated_inr=D(90), lines=(sip("20"),))  # fmt: skip


def test_period_is_the_first_of_a_month() -> None:
    with pytest.raises(ValidationError, match="first day"):
        build_envelope(date(2026, 11, 2), D(100), [])


def test_reserve_use_lines_sit_outside_the_budget() -> None:
    env = build_envelope(P, D(100), [line(Kind.RESERVE_USE, "5000", asset_id=1, reserve_id=1)])
    assert env.unallocated_inr == 100 and len(env.lines) == 1


@pytest.mark.parametrize("bad", ["0", "-5", "10.001"])
def test_line_amounts_are_positive_rupees_and_paise(bad: str) -> None:
    with pytest.raises(ValidationError, match="positive rupees and paise"):
        line(Kind.SIP, bad, asset_id=1)


@pytest.mark.parametrize(
    "kind,fields",
    [(Kind.SIP, {}), (Kind.SIP, {"asset_id": 1, "reserve_id": 1}), (Kind.NEW_ALLOCATION, {"reserve_id": 1}),
     (Kind.RESERVE_TRANSFER, {"asset_id": 1}), (Kind.RESERVE_TRANSFER, {}), (Kind.RESERVE_USE, {"asset_id": 1})],
)  # fmt: skip
def test_each_kind_has_its_own_shape(kind: Kind, fields: dict[str, int]) -> None:
    with pytest.raises(ValidationError, match="wrong asset/reserve"):
        line(kind, "10", **fields)


def test_is_paise() -> None:
    assert is_paise(D("1.10")) and is_paise(D("5")) and not is_paise(D("1.005"))


def test_month_end() -> None:
    assert month_end(date(2026, 2, 1)) == date(2026, 2, 28)
    assert month_end(date(2028, 2, 1)) == date(2028, 2, 29)
    assert month_end(date(2026, 12, 1)) == date(2026, 12, 31)


def m(**kw: object) -> Mandate:
    base: dict[str, object] = dict(id=1, asset_id=7, account_id=1, amount_inr=D(2000), day_of_month=5,
                                   platform="ZERODHA_COIN", start_date=date(2026, 1, 1), end_date=None, status="ACTIVE")  # fmt: skip
    return Mandate(**{**base, **kw})  # type: ignore[arg-type]


def test_active_mandates_are_committed_and_others_are_not() -> None:
    assert active_in(m(), P)
    assert not active_in(m(status="PAUSED"), P) and not active_in(m(status="ENDED"), P)
    assert not active_in(m(start_date=date(2026, 12, 1)), P)  # starts next month
    assert active_in(m(start_date=date(2026, 11, 30)), P)  # starts on the last day
    assert not active_in(m(end_date=date(2026, 10, 31)), P)  # ended last month
    assert active_in(m(end_date=date(2026, 11, 1)), P)  # ends on the first day


def test_sip_lines_carry_the_mandate() -> None:
    lines = sip_lines([m(), m(id=2, status="PAUSED"), m(id=3, amount_inr=D("750.50"), asset_id=9)], P)
    assert [(x.kind, x.amount_inr, x.asset_id, x.sip_mandate_id) for x in lines] == [
        (Kind.SIP, 2000, 7, 1), (Kind.SIP, D("750.50"), 9, 3)]  # fmt: skip


def test_over_allocation_message_names_the_amounts() -> None:
    with pytest.raises(OverAllocated, match=r"^lines add up to 10000\.01, above the budget 10000$"):
        build_envelope(P, D(10000), [sip("10000.01")])
