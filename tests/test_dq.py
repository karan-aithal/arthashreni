"""Pure data-quality logic: Measured (E2), calendars and the E3 checks. No database."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from arthashreni.dq.calendars import (
    calendar_is_assumed,
    expected_latest,
    is_session,
    last_session_on_or_before,
)
from arthashreni.dq.checks import disagreement, impossible_value, jump, staleness
from arthashreni.dq.measured import (
    Measured,
    QualityStatus,
    m_add,
    m_div,
    m_mul,
    m_sub,
    m_sum,
    missing,
    with_status,
    worst,
)

T0 = datetime(2026, 11, 1, tzinfo=UTC)
T1 = datetime(2026, 11, 5, tzinfo=UTC)
D = Decimal


def m(
    v: str | None, status: QualityStatus = QualityStatus.OK, at: datetime = T1, src: str = "a"
) -> Measured[Decimal]:
    return Measured(None if v is None else D(v), status, at, (src,))


# ---- Measured ---------------------------------------------------------------------------------


def test_severity_order_matches_adr_0007() -> None:
    order = ["ok", "estimated", "stale", "conflicting", "missing", "quarantined"]
    assert [s.value for s in sorted(QualityStatus, key=lambda s: s.severity)] == order
    assert worst() is QualityStatus.OK


def test_arithmetic_propagates_worst_status_oldest_as_of_and_sources() -> None:
    r = m_add(m("1.5", QualityStatus.STALE, T0, "a"), m("2", QualityStatus.ESTIMATED, T1, "b"))
    assert r.value == D("3.5")
    assert r.status is QualityStatus.STALE
    assert r.as_of == T0
    assert r.source_ids == ("a", "b")
    assert m_sub(m("5"), m("2")).value == 3
    assert m_mul(m("5"), m("2")).value == 10
    assert m_div(m("10"), m("4")).value == D("2.5")


def test_division_by_zero_is_missing_not_an_exception() -> None:
    r = m_div(m("1"), m("0"))
    assert r.value is None and r.status is QualityStatus.MISSING
    assert "division by zero" in r.notes
    assert m_div(m("1"), m("0", QualityStatus.QUARANTINED)).status is QualityStatus.MISSING


def test_arithmetic_overflow_is_missing_not_an_exception() -> None:
    huge = m_mul(m("1e999999"), m("1e999999"))
    assert huge.value is None and huge.status is QualityStatus.MISSING


def test_missing_and_quarantined_inputs_give_no_value() -> None:
    assert m_add(m("1"), m(None, QualityStatus.MISSING)).status is QualityStatus.MISSING
    r = m_add(m("1"), m("2", QualityStatus.QUARANTINED))
    assert r.value is None and r.status is QualityStatus.QUARANTINED and not r.usable
    assert missing().status is QualityStatus.MISSING and not missing().usable


def test_with_status_only_degrades() -> None:
    x = with_status(m("1", QualityStatus.CONFLICTING), QualityStatus.STALE, "late")
    assert x.status is QualityStatus.CONFLICTING
    assert x.notes == ("late",)
    assert with_status(m("1"), QualityStatus.STALE, "late").status is QualityStatus.STALE


def test_m_sum() -> None:
    r = m_sum([m("1"), m("2", QualityStatus.ESTIMATED), m("3", at=T0)])
    assert (r.value, r.status, r.as_of) == (6, QualityStatus.ESTIMATED, T0)
    assert m_sum([], T1).value == 0


statuses = st.sampled_from(list(QualityStatus))


@pytest.mark.property
@given(
    a=statuses, b=statuses, x=st.decimals(-(10**6), 10**6, places=2), y=st.decimals(-(10**6), 10**6, places=2)
)
def test_combined_status_is_the_worst_input(
    a: QualityStatus, b: QualityStatus, x: Decimal, y: Decimal
) -> None:
    r = m_add(Measured(x, a, T1, ("a",)), Measured(y, b, T0, ("b",)))
    assert r.status.severity >= max(a.severity, b.severity)
    assert r.as_of == T0
    if r.value is not None:
        assert r.value == x + y
        assert r.status is worst(a, b)  # a value is only produced when nothing forced it to missing


# ---- calendars --------------------------------------------------------------------------------


def test_exchange_sessions() -> None:
    assert is_session("XNYS", date(2026, 11, 25)) and not is_session(
        "XNYS", date(2026, 11, 26)
    )  # Thanksgiving
    assert not is_session("XBOM", date(2026, 11, 7))  # Saturday
    assert is_session("WEEKDAY", date(2026, 11, 9)) and not is_session("WEEKDAY", date(2026, 11, 8))
    assert is_session("XNYS", date(2099, 1, 5))  # beyond the packaged calendar: weekdays, no crash


BSE_HOLIDAY = date(2026, 11, 10)  # a BSE holiday (Tuesday)


@pytest.mark.parametrize("market", ["AMFI", "FBIL", "SBI"])
def test_derived_calendars_follow_the_source_not_the_bse(market: str) -> None:
    seen = {date(2026, 11, 5), date(2026, 11, 6), BSE_HOLIDAY, date(2026, 11, 12)}  # 9th and 11th were gaps
    assert is_session(market, BSE_HOLIDAY, seen)  # published although the BSE was shut: no BSE assumption
    assert not is_session(market, date(2026, 11, 9), seen)  # inside the span but not published: a holiday
    assert not is_session(market, date(2026, 11, 11), seen)
    assert not is_session(market, date(2026, 11, 7), seen)  # a Saturday nobody published
    assert is_session(market, date(2026, 11, 8), seen | {date(2026, 11, 8)})  # a Sunday that was published
    assert is_session(market, date(2026, 11, 13), seen)  # after the span: weekdays assumed
    assert is_session(market, date(2026, 11, 2), seen)  # before the span: weekdays assumed
    assert is_session(market, BSE_HOLIDAY) and not is_session(
        market, date(2026, 11, 8)
    )  # no history: weekdays


def test_assumed_calendar_is_reported_and_named_in_the_stale_note() -> None:
    assert calendar_is_assumed("AMFI", ()) and not calendar_is_assumed("AMFI", {date(2026, 11, 2)})
    assert not calendar_is_assumed("XNYS", ()) and not calendar_is_assumed("WEEKDAY", ())
    r = staleness(date(2026, 11, 3), date(2026, 11, 11), "FBIL", 1)
    assert r.status is QualityStatus.STALE and "FBIL calendar not yet known, weekdays assumed" in (
        r.note or ""
    )
    known = staleness(date(2026, 11, 3), date(2026, 11, 11), "FBIL", 1, {date(2026, 11, 3)})
    assert "assumed" not in (known.note or "")


def test_a_gap_in_the_source_history_is_not_stale() -> None:
    seen = {date(2026, 3, 2), date(2026, 3, 4)}  # the 3rd is a gap: the source did not publish
    assert staleness(date(2026, 3, 2), date(2026, 3, 3), "FBIL", 0, seen).status is QualityStatus.OK
    assert staleness(date(2026, 3, 2), date(2026, 3, 4), "FBIL", 0, seen).status is QualityStatus.STALE


def test_expected_latest_skips_holidays_and_applies_delay() -> None:
    assert last_session_on_or_before("XNYS", date(2026, 11, 26)) == date(2026, 11, 25)
    assert expected_latest("XNYS", date(2026, 11, 27), 1) == date(2026, 11, 25)
    assert expected_latest("XNYS", date(2026, 11, 9), 0) == date(2026, 11, 9)
    assert expected_latest("XBOM", date(2026, 11, 9), 1) == date(2026, 11, 6)  # Monday: Friday is the latest


# ---- E3 checks --------------------------------------------------------------------------------


def test_missing_on_a_holiday_is_ok_and_on_a_trading_day_is_stale() -> None:
    # Fri 27 Nov: Thursday was Thanksgiving, so Wednesday's close is the newest a source can have.
    assert staleness(date(2026, 11, 25), date(2026, 11, 27), "XNYS", 1).status is QualityStatus.OK
    late = staleness(date(2026, 11, 24), date(2026, 11, 27), "XNYS", 1)
    assert late.status is QualityStatus.STALE and "1 days behind" in (late.note or "")
    assert staleness(None, date(2026, 11, 27), "XNYS", 1).status is QualityStatus.MISSING


def test_stale_note_names_both_dates() -> None:
    r = staleness(date(2026, 11, 3), date(2026, 11, 11), "XBOM", 1)
    assert (
        r.status is QualityStatus.STALE
        and "2026-11-03" in (r.note or "")
        and "2026-11-09" in (r.note or "")  # 10 Nov 2026 is a BSE holiday
    )


@pytest.mark.parametrize("kind,value", [("FX rate", "0"), ("NAV", "-1"), ("price", "0.00")])
def test_impossible_values_are_quarantined(kind: str, value: str) -> None:
    assert impossible_value(kind, D(value)).status is QualityStatus.QUARANTINED
    assert impossible_value(kind, D("0.0001")).status is QualityStatus.OK


def history(n: int = 30, start: str = "100") -> list[Decimal]:
    return [D(start) + D(i % 3) / 10 for i in range(n)]  # tiny wiggle around 100


def test_unexplained_jump_is_quarantined() -> None:
    r = jump(history(), D("130"), "price")
    assert r.status is QualityStatus.QUARANTINED and "29.74%" in (r.note or "")


def test_normal_move_and_floor() -> None:
    assert jump(history(), D("100.5"), "price").status is QualityStatus.OK
    # flat liquid-fund NAV: any move below the floor is noise, however small the volatility
    flat = [D("1000") + D(i) * D("0.01") for i in range(30)]
    assert jump(flat, flat[-1] * D("1.005"), "nav").status is QualityStatus.OK
    assert jump(flat, flat[-1] * D("1.05"), "nav").status is QualityStatus.QUARANTINED


def test_jump_explained_by_a_corporate_action() -> None:
    on = date(2026, 11, 5)
    r = jump(history(), D("50"), "price", action_dates=[date(2026, 11, 4)], on=on)
    assert r.status is QualityStatus.OK and "corporate action" in (r.note or "")
    far = jump(history(), D("50"), "price", action_dates=[date(2026, 10, 1)], on=on)
    assert far.status is QualityStatus.QUARANTINED


def test_too_little_history_is_said_not_silently_passed() -> None:
    r = jump(history(5), D("500"), "price")
    assert r.status is QualityStatus.OK and "skipped" in (r.note or "")
    assert "skipped" in (jump([], D("1"), "price").note or "")


def test_source_disagreement() -> None:
    res, d = disagreement(D("102.5"), D("100"), D("0.02"))
    assert res.status is QualityStatus.CONFLICTING and d == D("0.025")
    assert disagreement(D("101"), D("100"), D("0.02"))[0].status is QualityStatus.OK
