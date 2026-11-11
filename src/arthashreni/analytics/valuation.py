"""Positions, valuation, P&L, income and costs (F1). Every figure is `Measured`.

Prices arrive as `Measured` in the asset's own currency; foreign assets need an FX `Measured` whose
rate type is named by the caller (ADR-0016). Ledger-derived figures (units, cost) are `ok` by
construction: the ledger is the source of truth, and unexplained reconciliation differences block the
monthly review separately (Phase 2).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from arthashreni.dq.measured import (
    Measured,
    QualityStatus,
    day_stamp,
    exact,
    m_div,
    m_mul,
    m_sub,
    m_sum,
    missing,
    with_status,
)
from arthashreni.ledger.types import ONE, ZERO, LotBook, TxnRow, TxnType

LEDGER = ("ledger",)


def from_ledger(value: Decimal, as_of: datetime, note: str | None = None) -> Measured[Decimal]:
    return Measured(value, QualityStatus.OK, as_of, LEDGER, (note,) if note else ())


@dataclass(frozen=True)
class AssetInfo:
    id: int
    name: str
    asset_class: str
    currency: str
    country: str
    sector: str | None = None
    market_cap: str | None = None
    fund_house: str | None = None


@dataclass(frozen=True)
class Position:
    """Units and remaining cost of one asset in one account, from the FIFO lots."""

    account_id: int
    asset: AssetInfo
    units: Decimal
    cost_inr: Decimal
    cost_orig: Decimal


@exact
def positions_from_book(book: LotBook, assets: Mapping[int, AssetInfo]) -> list[Position]:
    acc: dict[tuple[int, int], list[Decimal]] = {}
    for lot in book.lots:
        if lot.remaining_qty == 0:
            continue
        # Remaining cost in the original currency: the share of the lot's cost that is left.
        orig = lot.cost_orig * lot.remaining_cost_inr / lot.cost_inr if lot.cost_inr else ZERO
        row = acc.setdefault((lot.account_id, lot.asset_id), [ZERO, ZERO, ZERO])
        row[0] += lot.remaining_qty
        row[1] += lot.remaining_cost_inr
        row[2] += orig
    return [
        Position(account, assets[asset], units, cost_inr, cost_orig)
        for (account, asset), (units, cost_inr, cost_orig) in sorted(acc.items())
    ]


@dataclass(frozen=True)
class HoldingValue:
    position: Position
    units: Measured[Decimal]
    price: Measured[Decimal]  # in the asset's currency
    fx: Measured[Decimal] | None  # None for INR assets
    fx_rate_type: str | None
    value_local: Measured[Decimal]
    value_inr: Measured[Decimal]
    cost_inr: Measured[Decimal]
    unrealised_inr: Measured[Decimal]
    unrealised_pct: Measured[Decimal]


def value_position(
    pos: Position,
    price: Measured[Decimal],
    fx: Measured[Decimal] | None,
    fx_rate_type: str | None,
    as_of: date,
) -> HoldingValue:
    """Market value in the asset's currency and in INR. A foreign asset without `fx` is `missing`."""
    stamp = day_stamp(as_of)
    units, cost = from_ledger(pos.units, stamp), from_ledger(pos.cost_inr, stamp)
    value_local = m_mul(units, price)
    if pos.asset.currency == "INR":
        fx, fx_rate_type, value_inr = None, None, value_local
    elif fx is None or fx_rate_type is None:
        fx = None
        value_inr = missing(stamp, f"no {pos.asset.currency}/INR rate given for {pos.asset.name}")
    else:
        value_inr = m_mul(value_local, fx)
    unrealised = m_sub(value_inr, cost)
    return HoldingValue(
        pos, units, price, fx, fx_rate_type, value_local, value_inr, cost, unrealised, m_div(unrealised, cost)
    )


@dataclass(frozen=True)
class Totals:
    value_inr: Measured[Decimal]
    cost_inr: Measured[Decimal]
    unrealised_inr: Measured[Decimal]
    unrealised_pct: Measured[Decimal]
    holdings: int
    unvalued: tuple[str, ...]


def split_valued(holdings: Iterable[HoldingValue]) -> tuple[list[HoldingValue], list[HoldingValue]]:
    valued: list[HoldingValue] = []
    unvalued: list[HoldingValue] = []
    for h in holdings:
        (valued if h.value_inr.usable else unvalued).append(h)
    return valued, unvalued


def totals(holdings: Sequence[HoldingValue], as_of: date) -> Totals:
    """Sums over holdings that have a usable value. Holdings without one are listed and the totals are
    marked `estimated`, never silently shortened (A3.6, A3.7)."""
    valued, unvalued = split_valued(holdings)
    stamp = day_stamp(as_of)
    names = tuple(h.position.asset.name for h in unvalued)
    value = m_sum((h.value_inr for h in valued), stamp)
    cost = m_sum((h.cost_inr for h in valued), stamp)
    if names:
        note = (
            f"{len(names)} of {len(holdings)} holdings have no usable value and are left out: "
            f"{', '.join(names)}"
        )
        value = with_status(value, QualityStatus.ESTIMATED, note)
        cost = with_status(cost, QualityStatus.ESTIMATED, note)
    unrealised = m_sub(value, cost)
    return Totals(value, cost, unrealised, m_div(unrealised, cost), len(holdings), names)


# ---- realised P&L ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Realised:
    proceeds_inr: Measured[Decimal]
    cost_inr: Measured[Decimal]
    gain_inr: Measured[Decimal]


@exact
def realised_pnl(book: LotBook, as_of: date) -> tuple[dict[int, Realised], Realised]:
    """FIFO realised gain per asset and in total, in INR, before any tax (C2). Tax is not computed here:
    no verified tax rule exists yet, so any tax figure is "tax unavailable" (C7)."""
    stamp = day_stamp(as_of)
    asset_of = {lot.id: lot.asset_id for lot in book.lots}
    sums: dict[int, list[Decimal]] = {}
    for c in book.consumptions:
        row = sums.setdefault(asset_of[c.lot_id], [ZERO, ZERO])
        row[0] += c.proceeds_inr
        row[1] += c.cost_inr_consumed

    def build(proceeds: Decimal, cost: Decimal) -> Realised:
        return Realised(
            from_ledger(proceeds, stamp),
            from_ledger(cost, stamp),
            from_ledger(proceeds - cost, stamp, "before tax; tax unavailable (no verified rule)"),
        )

    per_asset = {a: build(p, c) for a, (p, c) in sums.items()}
    return per_asset, build(
        sum((r[0] for r in sums.values()), ZERO), sum((r[1] for r in sums.values()), ZERO)
    )


# ---- income and costs --------------------------------------------------------------------------


@dataclass(frozen=True)
class IncomeAndCosts:
    dividends_inr: Measured[Decimal]  # cash dividends received, net of any withholding
    interest_inr: Measured[Decimal]
    reinvested_dividends_inr: Measured[Decimal]  # units received instead of cash, at their price
    fees_inr: Measured[Decimal]
    taxes_inr: Measured[Decimal]  # taxes withheld or charged as the ledger recorded them
    stamp_duty_inr: Measured[Decimal]


@exact
def income_and_costs(txns: Iterable[TxnRow], as_of: date) -> IncomeAndCosts:
    """Dividends, interest, fees, taxes and stamp duty, in INR, from the effective ledger up to `as_of`.

    Fee and tax columns are in the transaction's currency and are converted at its own FX rate. A
    standalone FEE or TAX row states its amount as net cash, so only that is counted (no double count).
    """
    dividends = interest = reinvested = fees = taxes = stamp = ZERO
    for t in txns:
        if t.trade_date > as_of:
            continue
        kind, fx = t.effective_type, t.fx_rate or ONE
        if kind is TxnType.DIVIDEND:
            dividends += t.net_cash_inr
        elif kind is TxnType.INTEREST:
            interest += t.net_cash_inr
        elif kind is TxnType.DIVIDEND_REINVESTMENT and t.quantity is not None and t.price is not None:
            reinvested += t.quantity * t.price * fx
        if kind is TxnType.FEE:
            fees -= t.net_cash_inr
        elif kind is TxnType.TAX:
            taxes -= t.net_cash_inr
        else:
            fees += t.fees * fx
            taxes += t.taxes * fx
            stamp += t.stamp_duty * fx
    stamp_at = day_stamp(as_of)
    return IncomeAndCosts(
        *(from_ledger(v, stamp_at) for v in (dividends, interest, reinvested, fees, taxes, stamp))
    )
