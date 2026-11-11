# [VERIFY] register

Every time-sensitive fact used by the project, with source and check date. Status: `verified` (source + date recorded), `open` (not yet confirmed; must not be relied on), `not yet touched` (belongs to a later phase).

| # | Item | Status | Finding | Source | Checked |
| --- | --- | --- | --- | --- | --- |
| V1 | casparser support (C3) | verified | v1.4.1 (2026-08-30), MIT; CAMS, KFintech, NSDL, CDSL supported; MF Central unsupported | https://pypi.org/project/casparser/ | 2026-11-11 |
| V2 | SBI TT archive (C5) | verified (unofficial source) | MIT; data from Jan 2020; daily updates; gaps on non-publication days. Second archive: open | https://github.com/sahilgupta/sbi-fx-ratekeeper | 2026-11-11 |
| V3 | US price API terms (D1) | verified | Tiingo free: 50/h, 1,000/day, 500 symbols/month, internal use only. Alpha Vantage free: 25/day | https://www.tiingo.com/about/pricing ; https://www.alphavantage.co/support/ | 2026-11-11 |
| V4 | NSE/BSE automated price files (D1) | verified: not permitted (NSE); not shown permitted (BSE) | NSE ToU prohibits automated collection without written consent | https://www.nseindia.com/static/nse-terms-of-use (search-indexed copy) ; https://bseindia.com/static/about/Terms_condition.htm | 2026-11-11 |
| V5 | SEC EDGAR access rules (D1) | verified | Declared User-Agent; ≤ 10 req/s | https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data | 2026-11-11 |
| V6 | AMFI NAV file and terms (D1) | verified | `NAVAll.txt` URL; personal non-commercial use; no storing significant portion | https://www.amfiindia.com/net-asset-value/nav-download ; https://www.amfiindia.com/terms-of-use | 2026-11-11 |
| V7 | FBIL reference rate (C5) | verified (access); FBIL terms open | FBIL publishes weekdays since 2018-07-10; automated via Frankfurter FBIL-provider endpoint (USD/INR 94.8606 on 2026-11-03; mirror lags ~1 week). FBIL terms page unreadable | https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=44393 ; https://api.frankfurter.dev/v2/providers/fbil | 2026-11-11 |
| V8 | FRED terms (D1) | verified | Key required; attribution notice when distributed | https://fred.stlouisfed.org/docs/api/terms_of_use.html | 2026-11-11 |
| V9 | RBI DBIE access (D1) | verified: manual only | Excel download, no official API found; portal at data.rbi.org.in | RBI DBIE brochure/search results | 2026-11-11 |
| V10 | SME IPO minimum application (B3) | verified | ICDR Reg. 267: minimum two lots, application value above ₹2 lakh (March 2025 amendment). SEBI consultation paper (14 Oct 2026) proposes ₹1 lakh as four lots of ≥ ₹25,000; draft only. Re-check for a final circular when B3 is coded (Phase 5). SME IPOs stay excluded either way | https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1791972839578.pdf | 2026-11-11 |
| V11 | Sovereign Gold Bond issuance (C8) | verified | Latest official issuance notice is SGB 2023-24 (PIB, Dec 2023, tranches to Feb 2024); no later official issuance notice found on PIB. No formal discontinuation notice exists either. Design treats SGB as existing holdings only, which holds in both cases | https://www.pib.gov.in/Pressreleaseshare.aspx?PRID=1984145 | 2026-11-11 |
| V12 | exchange_calendars XNYS/XBOM (E3) | verified | v4.13.2 (2026-03-10), Apache-2.0, XNYS and XBOM included | https://pypi.org/project/exchange-calendars/ | 2026-11-11 |
| V13 | pyxirr (G1.5) | verified | v0.10.8 (2025-11-03), Unlicense | https://pypi.org/project/pyxirr/ | 2026-11-11 |
| V14 | Zerodha Console exports (C3) | partly verified | Tradebook XLSX/CSV; Coin MF order location open | Zerodha support article | 2026-11-11 |
| V15 | INDmoney statement format (C3) | open | Statements downloadable; format unconfirmed | INDmoney mandatory disclosures page | 2026-11-11 |
| V16 | MF NAV applicability and stamp duty (C6) | not yet touched | Phase 2/4 | — | — |
| V17 | All tax rates (C7): equity/debt MF, foreign stock gains, US dividend withholding, Form 67, TCS on LRS, Schedule FA | not yet touched | Enter only when `config/tax_rules.yaml` is created; official sources only | — | — |
| V18 | Ollama models and licences (F5) | not yet touched | Phase 8 | — | — |
| V19 | Free chart library licence (Part H) | not yet touched | Phase 6 | — | — |
| V20 | pyrage availability and licence (ADR-0014) | verified | Windows not needed (ADR-0018); pyrage 1.4.0 MIT, works on Linux | https://github.com/woodruffw/pyrage | 2026-11-11 |
