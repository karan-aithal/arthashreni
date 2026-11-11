"""Zerodha Console exports (tradebook and equity holdings), CSV or XLSX.

ASSUMED FORMAT (verify against a real export; open item V14 in docs/verify-register.md):
tradebook columns symbol, isin, trade_date, exchange, segment, series, trade_type, quantity, price,
trade_id, order_id; holdings columns symbol, isin, quantity_available, average_price. Coin mutual-fund
orders are not covered until a sample file confirms where they appear.
"""

from datetime import date
from pathlib import Path

from pydantic import BaseModel

from arthashreni.ingestion.build import build_txn
from arthashreni.ingestion.common import (
    STATEMENT_HOLDING,
    TRANSACTION,
    ImportRowError,
    ParsedFile,
    Resolver,
    make_row,
    need_decimal,
    parse_date,
)
from arthashreni.ingestion.tables import read_table
from arthashreni.ledger.models import NewStatementHolding

TRADEBOOK_REQUIRED = {"isin", "trade_date", "trade_type", "quantity", "price", "trade_id"}
HOLDINGS_REQUIRED = {"isin", "quantity_available", "average_price"}
_SIDES = {"buy": "BUY", "sell": "SELL"}


def parse_tradebook(path: Path, resolver: Resolver, account: str) -> ParsedFile:
    out = ParsedFile("ZERODHA_CONSOLE")
    out.warnings.append(
        "Tradebook has no brokerage or charges; fees are recorded as 0, so cost basis excludes them. "
        "Format is assumed (V14)."
    )
    for i, raw in enumerate(read_table(path, TRADEBOOK_REQUIRED), start=1):

        def build(r: dict[str, str] = raw) -> tuple[tuple[object, ...], BaseModel]:
            segment = r.get("segment", "EQ").strip().upper() or "EQ"
            if segment != "EQ":
                raise ImportRowError(f"segment {segment!r} is not supported (equity delivery only)")
            side = _SIDES.get(r["trade_type"].strip().lower())
            if side is None:
                raise ImportRowError(f"unknown trade_type {r['trade_type']!r}")
            return build_txn(
                resolver,
                source_kind="ZERODHA_CONSOLE",
                source_document=path.name,
                account=account,
                asset_ref=r["isin"],
                type_name=side,
                trade_date=parse_date(r["trade_date"]),
                quantity=need_decimal(r["quantity"], "quantity"),
                price=need_decimal(r["price"], "price"),
                external_id=r["trade_id"],
            )

        out.rows.append(make_row(i, TRANSACTION, dict(raw), build))
    return out


def parse_holdings(path: Path, resolver: Resolver, account: str, as_of: date) -> ParsedFile:
    out = ParsedFile("ZERODHA_CONSOLE")
    out.period_end = as_of
    out.warnings.append("Holdings units are read from quantity_available (assumed column meaning, V14).")
    for i, raw in enumerate(read_table(path, HOLDINGS_REQUIRED), start=1):

        def build(r: dict[str, str] = raw) -> tuple[tuple[object, ...], BaseModel]:
            holding = NewStatementHolding(
                account_id=resolver.account(account),
                asset_id=resolver.asset(r["isin"]).id,
                as_of=as_of,
                units=need_decimal(r["quantity_available"], "quantity_available"),
                source_document=path.name,
            )
            return ("ZERODHA_HOLDINGS", account, holding.asset_id, as_of), holding

        out.rows.append(make_row(i, STATEMENT_HOLDING, dict(raw), build))
    return out
