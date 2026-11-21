# ADR-0026: Risk engine conventions and provisional thresholds (Phase 5)

Status: Accepted, 2026-11-12. Fills gaps in F2; changes no Part A invariant. Thresholds are hypotheses (V31).

## Decisions

**Thresholds live in `config/risk_thresholds.toml`.** Every row has its unit, direction, three levels, a rationale and a `backtested` flag; a test fails if a row lacks a rationale, is unordered, or claims to be backtested. **All are provisional and none is backtested:** there is no investor history, and months of synthetic DEMO data cannot test a drawdown rule. They are conventional vocabulary (10% correction, 20% bear market, 30% crash for indices; wider bands for single holdings; six months of expenses as an emergency fund), not predictions. No threshold is presented as a crash predictor and no level triggers a trade (invariant 12). Recalibrate once real history is imported (V31).

**Signals** are `Measured` values with a level, or no level when the input is missing or quarantined: missing is never "normal". Market-wide: index drawdown and volatility for the four documented benchmarks (index levels must be imported, V30), USD/INR 30-day move (FBIL reference). Rates, inflation and oil are signals with no feed (FRED, RBI DBIE, MOSPI adapters are not built): always `missing`, named in the report. Breadth is not built. Portfolio: top-3 weight, foreign-currency share, IPS band breaches (class, country, currency), rise in average pairwise correlation (60-day against 365-day), emergency-fund months. Per holding: drawdown from its running peak. Overlap is `missing` until fund-house holdings arrive (Phase 7).

**Four levels from counts.** Severe Stress: two or more signals at Severe. High Risk: one at Severe or two at High. Caution: one at High or two at Caution. Normal otherwise. Missing signals do not count and are listed with "rests on X of Y signals". Band-breach signals count once (the worst), because an overweight class is also an overweight country and currency; all are still stored and shown.

**Market vs asset-specific.** For a holding at Caution or worse, compare its fall with its benchmark's over the same dates: the ratio of benchmark fall to asset fall at or above 0.6 is market-wide, at or below 0.3 (or the benchmark rose) is asset-specific, in between or without a usable benchmark (correlation below 0.5, no series, no fall) is uncertain. Fundamentals and filings are not available before Phase 7, so alerts label market-wide declines "temporary" and everything else "uncertain"; "structural" needs evidence the system does not have yet.

**Veto.** Only a severe, asset-specific decline vetoes new money for an asset (F3.3). It never sells or cancels anything.

**Scenarios** are labelled "scenario, not a prediction": equity -10/-20/-30%, the largest held sector -30%, USD/INR +/-10% (foreign holdings only), joint India + US stress (equity -20% with INR 5% weaker, so US holdings move by 0.8 x 1.05 - 1), gold and silver -15%. Output is INR per holding and total, `estimated` when a holding has no usable value. Mutual funds are treated as equity-oriented because no scheme category is stored (V32).

**Alerts** carry what happened, when, assets, evidence ids, sources, portfolio impact (asset-level: value times the fall), classification, scenario ids, possible responses (from the allowed set), the consequence of doing nothing and what to watch. The key includes the level, so a condition that persists is silent, an escalation is a new alert (and closes the old key), and a cleared condition closes. Only High Risk and above raise alerts by default (`alert_min_level`); Caution appears in the daily summary. The daily risk run happens after the ingest job and never fails it; a finished job is never an alert. There is no notification channel yet (the desktop notification is later work): alerts are stored and shown in the CLI, API and report.

## Consequences
The level is explainable (which signals, how many available). On DEMO data it rests on about a third of its signals, and the report says so.
