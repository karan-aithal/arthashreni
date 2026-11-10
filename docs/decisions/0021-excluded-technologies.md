# ADR-0021: Excluded technologies and data scope

Status: Accepted, 2026-11-11 (investor instruction)

## Decision
Excluded, and enforced by the dependency deny-list in `make verify`:

| Excluded | Reason | Used instead |
| --- | --- | --- |
| cron as scheduler | no missed-run recovery or run history | APScheduler + `job_runs` catch-up (ADR-0019) |
| Streamlit | constitution specifies a Next.js UI from Phase 6 | Next.js (Phase 6) |
| PyMuPDF (`pymupdf`, `fitz`) | AGPL-3.0 licence | `casparser`, `pdfplumber` / `pdfminer.six` |
| Gmail API (`google-api-python-client`, `google-auth-oauthlib`) | needs a Google Cloud OAuth project; broader access than needed | opt-in read-only IMAP (G3) |
| Kite Connect (`kiteconnect`) and any broker API | needs a broker session (A3.3); paid market data | statements and manual entry |
| Paid data providers | zero-cost requirement | free sources in `docs/data-sources/feasibility.md` |

Also excluded: SME IPOs from the investment universe, and automated monitoring of exchange disclosures (NSE terms). The ₹2 lakh rule and the ₹1 lakh proposal stay in `docs/verify-register.md` as research notes only and do not change any behaviour.

## Consequences
Reversing any row needs a new ADR.
