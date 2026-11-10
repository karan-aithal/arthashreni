# Data-source feasibility

Checked on 2026-11-11 from the official pages linked below unless marked otherwise. This file is the human-readable form of `ref.data_source_registry` (created in Phase 3). Each adapter re-checks its source's terms before it is built; a change in terms disables the adapter.

Legend for **Automation**: `yes` = terms or official docs allow scripted access for personal use; `manual` = use downloads/statements by hand; `no` = terms prohibit it.

## Summary

| id | Source | Category | Automation | Auth | Free-tier / rate limit | Storage / redistribution | Priority | MVP |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `amfi_nav` | AMFI daily NAV file | MF NAV | yes (public file, personal non-commercial use) | none | 1 fetch/business day (our own limit) | personal use only; do not store a "significant portion"; no redistribution | 1 | yes |
| `amfi_nav_hist` | AMFI NAV history tool | MF NAV history | manual (form, max 90 days per request, no direct URL) | none | — | same as above | 3 | yes (manual) |
| `cas_nav` | NAV/units in CAMS/KFintech CAS | MF NAV fallback | manual file | PDF password per import | — | personal | 2 | yes |
| `sbi_tt_archive` | `sahilgupta/sbi-fx-ratekeeper` CSV | FX (SBI TT buy) | yes (public GitHub, MIT) | none | 1 fetch/day | MIT; data originates from SBI, unofficial | 1 (tax FX) | yes |
| `fbil_ref` | FBIL USD/INR reference rate (provider: Frankfurter; series: FBIL) | FX (reference) | yes (Frankfurter public API, no key) | none | no daily/monthly cap; abuse rate-limited; we use 1 req/day | Frankfurter: provider data subject to FBIL terms (FBIL terms page unreadable) | 1 (analytics FX) | yes |
| `fbil_manual` | FBIL rates entered by hand | FX (reference) fallback | manual CSV | none | — | — | 2 | yes |
| `broker_fx` | INDmoney statement FX | FX (actual) | manual file | none | — | personal | 1 (cost FX) | yes |
| `tiingo_eod` | Tiingo EOD API | US prices | yes | personal API key | 50 req/hour, 1,000 req/day, 500 unique symbols/month, 1 GB/month | "Internal Use Only"; may not display or share data with another person | 1 | yes |
| `alphavantage_eod` | Alpha Vantage | US prices fallback | yes | personal API key | 25 req/day | not re-checked beyond quota | 2 | yes |
| `indmoney_stmt` | INDmoney statement prices | US prices last resort | manual file | none | — | personal | 3 | yes |
| `sec_companyfacts` | SEC EDGAR XBRL `companyfacts` | US fundamentals | yes | none; declared User-Agent with contact email | max 10 req/s; no crawling | public data | 1 | yes |
| `nse_bhavcopy` | NSE daily price files | IN ETF/stock prices | **no** (ToU prohibits automated collection without written consent) | — | — | — | — | no |
| `bse_bhavcopy` | BSE bhavcopy | IN ETF/stock prices | manual (no clause found permitting automation) | — | — | not verified | 3 | manual only |
| `cas_console_price` | Prices on CAS / Zerodha Console holdings | IN ETF/stock prices | manual file | none | — | personal | 1 | yes |
| `fred` | FRED API | US macro | yes | personal API key | no fixed number published; reasonable bandwidth | personal use; third-party copyrighted series need owner permission; attribution notice if distributed | 1 | later (Phase 5 risk) |
| `rbi_dbie` | RBI DBIE (data.rbi.org.in) | IN macro | manual (Excel download; no official API found) | none | — | terms not found | 1 | later |
| `mospi` | MOSPI | IN macro | not checked | — | — | — | 2 | later |
| `fund_disclosures` | Fund-house monthly portfolio files | Fund holdings | not checked (per fund house, Phase 7) | — | — | — | 1 | later |
| `issuer_etf_holdings` | US ETF issuer holdings files | ETF holdings | not checked (Phase 7) | — | — | — | 1 | later |
| `sebi_nse_bse_ipo` | SEBI/NSE/BSE DRHP/RHP | IPO filings | manual download (NSE automation prohibited) | — | — | — | 1 | later |
| `official_rss` | Official RSS feeds | News | not checked (Phase 7) | — | — | — | 1 | later |
| `social` | Reddit, X, Discord | Social | out of scope | — | — | — | — | no |

## Evidence per source

### AMFI (`amfi_nav`, `amfi_nav_hist`)
- Daily file: `https://portal.amfiindia.com/spages/NAVAll.txt`, linked from https://www.amfiindia.com/net-asset-value/nav-download (checked 2026-11-11).
- History: same page; form-based, "a maximum period of 90 days at a time"; no direct URL given.
- Terms (https://www.amfiindia.com/terms-of-use, checked 2026-11-11): site licensed "for your personal and non-commercial use only"; "You shall not store electronically any significant portion of any part of the Site"; no public display/transmission/publication. Nothing specific about automated download.
- Design consequence: one request per business day, keep only rows for held/watched schemes, never redistribute. Risk: the clause on storage is broad; if AMFI objects, fall back to CAS NAVs + manual. Recorded as an open legal-interpretation risk, not a blocker for personal use.

### SBI TT archive (`sbi_tt_archive`)
- https://github.com/sahilgupta/sbi-fx-ratekeeper (checked 2026-11-11): MIT licence; CSV per currency (e.g. `csv_files/SBI_REFERENCE_RATES_USD.csv`); coverage "from Jan 2020 onwards"; updated daily automatically; some working days missing when SBI did not publish; rates are SBI TT buying/selling.
- Unofficial. Part C5 asks for a second archive to cross-check: **open** — no second independent archive identified yet. Until found, SBI TT values are stored with `quality_status = estimated` and a note "single unofficial archive", and tax outputs that depend on them say so.
- Coverage starts 2020-01: transactions before that date get "tax FX unavailable".

### FBIL (`fbil_ref`)
- FBIL took over USD/INR reference-rate computation and dissemination from RBI on 2018-07-10, published every weekday except Mumbai bank holidays on www.fbil.org.in (RBI press release, https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=44393, checked 2026-11-11).
- The FBIL site is a JavaScript application; its terms page (`https://www.fbil.org.in/#/termsandcondition`) could not be read on 2026-11-11.
- **Automated path found:** Frankfurter (https://frankfurter.dev, open source, no API key, "no monthly or daily caps") lists FBIL as a provider. `GET https://api.frankfurter.dev/v2/providers/fbil` returned (2026-11-11): rate type "reference rate", pivot INR, currencies incl. USD, `start_date` 2018-07-10, `end_date` 2026-11-03, `publishes_missed` 4. `GET .../v2/providers/fbil/rates?base=USD&quotes=INR` returned USD/INR 94.8606 for 2026-11-03.
- Use the FBIL-provider endpoint only, never Frankfurter's default blended rate (that is Frankfurter's own calculation, not FBIL). Store as `FBIL_REF` with source `fbil_ref` and a note "via Frankfurter mirror".
- The mirror lagged about a week on 2026-11-11, so the E3 staleness check will often mark it `stale`; that is the correct result. Manual CSV stays as fallback.
- **Trap:** Frankfurter's provider key `SBI` is Sedlabanki Islands (Iceland's central bank), not State Bank of India. Never use it for `SBI_TT_BUY`.
- Remaining open: FBIL's own terms of use.

### Tiingo (`tiingo_eod`)
- https://www.tiingo.com/about/pricing (checked 2026-11-11): Starter (free) — 50 requests/hour, 1,000/day, 500 unique symbols/month, 1 GB/month; licence "Internal Use Only"; you "may not display or share the data with another person or organization".
- Fits: ~10 symbols, one EOD call each per day. The single-user UI on localhost is internal use.

### Alpha Vantage (`alphavantage_eod`)
- https://www.alphavantage.co/support/ (checked 2026-11-11): free service "25 API requests per day". Enough as a fallback for ≤ 10 symbols only if Tiingo fails; terms of use not re-read — re-check before building the adapter.

### SEC EDGAR (`sec_companyfacts`)
- https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data (checked 2026-11-11): declare a User-Agent such as "Sample Company Name AdminContact@<domain>.com"; "Current max request rate: 10 requests/second"; "Download only what you need"; no botnets or crawling.
- The contact email comes from `.env` (`SEC_USER_AGENT`), set by the investor; it is the only personal datum sent externally and is sent only with consent recorded in config.

### NSE (`nse_bhavcopy`)
- NSE Terms of Use (https://www.nseindia.com/static/nse-terms-of-use; page timed out on direct fetch 2026-11-11, content taken from search-indexed copies the same day) prohibit "systematic or automated data collection activities (including scraping, data mining, data extraction and data harvesting)" without express written consent. Bhavcopy is not carved out.
- NSE lists an official MCP data service (https://www.nseindia.com/nse-mcp) subject to NSE terms; not evaluated — it would be a future ADR.
- Decision: no automated NSE access.

### BSE (`bse_bhavcopy`)
- Bhavcopy page https://bseindia.com/markets/MarketInfo/BhavCopy.aspx; T&C https://bseindia.com/static/about/Terms_condition.htm (search-indexed 2026-11-11). No clause found permitting automated download. Decision: manual download only, imported through the inbox.

### FRED (`fred`)
- https://fred.stlouisfed.org/docs/api/terms_of_use.html (checked 2026-11-11): API key required; bandwidth limits may be imposed (no fixed number); third-party copyrighted series require owner permission beyond personal use; distributed apps must show "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis."

### RBI DBIE (`rbi_dbie`)
- Portal moved to https://data.rbi.org.in; supports Excel download; no official API or terms of use found (2026-11-11). Manual import only.

### Statements
- **casparser** (https://pypi.org/project/casparser/, checked 2026-11-11): v1.4.1, released 2026-08-30, MIT; parses original CAS PDFs from CAMS, KFintech, NSDL, CDSL; MF Central CAS listed as unsupported.
- **Zerodha Console** (https://support.zerodha.com/category/console/reports/articles/where-can-i-see-all-the-trades-i-ve-taken-for-a-particular-period, 2026-11-11): Reports → Tradebook, choose segment and dates, download XLSX or CSV. Whether Coin MF orders appear in the standard tradebook or only an external tradebook is **open**; resolve with a sample file in Phase 2.
- **INDmoney US stocks**: account statement under US Stocks → Manage → Account Statement (third-party guide, 2026-11-11); INDmoney mandatory disclosures (https://www.indmoney.com/page/mandatory-disclosures) state transaction, holding and tax P&L reports are downloadable. **File format (PDF/CSV) unconfirmed** — Phase 2 builds the parser against a sample the investor provides, or a synthetic sample clearly marked as assumed format.

## Options considered and rejected
| Option | Reason |
| --- | --- |
| Zerodha Kite Connect / other broker APIs for Indian ETF prices | Need a logged-in broker session, which invariant A3.3 forbids; Kite Connect market data is also paid |
| Frankfurter blended USD/INR as a rate type | Not FBIL, not SBI TT, not broker; would blur the three labelled series (ADR-0016) |
| Scheduled monitoring of NSE/BSE pages for IPO/SME data | NSE terms forbid automated collection; IPO data comes from SEBI-hosted DRHP/RHP PDFs, downloaded by hand |
| PyMuPDF for PDF parsing | AGPL-3.0 licence (ADR-0002 keeps AGPL out of the core); `casparser` and `pdfplumber`/`pdfminer.six` (MIT) cover the need |

## Open items

1. Second SBI TT archive for cross-check.
2. FBIL's own terms of use (automated path solved via Frankfurter).
3. Alpha Vantage terms of use text.
4. Zerodha Coin MF order export location.
5. INDmoney statement format.
6. Fund-house disclosure files, ETF issuer files, RSS feeds, MOSPI: check in the phase that uses them.
