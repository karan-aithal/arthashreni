"""CLI for Phase 5: budget, SIPs, reserves, IPS, personal context, IPO sinking fund, risk and the monthly review.

Every write here records the investor's own setup or decision. Nothing places, changes or cancels a trade, SIP
or mandate at a broker (invariant 2)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import typer

from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope
from arthashreni.ledger.assets import resolve_asset
from arthashreni.ledger.repository import account_ids
from arthashreni.policy import store as pstore
from arthashreni.policy.context import FIELDS, Certainty
from arthashreni.policy.ips import IPS, Band

budget_app = typer.Typer(no_args_is_help=True, help="Monthly budget (B1).")
sip_app = typer.Typer(no_args_is_help=True, help="Running SIP mandates: recorded here, never placed (B1).")
reserve_app = typer.Typer(no_args_is_help=True, help="Cash reserve and IPO sinking fund (B2, B3).")
ips_app = typer.Typer(no_args_is_help=True, help="Investment Policy Statement (B5).")
context_app = typer.Typer(no_args_is_help=True, help="Personal financial context (B6).")
ipo_app = typer.Typer(no_args_is_help=True, help="Mainboard IPO sinking fund and ASBA events (B3).")
risk_app = typer.Typer(no_args_is_help=True, help="Risk engine (F2).")
review_app = typer.Typer(no_args_is_help=True, help="Monthly review and decisions (F3).")

APPS = [
    ("budget", budget_app), ("sip", sip_app), ("reserve", reserve_app), ("ips", ips_app),
    ("context", context_app), ("ipo", ipo_app), ("risk", risk_app), ("review", review_app),
]  # fmt: skip


def _day(s: str | None) -> date:
    return date.fromisoformat(s) if s else date.today()


def _period(s: str | None) -> date:
    d = _day(s)
    return d.replace(day=1)


def _fail(msg: str) -> Any:
    typer.echo(msg, err=True)
    raise typer.Exit(1)


# ---- budget, SIPs, reserves -----------------------------------------------------------------------------


@budget_app.command("set")
def budget_set(amount: Decimal, effective_from: Annotated[str, typer.Option("--from")]) -> None:
    """Set the monthly budget (all asset classes combined) from a date. History is kept."""
    with session_scope() as db:
        pstore.set_budget(db, amount, date.fromisoformat(effective_from))
    typer.echo(f"monthly budget {amount:,.2f} INR effective from {effective_from}")


@budget_app.command("show")
def budget_show(period: Annotated[str | None, typer.Option("--period")] = None) -> None:
    with session_scope() as db:
        b = pstore.budget_for(db, _period(period))
    typer.echo("no budget configured" if b is None else f"{b[1]:,.2f} INR")


@sip_app.command("add")
def sip_add(
    asset_ref: str,
    account: Annotated[str, typer.Option(help="Account label.")],
    amount: Annotated[Decimal, typer.Option()],
    day: Annotated[int, typer.Option(help="Day of month.")],
    start: Annotated[str, typer.Option()],
    platform: Annotated[str, typer.Option()],
    end: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Record a running SIP so its amount is deducted before free money is considered."""
    with session_scope() as db:
        a = resolve_asset(db, get_settings().dataset, asset_ref)
        mid = pstore.add_sip(
            db, a.id, account_ids(db)[account], amount, day, platform, date.fromisoformat(start),
            date.fromisoformat(end) if end else None,
        )  # fmt: skip
    typer.echo(f"SIP mandate {mid}: {amount:,.2f} INR on day {day} into {a.name}")


@sip_app.command("list")
def sip_list() -> None:
    with session_scope() as db:
        for m in pstore.load_mandates(db):
            typer.echo(f"{m.id:>4}  {m.status:<7} {m.amount_inr:>10,.2f}  day {m.day_of_month:>2}  asset {m.asset_id}  "
                       f"{m.platform}  {m.start_date}..{m.end_date or ''}")  # fmt: skip


@sip_app.command("end")
def sip_end(mandate_id: int, on: Annotated[str, typer.Option("--on")]) -> None:
    """Record that a mandate ended (a new version; the old row stays)."""
    with session_scope() as db:
        pstore.change_sip(db, mandate_id, status="ENDED", end_date=date.fromisoformat(on))
    typer.echo("recorded")


@reserve_app.command("add")
def reserve_add(
    name: str,
    kind: Annotated[str, typer.Option(help="CASH_RESERVE or IPO_SINKING_FUND")],
    target: Annotated[Decimal | None, typer.Option()] = None,
) -> None:
    with session_scope() as db:
        pstore.ensure_reserve(db, name, kind, target)
    typer.echo(f"reserve {name} ready")


@reserve_app.command("entry")
def reserve_entry(
    name: str,
    kind: Annotated[str, typer.Argument(help="TRANSFER_IN, DIVIDEND_IN, SALE_PROCEEDS_IN")],
    amount: Decimal,
    on: Annotated[str | None, typer.Option("--date")] = None,
    note: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Record money coming into a reserve (dividends and sale proceeds go here, never into the budget, B2)."""
    if kind not in ("TRANSFER_IN", "DIVIDEND_IN", "SALE_PROCEEDS_IN"):
        _fail(
            "use `review decide` to spend from a reserve; kinds here are TRANSFER_IN, DIVIDEND_IN, SALE_PROCEEDS_IN"
        )
    with session_scope() as db:
        rid = pstore.ensure_reserve(db, name, "CASH_RESERVE")
        pstore.reserve_add(db, rid, kind, amount, _day(on), note=note)
    typer.echo("recorded")


@reserve_app.command("show")
def reserve_show() -> None:
    with session_scope() as db:
        for b in pstore.reserve_balances(db):
            tgt = "" if b.target_inr is None else f" of target {b.target_inr:,.2f}"
            typer.echo(
                f"{b.name:<24} {b.kind:<16} available {b.available_inr:>12,.2f}  blocked {b.blocked_inr:>10,.2f}{tgt}"
            )


# ---- IPS and context ------------------------------------------------------------------------------------


@ips_app.command("template")
def ips_template() -> None:
    """Print an example IPS to edit. It is only an example: the bands are yours to choose."""
    example = IPS(
        asset_class_bands={
            "MF": Band(low=Decimal("0.3"), high=Decimal("0.6")),
            "EQUITY_US": Band(low=Decimal("0.1"), high=Decimal("0.3")),
            "GOLD_ETF": Band(low=Decimal("0"), high=Decimal("0.2")),
        },
    )
    typer.echo(example.model_dump_json(indent=2))


@ips_app.command("draft")
def ips_draft(json_file: Path) -> None:
    """Validate and store an IPS as a DRAFT. A draft is never used for allocation."""
    ips = IPS.model_validate_json(json_file.read_text())
    with session_scope() as db:
        v = pstore.create_ips_draft(db, ips)
    typer.echo(
        f"IPS draft version {v} stored. Not approved: run `ips approve {v} --note ...` after reading it."
    )


@ips_app.command("approve")
def ips_approve(version: int, note: Annotated[str, typer.Option(help="Why you approve it.")]) -> None:
    """Only you approve an IPS. This supersedes the previously approved version."""
    with session_scope() as db:
        pstore.approve_ips(db, version, note)
    typer.echo(f"IPS version {version} approved")


@ips_app.command("show")
def ips_show() -> None:
    with session_scope() as db:
        row = pstore.approved_ips(db)
    typer.echo("no approved IPS" if row is None else f"version {row[1]}\n{row[2].model_dump_json(indent=2)}")


@context_app.command("set")
def context_set(
    field: Annotated[str, typer.Argument(help=", ".join(FIELDS))],
    value: Annotated[str, typer.Argument(help="JSON, e.g. 600000 or true or []")],
    certainty: Annotated[str, typer.Option(help="KNOWN, ASSUMED or UNKNOWN")] = "KNOWN",
) -> None:
    if field not in (*FIELDS, "thesis_failed_asset_ids"):
        _fail(f"unknown field {field!r}")
    with session_scope() as db:
        pstore.set_context(db, field, json.loads(value), Certainty(certainty))
    typer.echo("recorded")


@context_app.command("show")
def context_show() -> None:
    with session_scope() as db:
        ctx = pstore.load_context(db)
    for f in FIELDS:
        c = ctx.get(f)
        typer.echo(f"{f:<26} {'unknown' if c is None else f'{c.value} [{c.certainty.value}]'}")


# ---- IPO ------------------------------------------------------------------------------------------------


@ipo_app.command("add")
def ipo_add(
    ref: str,
    board: Annotated[str, typer.Option(help="MAINBOARD or SME")],
    min_lot: Annotated[Decimal | None, typer.Option(help="Minimum lot value from the RHP.")] = None,
    rhp: Annotated[str | None, typer.Option(help="Where the RHP is kept.")] = None,
    research: Annotated[str, typer.Option(help="UNRATED, ALLOWED or NOT_ALLOWED")] = "UNRATED",
) -> None:
    with session_scope() as db:
        pstore.add_ipo(db, ref, board.upper(), rhp, min_lot, research)
    typer.echo(
        "recorded" + (" (SME IPOs are excluded automatically)" if board.upper() != "MAINBOARD" else "")
    )


@ipo_app.command("block")
def ipo_block(
    ref: str, reserve: str, amount: Decimal, on: Annotated[str | None, typer.Option("--date")] = None
) -> None:
    """Record the ASBA block you made yourself; the money stays committed until released."""
    with session_scope() as db:
        rid = pstore.ensure_reserve(db, reserve, "IPO_SINKING_FUND")
        try:
            pstore.ipo_block(db, ref, rid, amount, _day(on))
        except pstore.PolicyError as exc:
            _fail(str(exc))
    typer.echo("block recorded")


@ipo_app.command("settle")
def ipo_settle(
    ref: str, reserve: str, allotted: Decimal, on: Annotated[str | None, typer.Option("--date")] = None
) -> None:
    """Record allotment (full, partial or none); the rest of the blocked money is released."""
    with session_scope() as db:
        rid = pstore.ensure_reserve(db, reserve, "IPO_SINKING_FUND")
        status = pstore.ipo_settle(db, ref, rid, allotted, _day(on))
    typer.echo(f"{ref}: {status}")


# ---- risk and review ------------------------------------------------------------------------------------


@risk_app.command("run")
def risk_run(
    on: Annotated[str | None, typer.Option("--date")] = None,
    save: Annotated[bool, typer.Option("--save", help="Store signals, level and new alerts.")] = False,
) -> None:
    """Signals, level, scenarios and new alerts. Stored only with --save."""
    from arthashreni.review import run_risk

    ds = get_settings().dataset
    with session_scope() as db:
        r = run_risk(db, ds, _day(on), persist=save)
    typer.echo(
        f"{ds.upper()} risk on {r.day}: {r.level.level.name} (from {r.level.available} of {r.level.total} signals)"
    )
    for s in r.signals:
        v = "missing" if s.value.value is None else f"{s.value.value:.4f}"
        typer.echo(
            f"  {s.id:<40} {v:>10}  {s.level.name if s.level is not None else 'no level':<14} [{s.value.status.value}]"
        )
    for sc in r.scenarios:
        t = "missing" if sc.total_inr.value is None else f"{sc.total_inr.value:,.2f}"
        typer.echo(f"  scenario {sc.id:<20} {t:>14} INR [{sc.total_inr.status.value}] (not a prediction)")
    typer.echo(
        f"new alerts: {len(r.diff.new)}, unchanged (silent): {len(r.diff.unchanged)}, cleared: {len(r.diff.cleared)}"
    )
    for a in r.diff.new:
        typer.echo(f"  ALERT {a.level.name}: {a.what}")
    typer.echo(f"data confidence: {r.confidence.level.value} {list(r.confidence.reasons)}")


@review_app.command("run")
def review_run(
    period: Annotated[str | None, typer.Option(help="Month, e.g. 2026-11-01")] = None,
    on: Annotated[str | None, typer.Option("--date")] = None,
    commit: Annotated[
        bool, typer.Option("--commit", help="Store the month's envelope and recommendation (once).")
    ] = False,
) -> None:
    """Produce the monthly recommendation and report. Without --commit nothing is stored."""
    from arthashreni.reports.monthly import monthly_markdown
    from arthashreni.review import monthly_review

    ds = get_settings().dataset
    today = _day(on)
    with session_scope() as db:
        try:
            m = monthly_review(
                db, ds, _period(period) if period else today.replace(day=1), today, persist_risk=commit
            )
        except pstore.PolicyError as exc:
            _fail(str(exc))
        md = monthly_markdown(m.recommendation, m.risk.level, m.risk.alerts)
        if commit:
            try:
                rid = pstore.allocate_period(db, m.recommendation, m.ips_row_id)
            except pstore.PeriodAllocated as exc:
                _fail(str(exc))
            out = get_settings().data_dir / "reports" / f"review-{m.recommendation.period:%Y-%m}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md)
            typer.echo(f"stored as recommendation {rid}; report written to {out}")
    typer.echo(md)


@review_app.command("decide")
def review_decide(
    recommendation_id: int,
    decision: Annotated[str, typer.Option(help="APPROVE, REJECT, MODIFY or DEFER")],
    reason: Annotated[str, typer.Option()],
    line: Annotated[int | None, typer.Option(help="Recommendation line id; all lines if omitted.")] = None,
    on: Annotated[str | None, typer.Option("--date")] = None,
) -> None:
    """Record your decision. This executes nothing: you place any order yourself at your broker."""
    with session_scope() as db:
        try:
            pstore.record_decision(db, recommendation_id, line, decision.upper(), reason, _day(on))
        except pstore.PolicyError as exc:
            _fail(str(exc))
    typer.echo("decision recorded (nothing was executed)")


@review_app.command("close")
def review_close(period: str, on: Annotated[str | None, typer.Option("--date")] = None) -> None:
    """Close a month: the unallocated remainder moves to the cash reserve (it does not raise next month's budget)."""
    with session_scope() as db:
        rid = pstore.ensure_reserve(db, "Cash reserve", "CASH_RESERVE")
        try:
            left = pstore.sweep_leftover(db, _period(period), rid, _day(on))
        except pstore.PolicyError as exc:
            _fail(str(exc))
    typer.echo(f"{left:,.2f} INR moved to the cash reserve")
