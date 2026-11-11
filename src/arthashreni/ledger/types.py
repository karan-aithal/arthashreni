"""Ledger value types. Decimal only; no float anywhere in this package (ADR-0003)."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

ZERO = Decimal(0)
ONE = Decimal(1)
_Q4 = Decimal("0.0001")
_Q8 = Decimal("0.00000001")


def q4(x: Decimal) -> Decimal:
    """Money precision (NUMERIC(20,4))."""
    return x.quantize(_Q4, rounding=ROUND_HALF_EVEN)


def q8(x: Decimal) -> Decimal:
    """Quantity / price precision (NUMERIC(24,8))."""
    return x.quantize(_Q8, rounding=ROUND_HALF_EVEN)


class LedgerError(ValueError):
    """The ledger would enter an impossible state."""


class NegativeHoldingError(LedgerError):
    def __init__(self, txn_id: int, account_id: int, asset_id: int, short: Decimal) -> None:
        super().__init__(
            f"transaction {txn_id} would make holding negative "
            f"(account {account_id}, asset {asset_id}, short by {short})"
        )
        self.txn_id = txn_id
        self.account_id = account_id
        self.asset_id = asset_id
        self.short = short


class TxnType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    SIP = "SIP"
    DIVIDEND = "DIVIDEND"
    DIVIDEND_REINVESTMENT = "DIVIDEND_REINVESTMENT"
    FEE = "FEE"
    TAX = "TAX"
    SPLIT = "SPLIT"
    BONUS = "BONUS"
    MERGER = "MERGER"
    REDEMPTION = "REDEMPTION"
    SWITCH_IN = "SWITCH_IN"
    SWITCH_OUT = "SWITCH_OUT"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    FX_CONVERSION = "FX_CONVERSION"
    IPO_APPLICATION = "IPO_APPLICATION"
    IPO_BLOCK = "IPO_BLOCK"
    IPO_ALLOTMENT = "IPO_ALLOTMENT"
    IPO_REFUND = "IPO_REFUND"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    CORRECTION = "CORRECTION"
    INTEREST = "INTEREST"  # SGB coupons (ADR-0020)


ACQUIRING = frozenset(
    {
        TxnType.BUY,
        TxnType.SIP,
        TxnType.DIVIDEND_REINVESTMENT,
        TxnType.TRANSFER_IN,
        TxnType.SWITCH_IN,
        TxnType.IPO_ALLOTMENT,
    }
)
DISPOSING = frozenset({TxnType.SELL, TxnType.REDEMPTION, TxnType.SWITCH_OUT, TxnType.TRANSFER_OUT})
UNIT_TYPES = ACQUIRING | DISPOSING
# Unit changes from splits, bonuses and mergers come only from the corporate_actions table.
RESERVED_TYPES = frozenset({TxnType.SPLIT, TxnType.BONUS, TxnType.MERGER, TxnType.CORPORATE_ACTION})
# Net cash is derived from quantity x price for these; internal unit moves carry no external
# cash; every other type must state its net cash explicitly.
CASH_DERIVABLE = frozenset({TxnType.BUY, TxnType.SIP, TxnType.SELL, TxnType.REDEMPTION})
NO_EXTERNAL_CASH = frozenset(
    {
        TxnType.DIVIDEND_REINVESTMENT,
        TxnType.SWITCH_IN,
        TxnType.SWITCH_OUT,
        TxnType.TRANSFER_IN,
        TxnType.TRANSFER_OUT,
    }
)
# Sign of net cash (negative = money leaves the investor). None = either sign.
CASH_SIGN: dict[TxnType, int | None] = {
    TxnType.BUY: -1,
    TxnType.SIP: -1,
    TxnType.SELL: 1,
    TxnType.REDEMPTION: 1,
    TxnType.DIVIDEND: 1,
    TxnType.INTEREST: 1,
    TxnType.FEE: -1,
    TxnType.TAX: -1,
    TxnType.DEPOSIT: 1,
    TxnType.WITHDRAWAL: -1,
    TxnType.IPO_APPLICATION: -1,
    TxnType.IPO_BLOCK: -1,
    TxnType.IPO_ALLOTMENT: -1,
    TxnType.IPO_REFUND: 1,
    TxnType.FX_CONVERSION: None,
}


class ActionType(StrEnum):
    SPLIT = "SPLIT"
    BONUS = "BONUS"
    MERGER = "MERGER"
    NAME_CHANGE = "NAME_CHANGE"


@dataclass(frozen=True)
class TxnRow:
    """A stored ledger row. `effective_type` resolves CORRECTION rows."""

    id: int
    asset_id: int | None
    account_id: int
    type: TxnType
    trade_date: date
    quantity: Decimal | None
    price: Decimal | None
    currency: str
    fx_rate: Decimal | None
    fx_rate_type: str | None
    fees: Decimal
    taxes: Decimal
    stamp_duty: Decimal
    net_cash_inr: Decimal
    net_cash_orig: Decimal
    corrected_type: TxnType | None = None
    supersedes_id: int | None = None

    @property
    def effective_type(self) -> TxnType:
        return self.corrected_type if self.type is TxnType.CORRECTION and self.corrected_type else self.type

    @property
    def is_void(self) -> bool:
        return (
            self.supersedes_id is not None
            and (self.quantity or ZERO) == 0
            and self.net_cash_orig == 0
            and self.net_cash_inr == 0
        )


@dataclass(frozen=True)
class CorporateAction:
    id: int
    asset_id: int
    action_type: ActionType
    ex_date: date
    ratio_from: Decimal
    ratio_to: Decimal
    successor_asset_id: int | None = None


@dataclass(frozen=True)
class LotResult:
    id: int
    transaction_id: int | None
    corporate_action_id: int | None
    adjusted_by_ca_id: int | None
    asset_id: int
    account_id: int
    acquired_on: date
    original_qty: Decimal
    remaining_qty: Decimal
    cost_inr: Decimal
    cost_orig: Decimal
    remaining_cost_inr: Decimal
    fx_rate_type: str | None
    is_bonus: bool


@dataclass(frozen=True)
class Consumption:
    id: int
    lot_id: int
    disposal_transaction_id: int
    qty: Decimal
    cost_inr_consumed: Decimal
    proceeds_inr: Decimal
    holding_days: int


@dataclass(frozen=True)
class LotBook:
    lots: tuple[LotResult, ...]
    consumptions: tuple[Consumption, ...]

    def holdings(self) -> dict[tuple[int, int], Decimal]:
        """Units per (account_id, asset_id) after all events; zero holdings are omitted."""
        out: dict[tuple[int, int], Decimal] = {}
        for lot in self.lots:
            key = (lot.account_id, lot.asset_id)
            out[key] = out.get(key, ZERO) + lot.remaining_qty
        return {k: v for k, v in out.items() if v != 0}
