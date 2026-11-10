# Security and privacy model

Status: Phase 0 design, 2026-11-11.

## Assets to protect
Ledger and holdings, statements (CAS PDFs contain PAN, name, email, folio numbers), personal financial context, IPS, decisions, API keys for data sources, backup private key.

## Threats considered
| Threat | Control |
| --- | --- |
| Data leaves the machine | No telemetry. Outbound HTTP only through one `core.http` client with an allow-list of hosts from the source registry; any other host raises. Every outbound request is logged (host, path, time, bytes), never with personal data. |
| LAN or browser access to the API | Bind `127.0.0.1`; Argon2id-hashed local password; `HttpOnly`/`SameSite=Strict` session; CSRF header token (ADR-0012). |
| Broker account compromise via this tool | No broker credentials, sessions or OTPs anywhere; no authenticated scraping; no execution code (ADR-0011). |
| CAS password exposure | Entered per import, held in memory only for the parse call, never logged, never written. |
| Secrets in git | `.env` git-ignored; `.env.example` has placeholders only; `make verify` runs a secret scan (`detect-secrets` baseline) and fails on new findings. |
| Personal identifiers in DB and logs | Folio/account numbers stored masked (last 4); PAN not stored; optional redaction of names/emails at import. Structured logs pass through a redaction filter (PAN pattern, email, 10+ digit numbers). |
| Stolen disk / backup | Backups age-encrypted to a public key; private key off-machine (ADR-0014). Full-disk encryption on the host is recommended to the investor (manual). |
| Malicious statement or document | File type/size checks before parsing; parsers run on bytes, no macros, no shell-out with file content; text from documents is data, never instructions (A3.11). |
| Prompt injection (Phase 8) | Retrieved text wrapped as untrusted; LLM output cannot trigger actions; numeric and citation verification; injection fixtures in tests. |
| Supply chain | Locked dependencies (`uv.lock`), licence check, `pip-audit` in `make verify`. |
| DEMO/REAL mix | Schema + role separation (ADR-0004). |

## External data sent, with consent
| Data | Destination | Why | Consent |
| --- | --- | --- | --- |
| Contact email in User-Agent | SEC EDGAR | SEC requires a declared User-Agent | Investor sets `SEC_USER_AGENT` in `.env`; adapter disabled until set |
| API keys | Tiingo, Alpha Vantage, FRED | Authentication | Investor creates and enters keys |
| Ticker symbols requested | Tiingo/Alpha Vantage | Price lookup | Implied by enabling the adapter; logged |
| IMAP login (app password) | Investor's mail server | Optional CAS email fetch (Phase 3+) | Opt-in; read-only; dedicated folder; app password in OS keyring, not in DB |

No portfolio values, quantities or personal context are ever sent anywhere.

## Out of scope
Multi-user, remote access, hardened deployment on a server.
