"""AMFI daily NAV file (`NAVAll.txt`). One request per business day; only held schemes are kept.

AMFI's terms forbid storing "any significant portion" of the site (V6), so `fetch` validates the whole
file and then returns only the lines of the schemes we hold: the raw payload we store is that excerpt.
"""

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from arthashreni.ingestion.sources.contract import (
    Adapter,
    Observation,
    PriceObs,
    RawPayload,
    SchemaDrift,
    Window,
)
from arthashreni.ingestion.sources.http import Http

URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
HEADER = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
    "Scheme Name;Plan;Option;Net Asset Value;Date"
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1
    )
}


def parse_amfi_date(s: str) -> date:
    try:
        d, m, y = s.strip().split("-")
        return date(int(y), _MONTHS[m.title()], int(d))
    except (ValueError, KeyError) as exc:
        raise SchemaDrift(f"AMFI date {s!r} is not DD-Mon-YYYY") from exc


def _rows(text: str) -> list[list[str]]:
    lines = [ln.strip() for ln in text.splitlines()]
    if not lines or lines[0] != HEADER:
        raise SchemaDrift(f"AMFI header changed: {lines[0][:120] if lines else '(empty file)'!r}")
    rows = [ln.split(";") for ln in lines[1:] if ln.count(";") == 7]
    if not rows:
        raise SchemaDrift("AMFI file has no scheme rows")
    for r in rows:
        if not r[0].isdigit():
            raise SchemaDrift(f"AMFI scheme code {r[0]!r} is not numeric")
    return rows


class AmfiNav(Adapter):
    source_id = "amfi_nav"

    def __init__(self, http: Http, amfi_codes: set[str]) -> None:
        self.http, self.codes = http, amfi_codes

    def fetch(self, window: Window) -> RawPayload:
        body = self.http.get(URL)
        text = body.decode("utf-8", errors="replace")
        rows = _rows(text)
        keep = [HEADER] + [";".join(r) for r in rows if r[0] in self.codes]
        return RawPayload(self.source_id, datetime.now(UTC), window, "\n".join(keep).encode(), 200, "txt")

    def parse(self, payload: RawPayload) -> list[Observation]:
        out: list[Observation] = []
        for r in _rows(payload.body.decode("utf-8")) if payload.body.count(b"\n") else []:
            try:
                nav = Decimal(r[6])
            except InvalidOperation as exc:
                raise SchemaDrift(f"AMFI NAV {r[6]!r} for scheme {r[0]} is not a number") from exc
            out.append(
                PriceObs(
                    asset_ref=f"AMFI:{r[0]}",
                    valid_at=parse_amfi_date(r[7]),
                    close=nav,
                    currency="INR",
                    price_type="NAV",
                )
            )
        return out
