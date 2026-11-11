"""INDmoney US-stock statement parser.

ASSUMED FORMAT: the real statement format is unconfirmed (V15 in docs/verify-register.md), so this
parser is written against the sample layout below. Adjust the column map once a real, redacted
statement is available. Columns: trade_date, symbol, exchange, txn_type, quantity, price_usd,
fees_usd, tax_usd, amount_usd, usd_inr_rate, reference_id.
"""

from pathlib import Path

from pydantic import BaseModel

from arthashreni.ingestion.build import build_txn
from arthashreni.ingestion.common import (
    TRANSACTION,
    ImportRowError,
    ParsedFile,
    Resolver,
    make_row,
    need_decimal,
    parse_date,
    parse_decimal,
)
from arthashreni.ingestion.tables import read_table
from arthashreni.ledger.types import CASH_SIGN, TxnType

REQUIRED = {"trade_date", "txn_type", "usd_inr_rate", "amount_usd"}
SAMPLE_HEADER = (
    "trade_date,symbol,exchange,txn_type,quantity,price_usd,fees_usd,tax_usd,amount_usd,"
    "usd_inr_rate,reference_id"
)  # fmt: skip
_MIC = {"NASDAQ": "XNAS", "NYSE": "XNYS", "XNAS": "XNAS", "XNYS": "XNYS"}


def parse_statement(path: Path, resolver: Resolver, account: str) -> ParsedFile:
    out = ParsedFile("INDMONEY")
    out.warnings.append("INDmoney statement format is ASSUMED (V15); verify against a real statement.")
    for i, raw in enumerate(read_table(path, REQUIRED), start=1):

        def build(r: dict[str, str] = raw) -> tuple[tuple[object, ...], BaseModel]:
            type_name = r["txn_type"].strip().upper()
            try:
                txn_type = TxnType(type_name)
            except ValueError as exc:
                raise ImportRowError(f"unknown txn_type {type_name!r}") from exc
            symbol = r.get("symbol", "").strip()
            asset_ref = None
            if symbol:
                exchange = r.get("exchange", "").strip().upper()
                if exchange not in _MIC:
                    raise ImportRowError(f"unknown exchange {exchange!r} (use NASDAQ/NYSE or the MIC)")
                asset_ref = f"TICKER:{_MIC[exchange]}:{symbol}"
            amount = parse_decimal(r.get("amount_usd"))
            sign = CASH_SIGN.get(txn_type)
            net_cash = None
            if amount is not None and amount != 0:
                net_cash = amount if sign is None else abs(amount) * sign
            return build_txn(
                resolver,
                source_kind="INDMONEY",
                source_document=path.name,
                account=account,
                asset_ref=asset_ref,
                type_name=type_name,
                trade_date=parse_date(r["trade_date"]),
                quantity=parse_decimal(r.get("quantity")),
                price=parse_decimal(r.get("price_usd")),
                currency="USD",
                fx_rate=need_decimal(r["usd_inr_rate"], "usd_inr_rate"),
                fx_rate_type="BROKER_ACTUAL",
                fees=parse_decimal(r.get("fees_usd")),
                taxes=parse_decimal(r.get("tax_usd")),
                net_cash_orig=net_cash,
                external_id=r.get("reference_id", ""),
            )

        out.rows.append(make_row(i, TRANSACTION, dict(raw), build))
    return out
