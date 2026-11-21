"""The template monthly report (F3, invariant 13): exact output for fixed inputs, no database, no LLM."""

from datetime import date
from decimal import Decimal

from arthashreni.policy.envelope import Envelope, EnvelopeLine, Kind
from arthashreni.policy.recommendation import Action, RecLine, Recommendation
from arthashreni.reports.monthly import _inr, monthly_markdown
from arthashreni.risk.alerts import Alert
from arthashreni.risk.levels import LevelResult
from arthashreni.risk.thresholds import Level

D = Decimal
PERIOD = date(2026, 11, 1)


def envelope(sip: str = "2000", new: str = "0", reserve: str = "0", free: str = "8000") -> Envelope:
    lines = (EnvelopeLine(kind=Kind.SIP, amount_inr=D(sip), asset_id=1),) if D(sip) else ()
    return Envelope(
        period=PERIOD, opening_budget_inr=D("10000"), committed_sip_inr=D(sip), new_allocations_inr=D(new),
        reserve_transfers_inr=D(reserve), unallocated_inr=D(free), lines=lines,
    )  # fmt: skip


def rec(**kw: object) -> Recommendation:
    base: dict[str, object] = dict(
        review_date=date(2026, 11, 12), period=PERIOD, dataset="DEMO", label="DEMO: synthetic data", personalised=True,
        risk_level="HIGH_RISK", overall_action=Action.CONTINUE_SIP, budget_inr=D("10000"), envelope=envelope(),
        lines=(RecLine(action=Action.CONTINUE_SIP, amount_inr=D("2000"), subject="Fund A", envelope_ref=0,
                       reasons=("running SIP is pre-committed",), data_freshness="stale", confidence="low"),),
        confidence="low",
    )  # fmt: skip
    return Recommendation(**{**base, **kw})  # type: ignore[arg-type]


def test_money_is_printed_with_grouping_and_two_decimals() -> None:
    assert _inr(D("1234567.5")) == "Rs 1,234,567.50" and _inr(D("0")) == "Rs 0.00"


def test_a_personalised_report_exactly() -> None:
    md = monthly_markdown(rec())
    assert (
        md
        == """# Monthly review November 2026

**DEMO: synthetic data**

Dataset: DEMO | Review date: 2026-11-12 | Overall action: `continue_sip`
Risk level: **HIGH_RISK** | Data confidence: **low** (computed, not judged)

## Budget envelope
| Item | Amount |
| --- | ---: |
| Opening budget | Rs 10,000.00 |
| Committed SIPs | Rs 2,000.00 |
| New allocations | Rs 0.00 |
| Reserve transfers | Rs 0.00 |
| Unallocated (holding cash) | Rs 8,000.00 |

The four parts add up to the opening budget; the budget is never exceeded.

## Lines
- **continue_sip** Fund A Rs 2,000.00 (data: stale, confidence: low)
  - running SIP is pre-committed

## Approval
Nothing here is executed. Every line needs your explicit decision (approve, reject, modify or defer), recorded with a reason.
"""
    )
    assert "Nothing personalised" not in md


def test_without_an_ips_the_report_says_what_is_missing_first() -> None:
    md = monthly_markdown(rec(personalised=False, missing=("approved Investment Policy Statement", "budget")))
    assert "## Nothing personalised yet\nNo approved Investment Policy Statement exists" in md
    assert (
        "(invariant 9). Missing:\n- approved Investment Policy Statement\n- budget\n\n## Budget envelope"
        in md
    )


def test_a_zero_amount_line_shows_no_amount_and_a_score_is_shown() -> None:
    line = RecLine(action=Action.HOLD_CASH, amount_inr=D(0), subject="cash", reasons=("a", "b"),
                   score={"total": "0.8125", "note": "advisory"})  # fmt: skip
    md = monthly_markdown(
        rec(envelope=envelope(sip="0", free="10000"), lines=(line,), overall_action=Action.HOLD_CASH)
    )
    assert (
        "- **hold_cash** cash (data: ok, confidence: low)\n  - a\n  - b\n  - advisory score 0.81 (advisory)"
        in md
    )
    unscored = RecLine(
        action=Action.HOLD, amount_inr=D(0), subject="x", reasons=("r",), score={"total": None, "note": "n"}
    )
    assert "advisory score" not in monthly_markdown(
        rec(envelope=envelope(sip="0", free="10000"), lines=(unscored,))
    )


def test_reserve_movements_scenarios_and_notes() -> None:
    md = monthly_markdown(
        rec(
            reserve_movements=({"direction": "transfer in", "reserve": "Cash reserve", "amount_inr": "500", "reason": "leftover"},),
            scenarios=(
                {"label": "Equity -10%", "total_inr": "-1234.5", "total_pct": "-0.1", "status": "stale"},
                {"label": "FX", "total_inr": None, "total_pct": None, "status": "missing"},
            ),
            warnings=("no data for rates",), assumptions=("IPS v1",), confidence_reasons=("coverage low",),
        )
    )  # fmt: skip
    assert "## Reserve movements (need your approval)\n- transfer in: Cash reserve Rs 500.00 (leftover)" in md
    assert (
        "## Stress scenarios (scenarios, not predictions)\n- Equity -10%: Rs -1,234.50 (-10.0%) [stale]\n- FX: missing [missing]"
        in md
    )
    for title, item in (
        ("Warnings", "no data for rates"),
        ("Assumptions", "IPS v1"),
        ("Confidence reasons", "coverage low"),
    ):
        assert f"## {title}\n- {item}" in md
    assert (
        md.index("## Reserve movements")
        < md.index("## Stress scenarios")
        < md.index("## Warnings")
        < md.index("## Approval")
    )
    assert "## Risk" not in md  # no level result given


def test_the_risk_section_lists_drivers_and_alerts() -> None:
    level = LevelResult(
        Level.HIGH_RISK, {"caution": 3, "high": 2, "severe": 0}, 7, 10, ("top3_weight", "fx"), ()
    )
    alert = Alert(key="k", level=Level.HIGH_RISK, what="Top 3 are 90%", when=PERIOD, assets=(), evidence=(), sources=(),
                  impact_inr=None, classification="uncertain", scenarios=(), responses=("review thesis", "hold cash"),
                  if_nothing="", watch="")  # fmt: skip
    md = monthly_markdown(rec(), level, [alert])
    assert (
        "## Risk\nLevel HIGH_RISK, from 7 of 10 signals (caution or worse: 3, high or worse: 2, severe: 0).\n"
        "- driver: top3_weight\n- driver: fx\n"
        "- alert: Top 3 are 90%; classification uncertain; possible responses: review thesis, hold cash" in md
    )
