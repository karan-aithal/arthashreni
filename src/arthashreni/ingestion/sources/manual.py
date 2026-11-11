"""Manual CSV imports: the last resort for any price or FX series (D1)."""

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from arthashreni.ingestion.sources.contract import (
    Adapter,
    FxObs,
    Observation,
    PriceObs,
    RawPayload,
    SchemaDrift,
    Window,
)

PRICE_HEADER = ["asset_ref", "valid_at", "close", "currency", "price_type"]
FX_HEADER = ["rate_type", "valid_at", "rate"]
_SERIES = {"FBIL_REF": "FBIL"}  # SBI TT and broker rates have no manual path (statement import / archive)


def _read(text: str, header: list[str]) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != header:
        raise SchemaDrift(f"expected columns {header}, got {reader.fieldnames}")
    return list(reader)


def _num(s: str, what: str) -> Decimal:
    try:
        return Decimal(s.strip())
    except InvalidOperation as exc:
        raise SchemaDrift(f"{what} {s!r} is not a number") from exc


def _day(s: str) -> date:
    try:
        return date.fromisoformat(s.strip())
    except ValueError as exc:
        raise SchemaDrift(f"date {s!r} is not YYYY-MM-DD") from exc


class _FileAdapter(Adapter):
    def __init__(self, path: Path) -> None:
        self.path = path

    def fetch(self, window: Window) -> RawPayload:
        return RawPayload(self.source_id, datetime.now(UTC), window, self.path.read_bytes(), None, "csv")


class ManualPrices(_FileAdapter):
    """`asset_ref,valid_at,close,currency,price_type` (price_type CLOSE, NAV or STATEMENT)."""

    source_id = "manual_prices"

    def parse(self, payload: RawPayload) -> list[Observation]:
        out: list[Observation] = []
        for r in _read(payload.body.decode("utf-8"), PRICE_HEADER):
            if r["price_type"] not in ("CLOSE", "NAV", "STATEMENT"):
                raise SchemaDrift(f"price_type {r['price_type']!r} is not CLOSE, NAV or STATEMENT")
            out.append(
                PriceObs(
                    asset_ref=r["asset_ref"].strip(),
                    valid_at=_day(r["valid_at"]),
                    close=_num(r["close"], "close"),
                    currency=r["currency"].strip().upper(),
                    price_type=r["price_type"],
                )
            )
        return out


class ManualFx(_FileAdapter):
    """`rate_type,valid_at,rate` for FBIL_REF (fallback when the Frankfurter mirror lags)."""

    source_id = "fbil_manual"

    def parse(self, payload: RawPayload) -> list[Observation]:
        out: list[Observation] = []
        for r in _read(payload.body.decode("utf-8"), FX_HEADER):
            if r["rate_type"] not in _SERIES:
                raise SchemaDrift(f"manual FX supports {sorted(_SERIES)}, got {r['rate_type']!r}")
            out.append(
                FxObs(
                    rate_type="FBIL_REF",
                    underlying_series="FBIL",
                    provider="manual",
                    valid_at=_day(r["valid_at"]),
                    rate=_num(r["rate"], "rate"),
                )
            )
        return out
