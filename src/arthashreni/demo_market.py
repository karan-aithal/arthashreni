"""DEMO market data: committed synthetic fixtures served through the real adapters (no network).

Keeps the DEMO dataset free of live data (Part H, Phase 3 risk note): same parsing, checks and storage as
real sources, with an `httpx` transport that answers from `demo/market/`.
"""

from datetime import date
from pathlib import Path

import httpx
from sqlalchemy import text

from arthashreni.core.config import PROJECT_ROOT
from arthashreni.db.session import session_scope
from arthashreni.ingestion.sources import fx as fx_mod
from arthashreni.ingestion.sources import sec as sec_mod
from arthashreni.ingestion.sources import tiingo as tiingo_mod
from arthashreni.ingestion.sources.amfi import URL as AMFI_URL
from arthashreni.ingestion.sources.contract import Window
from arthashreni.ingestion.sources.ingest import run_daily
from arthashreni.ingestion.sources.manual import ManualPrices
from arthashreni.ingestion.sources.registry import sync_registry
from arthashreni.ingestion.sources.runner import RunResult, run_adapter

DEMO_MARKET_DIR = PROJECT_ROOT / "demo" / "market"
DEMO_TODAY = date(2026, 11, 10)
DEMO_CIK = "9999999"
_WINDOW = Window(date(2026, 9, 1), DEMO_TODAY)


class FixtureTransport(httpx.BaseTransport):
    """Answers the adapters' URLs from files in `directory`. Anything else is a 404."""

    def __init__(self, directory: Path = DEMO_MARKET_DIR) -> None:
        self.dir = directory
        self.seen: list[str] = []

    def _file(self, url: httpx.URL) -> Path | None:
        u = str(url).split("?")[0]
        if u == AMFI_URL:
            return self.dir / "NAVAll.txt"
        if u == fx_mod.SBI_URL:
            return self.dir / "SBI_REFERENCE_RATES_USD.csv"
        if u == fx_mod.FBIL_URL:
            return self.dir / "fbil_rates.json"
        if u.startswith(tiingo_mod.URL.split("{")[0]):
            return self.dir / f"tiingo_{u.split('/daily/')[1].split('/')[0]}.json"
        if u.startswith(sec_mod.URL.split("{")[0]):
            return self.dir / f"sec_CIK{u.rsplit('CIK', 1)[1].removesuffix('.json')}.json"
        return None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        path = self._file(request.url)
        if path is None or not path.exists():
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=path.read_bytes(), request=request)


def load_demo_market(directory: Path = DEMO_MARKET_DIR) -> list[RunResult]:
    """Run every automated adapter against the fixtures, then the manual price file."""
    with session_scope("demo") as db:
        sync_registry(db)
        db.execute(
            text("UPDATE ref.assets SET cik = :c WHERE ticker = 'DTECH' AND is_demo AND cik IS NULL"),
            {"c": DEMO_CIK},
        )
    results = [run_adapter("demo", ManualPrices(directory / "manual_prices.csv"), _WINDOW, offline=True)]
    transport = FixtureTransport(directory)
    windows = {s: _WINDOW for s in ("amfi_nav", "sbi_tt_archive", "fbil_ref", "tiingo_eod")}
    windows["sec_companyfacts"] = Window(date(2026, 1, 1), DEMO_TODAY)  # filings from earlier in 2026
    results += run_daily(
        "demo", DEMO_TODAY, transport=transport, offline=True, sleep=lambda _s: None, force=True,
        windows=windows,
    )  # fmt: skip
    return results
