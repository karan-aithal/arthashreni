#!/usr/bin/env python3
"""Write the synthetic DEMO market-data fixtures into demo/market/ (deterministic; no live data).

Formats copy the real sources (AMFI NAVAll.txt, SBI archive CSV, Frankfurter FBIL JSON, Tiingo JSON,
SEC companyfacts, manual CSV), but every number is made up. Fake scheme code 900001, fake CIK 0009999999.
"""

import csv
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "demo" / "market"
START, END = date(2026, 9, 1), date(2026, 11, 10)


def weekdays(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5]


def walk(n: int, start: Decimal, step: Decimal, seed: int) -> list[Decimal]:
    out, x = [], start
    for i in range(n):
        x += step * (((i * 7 + seed) % 5) - 2)  # small deterministic wiggle, no randomness
        out.append(x.quantize(Decimal("0.0001")))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    days = weekdays(START, END)
    prev, last = days[:-1], days[-1]  # manual history up to the day before the AMFI fixture

    nav = walk(len(prev), Decimal("20.0000"), Decimal("0.0040"), 1)
    etf = walk(len(prev), Decimal("250.0000"), Decimal("0.4000"), 2)
    stock = walk(len(prev), Decimal("500.0000"), Decimal("1.2000"), 3)
    with (OUT / "manual_prices.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["asset_ref", "valid_at", "close", "currency", "price_type"])
        for d, a, b, c in zip(prev, nav, etf, stock, strict=True):
            w.writerow(["AMFI:900001", d, a, "INR", "NAV"])
            w.writerow(["TICKER:XNSE:DEMOETF", d, b, "INR", "CLOSE"])
            w.writerow(["TICKER:XNSE:DSTK", d, c, "INR", "CLOSE"])

    (OUT / "NAVAll.txt").write_text(
        "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
        "Scheme Name;Plan;Option;Net Asset Value;Date\r\n"
        " \r\nOpen Ended Schemes(Equity Scheme - Large Cap Fund)\r\n \r\nDemo Mutual Fund\r\n \r\n"
        "900001;ZZDEMOMF0001;-;Demo Growth Equity Fund;Direct Plan;Growth Option;"
        f"{nav[-1] + Decimal('0.0040')};{last:%d-%b-%Y}\r\n"
        f"900002;ZZDEMOMF0002;-;Demo Other Fund;Direct Plan;Growth Option;11.1111;{last:%d-%b-%Y}\r\n",
        newline="",
    )

    fx_days = weekdays(date(2026, 10, 12), END)
    sbi = walk(len(fx_days), Decimal("83.0000"), Decimal("0.0300"), 4)
    fbil = [(s * Decimal("1.0120")).quantize(Decimal("0.0001")) for s in sbi]
    with (OUT / "SBI_REFERENCE_RATES_USD.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["DATE", "PDF FILE", "TT BUY", "TT SELL", "BILL BUY", "BILL SELL", "FOREX TRAVEL CARD BUY",
                    "FOREX TRAVEL CARD SELL", "CN BUY", "CN SELL"])  # fmt: skip
        for d, r in zip(fx_days, sbi, strict=True):
            w.writerow([f"{d} 09:15", "demo", r, r + 1, r, r, r, r, r, r])
    (OUT / "fbil_rates.json").write_text(
        json.dumps(
            [
                {"date": str(d), "base": "USD", "quote": "INR", "rate": float(r)}
                for d, r in zip(fx_days, fbil, strict=True)
            ]
        )
    )

    us_days = weekdays(date(2026, 10, 5), END - timedelta(days=1))
    us = walk(len(us_days), Decimal("180.0000"), Decimal("0.6000"), 5)
    rows = [
        {"date": f"{d}T00:00:00.000Z", "close": float(c), "high": float(c), "low": float(c), "open": float(c),
         "volume": 1000, "adjClose": float(c), "divCash": 0.0, "splitFactor": 1.0}
        for d, c in zip(us_days, us, strict=True)
    ]  # fmt: skip
    (OUT / "tiingo_DTECH.json").write_text(json.dumps(rows))

    def fact(end: str, val: int, fp: str, fy: int, filed: str, accn: str) -> dict[str, object]:
        return {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": "10-Q", "filed": filed}

    facts = {
        "cik": 9999999,
        "entityName": "Demo Tech Inc (synthetic)",
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [
                    fact("2026-03-31", 1_000_000, "Q1", 2026, "2026-05-02", "0009999999-26-000001"),
                    fact("2026-06-30", 1_100_000, "Q2", 2026, "2026-08-01", "0009999999-26-000002"),
                ]}},
                "NetIncomeLoss": {"units": {"USD": [
                    fact("2026-06-30", 150_000, "Q2", 2026, "2026-08-01", "0009999999-26-000002"),
                ]}},
            }
        },
    }  # fmt: skip
    (OUT / "sec_CIK0009999999.json").write_text(json.dumps(facts))


if __name__ == "__main__":
    main()
