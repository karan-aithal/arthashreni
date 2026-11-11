"""XIRR in exact Decimal arithmetic (F1, ADR-0024).

Money-weighted annual return `r` solving  sum(cf_i * (1 + r) ** -((d_i - d_0) / 365)) = 0  (Excel's
actual/365 convention). No float anywhere: the equation is solved in `x = ln(1 + r)` by bracketing and
bisection, so the answer is as exact as the 40-digit context allows.

It refuses to guess. It returns `missing` with the reason when
  * the flows have no sign change (nothing to solve), or fewer than two dates;
  * no root exists inside -99.9% .. +1,200,000% a year;
  * more than one rate solves the flows, or the only root has even multiplicity (the NPV touches zero
    without crossing, so the rate is not determined by the data).

What is *proved* and what is only *checked*:
  * Descartes (valid for real exponents): if the flows themselves change sign exactly once there is exactly
    one root, and it is simple, for every rate above -100%. Status `ok`.
  * Norstrom: if the running total changes sign exactly once (and the total is not zero) there is exactly one
    root for rates ABOVE 0%. It says nothing about negative rates: a second root below 0% can exist
    (counterexample in tests). So it narrows the search but does not make the answer `ok`.
  * Otherwise a 5% log-rate scan counts crossings, and every local extremum of the NPV between grid points is
    refined to find hidden crossing pairs and tangent (even-multiplicity) roots. Exactly one root found is
    returned `estimated`, with the reason; the scan cannot resolve two stationary points inside one grid step.
"""

import itertools
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, localcontext

from arthashreni.analytics import PRECISION
from arthashreni.dq.measured import Measured, QualityStatus, day_stamp, missing, worst

_DAYS = Decimal(365)
_X_LO, _X_HI = Decimal(-7), Decimal(14)  # r = -99.9% .. about +1,200,000%; both on the 0.25 and 0.05 grids
_COARSE, _FINE = Decimal("0.25"), Decimal("0.05")
_WIDTH = Decimal("1e-30")
_TOUCH = Decimal("1e-18")  # |NPV| / sum|PV| below this at an extremum means the NPV touches zero


def _g(x: Decimal, flows: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
    return sum((cf * (-x * t).exp() for t, cf in flows), Decimal(0))


def _sign_changes(values: Sequence[Decimal]) -> int:
    signs = [v > 0 for v in values if v != 0]
    return sum(1 for a, b in itertools.pairwise(signs) if a != b)


def _dg(x: Decimal, flows: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
    """Derivative of `_g` in x."""
    return sum((-t * cf * (-x * t).exp() for t, cf in flows), Decimal(0))


def _grid(flows: Sequence[tuple[Decimal, Decimal]], step: Decimal) -> list[tuple[Decimal, Decimal]]:
    out, x = [], _X_LO
    while x <= _X_HI:
        out.append((x, _g(x, flows)))
        x += step
    return out


def _crossings(grid: Sequence[tuple[Decimal, Decimal]]) -> list[tuple[Decimal, Decimal]]:
    """Brackets (x_lo, x_hi) where g changes sign. An exact zero on the grid counts as one root."""
    out: list[tuple[Decimal, Decimal]] = []
    for (x, prev), (nxt, cur) in itertools.pairwise(grid):
        if prev == 0:
            out.append((x, x))
        elif cur != 0 and (prev > 0) != (cur > 0):
            out.append((x, nxt))
    if grid[-1][1] == 0:
        out.append((grid[-1][0], grid[-1][0]))
    return out


def _hidden_roots(flows: Sequence[tuple[Decimal, Decimal]], grid: Sequence[tuple[Decimal, Decimal]]) -> int:
    """Roots the grid cannot see: 2 for a pair of crossings inside one step, 1 for a tangent root.

    At each strict local extremum of g on the grid (all three values the same sign) the true extremum is
    located by bisecting g' between the neighbours. If it has the opposite sign, g crosses zero twice there;
    if it is zero to within rounding, g touches zero without crossing (even multiplicity)."""
    found = 0
    for (x0, g0), (_, g1), (x2, g2) in zip(grid, grid[1:], grid[2:], strict=False):
        if g0 == 0 or g1 == 0 or g2 == 0 or not (g0 > 0) == (g1 > 0) == (g2 > 0):
            continue
        if not (g1 - g0) * (g2 - g1) < 0:
            continue
        lo, hi = x0, x2
        d_lo = _dg(lo, flows)
        if d_lo == 0 or (d_lo > 0) == (_dg(hi, flows) > 0):
            continue  # no single stationary point to refine here
        while hi - lo > _WIDTH:
            mid = (lo + hi) / 2
            if (_dg(mid, flows) > 0) == (d_lo > 0):
                lo = mid
            else:
                hi = mid
        gv = _g(lo, flows)
        scale = sum((abs(cf) * (-lo * t).exp() for t, cf in flows), Decimal(0))
        if abs(gv) <= scale * _TOUCH:
            found += 1
        elif (gv > 0) != (g1 > 0):
            found += 2
    return found


def _bisect(flows: Sequence[tuple[Decimal, Decimal]], lo: Decimal, hi: Decimal) -> Decimal:
    if lo == hi:
        return lo
    g_lo = _g(lo, flows)
    while hi - lo > _WIDTH:
        mid = (lo + hi) / 2
        g_mid = _g(mid, flows)
        if g_mid == 0:
            return mid
        if (g_mid > 0) == (g_lo > 0):
            lo, g_lo = mid, g_mid
        else:
            hi = mid
    return (lo + hi) / 2


def xirr(
    flows: Sequence[tuple[date, Decimal]], *, sources: tuple[str, ...] = ("ledger",)
) -> Measured[Decimal]:
    """Annual money-weighted return as a fraction (0.12 = 12%), or `missing` with the reason."""
    if not flows:
        return missing(note="XIRR needs cash flows")
    by_day: dict[date, Decimal] = {}
    for d, cf in flows:
        by_day[d] = by_day.get(d, Decimal(0)) + cf
    days = sorted(by_day)
    as_of = day_stamp(days[-1])

    def none(why: str) -> Measured[Decimal]:
        return Measured(None, QualityStatus.MISSING, as_of, sources, (f"XIRR undefined: {why}",))

    if len(days) < 2:
        return none("all cash flows fall on one date")
    amounts = [by_day[d] for d in days]
    if not (any(a > 0 for a in amounts) and any(a < 0 for a in amounts)):
        return none("cash flows do not change sign (nothing was both paid in and taken out)")

    with localcontext() as ctx:
        ctx.prec = PRECISION
        flows_t = [(Decimal((d - days[0]).days) / _DAYS, by_day[d]) for d in days]
        running, cumulative = Decimal(0), []
        for a in amounts:
            running += a
            cumulative.append(running)
        proved = _sign_changes(amounts) == 1  # Descartes: one simple root for every rate above -100%
        # Norstrom (one sign change in the running total, total not zero) proves uniqueness only above 0%.
        norstrom = running != 0 and _sign_changes(cumulative) == 1
        grid = _grid(flows_t, _COARSE if proved else _FINE)
        brackets = _crossings(grid)
        hidden = 0 if proved else _hidden_roots(flows_t, grid)
        status, notes = QualityStatus.OK, ["XIRR: money-weighted, actual/365"]
        if len(brackets) + hidden > 1:
            return none(f"{len(brackets) + hidden} different rates solve these cash flows (multiple roots)")
        if hidden:
            return none("the only root has even multiplicity: the NPV touches zero without crossing")
        if not brackets:
            return none("no root between -99.9% and +1,200,000% a year")
        if not proved:
            status = QualityStatus.ESTIMATED
            notes.append(
                "cash-flow pattern admits more than one root; only one was found "
                + (
                    "(unique above 0% by Norstrom; negative rates checked by scan only)"
                    if norstrom
                    else "(uniqueness not proved; checked by scan only)"
                )
            )
        rate = _bisect(flows_t, *brackets[0]).exp() - 1
    if (days[-1] - days[0]).days < 365:
        notes.append("period shorter than one year: annualised figure is an extrapolation")
    return Measured(rate, worst(status), as_of, sources, tuple(notes))
