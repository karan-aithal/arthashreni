# ADR-0015: Initial data-source selections

Status: Accepted, 2026-11-11. Evidence in `docs/data-sources/feasibility.md`.

## Decision
- **MF NAV:** AMFI `NAVAll.txt`, once per business day. Store only rows for held/watched schemes (AMFI terms forbid storing "any significant portion" of the site). History fallback: CAS NAVs.
- **US EOD prices:** Tiingo free tier primary (1,000 requests/day, 50/hour, 500 symbols/month, internal use only); Alpha Vantage free tier (25 requests/day) as secondary. Personal API keys in `.env`, never committed. Data not redistributed.
- **US fundamentals:** SEC EDGAR `companyfacts`, declared User-Agent with a contact email from `.env`, ≤ 10 req/s (we will use ≤ 2 req/s).
- **Indian ETF/stock prices:** **no automated NSE/BSE download.** NSE Terms of Use prohibit automated data collection without written consent; BSE terms were not found to permit it. Use prices from CAS/Console statements plus manual CSV. Revisit if an official permitted channel is confirmed.
- **FX:** SBI TT buying from `sahilgupta/sbi-fx-ratekeeper` CSV (MIT, unofficial) for tax; FBIL reference rate from Frankfurter's FBIL-provider endpoint (never its blended rate), manual CSV as fallback; broker rate from INDmoney statements.
- **US macro:** FRED API with personal key. **Indian macro:** RBI DBIE (data.rbi.org.in) manual Excel download; no official API found.
- **Social media:** out of scope.

## Consequences
Statement prices keep their statement date as `valid_at`, the import time as `recorded_at` and a link to the statement; their age is computed and shown, and stale prices are flagged in valuation and risk. No unrelated price is ever substituted.

Indian exchange-traded prices update only when statements are imported or entered manually; DQ marks them `stale` between statements. Acceptable at a monthly decision cadence; daily risk for Indian ETFs is weaker and the report must say so.
