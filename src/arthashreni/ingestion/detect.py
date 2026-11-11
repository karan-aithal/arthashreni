"""Recognise a file's format from its extension and header names (D4)."""

from pathlib import Path

from arthashreni.ingestion.tables import peek_headers

CAS_PDF = "CAS_PDF"
TRANSACTIONS = "CSV_TEMPLATE"
CORPORATE_ACTIONS = "CSV_CORPORATE_ACTIONS"
HOLDINGS = "CSV_HOLDINGS"
ZERODHA_TRADEBOOK = "ZERODHA_TRADEBOOK"
ZERODHA_HOLDINGS = "ZERODHA_HOLDINGS"
INDMONEY = "INDMONEY"

# Needs an account chosen by the investor because the file itself does not name one.
NEEDS_ACCOUNT = {ZERODHA_TRADEBOOK, ZERODHA_HOLDINGS, INDMONEY}

_SIGNATURES: list[tuple[str, set[str]]] = [
    (ZERODHA_TRADEBOOK, {"trade_type", "trade_id", "isin"}),
    (INDMONEY, {"usd_inr_rate", "amount_usd", "txn_type"}),
    (CORPORATE_ACTIONS, {"action_type", "ex_date", "ratio_from", "ratio_to"}),
    (TRANSACTIONS, {"asset_ref", "txn_type", "trade_date"}),
    (HOLDINGS, {"asset_ref", "as_of", "units"}),
    (ZERODHA_HOLDINGS, {"isin", "quantity_available", "average_price"}),
]


def detect_format(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return CAS_PDF
    if suffix not in {".csv", ".xlsx"}:
        return None
    headers = peek_headers(path)
    for kind, required in _SIGNATURES:
        if required <= headers:
            return kind
    return None
