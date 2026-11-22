"""Phase 7 gate: fund-holdings overlap reconciles on a fixture, duplicate news collapses to one event, and IPO
eligibility respects the sinking fund, the research checklist and the SME exclusion. Also: restated files are new
rows, stale holdings travel as `stale`, claims need stored evidence, research tables are append-only and the
two datasets stay apart."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from arthashreni import research_store as rs
from arthashreni.cli import app as cli_app
from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope
from arthashreni.demo import seed_demo
from arthashreni.demo_market import DEMO_TODAY, load_demo_market
from arthashreni.dq.measured import QualityStatus
from arthashreni.ledger.assets import resolve_asset
from arthashreni.policy import store as pstore
from arthashreni.portfolio import build_report
from arthashreni.research.ipo import REQUIRED_FACTS

pytestmark = pytest.mark.usefixtures("ledger_db")
D = Decimal
runner = CliRunner()
FUND, ETF, STOCK = "ZZDEMOMF0001", "ZZDEMOETF001", "ZZDEMOEQ0001"
CURRENT = date(2026, 9, 30)  # the month end that must be public on DEMO_TODAY (10 Nov 2026)

FUND_CSV = f"isin,name,pct_to_nav\n{STOCK},Demo Stock Co,40\nINE002B01012,Beta Ltd,35.5\n,TREPS,24.5\n"
ETF_CSV = f"isin,name,pct_to_nav\n{STOCK},Demo Stock Co,25\nINE003C01013,Gamma Ltd,50\n,TREPS,25\n"

JULY_ETF_CSV = f"isin,name,pct_to_nav\n{STOCK},Demo Stock Co,26\nINE003C01013,Gamma Ltd,50\n,TREPS,24\n"
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>RBI keeps repo rate unchanged</title><link>https://rbi.example/a</link>
 <description>Policy rate held.</description><pubDate>Wed, 05 Nov 2026 10:00:00 +0530</pubDate></item>
<item><title>SEBI tightens IPO disclosure</title><link>https://sebi.example/1</link>
 <pubDate>Thu, 06 Nov 2026 11:00:00 +0530</pubDate></item>
<item><title>Broken</title><link>https://x.example/</link></item></channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>RBI keeps repo-rate UNCHANGED!</title><link href="https://other.example/x"/>
<updated>2026-11-05T04:35:00Z</updated></entry></feed>"""


@pytest.fixture(scope="module", autouse=True)
def world(ledger_db: str, tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    mp = pytest.MonkeyPatch()
    mp.setenv("ARTHA_RAW_DIR", str(tmp_path_factory.mktemp("raw")))
    get_settings.cache_clear()
    seed_demo()
    load_demo_market()
    yield
    mp.undo()
    get_settings.cache_clear()


def asset_id(ref: str) -> int:
    with session_scope("demo") as db:
        return resolve_asset(db, "demo", ref).id


def count(table: str, dataset: str = "demo") -> int:
    with session_scope(dataset) as db:  # type: ignore[arg-type]
        return int(db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


# ---- gate 1: overlap reconciles on a fixture ------------------------------------------------------------


def test_overlap_reconciles_from_stored_files() -> None:
    f, e = asset_id(FUND), asset_id(ETF)
    with session_scope("demo") as db:
        assert rs.import_holdings(db, f, CURRENT, FUND_CSV) == 3
        assert rs.import_holdings(db, e, CURRENT, ETF_CSV) == 3
    with session_scope("demo") as db:
        loaded = rs.load_fund_holdings(db, DEMO_TODAY)
    assert loaded.stale == frozenset()
    assert sum(loaded.weights[f].values()) == D(1) and sum(loaded.weights[e].values()) == D(1)
    from arthashreni.analytics.allocation import overlap

    # Demo Stock min(40,25)=25 + TREPS min(24.5,25)=24.5 -> 49.5%, worked out by hand
    m = overlap(loaded.weights[f], loaded.weights[e], datetime(2026, 11, 10, tzinfo=UTC))
    assert m.value == D("0.495") and m.status is QualityStatus.OK


def test_rejected_duplicate_and_restated_files() -> None:
    f = asset_id(FUND)
    before = count("fund_holdings")
    with session_scope("demo") as db, pytest.raises(rs.ResearchError, match="rejected: weights add up"):
        rs.import_holdings(db, f, CURRENT, f"isin,name,pct_to_nav\n{STOCK},X,50\n")
    with session_scope("demo") as db, pytest.raises(rs.ResearchError, match="already imported"):
        rs.import_holdings(db, f, CURRENT, FUND_CSV)
    assert count("fund_holdings") == before  # nothing partial was stored
    between = datetime.now(UTC)
    restated = f"isin,name,pct_to_nav\n{STOCK},Demo Stock Co,60\n,TREPS,40\n"
    with session_scope("demo") as db:
        rs.import_holdings(db, f, CURRENT, restated)  # same disclosure date, corrected file: new rows
    with session_scope("demo") as db:
        now = rs.load_fund_holdings(db, DEMO_TODAY).weights[f]
        then = rs.load_fund_holdings(db, DEMO_TODAY, known_at=between).weights[f]
    assert now == {STOCK: D("0.6"), "name:treps": D("0.4")}
    assert then[STOCK] == D("0.4")  # what we knew before the restatement (E4)


def test_look_through_in_the_report_uses_files_and_flags_stale_ones() -> None:
    with session_scope("demo") as db:
        r = build_report(db, "demo", DEMO_TODAY)
    lt = r.look_through
    assert "Demo Growth Equity Fund - Direct Growth" not in lt.without_data  # the fund has a file now
    assert STOCK in lt.exposure and lt.exposure[STOCK].status is not QualityStatus.MISSING
    old = date(2026, 7, 31)  # two month ends behind: stale on 10 Nov
    with session_scope("demo") as db:
        rs.import_holdings(db, asset_id(ETF), old, JULY_ETF_CSV)
    with session_scope("demo") as db:  # a newer file exists for the ETF, so the old one is ignored
        assert rs.load_fund_holdings(db, DEMO_TODAY).stale == frozenset()
    with session_scope("demo") as db:  # looking back before the September file: only the July one is known
        loaded = rs.load_fund_holdings(db, date(2026, 9, 29))
    assert asset_id(ETF) in loaded.stale and asset_id(FUND) not in loaded.weights


# ---- gate 2: duplicate news collapses to one event ------------------------------------------------------


def test_duplicate_news_collapses_to_one_event() -> None:
    with session_scope("demo") as db:
        first = rs.import_news(db, RSS, "rbi_press")
        again = rs.import_news(db, RSS, "rbi_press")
        other = rs.import_news(db, ATOM, "sebi_feed")
    assert (first.new, first.merged, first.repeat, first.rejected) == (2, 0, 0, 1)
    assert (again.new, again.merged, again.repeat) == (0, 0, 2)  # same feed again changes nothing
    assert (other.new, other.merged) == (0, 1)  # same headline, same IST day, other feed
    assert count("news_events") == 2
    with session_scope("demo") as db:
        row = db.execute(
            text("SELECT sources, seen_count, url FROM news_events WHERE title LIKE 'RBI%'")
        ).one()
        assert sorted(row.sources) == ["rbi_press", "sebi_feed"] and row.seen_count == 2
        assert row.url == "https://rbi.example/a"  # the first sighting's link is kept
        with pytest.raises(rs.ResearchError, match="feed rejected"):
            rs.import_news(db, b"<html/>", "x")


# ---- gate 3: IPO eligibility respects sinking fund, checklist and SME exclusion -----------------------------


def fund_reserve(amount: str) -> int:
    with session_scope("demo") as db:
        rid = pstore.ensure_reserve(db, "IPO fund", "IPO_SINKING_FUND")
        pstore.reserve_add(db, rid, "TRANSFER_IN", D(amount), date(2026, 11, 1), note="test")
    return rid


def test_ipo_needs_checklist_classification_and_money() -> None:
    rid = fund_reserve("10000")
    with session_scope("demo") as db:
        pstore.add_ipo(db, "ACME", "MAINBOARD", "rhp-acme.pdf", None, "UNRATED")
        with pytest.raises(pstore.PolicyError, match="minimum lot value is not read"):
            pstore.ipo_block(db, "ACME", rid, D("15000"), DEMO_TODAY)
    with session_scope("demo") as db, pytest.raises(rs.ResearchError, match="checklist incomplete"):
        rs.classify_ipo(db, "ACME", "ALLOWED", "looks fine")
    with session_scope("demo") as db:
        for fact in REQUIRED_FACTS:
            value = "14,800" if fact == "min_lot_value" else "read"
            rs.record_ipo_fact(db, "ACME", fact, value, "RHP p.12")
        assert rs.recorded_facts(db, "ACME")["min_lot_value"] == "14,800"
        assert db.execute(
            text("SELECT min_lot_value_inr FROM ipo_applications WHERE ipo_ref='ACME'")
        ).scalar_one() == D(14800)
    with session_scope("demo") as db:  # complete checklist but still unrated: cannot apply
        with pytest.raises(pstore.PolicyError, match="UNRATED, not ALLOWED"):
            pstore.ipo_block(db, "ACME", rid, D("15000"), DEMO_TODAY)
    with session_scope("demo") as db, pytest.raises(rs.ResearchError, match="needs a reason"):
        rs.classify_ipo(db, "ACME", "ALLOWED", "  ")
    with session_scope("demo") as db:
        rs.classify_ipo(db, "ACME", "ALLOWED", "reviewed RHP risk factors")
    assert count("ipo_classifications") == 1
    with session_scope("demo") as db:  # allowed, but the reserve (10,000) does not cover 14,800
        with pytest.raises(pstore.PolicyError, match="does not cover the minimum application"):
            pstore.ipo_block(db, "ACME", rid, D("15000"), DEMO_TODAY)
        pstore.reserve_add(db, rid, "TRANSFER_IN", D("5000"), date(2026, 11, 5), note="top up")
        pstore.ipo_block(db, "ACME", rid, D("15000"), DEMO_TODAY)  # 15,000 reserve covers it now
    with session_scope("demo") as db, pytest.raises(rs.ResearchError, match="APPLIED; only a PLANNED"):
        rs.classify_ipo(db, "ACME", "ALLOWED", "again")


def test_a_changed_fact_withdraws_the_classification_until_reclassified() -> None:
    rid = fund_reserve("20000")
    with session_scope("demo") as db:
        pstore.add_ipo(db, "REVISED", "MAINBOARD", "rhp.pdf", None, "UNRATED")
        for fact in REQUIRED_FACTS:
            rs.record_ipo_fact(db, "REVISED", fact, "14,800" if fact == "min_lot_value" else "read", "RHP")
        rs.classify_ipo(db, "REVISED", "ALLOWED", "reviewed risk factors")
    with session_scope("demo") as db:  # the RHP is amended: a revised lot size
        rs.record_ipo_fact(db, "REVISED", "min_lot_value", "15,200", "RHP addendum")
        assert (
            db.execute(
                text("SELECT research_class FROM ipo_applications WHERE ipo_ref='REVISED'")
            ).scalar_one()
            == "UNRATED"
        )
    with session_scope("demo") as db, pytest.raises(pstore.PolicyError, match="UNRATED, not ALLOWED"):
        pstore.ipo_block(db, "REVISED", rid, D("16000"), DEMO_TODAY)
    assert count("ipo_classifications") >= 1  # the old ALLOWED row is history, not deleted
    with session_scope("demo") as db:
        rs.classify_ipo(db, "REVISED", "ALLOWED", "re-read the addendum")
        pstore.ipo_block(db, "REVISED", rid, D("16000"), DEMO_TODAY)


def test_a_fact_does_not_loosen_a_not_allowed_classification() -> None:
    with session_scope("demo") as db:
        pstore.add_ipo(db, "NOGO", "MAINBOARD", "rhp.pdf", None, "UNRATED")
        rs.classify_ipo(db, "NOGO", "NOT_ALLOWED", "weak financials")
        rs.record_ipo_fact(db, "NOGO", "issue_size", "500 cr", "RHP")
        assert (
            db.execute(text("SELECT research_class FROM ipo_applications WHERE ipo_ref='NOGO'")).scalar_one()
            == "NOT_ALLOWED"
        )


def test_sme_ipos_are_never_eligible() -> None:
    rid = fund_reserve("10000")
    with session_scope("demo") as db:
        pstore.add_ipo(db, "SMECO", "SME", None, D("150000"), "UNRATED")
        assert (
            db.execute(text("SELECT status FROM ipo_applications WHERE ipo_ref='SMECO'")).scalar_one()
            == "EXCLUDED"
        )
        for fact in REQUIRED_FACTS:
            rs.record_ipo_fact(db, "SMECO", fact, "1" if fact == "min_lot_value" else "x", "RHP")
    with session_scope("demo") as db, pytest.raises(rs.ResearchError, match="SME"):
        rs.classify_ipo(db, "SMECO", "ALLOWED", "tempting")
    with session_scope("demo") as db, pytest.raises(pstore.PolicyError, match="excluded"):
        pstore.ipo_block(db, "SMECO", rid, D("150000"), DEMO_TODAY)
    with session_scope("demo") as db:  # a fact does not change an excluded IPO's minimum
        assert db.execute(
            text("SELECT min_lot_value_inr FROM ipo_applications WHERE ipo_ref='SMECO'")
        ).scalar_one() == D(150000)
        rs.classify_ipo(db, "SMECO", "NOT_ALLOWED", "SME board")  # saying no is always fine


def test_ipo_fact_validation() -> None:
    with session_scope("demo") as db:
        pstore.add_ipo(db, "BETA", "MAINBOARD", None, None, "UNRATED")
        for args, msg in [
            (("NOPE", "issue_size", "1", "d"), "no IPO"),
            (("BETA", "colour", "blue", "d"), "unknown fact"),
            (("BETA", "issue_size", " ", "d"), "needs a value"),
            (("BETA", "issue_size", "1", ""), "needs a value"),
            (("BETA", "min_lot_value", "-4", "d"), "positive"),
            (("BETA", "min_lot_value", "lots", "d"), "not a number"),
        ]:
            with db.begin_nested(), pytest.raises(rs.ResearchError, match=msg):
                rs.record_ipo_fact(db, *args)
        with db.begin_nested(), pytest.raises(rs.ResearchError, match="not a classification"):
            rs.classify_ipo(db, "BETA", "UNRATED", "x")


# ---- claims, append-only tables, dataset isolation -----------------------------------------------------


def test_claims_need_stored_evidence() -> None:
    with session_scope("demo") as db:
        nid = int(db.execute(text("SELECT id FROM news_events ORDER BY id LIMIT 1")).scalar_one())
        cid = rs.add_claim(db, "RBI", "policy rate unchanged", "NEWS", nid)
        for kind, eid, msg in [("NEWS", 999999, "no NEWS evidence"), ("GOSSIP", 1, "evidence kind")]:
            with db.begin_nested(), pytest.raises(rs.ResearchError, match=msg):
                rs.add_claim(db, "RBI", "x", kind, eid)
        with db.begin_nested(), pytest.raises(rs.ResearchError, match="subject and text"):
            rs.add_claim(db, " ", "x", "NEWS", nid)
        hid = int(db.execute(text("SELECT id FROM fund_holdings LIMIT 1")).scalar_one())
        rs.add_claim(db, "Demo fund", "holds Demo Stock", "HOLDINGS", hid)
        fid = int(db.execute(text("SELECT id FROM ipo_research LIMIT 1")).scalar_one())
        rs.add_claim(db, "ACME", "lot size", "IPO_FACT", fid)
    assert cid > 0 and count("research_claims") == 3


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("fund_holdings", "weight"),
        ("ipo_research", "value"),
        ("ipo_classifications", "reason"),
        ("research_claims", "claim"),
    ],
)
def test_research_history_is_append_only(table: str, column: str) -> None:
    stmts = (f"UPDATE {table} SET {column} = {column}", f"DELETE FROM {table}", f"TRUNCATE {table}")  # noqa: S608
    for stmt in stmts:
        with (
            session_scope("demo") as db,
            pytest.raises(Exception, match="append-only|not allowed|permission"),
        ):
            db.execute(text(stmt))


def test_demo_research_never_reaches_the_real_dataset() -> None:
    for table in ("fund_holdings", "news_events", "ipo_research", "ipo_classifications", "research_claims"):
        assert count(table, "real") == 0


# ---- CLI ---------------------------------------------------------------------------------------------------


def test_cli_end_to_end(tmp_path: Path) -> None:
    def run(*args: str) -> str:
        res = runner.invoke(cli_app, list(args))
        assert res.exit_code == 0, res.output
        return res.output

    h = tmp_path / "etf.csv"
    h.write_text(ETF_CSV.replace("Demo Stock Co,25", "Demo Stock Co,20").replace("TREPS,25", "TREPS,30"))
    assert "3 holdings stored" in run("holdings", "import", ETF, str(h), "--as-of", "2026-10-31")
    out = run("holdings", "overlap", FUND, ETF, "--date", "2026-11-10")
    # fund file 60 stock / 40 TREPS vs newer ETF file 20 stock / 50 Gamma / 30 TREPS: 20 + 30 = 50%
    assert out.strip().endswith("Demo India ETF: 50.00% [ok]")
    bad = runner.invoke(cli_app, ["holdings", "import", ETF, str(h), "--as-of", "2026-10-31"])
    assert bad.exit_code == 1 and "already imported" in bad.output
    f = tmp_path / "feed.xml"
    f.write_bytes(RSS)
    assert "0 new, 0 already known from another feed, 2 repeats, 1 rejected" in run(
        "news", "import", str(f), "--feed", "rbi_press"
    )
    assert "RBI keeps repo rate unchanged" in run("news", "list")
    run("ipo", "add", "GAMMA", "--board", "MAINBOARD")
    out = run("ipo-research", "checklist", "GAMMA")
    assert "min_lot_value" in out and "MISSING" in out and "missing: min_lot_value" in out
    no = runner.invoke(cli_app, ["ipo-research", "classify", "GAMMA", "allowed", "--reason", "hope"])
    assert no.exit_code == 1 and "checklist incomplete" in no.output
    assert "fact recorded" in run(
        "ipo-research", "fact", "GAMMA", "issue_size", "500 cr", "--source", "RHP p.3"
    )
    assert "claim" in run("claim", "add", "GAMMA", "size noted", "--kind", "ipo_fact", "--evidence-id", "1")
    assert "size noted" in run("claim", "list", "--subject", "GAMMA")


def test_cli_overlap_of_stale_files_is_marked_stale(tmp_path: Path) -> None:
    old = tmp_path / "fund_july.csv"
    old.write_text(f"isin,name,pct_to_nav\n{STOCK},Demo Stock Co,50\n,TREPS,50\n")
    res = runner.invoke(cli_app, ["holdings", "import", FUND, str(old), "--as-of", "2026-07-31"])
    assert res.exit_code == 0, res.output
    # on 29 Sep only the July files are known (ETF: stock 26, TREPS 24): 26 + 24 = 50%, but 31 Aug is overdue
    res = runner.invoke(cli_app, ["holdings", "overlap", FUND, ETF, "--date", "2026-09-29"])
    assert res.exit_code == 0, res.output
    assert "50.00% [stale]" in res.output and "out of date" in res.output
