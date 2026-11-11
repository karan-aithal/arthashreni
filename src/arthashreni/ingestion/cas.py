"""CAS PDF importer (CAMS / KFintech detailed, NSDL / CDSL holdings) on top of `casparser` (MIT).

casparser returns typed models: `CASData` (CAMS/KFintech, with transactions) or `NSDLCASData`
(NSDL/CDSL, holdings only). This module maps them onto ledger candidates and enforces C6:
units are never trusted, each transaction must satisfy units x NAV ~= amount, and each scheme's
printed closing balance must equal casparser's recomputed one. Any failure blocks the batch.
"""

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from casparser.enums import TransactionType as CT
from casparser.types import CASData, NSDLCASData, Scheme, TransactionData
from pydantic import BaseModel

from arthashreni.ingestion.build import build_txn
from arthashreni.ingestion.common import (
    STATEMENT_HOLDING,
    TRANSACTION,
    ImportRowError,
    NeedsReview,
    ParsedFile,
    ParsedRow,
    Resolver,
    make_row,
    parse_date,
)
from arthashreni.ledger.models import NewStatementHolding
from arthashreni.ledger.types import ZERO, q4

UNITS_TOLERANCE = Decimal("0.001")  # MF units are 3 decimals
AMOUNT_TOLERANCE = Decimal("0.01")  # rupee rounding on the printed amount

_MAP: dict[str, str] = {
    CT.PURCHASE.value: "BUY",
    CT.PURCHASE_SIP.value: "SIP",
    CT.REDEMPTION.value: "REDEMPTION",
    CT.DIVIDEND_REINVEST.value: "DIVIDEND_REINVESTMENT",
    CT.SWITCH_IN.value: "SWITCH_IN",
    CT.SWITCH_IN_MERGER.value: "SWITCH_IN",
    CT.SWITCH_OUT.value: "SWITCH_OUT",
    CT.SWITCH_OUT_MERGER.value: "SWITCH_OUT",
    CT.GIFT_IN.value: "TRANSFER_IN",
    CT.GIFT_OUT.value: "TRANSFER_OUT",
    CT.DIVIDEND_PAYOUT.value: "DIVIDEND",
    CT.STT_TAX.value: "TAX",
    CT.TDS_TAX.value: "TAX",
}
_UNIT_TYPES = {"BUY", "SIP", "REDEMPTION", "DIVIDEND_REINVESTMENT", "SWITCH_IN", "SWITCH_OUT",
               "TRANSFER_IN", "TRANSFER_OUT"}  # fmt: skip
_PURCHASES = {CT.PURCHASE.value, CT.PURCHASE_SIP.value}


def mask_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value)
    return "*" * max(len(cleaned) - 4, 0) + cleaned[-4:]


def folio_label(folio: str) -> str:
    return f"FOLIO-{mask_id(folio)}"


def demat_label(dp_id: str | None, client_id: str | None) -> str:
    return f"DEMAT-{mask_id((dp_id or '') + (client_id or '')) or 'UNKNOWN'}"


def read_cas(path: Path, password: str) -> CASData | NSDLCASData:
    """Parse a CAS PDF. The password is used for this call only and is never stored."""
    import casparser

    data: Any = casparser.read_cas_pdf(str(path), password)
    if isinstance(data, CASData | NSDLCASData):
        return data
    raise ImportRowError("casparser returned an unexpected result type")


def _scheme_ref(s: Scheme) -> str:
    if s.isin:
        return str(s.isin)
    if s.amfi:
        return f"AMFI:{s.amfi}"
    raise ImportRowError(f"scheme {s.scheme!r} has no ISIN or AMFI code")


def _stamp_allocation(txns: list[TransactionData]) -> tuple[dict[int, Decimal], set[date]]:
    """Share each date's stamp-duty lines across that date's purchases, pro rata to amount."""
    stamp: dict[date, Decimal] = defaultdict(lambda: ZERO)
    buys: dict[date, list[int]] = defaultdict(list)
    for i, t in enumerate(txns):
        d = parse_date(t.date)
        if t.type == CT.STAMP_DUTY_TAX and t.amount is not None:
            stamp[d] += abs(t.amount)
        elif t.type in _PURCHASES and t.amount is not None:
            buys[d].append(i)
    out: dict[int, Decimal] = {}
    folded: set[date] = set()
    for d, total in stamp.items():
        idx = buys.get(d)
        if not idx:
            continue
        amounts = [abs(txns[i].amount or ZERO) for i in idx]
        base, left = sum(amounts, ZERO), total
        for n, i in enumerate(idx):
            share = left if n == len(idx) - 1 else q4(total * amounts[n] / base)
            out[i] = share
            left -= share
        folded.add(d)
    return out, folded


def parse_cas(data: CASData | NSDLCASData, resolver: Resolver, source_document: str) -> ParsedFile:
    if isinstance(data, NSDLCASData):
        return _parse_depository(data, resolver, source_document)
    kind = "CAS_CAMS" if str(data.file_type) == "CAMS" else "CAS_KFIN"
    out = ParsedFile(kind)
    out.period_start = parse_date(data.statement_period.from_)
    out.period_end = parse_date(data.statement_period.to)
    out.blocking.extend(f"casparser warning: {w}" for w in data.parse_warnings)
    row_no = 0

    for folio in data.folios:
        account = folio_label(folio.folio)
        for scheme in folio.schemes:
            if abs(scheme.close - scheme.close_calculated) > UNITS_TOLERANCE:
                out.blocking.append(
                    f"{account} {scheme.scheme}: printed closing balance {scheme.close} differs from "
                    f"recomputed {scheme.close_calculated}"
                )
            stamps, folded = _stamp_allocation(scheme.transactions)
            for i, t in enumerate(scheme.transactions):
                if t.type == CT.STAMP_DUTY_TAX and parse_date(t.date) in folded:
                    continue  # recorded as stamp_duty on that day's purchase(s)
                row_no += 1
                out.rows.append(
                    _txn_row(row_no, t, stamps.get(i), scheme, account, resolver, source_document, kind)
                )
            row_no += 1
            out.rows.append(
                _holding_row(row_no, scheme, account, out.period_end, resolver, source_document, kind)
            )
    return out


def _txn_row(
    row_no: int,
    t: TransactionData,
    stamp: Decimal | None,
    scheme: Scheme,
    account: str,
    resolver: Resolver,
    source_document: str,
    kind: str,
) -> ParsedRow:
    raw: dict[str, Any] = {
        "folio": account, "scheme": scheme.scheme, "date": str(t.date), "type": str(t.type),
        "description": t.description, "amount": str(t.amount), "units": str(t.units), "nav": str(t.nav),
    }  # fmt: skip
    d_type = t.type.value if hasattr(t.type, "value") else str(t.type)

    def build() -> tuple[tuple[object, ...], BaseModel]:
        when = parse_date(t.date)
        kwargs: dict[str, Any]
        if d_type == CT.STAMP_DUTY_TAX.value:  # no purchase that day to attach it to
            kwargs = {"type_name": "TAX", "net_cash_orig": -abs(_need(t.amount, "amount"))}
        elif d_type in _MAP:
            txn_type = _MAP[d_type]
            if txn_type in _UNIT_TYPES:
                kwargs = _unit_kwargs(t, txn_type, stamp)
            elif txn_type == "DIVIDEND":
                kwargs = {"type_name": "DIVIDEND", "net_cash_orig": abs(_need(t.amount, "amount"))}
            else:  # TAX
                kwargs = {"type_name": "TAX", "net_cash_orig": -abs(_need(t.amount, "amount"))}
        else:
            raise NeedsReview(f"needs manual review: CAS entry type {d_type} ({t.description[:40]!r})")
        key, txn = build_txn(
            resolver,
            source_kind=kind,
            source_document=source_document,
            account=account,
            asset_ref=_scheme_ref(scheme),
            trade_date=when,
            external_id=scheme.rta_code,
            **kwargs,
        )
        return (*key, scheme.scheme, t.description[:30]), txn

    try:
        return make_row(row_no, TRANSACTION, raw, build)
    except _Absorbed:
        return ParsedRow(row_no, TRANSACTION, raw, status="NEW", reason="stamp duty folded into purchase")
    except _Manual as exc:
        return ParsedRow(row_no, TRANSACTION, raw, status="AMBIGUOUS", reason=str(exc))
    finally:
        pass


class _Absorbed(Exception):
    pass


class _Manual(Exception):
    pass


def _need(value: Decimal | None, name: str) -> Decimal:
    if value is None:
        raise ImportRowError(f"{name} is missing in the statement")
    return value


def _unit_kwargs(t: TransactionData, txn_type: str, stamp: Decimal | None) -> dict[str, Any]:
    units, nav, amount = abs(_need(t.units, "units")), _need(t.nav, "nav"), abs(_need(t.amount, "amount"))
    stamp = stamp or ZERO
    if txn_type in {"BUY", "SIP", "REDEMPTION"}:
        # C6: never trust units. Amount (less purchase stamp duty) must equal units x NAV.
        net = amount - stamp if txn_type != "REDEMPTION" else amount
        tolerance = nav * UNITS_TOLERANCE / 2 + AMOUNT_TOLERANCE
        if abs(units * nav - net) > tolerance:
            raise ImportRowError(
                f"UNITS_MISMATCH: units {units} x NAV {nav} = {q4(units * nav)} but amount is {net}"
            )
    kwargs: dict[str, Any] = {
        "type_name": txn_type,
        "quantity": units,
        "price": nav,
        "stamp_duty": stamp,
    }
    if txn_type in {"BUY", "SIP"}:
        kwargs["net_cash_orig"] = -amount
    elif txn_type == "REDEMPTION":
        kwargs["net_cash_orig"] = amount
    return kwargs


def _holding_row(
    row_no: int,
    scheme: Scheme,
    account: str,
    as_of: date | None,
    resolver: Resolver,
    source_document: str,
    kind: str,
) -> ParsedRow:
    raw = {"folio": account, "scheme": scheme.scheme, "close": str(scheme.close), "as_of": str(as_of)}

    def build() -> tuple[tuple[object, ...], BaseModel]:
        if as_of is None:
            raise ImportRowError("statement period end is missing")
        h = NewStatementHolding(
            account_id=resolver.account(account),
            asset_id=resolver.asset(_scheme_ref(scheme)).id,
            as_of=as_of,
            units=scheme.close,
            source_document=source_document,
        )
        return (kind, account, h.asset_id, as_of), h

    return make_row(row_no, STATEMENT_HOLDING, raw, build)


def _parse_depository(data: NSDLCASData, resolver: Resolver, source_document: str) -> ParsedFile:
    kind = "CAS_NSDL" if str(data.file_type) == "NSDL" else "CAS_CDSL"
    out = ParsedFile(kind)
    out.period_start = parse_date(data.statement_period.from_)
    out.period_end = parse_date(data.statement_period.to)
    out.blocking.extend(f"casparser warning: {w}" for w in data.parse_warnings)
    out.warnings.append("Depository CAS lists holdings only; no transactions are imported from it.")
    row_no = 0
    for acc in data.accounts:
        account = demat_label(acc.dp_id, acc.client_id)
        holdings: list[tuple[str, Decimal]] = (
            [(e.isin, e.num_shares) for e in acc.equities]
            + [(m.isin, m.balance) for m in acc.mutual_funds]
            + [(b.isin, b.num_bonds) for b in acc.bonds]
        )
        for isin, units in holdings:
            row_no += 1
            raw = {"account": account, "isin": isin, "units": str(units)}

            def build(isin: str = isin, units: Decimal = units, account: str = account):  # type: ignore[no-untyped-def]
                h = NewStatementHolding(
                    account_id=resolver.account(account),
                    asset_id=resolver.asset(isin).id,
                    as_of=out.period_end or date.min,
                    units=units,
                    source_document=source_document,
                )
                return (kind, account, h.asset_id, h.as_of), h

            out.rows.append(make_row(row_no, STATEMENT_HOLDING, raw, build))
    return out
