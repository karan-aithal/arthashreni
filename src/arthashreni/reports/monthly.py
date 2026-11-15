"""Monthly report (Markdown) from a recommendation, produced entirely from templates (F3, invariant 13).

It states what is missing first when the recommendation is not personalised, labels DEMO output, shows the
envelope, every line with its reasons, the reserve movements, the risk level and scenarios, the computed confidence
and that the investor must approve. Numbers are printed from the validated recommendation, never recomputed here."""

from collections.abc import Sequence
from decimal import Decimal

from arthashreni.policy.recommendation import Recommendation
from arthashreni.risk.alerts import Alert
from arthashreni.risk.levels import LevelResult


def _inr(v: Decimal) -> str:
    return f"Rs {v:,.2f}"


def monthly_markdown(
    rec: Recommendation, level: LevelResult | None = None, alerts: Sequence[Alert] = ()
) -> str:
    env = rec.envelope
    out = [f"# Monthly review {rec.period:%B %Y}", "", f"**{rec.label}**", ""]
    out.append(
        f"Dataset: {rec.dataset} | Review date: {rec.review_date} | Overall action: `{rec.overall_action.value}`"
    )
    out.append(
        f"Risk level: **{rec.risk_level}** | Data confidence: **{rec.confidence}** (computed, not judged)"
    )
    if not rec.personalised:
        out += [
            "",
            "## Nothing personalised yet",
            "No approved Investment Policy Statement exists, so only running SIPs are shown and no new money is "
            "allocated (invariant 9). Missing:",
            *[f"- {m}" for m in rec.missing],
        ]
    out += [
        "",
        "## Budget envelope",
        "| Item | Amount |",
        "| --- | ---: |",
        f"| Opening budget | {_inr(env.opening_budget_inr)} |",
        f"| Committed SIPs | {_inr(env.committed_sip_inr)} |",
        f"| New allocations | {_inr(env.new_allocations_inr)} |",
        f"| Reserve transfers | {_inr(env.reserve_transfers_inr)} |",
        f"| Unallocated (holding cash) | {_inr(env.unallocated_inr)} |",
        "",
        "The four parts add up to the opening budget; the budget is never exceeded.",
        "",
        "## Lines",
    ]
    for ln in rec.lines:
        amount = f" {_inr(ln.amount_inr)}" if ln.amount_inr else ""
        out.append(
            f"- **{ln.action.value}** {ln.subject}{amount} (data: {ln.data_freshness}, confidence: {ln.confidence})"
        )
        out += [f"  - {r}" for r in ln.reasons]
        if ln.score and ln.score.get("total") is not None:
            out.append(f"  - advisory score {Decimal(ln.score['total']):.2f} ({ln.score['note']})")
    if rec.reserve_movements:
        out += ["", "## Reserve movements (need your approval)"]
        out += [
            f"- {m['direction']}: {m['reserve']} {_inr(Decimal(m['amount_inr']))} ({m['reason']})"
            for m in rec.reserve_movements
        ]
    if level is not None:
        out += [
            "",
            "## Risk",
            f"Level {level.level.name}, from {level.available} of {level.total} signals "
            f"(caution or worse: {level.at_or_above['caution']}, high or worse: {level.at_or_above['high']}, "
            f"severe: {level.at_or_above['severe']}).",
            *[f"- driver: {d}" for d in level.drivers],
            *[
                f"- alert: {a.what}; classification {a.classification}; possible responses: {', '.join(a.responses)}"
                for a in alerts
            ],
        ]
    if rec.scenarios:
        out += ["", "## Stress scenarios (scenarios, not predictions)"]
        for s in rec.scenarios:
            tot = "missing" if s["total_inr"] is None else _inr(Decimal(s["total_inr"]))
            pct = "" if s["total_pct"] is None else f" ({Decimal(s['total_pct']) * 100:.1f}%)"
            out.append(f"- {s['label']}: {tot}{pct} [{s['status']}]")
    for title, items in (
        ("Warnings", rec.warnings),
        ("Assumptions", rec.assumptions),
        ("Confidence reasons", rec.confidence_reasons),
    ):
        if items:
            out += ["", f"## {title}", *[f"- {i}" for i in items]]
    out += [
        "",
        "## Approval",
        "Nothing here is executed. Every line needs your explicit decision (approve, reject, modify or defer), "
        "recorded with a reason.",
    ]
    return "\n".join(out) + "\n"
