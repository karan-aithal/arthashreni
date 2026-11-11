"""Source registry (D2): one row per source in docs/data-sources/feasibility.md, synced to the database."""

from dataclasses import dataclass, fields

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class SourceSpec:
    id: str
    name: str
    provider: str
    data_category: str
    auth_type: str
    priority: int
    underlying_series: str | None = None
    adapter: str | None = None  # None: no automated adapter (manual file, later phase or not permitted)
    free_tier: str | None = None
    rate_limit: str | None = None
    min_interval_seconds: float = 0.0
    daily_quota: int | None = None
    historical_coverage: str | None = None
    expected_delay_days: int = 1
    terms_url: str | None = None
    storage_permitted: str | None = None
    redistribution_permitted: str | None = None
    enabled: bool = False
    notes: str | None = None


_AMFI_TERMS = "https://www.amfiindia.com/terms-of-use"
_SPECS: list[SourceSpec] = [
    SourceSpec(
        "amfi_nav",
        "AMFI daily NAV file",
        "amfi",
        "MF NAV",
        "none",
        1,
        adapter="amfi_nav",
        rate_limit="1 fetch/business day (own limit)",
        min_interval_seconds=5,
        daily_quota=3,
        expected_delay_days=1,
        terms_url=_AMFI_TERMS,
        storage_permitted="personal use; held schemes only",
        redistribution_permitted="no",
        enabled=True,
        notes="NAVAll.txt has 8 columns since at least 2026-11-11 (Plan, Option added).",
    ),
    SourceSpec(
        "amfi_nav_hist",
        "AMFI NAV history tool",
        "amfi",
        "MF NAV history",
        "none",
        3,
        historical_coverage="form-based, max 90 days per request",
        terms_url=_AMFI_TERMS,
        notes="Manual only: import with `market import-prices`.",
    ),
    SourceSpec(
        "cas_nav",
        "NAV and units in CAMS/KFintech CAS",
        "cams_kfintech",
        "MF NAV fallback",
        "pdf password",
        2,
        notes="Manual file via the import inbox.",
    ),
    SourceSpec(
        "sbi_tt_archive",
        "SBI TT archive (sahilgupta/sbi-fx-ratekeeper)",
        "github",
        "FX (SBI TT buy)",
        "none",
        1,
        underlying_series="SBI",
        adapter="sbi_tt",
        rate_limit="1 fetch/day",
        min_interval_seconds=2,
        daily_quota=5,
        historical_coverage="2020-01 onwards",
        expected_delay_days=1,
        terms_url="https://github.com/sahilgupta/sbi-fx-ratekeeper",
        storage_permitted="MIT; data from SBI, unofficial",
        enabled=True,
        notes="Single unofficial archive; second archive for cross-check still open (V2).",
    ),
    SourceSpec(
        "fbil_ref",
        "FBIL USD/INR reference rate via Frankfurter",
        "frankfurter",
        "FX (reference)",
        "none",
        1,
        underlying_series="FBIL",
        adapter="fbil",
        rate_limit="no cap; we use 1 req/day",
        min_interval_seconds=1,
        daily_quota=20,
        historical_coverage="2018-07-10 onwards",
        expected_delay_days=1,
        terms_url="https://frankfurter.dev",
        enabled=True,
        notes="FBIL-provider endpoint only, never the blended rate. Mirror lagged ~1 week on 2026-11-11.",
    ),
    SourceSpec(
        "fbil_manual",
        "FBIL rates entered by hand",
        "manual",
        "FX (reference)",
        "none",
        2,
        underlying_series="FBIL",
        adapter="manual_fx",
        enabled=True,
        notes="`market import-fx`.",
    ),
    SourceSpec(
        "broker_fx",
        "INDmoney statement FX",
        "indmoney",
        "FX (actual)",
        "none",
        1,
        underlying_series="BROKER",
        notes="From statements (Phase 2 ledger rows).",
    ),
    SourceSpec(
        "tiingo_eod",
        "Tiingo EOD API",
        "tiingo",
        "US prices",
        "personal API key",
        1,
        adapter="tiingo",
        free_tier="50 req/hour, 1,000 req/day, 500 symbols/month",
        min_interval_seconds=1.5,
        daily_quota=1000,
        expected_delay_days=1,
        terms_url="https://www.tiingo.com/about/pricing",
        storage_permitted="internal use only",
        redistribution_permitted="no",
        enabled=True,
    ),
    SourceSpec(
        "alphavantage_eod",
        "Alpha Vantage",
        "alphavantage",
        "US prices fallback",
        "personal API key",
        2,
        free_tier="25 req/day",
        daily_quota=25,
        terms_url="https://www.alphavantage.co/support/",
        notes="Not built: terms not re-read (feasibility open item 3).",
    ),
    SourceSpec("indmoney_stmt", "INDmoney statement prices", "indmoney", "US prices last resort", "none", 3),
    SourceSpec(
        "manual_prices",
        "Manual price/NAV CSV",
        "manual",
        "Prices (any)",
        "none",
        3,
        adapter="manual_prices",
        expected_delay_days=0,
        enabled=True,
        notes="`market import-prices`: last resort for any price.",
    ),
    SourceSpec(
        "sec_companyfacts",
        "SEC EDGAR XBRL companyfacts",
        "sec",
        "US fundamentals",
        "declared User-Agent",
        1,
        adapter="sec",
        rate_limit="max 10 req/s (we use <= 2)",
        min_interval_seconds=0.5,
        daily_quota=500,
        expected_delay_days=3,
        terms_url="https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data",
        storage_permitted="public data",
        enabled=True,
    ),
    SourceSpec(
        "nse_bhavcopy",
        "NSE daily price files",
        "nse",
        "IN ETF/stock prices",
        "prohibited",
        9,
        terms_url="https://www.nseindia.com/static/nse-terms-of-use",
        notes="Terms prohibit automated collection (V4). Never enable.",
    ),
    SourceSpec(
        "bse_bhavcopy",
        "BSE bhavcopy",
        "bse",
        "IN ETF/stock prices",
        "manual",
        3,
        terms_url="https://bseindia.com/static/about/Terms_condition.htm",
        notes="Manual download only.",
    ),
    SourceSpec(
        "cas_console_price",
        "Prices on CAS / Zerodha Console",
        "statements",
        "IN ETF/stock prices",
        "none",
        1,
        notes="Statement prices via the inbox.",
    ),
    SourceSpec("fred", "FRED API", "fred", "US macro", "personal API key", 1, notes="Phase 5."),
    SourceSpec("rbi_dbie", "RBI DBIE", "rbi", "IN macro", "manual", 1, notes="Later: manual Excel."),
    SourceSpec("mospi", "MOSPI", "mospi", "IN macro", "unknown", 2, notes="Not checked."),
    SourceSpec(
        "fund_disclosures",
        "Fund-house monthly portfolio files",
        "amcs",
        "Fund holdings",
        "unknown",
        1,
        notes="Phase 7.",
    ),
    SourceSpec(
        "issuer_etf_holdings",
        "US ETF issuer holdings files",
        "issuers",
        "ETF holdings",
        "unknown",
        1,
        notes="Phase 7.",
    ),
    SourceSpec(
        "sebi_nse_bse_ipo", "SEBI/NSE/BSE DRHP/RHP", "sebi", "IPO filings", "manual", 1, notes="Later."
    ),
    SourceSpec("official_rss", "Official RSS feeds", "various", "News", "unknown", 1, notes="Phase 7."),
    SourceSpec(
        "social",
        "Reddit, X, Discord",
        "various",
        "Social",
        "out of scope",
        9,
        notes="Out of scope for the MVP (D1). Never enable.",
    ),
]
SPECS: dict[str, SourceSpec] = {s.id: s for s in _SPECS}

_COLUMNS = [f.name for f in fields(SourceSpec)]
_SYNCED = [c for c in _COLUMNS if c != "enabled"]  # `enabled` is the investor's switch once the row exists


def sync_registry(db: Session) -> None:
    """Insert missing registry rows and refresh descriptive columns. An existing `enabled` is kept."""
    cols = ", ".join(_COLUMNS)
    vals = ", ".join(f":{c}" for c in _COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _SYNCED if c != "id")
    for s in SPECS.values():
        db.execute(
            text(
                f"INSERT INTO ref.data_source_registry ({cols}) VALUES ({vals}) "  # noqa: S608
                f"ON CONFLICT (id) DO UPDATE SET {updates}"  # fixed column names only
            ),
            {c: getattr(s, c) for c in _COLUMNS},
        )
