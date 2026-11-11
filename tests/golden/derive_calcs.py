#!/usr/bin/env python3
"""Independent derivation of the Phase 4 calculation figures for the DEMO portfolio on 2026-11-10.

Like derive_expected.py this imports nothing from arthashreni. It reads the committed demo files
(demo/portfolio/*.csv, demo/market/*) and tests/golden/expected.json (the independently derived lots),
and re-derives with Fraction arithmetic: market values, unrealised P&L, income and costs, FX
attribution, allocation weights, HHI and top-N, and the XIRR of the portfolio and of every asset. XIRR
is solved here with plain float bisection on the NPV in a different parametrisation (rate, not ln(1+r)),
so it shares no code or method with `analytics.xirr`. Run: python tests/golden/derive_calcs.py
"""

import csv
import json
import sys
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALUATION = date(2026, 11, 10)
expected = json.loads((Path(__file__).with_name("expected.json")).read_text())


def dec(x: F, places: int) -> str:
    d = Decimal(x.numerator) / Decimal(x.denominator)
    return str(d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN))


def r4(x: F) -> str:
    return dec(x, 4)


def r10(x: F) -> str:
    return dec(x, 10)


# ---- inputs ---------------------------------------------------------------------------------------
assets = {
    (r["amfi_code"] and f"AMFI:{r['amfi_code']}") or f"TICKER:{r['exchange_mic']}:{r['ticker']}": r
    for r in csv.DictReader((ROOT / "demo/portfolio/assets.csv").open())
}
obs: dict[str, list[tuple[date, F]]] = {}
for r in csv.DictReader((ROOT / "demo/market/manual_prices.csv").open()):
    obs.setdefault(r["asset_ref"], []).append((date.fromisoformat(r["valid_at"]), F(r["close"])))
for line in (ROOT / "demo/market/NAVAll.txt").read_text().splitlines():
    if line.startswith("900001;"):
        f = line.split(";")
        obs.setdefault("AMFI:900001", []).append((date(2026, 11, 10), F(f[6])))  # 10-Nov-2026
for row in json.loads((ROOT / "demo/market/tiingo_DTECH.json").read_text()):
    obs.setdefault("TICKER:XNAS:DTECH", []).append(
        (date.fromisoformat(row["date"][:10]), F(str(row["close"])))
    )
fbil = [
    (date.fromisoformat(r["date"]), F(str(r["rate"])))
    for r in json.loads((ROOT / "demo/market/fbil_rates.json").read_text())
]


def last_on_or_before(series: list[tuple[date, F]], day: date) -> tuple[date, F]:
    return max((p for p in series if p[0] <= day), key=lambda p: p[0])


fx_date, fx_now = last_on_or_before(fbil, VALUATION)

# ---- positions from the independently derived lots --------------------------------------------------
pos: dict[str, dict[str, F]] = {}
for lot in expected["lots"]:
    q = F(lot["remaining_qty"])
    if q == 0:
        continue
    p = pos.setdefault(lot["asset"], {"units": F(0), "cost_inr": F(0)})
    p["units"] += q
    p["cost_inr"] += F(lot["remaining_cost_inr"])

holdings = {}
for ref, p in sorted(pos.items()):
    day, price = last_on_or_before(obs[ref], VALUATION)
    a = assets[ref]
    fx = fx_now if a["currency"] == "USD" else F(1)
    value_local = p["units"] * price
    value = value_local * fx
    holdings[ref] = dict(
        a=a,
        units=p["units"],
        cost=p["cost_inr"],
        price=price,
        price_date=day,
        value_local=value_local,
        value=value,
        fx=fx,
    )

total_value = sum((h["value"] for h in holdings.values()), F(0))
total_cost = sum((h["cost"] for h in holdings.values()), F(0))

# ---- ledger cash flows (re-derived from the CSV with the ADR-0022 rules) -----------------------------
flows: dict[str, list[tuple[date, F]]] = {}  # asset ref -> flows; "" holds flows with no asset
fees = taxes = div = reinvested = F(0)
for r in csv.DictReader((ROOT / "demo/portfolio/transactions.csv").open()):
    t, q, px = r["txn_type"], F(r["quantity"] or 0), F(r["price"] or 0)
    fx = F(r["fx_rate"]) if r["fx_rate"] else F(1)
    fee, tax = F(r["fees"]), F(r["taxes"])
    on = date.fromisoformat(r["trade_date"])
    cash = None
    if t in ("BUY", "SIP"):
        cash = -(q * px + fee) * fx
    elif t in ("SELL", "REDEMPTION"):
        cash = (q * px - fee) * fx
    elif t in ("DIVIDEND", "IPO_BLOCK", "IPO_REFUND"):
        cash = F(r["net_cash_orig"]) * fx if r["net_cash_orig"] else F(r["net_cash_inr"])
    if t == "DIVIDEND":
        div += cash  # type: ignore[operator]
    if t == "DIVIDEND_REINVESTMENT":
        reinvested += q * px * fx
    if t not in ("DEPOSIT", "FX_CONVERSION"):
        fees += fee * fx
        taxes += tax * fx
    if cash is not None:
        flows.setdefault(r["asset_ref"], []).append((on, F(dec(cash, 4))))  # ledger stores 4 dp

# a merger moves the old asset's history to the new one
flows.setdefault("TICKER:XNSE:DNEW", []).extend(flows.pop("TICKER:XNSE:DOLD", []))


def xirr(cfs: list[tuple[date, F]]) -> str:
    """Plain float bisection on rate (not ln(1+r)); returns a 12 dp string."""
    d0 = min(d for d, _ in cfs)

    def npv(rate: float) -> float:
        return sum(float(c) / (1 + rate) ** ((d - d0).days / 365) for d, c in cfs)

    lo, hi = -0.99, 1000.0
    f_lo = npv(lo)
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return f"{(lo + hi) / 2:.12f}"


all_flows = [c for ref, fl in flows.items() for c in fl]
end = [(VALUATION, total_value)]
paid_in = -sum((c for _, c in all_flows if c < 0), F(0))
gain = total_value + sum((c for _, c in all_flows), F(0))
asset_xirr = {}
for ref, fl in sorted(flows.items()):
    if ref and ref in assets:
        h = holdings.get(ref)
        tail = [(VALUATION, h["value"])] if h else []
        asset_xirr[ref] = (
            xirr(fl + tail) if any(c > 0 for _, c in fl + tail) and any(c < 0 for _, c in fl + tail) else None
        )

# ---- FX attribution of the one USD holding --------------------------------------------------------------
u = holdings["TICKER:XNAS:DTECH"]
cost_orig = F(110) * F("1.5") + F("0.4")  # lot 2 cost in USD ...
cost_orig = cost_orig * u["units"] / F("1.5")  # ... one unit of it remains
f0 = u["cost"] / cost_orig
local = u["value_local"] / cost_orig - 1
fxr = fx_now / f0 - 1
fx_att = dict(
    purchase_rate=r10(f0),
    local_return=r10(local),
    fx_return=r10(fxr),
    interaction=r10(local * fxr),
    inr_return=r10(u["value"] / u["cost"] - 1),
    local_effect_inr=r4((u["value_local"] - cost_orig) * f0),
    fx_effect_inr=r4(cost_orig * (fx_now - f0)),
    interaction_inr=r4((u["value_local"] - cost_orig) * (fx_now - f0)),
)


# ---- allocation ---------------------------------------------------------------------------------------
def weights(key: str) -> dict[str, str]:
    g: dict[str, F] = {}
    for h in holdings.values():
        g[h["a"][key] or "UNKNOWN"] = g.get(h["a"][key] or "UNKNOWN", F(0)) + h["value"]
    return {k: r10(v / total_value) for k, v in sorted(g.items())}


w = sorted((h["value"] / total_value for h in holdings.values()), reverse=True)
out = {
    "note": "Derived independently by tests/golden/derive_calcs.py (no arthashreni imports).",
    "valuation_date": str(VALUATION),
    "fx_rate_used": {"type": "FBIL_REF", "date": str(fx_date), "rate": dec(fx_now, 4)},
    "holdings": {
        ref: dict(
            units=dec(h["units"], 8).rstrip("0").rstrip("."),
            price=dec(h["price"], 4),
            price_date=str(h["price_date"]),
            value_inr=r4(h["value"]),
            cost_inr=r4(h["cost"]),
            unrealised_inr=r4(h["value"] - h["cost"]),
        )
        for ref, h in holdings.items()
    },
    "totals": dict(
        value_inr=r4(total_value),
        cost_inr=r4(total_cost),
        unrealised_inr=r4(total_value - total_cost),
        unrealised_pct=r10((total_value - total_cost) / total_cost),
    ),
    "realised_gain_inr": expected["realised"]["gain_inr"],
    "paid_in_inr": r4(paid_in),
    "gain_inr": r4(gain),
    "absolute_return": r10(gain / paid_in),
    "income_and_costs": dict(
        dividends_inr=r4(div), reinvested_dividends_inr=r4(reinvested), fees_inr=r4(fees), taxes_inr=r4(taxes)
    ),
    "xirr_portfolio": xirr(all_flows + end),
    "xirr_by_asset": asset_xirr,
    "fx_attribution_DTECH": fx_att,
    "allocation": {k: weights(k) for k in ("asset_class", "country", "currency", "sector")},
    "concentration": dict(top3=r10(sum(w[:3], F(0))), hhi=r10(sum((x * x for x in w), F(0)))),
}

target = Path(__file__).with_name("expected_calcs.json")
text = json.dumps(out, indent=2) + "\n"
if "--check" in sys.argv:
    if not target.exists() or target.read_text() != text:
        print("expected_calcs.json is out of date; run tests/golden/derive_calcs.py", file=sys.stderr)
        sys.exit(1)
    print("expected_calcs.json matches the independent derivation")
else:
    target.write_text(text)
    print(f"wrote {target}")
