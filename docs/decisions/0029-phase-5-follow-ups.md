# ADR-0029: Phase 5 follow-ups (notifications, holidays, fund category and theme, wider mutation testing)

Status: Accepted, 2026-11-22 (investor chose these four items from the Phase 5 limitations list; defaults are reversible). Changes no Part A invariant.

## Decisions
1. **Desktop notifications** (`notify.py`, setting `ARTHA_NOTIFY=desktop|off`, default `desktop`). Only new, de-duplicated risk alerts notify, highest level first, at most three per run plus one summary line; a finished job never does (G3). Transport is `notify-send` on this machine, called with a fixed argument list and `--` before the text. If it is missing or fails, the alert is still stored and shown (invariant 13). **Email and Telegram are not built**: they send portfolio-derived text off the machine and need your explicit, logged consent (invariant 10). Tests never raise real notifications (`ARTHA_NOTIFY=off` in `conftest`).
2. **Holiday-aware monthly job.** The monthly review runs from the first BSE trading day (`dq.calendars.first_session_of_month`, `exchange_calendars`), the cron window is days 1 to 7, and the per-month claim keeps it to one run. The calendar's own bound or a missing calendar falls back to weekdays.
3. **Fund category and theme** (migration 0005). `assets.fund_category` (EQUITY, HYBRID, DEBT, mutual funds only, CHECK-constrained) and `assets.theme`; the app roles may update only these two columns (like `cik`). `core/classify.py` is the one definition of "equity-oriented" and "risk money": a DEBT fund is neither; unknown and HYBRID count as equity. The IPS `max_theme` limit is enforced for assets that have a theme. The system never guesses a category or a theme.
4. **Mutation testing widened.** `risk` (not `risk/store.py`), `reports` and `policy/store.py` are now mutated; `scripts/mutation_gate.py` checks `risk` and `reports` separately and `policy` includes the store. Workers inside `mutants/` get their own throwaway database per process; `.env` is copied into the git-ignored `mutants/`. DB-backed tests are in the selection, so a full run takes about 25 minutes (not in `--fast`).

## Result (2026-11-22, `mutmut run`)
survivors: analytics 1.1%, ledger 1.2%, policy 1.8%, risk 0.1%, reports 0.0% (limit 10%). Remaining survivors in `policy/store.py` are in `ipo_settle` and `change_sip`; recorded, not hidden.

## Consequences
A debt fund can now receive new money while income is unstable and is not shocked in equity scenarios, but only after the investor sets its category. Until then behaviour is unchanged. Closes V32. Not done: backtesting thresholds (V31), tax rules (C7), email/Telegram.
