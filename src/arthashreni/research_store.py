"""Database side of Phase 7: fund holdings, news events, IPO checklist and classification, claims.

Service layer (like `portfolio` and `review`): the pure rules live in `arthashreni.research`. Everything
imported here is data, never an instruction (invariant 11); nothing here calls the network."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.dq.measured import QualityStatus
from arthashreni.research import ipo as ipo_rules
from arthashreni.research.holdings import HoldingsError, holdings_status, parse_holdings_csv
from arthashreni.research.news import FeedError, parse_feed

HOLDINGS_SOURCE = "fund_disclosures"
EVIDENCE_TABLES = {  # fixed map: the table name is never taken from input
    "NEWS": "news_events",
    "IPO_FACT": "ipo_research",
    "FUNDAMENTAL": "fundamentals",
    "HOLDINGS": "fund_holdings",
}


class ResearchError(Exception):
    pass


# ---- fund holdings ---------------------------------------------------------------------------------------


def import_holdings(db: Session, fund_asset_id: int, valid_at: date, csv_text: str) -> int:
    """Store one disclosure file for a fund. The whole file or nothing; the same file twice is an error."""
    try:
        rows = parse_holdings_csv(csv_text)
    except HoldingsError as exc:
        raise ResearchError(f"holdings file rejected: {exc}") from exc
    sha = hashlib.sha256(csv_text.encode()).hexdigest()
    seen = db.execute(
        text(
            "SELECT 1 FROM fund_holdings "
            "WHERE fund_asset_id = :f AND valid_at = :v AND file_sha256 = :h LIMIT 1"
        ),
        {"f": fund_asset_id, "v": valid_at, "h": sha},
    ).first()
    if seen:
        raise ResearchError("this exact file was already imported for this fund and date")
    for r in rows:
        db.execute(
            text(
                """INSERT INTO fund_holdings (fund_asset_id, valid_at, underlying_key, underlying_name,
                       weight, source_id, file_sha256) VALUES (:f, :v, :k, :n, :w, :s, :h)"""
            ),
            {
                "f": fund_asset_id,
                "v": valid_at,
                "k": r.key,
                "n": r.name,
                "w": r.weight,
                "s": HOLDINGS_SOURCE,
                "h": sha,
            },
        )
    return len(rows)


@dataclass(frozen=True)
class LoadedHoldings:
    weights: dict[int, dict[str, Decimal]]  # fund asset id -> underlying key -> weight
    stale: frozenset[int]


def load_fund_holdings(db: Session, today: date, known_at: datetime | None = None) -> LoadedHoldings:
    """Per fund: the newest disclosure date on or before `today`, and within it the newest file known by
    `known_at` (E4: a restated file is a new file; nothing is overwritten)."""
    known_at = known_at or datetime.now(UTC)
    rows = db.execute(
        text(
            """WITH latest AS (
                   SELECT DISTINCT ON (fund_asset_id) fund_asset_id, valid_at, file_sha256
                   FROM fund_holdings WHERE valid_at <= :d AND recorded_at <= :k
                   ORDER BY fund_asset_id, valid_at DESC, recorded_at DESC, id DESC)
               SELECT h.fund_asset_id, h.valid_at, h.underlying_key, h.weight
               FROM fund_holdings h JOIN latest l ON l.fund_asset_id = h.fund_asset_id
                AND l.valid_at = h.valid_at AND l.file_sha256 = h.file_sha256
               WHERE h.quality_status <> 'quarantined'"""
        ),
        {"d": today, "k": known_at},
    ).all()
    weights: dict[int, dict[str, Decimal]] = {}
    stale: set[int] = set()
    for r in rows:
        weights.setdefault(r.fund_asset_id, {})[r.underlying_key] = r.weight
        if holdings_status(r.valid_at, today) is not QualityStatus.OK:
            stale.add(r.fund_asset_id)
    return LoadedHoldings(weights, frozenset(stale))


# ---- news ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NewsImport:
    new: int
    merged: int  # the same event, already known from another feed
    repeat: int  # already known from this very feed
    rejected: int


def import_news(db: Session, body: bytes, feed_id: str) -> NewsImport:
    try:
        items, rejected = parse_feed(body)
    except FeedError as exc:
        raise ResearchError(f"feed rejected: {exc}") from exc
    new = merged = repeat = 0
    for it in items:
        row = db.execute(
            text(
                """INSERT INTO news_events (dedupe_key, title, summary, url, published_at, sources)
                   VALUES (:k, :t, :s, :u, :p, ARRAY[:f])
                   ON CONFLICT (dedupe_key) DO UPDATE SET last_seen_at = now(),
                       seen_count = news_events.seen_count + 1, sources = news_events.sources || :f
                   WHERE NOT (:f = ANY(news_events.sources))
                   RETURNING (xmax = 0) AS inserted"""
            ),
            {
                "k": it.dedupe_key,
                "t": it.title,
                "s": it.summary,
                "u": it.url,
                "p": it.published_at,
                "f": feed_id,
            },
        ).first()
        if row is None:
            repeat += 1
        elif row.inserted:
            new += 1
        else:
            merged += 1
    return NewsImport(new, merged, repeat, rejected)


def list_news(db: Session, limit: int = 50) -> list[Any]:
    return list(
        db.execute(
            text(
                "SELECT id, published_at, title, sources, url FROM news_events "
                "ORDER BY published_at DESC, id DESC LIMIT :n"
            ),
            {"n": limit},
        ).all()
    )


# ---- IPO checklist and classification --------------------------------------------------------------------


def _ipo_row(db: Session, ref: str) -> Any:
    row = db.execute(
        text("SELECT board, status FROM ipo_applications WHERE ipo_ref = :r"), {"r": ref}
    ).first()
    if row is None:
        raise ResearchError(f"no IPO {ref!r}; add it first (ipo add)")
    return row


def recorded_facts(db: Session, ref: str) -> dict[str, str]:
    """Newest value of each fact."""
    rows = db.execute(
        text(
            "SELECT DISTINCT ON (fact) fact, value FROM ipo_research "
            "WHERE ipo_ref = :r ORDER BY fact, id DESC"
        ),
        {"r": ref},
    ).all()
    return {r.fact: r.value for r in rows}


def record_ipo_fact(db: Session, ref: str, fact: str, value: str, source_doc: str) -> int:
    board, status = _ipo_row(db, ref)
    if fact not in ipo_rules.REQUIRED_FACTS:
        raise ResearchError(f"unknown fact {fact!r}; known: {', '.join(ipo_rules.REQUIRED_FACTS)}")
    if not value.strip() or not source_doc.strip():
        raise ResearchError("a fact needs a value and the document it came from")
    lot: Decimal | None = None
    if fact == "min_lot_value":
        try:
            lot = ipo_rules.parse_min_lot_value(value)
        except ValueError as exc:
            raise ResearchError(str(exc)) from exc
    fid = int(
        db.execute(
            text(
                "INSERT INTO ipo_research (ipo_ref, fact, value, source_doc) "
                "VALUES (:r, :f, :v, :d) RETURNING id"
            ),
            {"r": ref, "f": fact, "v": value.strip(), "d": source_doc.strip()},
        ).scalar_one()
    )
    if lot is not None and board == "MAINBOARD" and status == "PLANNED":
        db.execute(
            text("UPDATE ipo_applications SET min_lot_value_inr = :m WHERE ipo_ref = :r"),
            {"m": lot, "r": ref},
        )
    # A new fact changes what the classification was based on: the investor must classify again. The
    # ALLOWED row stays in ipo_classifications; NOT_ALLOWED already blocks, so it is left alone.
    db.execute(
        text(
            "UPDATE ipo_applications SET research_class = 'UNRATED' "
            "WHERE ipo_ref = :r AND research_class = 'ALLOWED' AND status = 'PLANNED'"
        ),
        {"r": ref},
    )
    return fid


def classify_ipo(db: Session, ref: str, research_class: str, reason: str) -> None:
    """The investor's call, recorded with its reason. ALLOWED needs a mainboard IPO and a full checklist."""
    board, status = _ipo_row(db, ref)
    if not reason.strip():
        raise ResearchError("a classification needs a reason")
    if status != "PLANNED" and research_class == "ALLOWED":
        raise ResearchError(f"{ref} is {status}; only a PLANNED IPO can be allowed")
    refusal = ipo_rules.classification_refusal(board, research_class, recorded_facts(db, ref))
    if refusal:
        raise ResearchError(refusal)
    db.execute(
        text("INSERT INTO ipo_classifications (ipo_ref, research_class, reason) VALUES (:r, :c, :why)"),
        {"r": ref, "c": research_class, "why": reason.strip()},
    )
    db.execute(
        text("UPDATE ipo_applications SET research_class = :c WHERE ipo_ref = :r"),
        {"c": research_class, "r": ref},
    )


# ---- claims ----------------------------------------------------------------------------------------------


def add_claim(db: Session, subject: str, claim: str, evidence_kind: str, evidence_id: int) -> int:
    """A research statement that points at stored evidence. No evidence, no claim (invariant 6)."""
    table = EVIDENCE_TABLES.get(evidence_kind)
    if table is None:
        raise ResearchError(f"evidence kind must be one of {', '.join(EVIDENCE_TABLES)}")
    if not subject.strip() or not claim.strip():
        raise ResearchError("a claim needs a subject and text")
    found = db.execute(text(f"SELECT 1 FROM {table} WHERE id = :i"), {"i": evidence_id}).first()  # noqa: S608
    if found is None:  # table comes from the fixed map above
        raise ResearchError(f"no {evidence_kind} evidence with id {evidence_id}")
    return int(
        db.execute(
            text(
                "INSERT INTO research_claims (subject, claim, evidence_kind, evidence_id) "
                "VALUES (:s, :c, :k, :e) RETURNING id"
            ),
            {"s": subject.strip(), "c": claim.strip(), "k": evidence_kind, "e": evidence_id},
        ).scalar_one()
    )
