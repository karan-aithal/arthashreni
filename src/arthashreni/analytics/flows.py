"""Investor cash flows from the effective ledger (the input of XIRR and TWR).

Seen from the investor: money paid in is negative, money received is positive (ADR-0022).
Counted: BUY, SIP, SELL, REDEMPTION, DIVIDEND, INTEREST, FEE, TAX, IPO_BLOCK, IPO_REFUND. An IPO
allotment carries no cash of its own: its block pays for it and the refund returns the rest.
Not counted: DEPOSIT, WITHDRAWAL and FX_CONVERSION (movements between the investor's bank and the
broker's cash, not investment results) and unit moves without external cash (SWITCH_*, TRANSFER_*,
DIVIDEND_REINVESTMENT), which are value-neutral for the portfolio as a whole.
"""

from collections.abc import Collection, Iterable
from datetime import date
from decimal import Decimal

from arthashreni.ledger.types import TxnRow, TxnType

COUNTED = frozenset(
    {
        TxnType.BUY,
        TxnType.SIP,
        TxnType.SELL,
        TxnType.REDEMPTION,
        TxnType.DIVIDEND,
        TxnType.INTEREST,
        TxnType.FEE,
        TxnType.TAX,
        TxnType.IPO_BLOCK,
        TxnType.IPO_REFUND,
    }
)
INTERNAL_MOVES = frozenset(
    {
        TxnType.SWITCH_IN,
        TxnType.SWITCH_OUT,
        TxnType.TRANSFER_IN,
        TxnType.TRANSFER_OUT,
        TxnType.DIVIDEND_REINVESTMENT,
    }
)


def cash_flows(
    txns: Iterable[TxnRow], *, asset_ids: Collection[int] | None = None, until: date | None = None
) -> list[tuple[date, Decimal]]:
    """INR flows of the portfolio (or of a set of assets, e.g. one asset and its merger predecessors),
    optionally up to and including `until`."""
    return [
        (t.trade_date, t.net_cash_inr)
        for t in txns
        if t.effective_type in COUNTED
        and t.net_cash_inr != 0
        and (asset_ids is None or t.asset_id in asset_ids)
        and (until is None or t.trade_date <= until)
    ]


def twr_flow(flows: Iterable[tuple[date, Decimal]], day: date) -> Decimal:
    """External flow INTO the valued portfolio on `day`, for TWR: minus the investor's cash flows that day.

    The valued portfolio is the holdings only (no cash balance), so everything that moves money between
    holdings and the investor is an external flow, taken at the END of the day:
      purchase / IPO block (investor pays)        -> positive, money added
      sale / redemption / IPO refund              -> negative, money taken out
      dividend / interest                         -> negative: the cash leaves the holdings, and not adding
                                                     it back later is what makes income count as return
      standalone fee / tax                        -> positive: paid in from outside but buys no value, so it
                                                     lowers the return, as a cost should (ADR-0024)
    Units moved without cash (switch, transfer, reinvested dividend) are not flows: the holdings keep them.
    """
    return -sum((cf for d, cf in flows if d == day), Decimal(0))


def paid_in(flows: Iterable[tuple[date, Decimal]]) -> Decimal:
    """Everything the investor paid out (purchases, fees, taxes), including money since returned."""
    return sum((-cf for _, cf in flows if cf < 0), Decimal(0))


def has_internal_moves(txns: Iterable[TxnRow], asset_ids: Collection[int] | None = None) -> bool:
    """True when units moved without cash, so an asset-level XIRR is only approximate."""
    return any(
        t.effective_type in INTERNAL_MOVES and (asset_ids is None or t.asset_id in asset_ids) for t in txns
    )
