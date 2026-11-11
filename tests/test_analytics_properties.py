"""Property tests for the calculation engine (G1.4): invariants over thousands of random inputs."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from arthashreni.analytics.allocation import allocate, concentration
from arthashreni.analytics.fx import attribute_fx
from arthashreni.analytics.returns import ValuePoint, twr
from arthashreni.analytics.series import correlation, max_drawdown
from arthashreni.analytics.valuation import AssetInfo, Position, totals, value_position
from arthashreni.analytics.xirr import xirr
from arthashreni.dq.measured import Measured, QualityStatus

pytestmark = pytest.mark.property
D = Decimal
D0 = date(2025, 1, 1)
STAMP = datetime(2025, 1, 1, tzinfo=UTC)
money = st.decimals(min_value=D("0.01"), max_value=D(10_000_000), places=4)
price = st.decimals(min_value=D("0.0001"), max_value=D(100_000), places=4)
units = st.decimals(min_value=D("0.001"), max_value=D(100_000), places=3)


def mm(v: Decimal, status: QualityStatus = QualityStatus.OK) -> Measured[Decimal]:
    return Measured(v, status, STAMP, ("p",))


holding = st.tuples(
    st.sampled_from(["MF", "EQUITY_IN", "ETF_IN", "EQUITY_US"]),
    st.sampled_from(["INR", "USD"]),
    st.sampled_from([None, "Tech", "Fin", "Energy"]),
    st.sampled_from([None, "LARGE", "MID"]),
    units,
    price,
    st.decimals(min_value=D(60), max_value=D(100), places=4),
    st.sampled_from(list(QualityStatus)[:4]),  # ok, estimated, stale, conflicting
)


def build(rows: list[tuple]) -> list:  # type: ignore[type-arg]
    out = []
    for i, (cls, cur, sector, cap, u, p, fx, status) in enumerate(rows, start=1):
        a = AssetInfo(i, f"H{i}", cls, cur, "IN" if cur == "INR" else "US", sector, cap)
        pos = Position(1, a, u, D(1), D(1))
        out.append(
            value_position(
                pos, mm(p, status), None if cur == "INR" else mm(fx), None if cur == "INR" else "FBIL_REF", D0
            )
        )
    return out


@given(st.lists(holding, min_size=1, max_size=12))
def test_weights_sum_to_one_and_value_is_units_times_price_times_fx(rows: list[tuple]) -> None:  # type: ignore[type-arg]
    holdings = build(rows)
    for h, (_, cur, _, _, u, p, fx, _) in zip(holdings, rows, strict=True):
        assert h.value_inr.value == u * p * (D(1) if cur == "INR" else fx)
    for dim in ("asset_class", "country", "currency", "sector", "market_cap", "holding"):
        slices = allocate(holdings, dim)  # type: ignore[arg-type]
        weights = [s.weight.value for s in slices]
        assert all(w is not None and 0 < w <= 1 for w in weights)
        assert abs(sum(weights, D(0)) - 1) < D("1e-25")  # type: ignore[arg-type]
        assert abs(
            sum((s.value_inr.value or 0 for s in slices), D(0)) - totals(holdings, D0).value_inr.value
        ) < D("1e-9")  # type: ignore[operator]


@given(st.lists(holding, min_size=1, max_size=12), st.integers(1, 6))
def test_concentration_bounds_and_status_inheritance(rows: list[tuple], n: int) -> None:  # type: ignore[type-arg]
    holdings = build(rows)
    c = concentration(holdings, n)
    assert c.hhi.value is not None and c.top_n_weight.value is not None
    count = len(rows)
    assert D(1) / count - D("1e-25") <= c.hhi.value <= D(1) + D("1e-25")
    assert c.top_n_weight.value <= D(1) + D("1e-25")
    assert c.top_n_weight.value >= min(n, count) / D(count) - D(
        "1e-25"
    )  # the top n hold at least their fair share
    worst = max((h.value_inr.status for h in holdings), key=lambda s: s.severity)
    assert c.hhi.status.severity >= worst.severity


@given(cost_local=money, cost_inr=money, value_local=money, rate=price)
def test_fx_attribution_always_reconciles(
    cost_local: Decimal, cost_inr: Decimal, value_local: Decimal, rate: Decimal
) -> None:
    a = attribute_fx(mm(cost_local), mm(cost_inr), mm(value_local), mm(rate), "FBIL_REF")
    assert a.reconciles and a.residual is not None and a.residual < D("1e-12")
    assert a.inr_return.value is not None and a.local_return.value is not None
    assert a.fx_return.value is not None and a.interaction.value is not None
    with localcontext() as ctx:  # the engine works at 40 digits; sum the parts with room to spare
        ctx.prec = 80
        # INR return = local return + FX effect + interaction (F1)
        gap = abs(a.inr_return.value - (a.local_return.value + a.fx_return.value + a.interaction.value))
    assert gap < D("1e-30") * max(D(1), abs(a.inr_return.value))
    assert a.gain_inr.value is not None and a.local_effect_inr.value is not None
    assert a.fx_effect_inr.value is not None and a.interaction_inr.value is not None
    parts = a.local_effect_inr.value + a.fx_effect_inr.value + a.interaction_inr.value
    assert abs(a.gain_inr.value - parts) < D("1e-12")


@given(
    growth=st.lists(st.decimals(min_value=D("0.5"), max_value=D("2"), places=3), min_size=1, max_size=8),
    flows=st.lists(st.integers(0, 5000), min_size=9, max_size=9),
    start=st.integers(100, 100_000),
)
def test_twr_ignores_the_size_and_timing_of_contributions(
    growth: list[Decimal], flows: list[int], start: int
) -> None:
    """Whatever is added at the end of each period, chain-linking returns exactly the growth factors."""
    with localcontext() as ctx:
        ctx.prec = 40
        value, points = D(start), [ValuePoint(D0, mm(D(start)), D(start))]
        expected = D(1)
        for i, g in enumerate(growth, start=1):
            value = value * g + flows[i]
            expected *= g
            points.append(ValuePoint(D0 + timedelta(days=30 * i), mm(value), D(flows[i])))
        result = twr(points).result.value
    assert result is not None and abs(result - (expected - 1)) < D("1e-25")


@given(st.lists(st.decimals(min_value=D(1), max_value=D(1000), places=2), min_size=2, max_size=40))
def test_drawdown_is_between_minus_one_and_zero_and_ordered(prices: list[Decimal]) -> None:
    d = max_drawdown([(D0 + timedelta(days=i), mm(p)) for i, p in enumerate(prices)])
    assert d.depth.value is not None and d.current_depth.value is not None
    assert -1 < d.depth.value <= 0 and d.depth.value <= d.current_depth.value <= 0
    if d.depth.value < 0:
        assert d.peak is not None and d.trough is not None and d.peak < d.trough
        if d.recovered_on is not None:
            assert d.recovered_on >= d.trough and d.recovery_days == (d.recovered_on - d.trough).days
    else:
        assert d.peak is None and d.recovered_on is None


@given(
    a=st.lists(st.decimals(min_value=D(1), max_value=D(500), places=2), min_size=6, max_size=30),
    b=st.lists(st.decimals(min_value=D(1), max_value=D(500), places=2), min_size=6, max_size=30),
)
def test_correlation_is_bounded_and_symmetric(a: list[Decimal], b: list[Decimal]) -> None:
    sa = [(D0 + timedelta(days=i), mm(p)) for i, p in enumerate(a)]
    sb = [(D0 + timedelta(days=i), mm(p)) for i, p in enumerate(b)]
    c = correlation(sa, sb, min_returns=3)
    assume(c.value is not None)
    assert c.value is not None and abs(c.value) <= 1 + D("1e-20")
    assert correlation(sb, sa, min_returns=3).value == pytest.approx(c.value, abs=D("1e-20"))


@given(
    paid=st.lists(st.tuples(st.integers(0, 400), st.integers(100, 50_000)), min_size=1, max_size=5),
    back=st.lists(st.tuples(st.integers(401, 1500), st.integers(100, 80_000)), min_size=1, max_size=4),
)
def test_xirr_root_has_zero_net_present_value(
    paid: list[tuple[int, int]], back: list[tuple[int, int]]
) -> None:
    flows = [(D0 + timedelta(days=d), D(-a)) for d, a in paid] + [
        (D0 + timedelta(days=d), D(a)) for d, a in back
    ]
    m = xirr(flows)
    if m.value is None:  # refusing is allowed (outside the search range); a wrong number is not
        assert m.status is QualityStatus.MISSING
        return
    with localcontext() as ctx:
        ctx.prec = 40
        npv = sum(cf / (1 + m.value) ** (D((d - D0).days) / 365) for d, cf in flows)
    scale = sum(abs(cf) for _, cf in flows)
    assert abs(npv) / scale < D("1e-15")
