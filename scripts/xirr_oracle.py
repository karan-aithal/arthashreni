#!/usr/bin/env python3
"""XIRR oracle (G1.5): our Decimal XIRR vs `pyxirr` vs LibreOffice `XIRR` run headless, on shared flows.

All three must agree within 1e-6, or all three must report no solution. Cases with several roots are not
compared: refusing them is deliberate (`analytics.xirr`), while other tools return one root arbitrarily.
Exit 1 on any disagreement, 2 if LibreOffice is not installed (verify must not pass without its oracle).
"""

import csv
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import openpyxl
import pyxirr
from openpyxl.utils import get_column_letter

from arthashreni.analytics.xirr import xirr

TOLERANCE = 1e-6
D0 = date(2024, 1, 15)
Flows = list[tuple[date, Decimal]]


def sip(months: int, amount: int, final: int) -> Flows:
    flows = [(date(2024 + (m // 12), m % 12 + 1, 5), Decimal(-amount)) for m in range(months)]
    return [*flows, (date(2024 + (months // 12), months % 12 + 1, 20), Decimal(final))]


def f(*pairs: tuple[date, int]) -> Flows:
    return [(d, Decimal(a)) for d, a in pairs]


CASES: dict[str, Flows] = {
    "ten percent in a year": f((date(2025, 1, 1), -1000), (date(2026, 1, 1), 1100)),
    "two contributions": f((date(2025, 1, 1), -1000), (date(2025, 7, 1), -1000), (date(2026, 6, 30), 2300)),
    "twelve monthly SIPs with a gain": sip(12, 10_000, 135_000),
    "twenty-four monthly SIPs at a loss": sip(24, 5_000, 100_000),
    "loss of a third over two years": f((date(2023, 3, 1), -3000), (date(2025, 3, 1), 2000)),
    "spans a leap day": f((date(2024, 2, 1), -1000), (date(2024, 3, 1), 1010)),
    "dividends then sale": f(
        (D0, -5000),
        (D0 + timedelta(days=90), 60),
        (D0 + timedelta(days=270), 60),
        (D0 + timedelta(days=500), 6000),
    ),
    "same-day flows": f((D0, -600), (D0, -400), (D0 + timedelta(days=400), 1500)),
    "zero return": f((D0, -1000), (D0 + timedelta(days=200), 1000)),
    "buy, part sale, buy again, final value": f(
        (D0, -10_000),
        (D0 + timedelta(days=200), 4000),
        (D0 + timedelta(days=300), -3000),
        (D0 + timedelta(days=700), 11_500),
    ),
    "no solution: only outflows": f((D0, -1000), (D0 + timedelta(days=100), -5)),
    "no solution: only inflows": f((D0, 1000), (D0 + timedelta(days=100), 5)),
}


def libreoffice(cases: dict[str, Flows]) -> dict[str, float | None]:
    """Write every case into one workbook (two columns each), let LibreOffice compute, read it back."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        print("xirr oracle: LibreOffice (soffice) is not installed", file=sys.stderr)
        sys.exit(2)
    with tempfile.TemporaryDirectory() as tmp:
        wb = openpyxl.Workbook()
        ws = wb.active
        assert ws is not None
        for i, flows in enumerate(cases.values()):
            dc, vc = 2 * i + 1, 2 * i + 2
            for r, (d, a) in enumerate(flows, start=2):
                ws.cell(r, dc, d).number_format = "yyyy-mm-dd"
                ws.cell(r, vc, float(a))
            n = len(flows) + 1
            letters = (get_column_letter(dc), get_column_letter(vc))
            cell = ws.cell(1, vc, f"=XIRR({letters[1]}2:{letters[1]}{n},{letters[0]}2:{letters[0]}{n})")
            cell.number_format = "0.000000000000"
        src = Path(tmp) / "xirr.xlsx"
        wb.save(src)
        subprocess.run(
            [soffice, "--headless", "--norestore", f"-env:UserInstallation=file://{tmp}/profile",
             "--convert-to", "csv", "--outdir", tmp, str(src)],
            check=True, capture_output=True, timeout=180,
        )  # fmt: skip
        first = next(csv.reader((Path(tmp) / "xirr.csv").open()))
    out: dict[str, float | None] = {}
    for i, name in enumerate(cases):
        raw = first[2 * i + 1].strip()
        try:
            out[name] = float(raw)
        except ValueError:  # Err:502 / #NUM! : LibreOffice reports no solution
            out[name] = None
    return out


def pyxirr_value(flows: Flows) -> float | None:
    try:
        return pyxirr.xirr([d for d, _ in flows], [float(a) for _, a in flows])
    except pyxirr.InvalidPaymentsError:
        return None


def main() -> int:
    lo = libreoffice(CASES)
    bad = 0
    for name, flows in CASES.items():
        mine = xirr(flows).value
        values = {
            "ours": None if mine is None else float(mine),
            "pyxirr": pyxirr_value(flows),
            "libreoffice": lo[name],
        }
        found = [v for v in values.values() if v is not None]
        agree = (not found) or (len(found) == 3 and max(found) - min(found) <= TOLERANCE)
        bad += not agree
        shown = ", ".join(f"{k}={'none' if v is None else f'{v:.10f}'}" for k, v in values.items())
        print(f"{'ok  ' if agree else 'FAIL'} {name}: {shown}")
    print(f"xirr oracle: {len(CASES)} cases, {bad} disagreement(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
