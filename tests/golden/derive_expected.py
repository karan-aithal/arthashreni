#!/usr/bin/env python3
"""Independent derivation of the golden expected values for demo/portfolio/.

This script does NOT import arthashreni. It re-derives every figure from the scenario in
demo/portfolio/transactions.csv with plain Fraction/date arithmetic, scenario by scenario, so the
expected values never come from the code under test. Run: python tests/golden/derive_expected.py
and commit expected.json.

Conventions: costs are quantity x price + fees (INR = original x FX); a disposal's proceeds are
(quantity x price - fees) x FX, split across consumed lots in proportion to quantity with the last
lot taking the remainder; cost of a part-consumed lot is pro rata; amounts round half-even to 4 dp.
"""

import json
import sys
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction as F
from pathlib import Path


def r4(x: F) -> str:
    d = Decimal(x.numerator) / Decimal(x.denominator)
    return str(d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN))


def q(x: F | int) -> str:
    """Quantity as a normalised string."""
    f = F(x)
    return (
        str(Decimal(f.numerator) / Decimal(f.denominator)).rstrip("0").rstrip(".")
        if f.denominator != 1
        else str(f.numerator)
    )


def days(a: date, b: date) -> int:
    return (b - a).days


lots: list[dict[str, object]] = []
cons: list[dict[str, object]] = []


def lot(
    acc: str, asset: str, on: date, orig: F, remaining: F, cost: F, rem_cost: F, bonus: bool = False
) -> None:
    lots.append(dict(account=acc, asset=asset, acquired_on=on.isoformat(), original_qty=q(orig),
                     remaining_qty=q(remaining), cost_inr=r4(cost), remaining_cost_inr=r4(rem_cost), is_bonus=bonus))  # fmt: skip


def con(acc: str, asset: str, sold: date, acquired: date, qty: F, cost: F, proceeds: F) -> None:
    cons.append(dict(account=acc, asset=asset, disposal_date=sold.isoformat(), acquired_on=acquired.isoformat(),
                     qty=q(qty), cost_inr_consumed=r4(cost), proceeds_inr=r4(proceeds), holding_days=days(acquired, sold)))  # fmt: skip


# ---- COIN-DEMO: mutual fund, SIPs, partial redemption, reinvested dividend ------------------------
A, MF = "COIN-DEMO", "AMFI:900001"
c1, c2, c3, c4 = F(10) * F(50), F("9.804") * 51, F("10.5") * F("47.619"), F("0.2") * 62
d1, d2, d3, d4 = date(2025, 1, 6), date(2025, 2, 5), date(2025, 4, 7), date(2025, 7, 1)
red_day, red_qty, red_px = date(2025, 6, 10), F(15), F(60)
red_total = red_qty * red_px  # 900, no fees
take1, take2 = F(10), F(5)  # lot1 fully, then 5 of lot2 (FIFO)
cost2 = c2 * take2 / F("9.804")  # pro rata
p1 = red_total * take1 / red_qty  # 600
p2 = red_total - p1  # last lot takes the remainder: 300
con(A, MF, red_day, d1, take1, c1, p1)
con(A, MF, red_day, d2, take2, cost2, p2)
lot(A, MF, d1, F(10), F(0), c1, F(0))
lot(A, MF, d2, F("9.804"), F("9.804") - take2, c2, c2 - cost2)
lot(A, MF, d3, F("10.5"), F("10.5"), c3, c3)
lot(A, MF, d4, F("0.2"), F("0.2"), c4, c4)
cash_coin = -c1 - c2 - c3 + red_total  # reinvested dividend moves no external cash
units_coin = (F("9.804") - take2) + F("10.5") + F("0.2")

# ---- KITE-DEMO: ETF ---------------------------------------------------------------------------
K, ETF = "KITE-DEMO", "TICKER:XNSE:DEMOETF"
e1, e2 = F(20) * 100 + 5, F(10) * 110 + 3
lot(K, ETF, date(2025, 1, 15), F(20), F(20), e1, e1)
lot(K, ETF, date(2025, 3, 10), F(10), F(10), e2, e2)

# ---- KITE-DEMO: stock with 1:5 split, 1:1 bonus, two sales, full exit ---------------------------
DS = "TICKER:XNSE:DSTK"
sA, sB = F(10) * 1000 + 20, F(5) * 1200 + 10  # 10020, 6010
dA, dB, dC = date(2024, 6, 3), date(2024, 11, 18), date(2025, 5, 20)
# split 1 -> 5 on 2025-02-14: lot A 50, lot B 25 (cost unchanged); bonus 1:1 on 75 held -> 75 free units
sale1_day, sale1_qty, sale1_gross, sale1_fee = date(2025, 8, 12), F(60), F(60) * 250, F(15)
sale1_net = sale1_gross - sale1_fee  # 14985
costB_part = sB * F(10) / F(25)  # 10 of lot B's 25 units
p_a = sale1_net * F(50) / sale1_qty
p_b = sale1_net - p_a
con(K, DS, sale1_day, dA, F(50), sA, p_a)
con(K, DS, sale1_day, dB, F(10), costB_part, p_b)
sale2_day, sale2_qty, sale2_net = date(2025, 10, 1), F(90), F(90) * 300 - 20  # 26980
p_b2 = sale2_net * F(15) / sale2_qty
p_c2 = sale2_net - p_b2
con(K, DS, sale2_day, dB, F(15), sB - costB_part, p_b2)
con(K, DS, sale2_day, dC, F(75), F(0), p_c2)
lot(K, DS, dA, F(10), F(0), sA, F(0))
lot(K, DS, dB, F(5), F(0), sB, F(0))
lot(K, DS, dC, F(75), F(0), F(0), F(0), bonus=True)

# ---- KITE-DEMO: merger 1 old -> 2 new, then a part sale ------------------------------------------
DN = "TICKER:XNSE:DNEW"
m1 = F(10) * 500
m_day, m_qty, m_net = date(2025, 11, 3), F(5), F(5) * 300
m_cost = m1 * m_qty / F(20)  # 20 new units carry the 5,000 cost
con(K, DN, m_day, date(2024, 9, 2), m_qty, m_cost, m_net)
lot(K, DN, date(2024, 9, 2), F(10), F(15), m1, m1 - m_cost)

# ---- KITE-DEMO: IPO, 100 applied, 40 allotted, refund of 9,000 ---------------------------------
ipo_cost = F(40) * 150
lot(K, "TICKER:XNSE:DIPO", date(2025, 7, 14), F(40), F(40), ipo_cost, ipo_cost)
cash_kite = (-e1 - e2 - sA - sB + sale1_net + sale2_net - m1 + m_net) + (-15000 + 9000)
# application and allotment rows carry 0 cash (the 15,000 block pays for the allotment)

# ---- INDMONEY-DEMO: US stock, FX at the broker rate ---------------------------------------------
IND, DT = "INDMONEY-DEMO", "TICKER:XNAS:DTECH"
u1, u2 = (F(2) * 100 + F("0.5")) * F("83.2"), (F("1.5") * 110 + F("0.4")) * 84
us_day, us_net_orig = date(2025, 9, 15), F(25, 10) * 130 - F("0.6")
us_net = us_net_orig * 86
us_p1 = us_net * F(2) / F("2.5")
us_p2 = us_net - us_p1
us_cost2 = u2 * F("0.5") / F("1.5")
con(IND, DT, us_day, date(2025, 3, 5), F(2), u1, us_p1)
con(IND, DT, us_day, date(2025, 4, 10), F("0.5"), us_cost2, us_p2)
lot(IND, DT, date(2025, 3, 5), F(2), F(0), u1, F(0))
lot(IND, DT, date(2025, 4, 10), F("1.5"), F(1), u2, u2 - us_cost2)
dividend_inr = F("2.98") * 85
cash_ind = 42000 - u1 - u2 + dividend_inr + us_net  # FX conversion is internal: 0 external INR

lots.sort(key=lambda x: (str(x["account"]), str(x["asset"]), str(x["acquired_on"])))
cons.sort(key=lambda x: (str(x["disposal_date"]), str(x["account"]), str(x["acquired_on"])))
real_cost = sum((F(c["cost_inr_consumed"]) for c in cons), F(0))  # type: ignore[arg-type]
real_proc = sum((F(c["proceeds_inr"]) for c in cons), F(0))  # type: ignore[arg-type]

expected = {
    "note": "Derived independently by tests/golden/derive_expected.py (no arthashreni imports).",
    "row_counts": {"transactions": 23, "corporate_actions": 3, "statement_holdings": 7},
    "holdings": {
        f"{A}|{MF}": q(units_coin),
        f"{K}|{ETF}": "30",
        f"{K}|{DN}": "15",
        f"{K}|TICKER:XNSE:DIPO": "40",
        f"{IND}|{DT}": "1",
    },
    "lots": lots,
    "consumptions": cons,
    "realised": {
        "cost_inr": r4(real_cost),
        "proceeds_inr": r4(real_proc),
        "gain_inr": r4(real_proc - real_cost),
    },
    "remaining_cost_inr": r4(sum((F(x["remaining_cost_inr"]) for x in lots), F(0))),  # type: ignore[arg-type]
    "net_cash_inr_by_account": {A: r4(cash_coin), K: r4(cash_kite), IND: r4(cash_ind)},
}
out = Path(__file__).with_name("expected.json")
text = json.dumps(expected, indent=2) + "\n"
if "--check" in sys.argv:  # make verify: the committed file must be exactly what this script derives
    if not out.exists() or out.read_text() != text:
        print("expected.json is out of date; run tests/golden/derive_expected.py", file=sys.stderr)
        sys.exit(1)
    print("expected.json matches the independent derivation")
else:
    out.write_text(text)
    print(f"wrote {out}")
