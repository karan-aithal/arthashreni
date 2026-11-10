# ADR-0016: FX rate types and where each is used

Status: Accepted, 2026-11-11

## Decision
| Use | Rate type | Missing-rate behaviour |
| --- | --- | --- |
| Tax computation (capital gains on foreign assets, Rule 115) | `SBI_TT_BUY` on the date the rule specifies | Output "tax unavailable" |
| Valuation, allocation, analytics, confidence weighting | `FBIL_REF` | Fall back to latest `SBI_TT_BUY` marked `estimated` |
| True cost of a conversion, cost-efficiency rule | `BROKER_ACTUAL` from statement | Use FBIL + configured markup, marked `estimated` |

Every function that converts currency takes `rate_type` as a required argument; there is no default.
Rule 115 applicability details (which date's rate per income type) are a \[VERIFY\] item for the tax phase.
