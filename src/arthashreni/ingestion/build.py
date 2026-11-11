"""Build a validated NewTxn from loosely-typed importer fields (shared by every parser)."""

from datetime import date
from decimal import Decimal

from arthashreni.ingestion.common import ImportRowError, Resolver
from arthashreni.ledger.cash import derive_cash
from arthashreni.ledger.models import NewTxn
from arthashreni.ledger.types import CASH_DERIVABLE, NO_EXTERNAL_CASH, ONE, ZERO, TxnType, q4


def build_txn(
    resolver: Resolver,
    *,
    source_kind: str,
    source_document: str,
    account: str,
    asset_ref: str | None,
    type_name: str,
    trade_date: date,
    settlement_date: date | None = None,
    quantity: Decimal | None = None,
    price: Decimal | None = None,
    currency: str = "INR",
    fx_rate: Decimal | None = None,
    fx_rate_type: str | None = None,
    fees: Decimal | None = None,
    taxes: Decimal | None = None,
    stamp_duty: Decimal | None = None,
    net_cash_inr: Decimal | None = None,
    net_cash_orig: Decimal | None = None,
    external_id: str = "",
) -> tuple[tuple[object, ...], NewTxn]:
    try:
        txn_type = TxnType(type_name.strip().upper())
    except ValueError as exc:
        raise ImportRowError(f"unknown transaction type {type_name!r}") from exc
    account_id = resolver.account(account)
    asset = resolver.asset(asset_ref) if asset_ref else None
    currency = currency.strip().upper() or "INR"
    if asset is not None and asset.currency != currency:
        raise ImportRowError(f"currency {currency} does not match {asset.name} ({asset.currency})")
    fees, taxes, stamp_duty = fees or ZERO, taxes or ZERO, stamp_duty or ZERO

    if net_cash_orig is None:
        if txn_type in CASH_DERIVABLE:
            if quantity is None or price is None:
                raise ImportRowError(f"{txn_type} needs quantity and price to derive net cash")
            net_cash_orig, derived_inr = derive_cash(
                txn_type, quantity, price, fees, taxes, stamp_duty, fx_rate
            )
            net_cash_inr = derived_inr if net_cash_inr is None else net_cash_inr
        elif txn_type in NO_EXTERNAL_CASH:
            net_cash_orig = ZERO
        else:
            raise ImportRowError(f"net_cash_orig is required for {txn_type}")
    if net_cash_inr is None:
        net_cash_inr = q4(net_cash_orig * (fx_rate or ONE))

    txn = NewTxn(
        asset_id=asset.id if asset else None,
        account_id=account_id,
        type=txn_type,
        trade_date=trade_date,
        settlement_date=settlement_date,
        quantity=quantity,
        price=price,
        currency=currency,
        fx_rate=fx_rate,
        fx_rate_type=fx_rate_type or None,
        fees=fees,
        taxes=taxes,
        stamp_duty=stamp_duty,
        net_cash_inr=net_cash_inr,
        net_cash_orig=net_cash_orig,
        source_document=source_document,
    )
    key = (
        source_kind,
        account,
        asset_ref or "",
        txn_type.value,
        trade_date,
        quantity,
        price,
        net_cash_orig,
        external_id,
    )
    return key, txn
