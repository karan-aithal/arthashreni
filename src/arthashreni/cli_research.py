"""CLI for Phase 7: fund holdings, overlap, news, IPO checklist and research claims.

Everything here records data or the investor's own judgement. Imported text is data, never an instruction
(invariant 11), and nothing contacts the network."""

from datetime import date
from pathlib import Path
from typing import Annotated, Any

import typer
from sqlalchemy import text

from arthashreni import research_store as rs
from arthashreni.analytics.allocation import overlap
from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope
from arthashreni.dq.measured import day_stamp
from arthashreni.ledger.assets import resolve_asset
from arthashreni.research.ipo import REQUIRED_FACTS, missing_facts

holdings_app = typer.Typer(no_args_is_help=True, help="Fund and ETF holdings files (look-through).")
news_app = typer.Typer(no_args_is_help=True, help="Official news feeds, de-duplicated.")
ipo_research_app = typer.Typer(no_args_is_help=True, help="IPO checklist from the RHP and classification.")
claim_app = typer.Typer(no_args_is_help=True, help="Research claims that cite stored evidence.")

APPS = [
    ("holdings", holdings_app),
    ("news", news_app),
    ("ipo-research", ipo_research_app),
    ("claim", claim_app),
]


def _fail(msg: str) -> Any:
    typer.echo(msg, err=True)
    raise typer.Exit(1)


@holdings_app.command("import")
def holdings_import(
    fund: Annotated[
        str, typer.Argument(help="ISIN, AMFI:<code> or TICKER:<MIC>:<symbol> of the fund or ETF.")
    ],
    file: Path,
    as_of: Annotated[str, typer.Option("--as-of", help="Disclosure date of the file (YYYY-MM-DD).")],
) -> None:
    """Import a holdings file (columns isin,name,pct_to_nav). The whole file or nothing."""
    with session_scope() as db:
        a = resolve_asset(db, get_settings().dataset, fund)
        try:
            n = rs.import_holdings(db, a.id, date.fromisoformat(as_of), file.read_text(encoding="utf-8"))
        except rs.ResearchError as exc:
            _fail(str(exc))
    typer.echo(f"{n} holdings stored for {a.name} as of {as_of}")


@holdings_app.command("overlap")
def holdings_overlap(
    a_ref: str, b_ref: str, on: Annotated[str | None, typer.Option("--date")] = None
) -> None:
    """Overlap of two funds: the sum of the smaller weight of each common holding."""
    day = date.fromisoformat(on) if on else date.today()
    with session_scope() as db:
        a, b = (resolve_asset(db, get_settings().dataset, r) for r in (a_ref, b_ref))
        loaded = rs.load_fund_holdings(db, day)
    m = overlap(loaded.weights.get(a.id), loaded.weights.get(b.id), day_stamp(day))
    typer.echo(
        f"overlap {a.name} / {b.name}: "
        + (f"{m.value:.2%} [{m.status.value}]" if m.value is not None else f"missing ({'; '.join(m.notes)})")
    )


@news_app.command("import")
def news_import(
    file: Path, feed: Annotated[str, typer.Option(help="Name of the feed, e.g. sebi_press.")]
) -> None:
    """Import an RSS or Atom file you saved from an official feed. Duplicates collapse to one event."""
    with session_scope() as db:
        try:
            r = rs.import_news(db, file.read_bytes(), feed)
        except rs.ResearchError as exc:
            _fail(str(exc))
    typer.echo(
        f"{r.new} new, {r.merged} already known from another feed, {r.repeat} repeats, {r.rejected} rejected"
    )


@news_app.command("list")
def news_list(limit: int = 20) -> None:
    with session_scope() as db:
        for n in rs.list_news(db, limit):
            typer.echo(f"{n.id:>5}  {n.published_at:%Y-%m-%d}  [{','.join(n.sources)}]  {n.title}")


@ipo_research_app.command("fact")
def ipo_fact(
    ref: str, fact: str, value: str, source: Annotated[str, typer.Option(help="RHP page or document.")]
) -> None:
    """Record one checklist fact read from the RHP; `min_lot_value` also sets the minimum application."""
    with session_scope() as db:
        try:
            fid = rs.record_ipo_fact(db, ref, fact, value, source)
        except rs.ResearchError as exc:
            _fail(str(exc))
    typer.echo(f"fact recorded (evidence IPO_FACT {fid})")


@ipo_research_app.command("checklist")
def ipo_checklist(ref: str) -> None:
    with session_scope() as db:
        facts = rs.recorded_facts(db, ref)
    for f in REQUIRED_FACTS:
        typer.echo(f"{f:<24} {facts.get(f, 'MISSING')}")
    typer.echo("complete" if not missing_facts(facts) else f"missing: {', '.join(missing_facts(facts))}")


@ipo_research_app.command("classify")
def ipo_classify(ref: str, research_class: str, reason: Annotated[str, typer.Option(help="Why.")]) -> None:
    """Your decision, with its reason. ALLOWED needs a mainboard IPO and a complete checklist."""
    with session_scope() as db:
        try:
            rs.classify_ipo(db, ref, research_class.upper(), reason)
        except rs.ResearchError as exc:
            _fail(str(exc))
    typer.echo(f"{ref}: {research_class.upper()}")


@claim_app.command("add")
def claim_add(
    subject: str,
    claim: str,
    kind: Annotated[str, typer.Option(help="NEWS, IPO_FACT, FUNDAMENTAL or HOLDINGS")],
    evidence_id: Annotated[int, typer.Option("--evidence-id")],
) -> None:
    with session_scope() as db:
        try:
            cid = rs.add_claim(db, subject, claim, kind.upper(), evidence_id)
        except rs.ResearchError as exc:
            _fail(str(exc))
    typer.echo(f"claim {cid} recorded")


@claim_app.command("list")
def claim_list(subject: Annotated[str | None, typer.Option()] = None) -> None:
    with session_scope() as db:
        rows = db.execute(
            text(
                "SELECT id, subject, claim, evidence_kind, evidence_id FROM research_claims "
                "WHERE (CAST(:s AS text) IS NULL OR subject = :s) ORDER BY id"
            ),
            {"s": subject},
        ).all()
    for r in rows:
        typer.echo(f"{r.id:>4}  {r.subject}: {r.claim}  [{r.evidence_kind} {r.evidence_id}]")
