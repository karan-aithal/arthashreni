"""Net cash derivation for trade types (negative = money leaves the investor)."""

from decimal import Decimal

from arthashreni.ledger.types import CASH_DERIVABLE, ONE, ZERO, TxnType, q4


def derive_cash(
    txn_type: TxnType,
    quantity: Decimal,
    price: Decimal,
    fees: Decimal,
    taxes: Decimal,
    stamp_duty: Decimal,
    fx_rate: Decimal | None,
) -> tuple[Decimal, Decimal]:
    """Return (net_cash_orig, net_cash_inr) for BUY/SIP (outflow) and SELL/REDEMPTION (inflow)."""
    if txn_type not in CASH_DERIVABLE:
        raise ValueError(f"cannot derive net cash for {txn_type}; state it explicitly")
    gross = quantity * price
    orig = (
        -(gross + fees + taxes + stamp_duty)
        if txn_type in {TxnType.BUY, TxnType.SIP}
        else (gross - fees - taxes - stamp_duty)
    )
    orig = q4(orig)
    return orig, q4(orig * (fx_rate or ONE)) if orig != ZERO else ZERO
