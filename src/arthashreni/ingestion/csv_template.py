"""Own CSV templates: transactions, corporate actions, statement holdings (C3 manual entry)."""

from pathlib import Path

from pydantic import BaseModel

from arthashreni.ingestion.build import build_txn
from arthashreni.ingestion.common import (
    CORPORATE_ACTION,
    STATEMENT_HOLDING,
    TRANSACTION,
    ParsedFile,
    Resolver,
    make_row,
    need_decimal,
    parse_date,
    parse_decimal,
)
from arthashreni.ingestion.tables import read_table
from arthashreni.ledger.models import NewCorporateAction, NewStatementHolding
from arthashreni.ledger.types import ActionType

TXN_COLUMNS = (
    "account,asset_ref,txn_type,trade_date,settlement_date,quantity,price,currency,fx_rate,fx_rate_type,"
    "fees,taxes,stamp_duty,net_cash_inr,net_cash_orig,external_id,notes"
)
ACTION_COLUMNS = "asset_ref,action_type,ex_date,ratio_from,ratio_to,successor_ref"
HOLDING_COLUMNS = "account,asset_ref,as_of,units"

TEMPLATES = {
    "transactions": (
        TXN_COLUMNS,
        "ZERODHA-COIN,INF000000001,SIP,2026-01-05,,10.123,98.76,INR,,,0,0,0,,,,monthly SIP",
    ),
    "corporate-actions": (ACTION_COLUMNS, "TICKER:XNSE:ABC,SPLIT,2026-03-01,1,5,"),
    "holdings": (HOLDING_COLUMNS, "ZERODHA-COIN,INF000000001,2026-03-31,40.492"),
}

TXN_REQUIRED = {"account", "asset_ref", "txn_type", "trade_date"}
ACTION_REQUIRED = {"asset_ref", "action_type", "ex_date", "ratio_from", "ratio_to"}
HOLDING_REQUIRED = {"account", "asset_ref", "as_of", "units"}


def parse_transactions(path: Path, resolver: Resolver) -> ParsedFile:
    out = ParsedFile("CSV_TEMPLATE")
    for i, raw in enumerate(read_table(path, TXN_REQUIRED), start=1):

        def build(r: dict[str, str] = raw) -> tuple[tuple[object, ...], BaseModel]:
            return build_txn(
                resolver,
                source_kind="CSV_TEMPLATE",
                source_document=path.name,
                account=r["account"],
                asset_ref=r.get("asset_ref") or None,
                type_name=r["txn_type"],
                trade_date=parse_date(r["trade_date"]),
                settlement_date=parse_date(r["settlement_date"]) if r.get("settlement_date") else None,
                quantity=parse_decimal(r.get("quantity")),
                price=parse_decimal(r.get("price")),
                currency=r.get("currency") or "INR",
                fx_rate=parse_decimal(r.get("fx_rate")),
                fx_rate_type=r.get("fx_rate_type") or None,
                fees=parse_decimal(r.get("fees")),
                taxes=parse_decimal(r.get("taxes")),
                stamp_duty=parse_decimal(r.get("stamp_duty")),
                net_cash_inr=parse_decimal(r.get("net_cash_inr")),
                net_cash_orig=parse_decimal(r.get("net_cash_orig")),
                external_id=r.get("external_id", ""),
            )

        out.rows.append(make_row(i, TRANSACTION, dict(raw), build))
    return out


def parse_corporate_actions(path: Path, resolver: Resolver) -> ParsedFile:
    out = ParsedFile("CSV_CORPORATE_ACTIONS")
    for i, raw in enumerate(read_table(path, ACTION_REQUIRED), start=1):

        def build(r: dict[str, str] = raw) -> tuple[tuple[object, ...], NewCorporateAction]:
            asset = resolver.asset(r["asset_ref"])
            successor = resolver.asset(r["successor_ref"]) if r.get("successor_ref") else None
            action = NewCorporateAction(
                asset_id=asset.id,
                action_type=ActionType(r["action_type"].strip().upper()),
                ex_date=parse_date(r["ex_date"]),
                ratio_from=need_decimal(r["ratio_from"], "ratio_from"),
                ratio_to=need_decimal(r["ratio_to"], "ratio_to"),
                successor_asset_id=successor.id if successor else None,
                source_document=path.name,
            )
            key = (
                action.asset_id,
                action.action_type.value,
                action.ex_date,
                action.ratio_from,
                action.ratio_to,
            )
            return key, action

        out.rows.append(make_row(i, CORPORATE_ACTION, dict(raw), build))
    return out


def parse_holdings(path: Path, resolver: Resolver) -> ParsedFile:
    out = ParsedFile("CSV_HOLDINGS")
    for i, raw in enumerate(read_table(path, HOLDING_REQUIRED), start=1):

        def build(r: dict[str, str] = raw) -> tuple[tuple[object, ...], NewStatementHolding]:
            holding = NewStatementHolding(
                account_id=resolver.account(r["account"]),
                asset_id=resolver.asset(r["asset_ref"]).id,
                as_of=parse_date(r["as_of"]),
                units=need_decimal(r["units"], "units"),
                source_document=path.name,
            )
            return ("CSV_HOLDINGS", holding.account_id, holding.asset_id, holding.as_of), holding

        out.rows.append(make_row(i, STATEMENT_HOLDING, dict(raw), build))
    return out
