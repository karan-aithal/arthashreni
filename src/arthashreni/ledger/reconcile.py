"""Unit comparison for reconciliation. MF units are 3 decimals; everything else 6."""

from decimal import ROUND_HALF_EVEN, Decimal

MF_DECIMALS = 3
DEFAULT_DECIMALS = 6


def unit_decimals(asset_class: str) -> int:
    return MF_DECIMALS if asset_class == "MF" else DEFAULT_DECIMALS


def quantize_units(x: Decimal, decimals: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN)


def unit_diff(ledger_units: Decimal, statement_units: Decimal, decimals: int) -> Decimal:
    """ledger minus statement, both rounded to the asset's unit precision. Zero means matched."""
    return quantize_units(ledger_units, decimals) - quantize_units(statement_units, decimals)
