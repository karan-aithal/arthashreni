"""USD/INR series: SBI TT buying (unofficial archive) and FBIL reference (Frankfurter FBIL provider)."""

import csv
import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from arthashreni.ingestion.sources.contract import (
    Adapter,
    FxObs,
    Observation,
    RawPayload,
    SchemaDrift,
    Window,
)
from arthashreni.ingestion.sources.http import Http

SBI_URL = "https://raw.githubusercontent.com/sahilgupta/sbi-fx-ratekeeper/main/csv_files/SBI_REFERENCE_RATES_USD.csv"
SBI_HEADER = [
    "DATE",
    "PDF FILE",
    "TT BUY",
    "TT SELL",
    "BILL BUY",
    "BILL SELL",
    "FOREX TRAVEL CARD BUY",
    "FOREX TRAVEL CARD SELL",
    "CN BUY",
    "CN SELL",
]
FBIL_URL = "https://api.frankfurter.dev/v2/providers/fbil/rates"
IST = ZoneInfo("Asia/Kolkata")


def _sbi_rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != SBI_HEADER:
        raise SchemaDrift(f"SBI archive columns changed: {reader.fieldnames}")
    return list(reader)


def _sbi_stamp(s: str) -> datetime:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    except ValueError as exc:
        raise SchemaDrift(f"SBI archive date {s!r} is not YYYY-MM-DD HH:MM") from exc


class SbiTt(Adapter):
    """TT buying rate. The archive writes 0.00 on days SBI gave none; that is kept and then quarantined."""

    source_id = "sbi_tt_archive"

    def __init__(self, http: Http) -> None:
        self.http = http

    def fetch(self, window: Window) -> RawPayload:
        text = self.http.get(SBI_URL).decode("utf-8")
        rows = _sbi_rows(text)
        keep = [r for r in rows if window.start <= _sbi_stamp(r["DATE"]).date() <= window.end]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=SBI_HEADER, lineterminator="\n")
        w.writeheader()
        w.writerows(keep)
        return RawPayload(self.source_id, datetime.now(UTC), window, buf.getvalue().encode(), 200, "csv")

    def parse(self, payload: RawPayload) -> list[Observation]:
        out: list[Observation] = []
        for r in _sbi_rows(payload.body.decode("utf-8")):
            stamp = _sbi_stamp(r["DATE"])
            try:
                rate = Decimal(r["TT BUY"])
            except InvalidOperation as exc:
                raise SchemaDrift(f"SBI TT BUY {r['TT BUY']!r} is not a number") from exc
            out.append(
                FxObs(
                    rate_type="SBI_TT_BUY",
                    underlying_series="SBI",
                    provider="sbi_fx_ratekeeper",
                    valid_at=stamp.date(),
                    observed_at=stamp.astimezone(UTC),
                    rate=rate,
                )
            )
        return out


class _FbilRow(BaseModel):
    date: date
    base: str
    quote: str
    rate: Decimal


class Fbil(Adapter):
    """FBIL reference rate from Frankfurter's FBIL-provider endpoint. Never Frankfurter's blended rate."""

    source_id = "fbil_ref"

    def __init__(self, http: Http) -> None:
        self.http = http

    def fetch(self, window: Window) -> RawPayload:
        body = self.http.get(
            FBIL_URL,
            params={
                "base": "USD",
                "quotes": "INR",
                "from": window.start.isoformat(),
                "to": window.end.isoformat(),
            },
        )
        return RawPayload(self.source_id, datetime.now(UTC), window, body, 200, "json")

    def parse(self, payload: RawPayload) -> list[Observation]:
        try:
            raw = json.loads(payload.body, parse_float=Decimal)
            if not isinstance(raw, list):
                raise SchemaDrift("FBIL response is not a list")
            rows = [_FbilRow.model_validate(x) for x in raw]
        except (ValueError, ValidationError) as exc:
            raise SchemaDrift(f"FBIL response does not match the expected shape: {str(exc)[:200]}") from exc
        for r in rows:
            if (r.base, r.quote) != ("USD", "INR"):
                raise SchemaDrift(f"FBIL row is {r.base}/{r.quote}, expected USD/INR")
        return [
            FxObs(
                rate_type="FBIL_REF",
                underlying_series="FBIL",
                provider="frankfurter",
                valid_at=r.date,
                rate=r.rate,
            )
            for r in rows
        ]
