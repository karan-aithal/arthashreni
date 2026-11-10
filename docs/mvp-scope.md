# MVP vs later

MVP = Phases 0–5 plus the read-only UI of Phase 6: a tested ledger, DQ layer, calculation engine, risk and policy engine, and template monthly report on DEMO data, then real data after the Phase 4 gate.

| Area | MVP (Phases 0–6) | Later |
| --- | --- | --- |
| Ledger | All C1 transaction types, corrections, FIFO lots, corporate actions | — |
| Imports | CAMS/KFintech CAS, NSDL/CDSL CAS (holdings), Zerodha Console CSV/XLSX, INDmoney statement (sample format), CSV templates, inbox watcher, preview, reconciliation | MF Central CAS parser; IMAP CAS fetch (opt-in, Phase 3+ if time) |
| Market data | AMFI NAV; SBI TT archive; FBIL manual; Tiingo + Alpha Vantage EOD; SEC companyfacts; statement prices for Indian ETFs | FRED/RBI macro feeds (Phase 5 risk uses them if available, else marked missing); fund-house and ETF holdings (Phase 7) |
| DQ | `Measured`, all E3 checks, quarantine queue, bitemporal `as_of`, source health, confidence | — |
| Calculations | All of F1; look-through marked `missing` until holdings files exist | Look-through populated in Phase 7 |
| Tax | Rules registry with only verified rules; otherwise "tax unavailable" | Full ITR schedules, Form 67 workflow |
| Risk/policy | B1–B6, F2, F3, stress scenarios, alerts, template report | SIP timing study (Phase 10), 5–10-year scenarios (Phase 10) |
| UI | Read-only pages (Phase 6) | Approval workflow, full dashboard (Phase 9) |
| Research | — | Fundamentals, overlap, IPO tracker, news (Phase 7) |
| AI | — | Local Ollama narrative layer (Phase 8) |
| Social media | Out of scope | Out of scope |
| Indian exchange prices | Statement/manual only (NSE terms) | Official licensed feed via ADR if ever needed |
