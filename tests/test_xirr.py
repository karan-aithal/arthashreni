"""XIRR: known answers, refusal cases, and agreement with pyxirr on random flows (G1.5)."""

import itertools
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pyxirr
from hypothesis import assume, given
from hypothesis import strategies as st

from arthashreni.analytics.xirr import xirr
from arthashreni.dq.measured import QualityStatus

D = Decimal
D0 = date(2025, 1, 1)


def test_one_year_ten_percent() -> None:
    m = xirr([(D0, D(-1000)), (date(2026, 1, 1), D(1100))])
    assert m.value is not None and abs(m.value - D("0.1")) < D("1e-15")
    assert m.status is QualityStatus.OK
    assert m.notes[0].startswith("XIRR: money-weighted")


def test_same_day_flows_are_netted_and_order_does_not_matter() -> None:
    a = xirr([(D0, D(-600)), (D0, D(-400)), (date(2026, 1, 1), D(1100))])
    b = xirr([(date(2026, 1, 1), D(1100)), (D0, D(-1000))])
    assert a.value == b.value and a.value is not None


def test_two_contributions() -> None:
    m = xirr([(D0, D(-1000)), (date(2025, 7, 1), D(-1000)), (date(2026, 6, 30), D(2300))])
    assert m.value is not None
    assert m.value == pytest.approx(D("0.11843549796"), abs=D("1e-10"))


def test_short_period_is_flagged_as_an_extrapolation() -> None:
    m = xirr([(D0, D(-1000)), (D0 + timedelta(days=30), D(1010))])
    assert m.value is not None and any("shorter than one year" in n for n in m.notes)


@pytest.mark.parametrize(
    ("flows", "reason"),
    [
        ([], "needs cash flows"),
        ([(D0, D(-1000))], "one date"),
        ([(D0, D(-1000)), (D0, D(1100))], "one date"),
        ([(D0, D(-1000)), (date(2026, 1, 1), D(-5))], "do not change sign"),
        ([(D0, D(1000)), (date(2026, 1, 1), D(5))], "do not change sign"),
        ([(D0, D(-1000)), (date(2026, 1, 1), D(0))], "do not change sign"),
        ([(D0, D(-1000)), (D0 + timedelta(days=9), D(10))], "no root"),  # loses more than 99.9% a year
    ],
)
def test_no_solution_is_missing_with_a_reason(flows: list[tuple[date, Decimal]], reason: str) -> None:
    m = xirr(flows)
    assert m.value is None and m.status is QualityStatus.MISSING
    assert any(reason in n for n in m.notes)


def test_multiple_roots_are_refused_not_guessed() -> None:
    # -100, +230, -132 a year apart (2021 is not a leap year, 2022-23 neither): roots near 10% and 20%
    flows = [(date(2021, 1, 1), D(-100)), (date(2022, 1, 1), D(230)), (date(2023, 1, 1), D(-132))]
    m = xirr(flows)
    assert m.value is None and m.status is QualityStatus.MISSING
    assert any("multiple roots" in n for n in m.notes)


def test_pattern_with_several_sign_changes_is_estimated_and_the_root_checks_out() -> None:
    flows = [
        (D0, D(-100)),
        (date(2025, 4, 1), D(150)),
        (date(2025, 7, 1), D(-100)),
        (date(2026, 1, 1), D(60)),
    ]
    m = xirr(flows)
    assert m.value is not None and m.status is QualityStatus.ESTIMATED
    assert any("more than one root" in n for n in m.notes)
    npv = sum(cf / (1 + m.value) ** (D((d - D0).days) / 365) for d, cf in flows)
    assert abs(npv) < D("1e-12")


def test_a_single_sign_change_in_the_flows_proves_a_unique_root() -> None:
    # running total stays at 0 after the second flow (no sign change there), but the flows change sign once
    m = xirr([(D0, D(-100)), (date(2025, 7, 1), D(100))])
    assert m.status is QualityStatus.OK and m.value is not None and abs(m.value) < D("1e-15")


def test_exact_zero_on_the_search_grid_is_found() -> None:
    # x = -6.9 + k * 0.25 hits r = 0 only if the grid passes through it; 0% return is the common case
    m = xirr([(D0, D(-1000)), (date(2026, 1, 1), D(1000))])
    assert m.value is not None and abs(m.value) < D("1e-15")


def test_total_loss_edge_returns_large_negative() -> None:
    m = xirr([(D0, D(-1000)), (date(2026, 1, 1), D(2))])  # -99.8%
    assert m.value is not None and m.value == pytest.approx(D("-0.998"), abs=D("1e-9"))


@pytest.mark.property
@given(
    outflows=st.lists(st.tuples(st.integers(0, 700), st.integers(100, 100_000)), min_size=1, max_size=6),
    gain_pct=st.integers(-60, 300),
    tail_days=st.integers(60, 1500),
)
def test_agrees_with_pyxirr_when_the_root_is_unique(
    outflows: list[tuple[int, int]], gain_pct: int, tail_days: int
) -> None:
    """Money out first, one lump back at the end: the cumulative flows change sign once, so the root is
    unique (Norstrom) and pyxirr must find the same one."""
    flows = [(D0 + timedelta(days=d), D(-a)) for d, a in outflows]
    paid = sum(a for _, a in outflows)
    end = D0 + timedelta(days=max(d for d, _ in outflows) + tail_days)
    back = D(paid) * (100 + gain_pct) / 100
    flows.append((end, back))
    mine = xirr(flows)
    assert mine.value is not None
    theirs = pyxirr.xirr([d for d, _ in flows], [float(a) for _, a in flows])
    assume(theirs is not None)
    assert abs(float(mine.value) - theirs) < 1e-6
    assert mine.status is QualityStatus.OK


# ---- multiple roots must never look like a single valid root ---------------------------------------------


def yrs(years: list[int], amounts: list[str | int]) -> list[tuple[date, Decimal]]:
    return [(D0 + timedelta(days=365 * y), D(a)) for y, a in zip(years, amounts, strict=True)]


def brute_roots(
    flows: list[tuple[date, Decimal]], lo: int = -7, hi: int = 14, steps_per_unit: int = 100
) -> list[float]:
    """Independent root finder for the tests: float NPV on a 0.01 log-rate grid, returns crossing rates."""
    import math

    d0 = min(d for d, _ in flows)
    pts = [((d - d0).days / 365, float(a)) for d, a in flows]

    def g(x: float) -> float:
        return sum(a * math.exp(-x * t) for t, a in pts)

    xs = [lo + i / steps_per_unit for i in range((hi - lo) * steps_per_unit + 1)]
    vals = [g(x) for x in xs]
    exact = [
        math.exp(xs[i]) - 1 for i in range(len(xs)) if vals[i] == 0
    ]  # e.g. 0% when the flows sum to zero
    crossing = [
        math.exp(xs[i]) - 1
        for i in range(len(xs) - 1)
        if vals[i] != 0 and vals[i + 1] != 0 and (vals[i] > 0) != (vals[i + 1] > 0)
    ]
    return sorted(exact + crossing)


def test_norstrom_counterexample_two_roots_one_negative() -> None:
    """Running total changes sign once (-8, -9, +19, +6), yet NPV is zero at about -52.8% and +16.2%. Norstrom's
    criterion only covers rates above 0%, so it must not be used to declare the answer unique."""
    flows = yrs([0, 3, 4, 5], [-8, -1, 28, -13])
    roots = brute_roots(flows)
    assert len(roots) == 2 and roots[0] < -0.4 and 0.1 < roots[1] < 0.2
    m = xirr(flows)
    assert m.value is None and m.status is QualityStatus.MISSING
    assert m.notes == ("XIRR undefined: 2 different rates solve these cash flows (multiple roots)",)


def test_another_norstrom_counterexample_where_the_positive_root_alone_would_look_fine() -> None:
    flows = yrs([0, 1, 4, 5], [13, -15, -20, 17])  # roots near -25.9% and +35.0%
    assert len(brute_roots(flows)) == 2
    assert xirr(flows).value is None


def test_even_multiplicity_root_is_refused() -> None:
    """NPV = (1 - 1.1 / (1 + r)) ** 2 >= 0: it touches zero at exactly 10% and never crosses. A sign-change
    search finds nothing here and a tangent root cannot be told apart from rounding, so the answer is refused."""
    flows = yrs([0, 1, 2], ["1", "-2.2", "1.21"])
    assert brute_roots(flows) == []  # no crossing for a sign-change scan to find
    m = xirr(flows)
    assert m.value is None and m.status is QualityStatus.MISSING
    assert m.notes == (
        "XIRR undefined: the only root has even multiplicity: the NPV touches zero without crossing",
    )
    negated = xirr(yrs([0, 1, 2], ["-1", "2.2", "-1.21"]))  # same root, NPV <= 0
    assert negated.value is None and negated.notes == m.notes


def test_a_tangent_root_next_to_a_crossing_root_is_still_multiple_roots() -> None:
    """(v - 10/11)^2 (v - 2/3) scaled to integers: a double root at 10% and a simple root at 50%. A scan that
    only counts sign changes sees one root and would return 50% as if it were the answer."""
    flows = yrs([0, 1, 2, 3], [-200, 740, -902, 363])
    assert len(brute_roots(flows)) == 1  # the crossing at 50%; the touch at 10% is invisible to sign changes
    m = xirr(flows)
    assert m.value is None and m.status is QualityStatus.MISSING
    assert m.notes == ("XIRR undefined: 2 different rates solve these cash flows (multiple roots)",)


def test_a_tangent_root_exactly_on_the_search_grid_is_refused() -> None:
    """NPV = (1 - 1 / (1 + r)) ** 2 touches zero at exactly 0%, a point the grid lands on. The NPV there is an
    exact 0, which must not be taken for a crossing: it has the same sign on both sides."""
    m = xirr(yrs([0, 1, 2], ["1", "-2", "1"]))
    assert m.value is None and m.status is QualityStatus.MISSING
    assert m.notes == (
        "XIRR undefined: the only root has even multiplicity: the NPV touches zero without crossing",
    )


def test_a_grid_aligned_tangent_root_next_to_a_crossing_root_is_multiple_roots() -> None:
    """(v - 1)^2 (v - 2): a double root at 0% (on the grid) and a simple root at -50%. Counting the exact zero
    as a crossing would make this look like two plain roots; either way it must be refused as multiple."""
    flows = yrs([0, 1, 2, 3], [-2, 5, -4, 1])
    m = xirr(flows)
    assert m.value is None and m.status is QualityStatus.MISSING
    assert m.notes == ("XIRR undefined: 2 different rates solve these cash flows (multiple roots)",)


def test_a_simple_root_exactly_on_the_grid_beside_a_second_crossing_is_two_roots() -> None:
    """(v - 1)(v - 2): simple roots at 0% (on the grid) and -50%. The exact zero is a real crossing here."""
    m = xirr(yrs([0, 1, 2], [2, -3, 1]))
    assert m.value is None and m.notes == (
        "XIRR undefined: 2 different rates solve these cash flows (multiple roots)",
    )


def test_two_crossings_inside_one_grid_step_are_found() -> None:
    # (v - a)(v - b) with a, b only 0.2% apart in rate: both crossings sit inside one 0.05 log-rate step
    a, b = D(1) / D("1.100"), D(1) / D("1.102")
    flows = yrs([0, 1, 2], [a * b * 1000, -(a + b) * 1000, 1000])
    m = xirr(flows)
    assert m.value is None and "multiple roots" in m.notes[0]


def test_single_sign_change_in_the_flows_is_the_only_proof_of_ok() -> None:
    proved = xirr(yrs([0, 1, 2], [-1000, 0, 1200]))
    assert proved.status is QualityStatus.OK
    # one sign change in the running total, several in the flows: unique above 0% only -> estimated, and says so
    flows = [
        (D0, D(-100)),
        (D0 + timedelta(days=91), D(60)),
        (D0 + timedelta(days=182), D(-30)),
        (D0 + timedelta(days=365), D(120)),
    ]
    m = xirr(flows)
    if m.value is not None:
        assert m.status is QualityStatus.ESTIMATED
        assert m.notes[1] == (
            "cash-flow pattern admits more than one root; only one was found "
            "(unique above 0% by Norstrom; negative rates checked by scan only)"
        )
    unproved = xirr(
        [
            (D0, D(-100)),
            (D0 + timedelta(days=91), D(150)),
            (D0 + timedelta(days=182), D(-100)),
            (D0 + timedelta(days=365), D(60)),
        ]
    )
    assert unproved.status is QualityStatus.ESTIMATED
    assert unproved.notes[1].endswith("(uniqueness not proved; checked by scan only)")
    zero_total = xirr(yrs([0, 1, 2], [-100, 150, -50]))  # total 0: 0% is a root; Norstrom does not apply
    assert zero_total.value is None or zero_total.status is QualityStatus.ESTIMATED


amount = st.integers(-40, 40).filter(lambda a: a != 0)


@pytest.mark.property
@given(years=st.lists(st.integers(0, 6), min_size=3, max_size=6, unique=True).map(sorted), data=st.data())
def test_a_returned_root_is_never_one_of_several(years: list[int], data: st.DataObject) -> None:
    """For any sign pattern: if an independent scan finds two or more crossings the result must be missing;
    `ok` is only ever given when the flows change sign exactly once; and what is returned is a real root."""
    amounts = [data.draw(amount) for _ in years]
    flows = yrs(years, amounts)
    m = xirr(flows)
    roots = brute_roots(flows)
    sign_changes = sum(1 for a, b in itertools.pairwise(amounts) if (a > 0) != (b > 0))
    if len(roots) >= 2:
        assert m.value is None
    if m.status is QualityStatus.OK:
        assert sign_changes == 1 and len(roots) == 1
    if m.value is not None:
        assert len(roots) == 1 and float(m.value) == pytest.approx(roots[0], rel=0.02, abs=0.02)


@pytest.mark.property
@given(years=st.lists(st.integers(0, 6), min_size=3, max_size=6, unique=True).map(sorted), data=st.data())
def test_norstrom_holds_above_zero_but_only_there(years: list[int], data: st.DataObject) -> None:
    """The theorem we rely on, as tested: one sign change in the running total (total not zero) leaves at
    most one root above 0%. (Below 0% there can be another; see the counterexamples.)"""
    amounts = [data.draw(amount) for _ in years]
    running = [sum(amounts[: i + 1]) for i in range(len(amounts))]
    signs = [r > 0 for r in running if r != 0]
    assume(running[-1] != 0 and sum(1 for a, b in itertools.pairwise(signs) if a != b) == 1)
    positive = [r for r in brute_roots(yrs(years, amounts), lo=0) if r > 0]
    assert len(positive) <= 1
