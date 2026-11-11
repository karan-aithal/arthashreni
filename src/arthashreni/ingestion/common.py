"""Shared importer types: parsed rows, files, resolver protocol, value parsing."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from arthashreni.ledger.assets import Asset, UnknownAsset
from arthashreni.ledger.types import LedgerError

TRANSACTION = "TRANSACTION"
CORPORATE_ACTION = "CORPORATE_ACTION"
STATEMENT_HOLDING = "STATEMENT_HOLDING"


class ImportRowError(ValueError):
    """A row cannot be imported; the message is shown in the preview."""


class NeedsReview(Exception):
    """The row is understood but must be handled by hand (e.g. a CAS entry type we do not map)."""


class Resolver(Protocol):
    def asset(self, ref: str) -> Asset: ...
    def account(self, label: str) -> int: ...


@dataclass
class ParsedRow:
    row_no: int
    item_kind: str
    raw: dict[str, Any]
    key: tuple[object, ...] | None = None
    candidate: BaseModel | None = None
    status: str = "NEW"  # NEW | DUPLICATE | AMBIGUOUS | REJECTED
    reason: str | None = None
    fingerprint: str | None = None


@dataclass
class ParsedFile:
    source_kind: str
    rows: list[ParsedRow] = field(default_factory=list)
    period_start: date | None = None
    period_end: date | None = None
    blocking: list[str] = field(default_factory=list)  # problems that stop the whole batch
    warnings: list[str] = field(default_factory=list)


def make_row(
    row_no: int,
    kind: str,
    raw: dict[str, Any],
    build: Callable[[], tuple[tuple[object, ...], BaseModel]],
) -> ParsedRow:
    """Run `build`; any validation problem becomes a REJECTED row with a short reason."""
    try:
        key, candidate = build()
    except NeedsReview as exc:
        return ParsedRow(row_no, kind, raw, status="AMBIGUOUS", reason=str(exc))
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        msg = str(first["msg"]).removeprefix("Value error, ")
        return ParsedRow(row_no, kind, raw, status="REJECTED", reason=f"{loc}: {msg}" if loc else msg)
    except (ImportRowError, UnknownAsset, LedgerError, ValueError, InvalidOperation) as exc:
        return ParsedRow(row_no, kind, raw, status="REJECTED", reason=str(exc) or type(exc).__name__)
    return ParsedRow(row_no, kind, raw, key=key, candidate=candidate)


_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%m/%d/%Y")


def parse_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ImportRowError(f"unreadable date {text!r}")


def parse_decimal(value: object, *, required: bool = False, name: str = "value") -> Decimal | None:
    text = "" if value is None else str(value).strip()
    text = re.sub(r"[,\s₹$]", "", text)
    if text.startswith("(") and text.endswith(")"):  # accounting negatives
        text = "-" + text[1:-1]
    if text in ("", "-", "--"):
        if required:
            raise ImportRowError(f"{name} is required")
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ImportRowError(f"{name} is not a number: {text!r}") from exc


def need_decimal(value: object, name: str) -> Decimal:
    result = parse_decimal(value, required=True, name=name)
    assert result is not None
    return result
