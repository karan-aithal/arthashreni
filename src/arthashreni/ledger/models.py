"""Validated inputs to the ledger. Importers produce these; the repository stores them.

Validation lives here so every path (CSV, CAS, Console, manual) enforces the same rules and a
bad row becomes a REJECTED preview line instead of reaching the database.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from arthashreni.ledger.types import (
    CASH_SIGN,
    RESERVED_TYPES,
    UNIT_TYPES,
    ZERO,
    ActionType,
    TxnType,
)

FxRateType = Literal["SBI_TT_BUY", "FBIL_REF", "BROKER_ACTUAL"]


class NewTxn(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_id: int | None = None
    account_id: int
    type: TxnType
    trade_date: date
    settlement_date: date | None = None
    price_date: date | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    currency: str = "INR"
    fx_rate: Decimal | None = None
    fx_rate_type: FxRateType | None = None
    fees: Decimal = ZERO
    taxes: Decimal = ZERO
    stamp_duty: Decimal = ZERO
    net_cash_inr: Decimal
    net_cash_orig: Decimal
    source_document: str
    verification_status: Literal["UNVERIFIED", "STATEMENT_MATCHED", "MANUAL_CONFIRMED"] = "UNVERIFIED"
    source_tz: str = "Asia/Kolkata"

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError(f"currency must be a 3-letter code, got {v!r}")
        return v

    @model_validator(mode="after")
    def _rules(self) -> "NewTxn":
        t = self.type
        if t is TxnType.CORRECTION or t in RESERVED_TYPES:
            raise ValueError(
                f"{t} cannot be imported as a transaction "
                "(corrections use `ledger correct`; splits/bonuses/mergers use corporate actions)"
            )
        if t in UNIT_TYPES:
            if self.asset_id is None:
                raise ValueError(f"{t} needs an asset")
            if self.quantity is None or self.quantity <= 0:
                raise ValueError(f"{t} needs a positive quantity")
            if self.price is None:
                raise ValueError(f"{t} needs a price")
        for name in ("quantity", "price", "fees", "taxes", "stamp_duty"):
            v = getattr(self, name)
            if v is not None and v < 0:
                raise ValueError(f"{name} must not be negative (direction comes from the type)")
        if self.currency != "INR" and (self.fx_rate is None or self.fx_rate_type is None):
            raise ValueError(f"{self.currency} transactions need fx_rate and fx_rate_type")
        if self.fx_rate is not None and (self.fx_rate <= 0 or self.fx_rate_type is None):
            raise ValueError("fx_rate must be positive and have an fx_rate_type")
        sign = CASH_SIGN.get(t)
        if sign is not None and self.net_cash_orig * sign < 0:
            raise ValueError(f"{t} net cash must be {'positive' if sign > 0 else 'negative'}")
        return self


class NewCorporateAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_id: int
    action_type: ActionType
    ex_date: date
    ratio_from: Decimal
    ratio_to: Decimal
    successor_asset_id: int | None = None
    source_document: str

    @model_validator(mode="after")
    def _rules(self) -> "NewCorporateAction":
        if self.ratio_from <= 0 or self.ratio_to <= 0:
            raise ValueError("ratios must be positive")
        if (self.action_type is ActionType.MERGER) != (self.successor_asset_id is not None):
            raise ValueError("MERGER needs a successor asset; other actions must not have one")
        return self


class NewStatementHolding(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: int
    asset_id: int
    as_of: date
    units: Decimal
    source_document: str

    @field_validator("units")
    @classmethod
    def _units(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("units must not be negative")
        return v
