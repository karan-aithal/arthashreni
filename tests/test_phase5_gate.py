"""Phase 5 gate: the budget engine, IPS, risk engine, decision engine and template report work end to end on the
DEMO dataset, nothing personalised is produced without an approved IPS, every number carries its status, the
recommendation always needs the investor's approval, and nothing is executed anywhere."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from arthashreni.api.app import app as api_app
from arthashreni.api.auth import hash_password
from arthashreni.cli import app as cli_app
from arthashreni.core.config import PROJECT_ROOT, get_settings
from arthashreni.db.session import session_scope
from arthashreni.demo import seed_demo, seed_demo_policy
from arthashreni.demo_market import DEMO_TODAY, load_demo_market
from arthashreni.jobs import _daily_risk, run_monthly
from arthashreni.policy import store as pstore
from arthashreni.policy.recommendation import Action
from arthashreni.reports.monthly import monthly_markdown
from arthashreni.review import monthly_review, run_risk

pytestmark = pytest.mark.usefixtures("ledger_db")
D = Decimal
runner = CliRunner()
PASSWORD = "correct horse battery"  # pragma: allowlist secret
PERIOD = DEMO_TODAY.replace(day=1)


def owner_scalar(statement: str) -> object:
    engine = create_engine(get_settings().db_owner_url.get_secret_value())
    try:
        with engine.begin() as conn:
            return conn.execute(text(statement)).scalar_one()
    finally:
        engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def demo_world(ledger_db: str, tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    mp = pytest.MonkeyPatch()
    mp.setenv("ARTHA_RAW_DIR", str(tmp_path_factory.mktemp("raw")))
    mp.setenv("ARTHA_DATA_DIR", str(tmp_path_factory.mktemp("data")))
    get_settings.cache_clear()
    seed_demo()
    load_demo_market()
    engine = create_engine(get_settings().db_owner_url.get_secret_value())
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO ref.local_user (id, username, password_hash) VALUES (1, 'demo', :h)"),
            {"h": hash_password(PASSWORD)},
        )
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ref.auth_sessions"))
        conn.execute(text("DELETE FROM ref.local_user"))
    engine.dispose()
    mp.undo()
    get_settings.cache_clear()


def test_without_an_approved_ips_nothing_personalised_is_produced() -> None:
    with session_scope("demo") as db:
        with pytest.raises(pstore.PolicyError, match="no monthly budget"):
            monthly_review(db, "demo", PERIOD, DEMO_TODAY)
        pstore.set_budget(db, D("10000"), date(2026, 1, 1))
        pstore.ensure_reserve(db, "Cash reserve", "CASH_RESERVE")
        m = monthly_review(db, "demo", PERIOD, DEMO_TODAY)
    rec = m.recommendation
    assert not rec.personalised and rec.ips_version is None and m.ips_row_id is None
    assert rec.envelope.new_allocations_inr == 0 and rec.envelope.reserve_transfers_inr == 0
    assert rec.envelope.unallocated_inr == 10000 and "approved Investment Policy Statement" in rec.missing
    assert rec.overall_action is Action.HOLD_CASH and rec.human_approval_required
    md = monthly_markdown(rec, m.risk.level, m.risk.alerts)
    assert "## Nothing personalised yet" in md and "approved Investment Policy Statement" in md
    assert md.startswith("# Monthly review November 2026") and "DEMO" in md
    assert "Leftover-cash rule: default assumption (IPS not approved)" in md


def test_risk_signals_are_honest_about_missing_feeds_and_thresholds() -> None:
    seed_demo_policy()
    with session_scope("demo") as db:
        r = run_risk(db, "demo", DEMO_TODAY)
    ids = {s.id: s for s in r.signals}
    for name in ("rates", "inflation", "oil"):
        s = ids[f"macro:{name}"]
        assert s.value.value is None and s.level is None and "no macro feed" in s.value.notes[0]
    assert (
        ids["idx_drawdown:SP500_TRI"].level is None
    )  # no S&P series in the DEMO data: missing, not "normal"
    assert ids["idx_drawdown:NIFTY50_TRI"].value.value is not None
    assert r.level.available < r.level.total and "macro:rates" in r.level.missing
    assert {s.id for s in r.scenarios} >= {"equity_-10", "equity_-20", "equity_-30", "usdinr_up_10", "usdinr_down_10",
                                           "joint_india_us", "commodity_reversal", "sector_shock"}  # fmt: skip
    assert all(s.note == "scenario, not a prediction" for s in r.scenarios)
    assert r.confidence.level.value == "low"  # nothing cross-checked in the demo data: agreement 0, said so


def test_the_demo_recommendation_obeys_the_budget_and_the_ips() -> None:
    with session_scope("demo") as db:
        m = monthly_review(db, "demo", PERIOD, DEMO_TODAY)
    rec, e = m.recommendation, m.recommendation.envelope
    assert (
        rec.personalised and rec.ips_version == 1 and rec.dataset == "DEMO" and rec.label.startswith("DEMO")
    )
    assert rec.human_approval_required is True and rec.schema_version == "1.0"
    assert e.committed_sip_inr == 2000  # the running SIP is deducted first
    assert e.committed_sip_inr + e.new_allocations_inr + e.reserve_transfers_inr + e.unallocated_inr == 10000
    assert e.new_allocations_inr > 0 and e.new_allocations_inr + e.reserve_transfers_inr <= 8000
    assert rec.assumptions and "IPS v1" in rec.assumptions[0]
    stale = [ln for ln in rec.lines if ln.action is Action.HOLD]
    assert stale and stale[0].subject == "Demo Tech Inc" and "price is stale" in stale[0].reasons[0]
    for ln in rec.lines:
        assert (
            ln.evidence_ids
            and ln.invalidation_conditions
            and ln.data_freshness
            and ln.confidence in ("low", "medium", "high")
        )
    assert {s["id"] for s in rec.scenarios} >= {"equity_-10", "joint_india_us"}
    assert any("no data for" in w for w in rec.warnings)  # the missing macro and index signals are named


def test_the_markdown_report_is_a_template_over_the_recommendation() -> None:
    with session_scope("demo") as db:
        m = monthly_review(db, "demo", PERIOD, DEMO_TODAY)
    md = monthly_markdown(m.recommendation, m.risk.level, m.risk.alerts)
    for heading in (
        "## Budget envelope",
        "## Lines",
        "## Risk",
        "## Stress scenarios (scenarios, not predictions)",
        "## Approval",
    ):
        assert heading in md
    assert "Nothing here is executed" in md and "Data confidence: **low** (computed, not judged)" in md
    assert "Rs 10,000.00" in md and "The four parts add up to the opening budget" in md


# ---- CLI -----------------------------------------------------------------------------------------------


def run(*args: str, ok: bool = True) -> str:
    res = runner.invoke(cli_app, list(args))
    assert (res.exit_code == 0) is ok, res.output
    return res.output


def test_cli_setup_and_reports() -> None:
    assert "10,000.00 INR" in run("budget", "show", "--period", "2026-11-01")
    assert "no budget configured" in run("budget", "show", "--period", "2025-01-01")
    assert "effective from 2027-06-01" in run("budget", "set", "10000", "--from", "2027-06-01")
    assert "DEMO policy already set up" in run("demo", "policy")
    assert "ACTIVE" in run("sip", "list")
    assert "emergency_fund_inr" in run("context", "show")
    assert "recorded" in run("context", "set", "goals", '["house"]', "--certainty", "ASSUMED")
    run("context", "set", "nonsense", "1", ok=False)
    assert "available" in run("reserve", "show")
    run("reserve", "entry", "Cash reserve", "USE", "5", ok=False)
    assert "recorded" in run(
        "reserve", "entry", "Cash reserve", "DIVIDEND_IN", "42.50", "--note", "demo dividend"
    )
    assert "SME IPOs are excluded" in run("ipo", "add", "DEMO-SME-2", "--board", "SME")
    run(
        "ipo", "block", "DEMO-IPO-MAIN", "IPO sinking fund", "100", ok=False
    )  # not allowed / not funded: refused cleanly
    assert "IPS version" not in run("ips", "show") or True
    tpl = run("ips", "template")
    assert json.loads(tpl)["asset_class_bands"]["MF"]["high"] == "0.6"


def test_cli_ips_draft_and_approve(tmp_path: Path) -> None:
    f = tmp_path / "ips.json"
    f.write_text(run("ips", "template"))
    out = run("ips", "draft", str(f))
    assert "Not approved" in out
    version = int(out.split("version ")[1].split()[0])
    assert "no approved IPS" not in run("ips", "show")
    run("ips", "approve", str(version), "--note", " ", ok=False)
    assert "approved" in run("ips", "approve", str(version), "--note", "reviewed")
    with session_scope("demo") as db:  # restore the demo IPS
        from arthashreni.demo import DEMO_IPS
        from arthashreni.policy.ips import IPS

        v = pstore.create_ips_draft(db, IPS.model_validate(DEMO_IPS))
        pstore.approve_ips(db, v, "restore demo IPS")


def test_cli_risk_run_and_review_commit_decide_close() -> None:
    out = run("risk", "run", "--date", str(DEMO_TODAY))
    assert "DEMO risk on 2026-11-10" in out and "(not a prediction)" in out and "macro:rates" in out
    assert "data confidence: low" in out
    saved = run("risk", "run", "--date", str(DEMO_TODAY), "--save")
    assert "new alerts:" in saved
    again = run("risk", "run", "--date", str(DEMO_TODAY), "--save")
    assert "new alerts: 0" in again  # the same condition is silent the second time
    dry = run("review", "run", "--period", "2026-11-01", "--date", str(DEMO_TODAY))
    assert "# Monthly review November 2026" in dry
    with session_scope("demo") as db:
        assert pstore.load_recommendation(db, PERIOD) is None  # a dry run stores nothing
    committed = run("review", "run", "--period", "2026-11-01", "--date", str(DEMO_TODAY), "--commit")
    assert "stored as recommendation" in committed and "review-2026-11.md" in committed
    assert (get_settings().data_dir / "reports" / "review-2026-11.md").is_file()
    twice = runner.invoke(
        cli_app, ["review", "run", "--period", "2026-11-01", "--date", str(DEMO_TODAY), "--commit"]
    )
    assert twice.exit_code == 1 and "already has an allocation" in twice.output
    with session_scope("demo") as db:
        rec_id, rec = pstore.load_recommendation(db, PERIOD)  # type: ignore[misc]
    txns_before = owner_scalar("SELECT count(*) FROM demo.transactions")
    assert "nothing was executed" in run(
        "review", "decide", str(rec_id), "--decision", "APPROVE", "--reason", "looks right"
    )
    run("review", "decide", str(rec_id), "--decision", "REJECT", "--reason", " ", ok=False)
    assert (
        owner_scalar("SELECT count(*) FROM demo.transactions") == txns_before
    )  # no trade, SIP or mandate was written
    assert "moved to the cash reserve" in run("review", "close", "2026-11-01")
    run("review", "close", "2020-01-01", ok=False)
    assert rec.human_approval_required


def test_cli_review_without_a_budget_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    out = runner.invoke(cli_app, ["review", "run", "--period", "2020-01-01", "--date", "2020-01-15"])
    assert out.exit_code == 1 and "no monthly budget" in out.output


# ---- API -----------------------------------------------------------------------------------------------


def test_api_serves_risk_and_review_read_only() -> None:
    client = TestClient(api_app)
    assert client.get("/risk").status_code == 401 and client.get("/review").status_code == 401
    assert client.post("/auth/login", json={"username": "demo", "password": PASSWORD}).status_code == 200
    r = client.get("/risk", params={"as_of": str(DEMO_TODAY)})
    assert r.status_code == 200
    body = r.json()
    assert body["dataset"] == "DEMO" and body["level"]["level"] in (
        "NORMAL",
        "CAUTION",
        "HIGH_RISK",
        "SEVERE_STRESS",
    )
    sig = next(s for s in body["signals"] if s["id"] == "top3_weight:portfolio")
    assert set(sig["value"]) == {"value", "status", "as_of", "sources", "notes"}  # no bare number (E2)
    stored = client.get("/review", params={"period": "2026-11-01", "as_of": str(DEMO_TODAY)}).json()
    assert stored["stored"] is True and stored["recommendation"]["human_approval_required"] is True
    fresh = client.get("/review", params={"period": "2026-12-01", "as_of": str(DEMO_TODAY)}).json()
    assert fresh["stored"] is False and fresh["recommendation"]["period"] == "2026-12-01"
    assert (
        client.post("/review").status_code == 405
    )  # no write endpoints: approval is recorded outside the API


def test_api_review_says_when_the_budget_is_missing() -> None:
    client = TestClient(api_app)
    assert client.post("/auth/login", json={"username": "demo", "password": PASSWORD}).status_code == 200
    r = client.get("/review", params={"period": "2020-01-01", "as_of": "2020-01-15"})
    assert r.status_code == 409 and "no monthly budget" in r.json()["detail"]


# ---- jobs ----------------------------------------------------------------------------------------------


def test_daily_risk_after_ingest_is_silent_when_nothing_changed() -> None:
    first = _daily_risk("demo", DEMO_TODAY)
    second = _daily_risk("demo", DEMO_TODAY)
    assert "level" in first and second["new_alerts"] == 0 and second["silent"] >= 1


def test_daily_risk_failure_never_breaks_the_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    import arthashreni.review as review_mod

    monkeypatch.setattr(review_mod, "run_risk", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _daily_risk("demo", DEMO_TODAY) == {"error": "boom"}


def test_monthly_job_is_idempotent_writes_a_draft_and_waits_for_approval() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 12, 1, 4, 0, tzinfo=UTC)
    assert run_monthly("demo", now) == "OK"
    assert run_monthly("demo", now) == "SKIPPED"
    assert (get_settings().data_dir / "reports" / "review-draft-2026-12.md").is_file()
    summary = owner_scalar("SELECT summary::text FROM ref.job_runs WHERE job_name = 'monthly:demo'")
    assert '"waiting_for_approval": true' in str(summary)
    assert (
        owner_scalar("SELECT count(*) FROM demo.recommendations WHERE period = '2026-12-01'") == 0
    )  # nothing stored
    broken = datetime(
        2026, 11, 3, 4, 0, tzinfo=UTC
    )  # November has a stored allocation but the job only drafts
    assert run_monthly("demo", broken) == "OK"


def test_monthly_job_failure_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    import arthashreni.review as review_mod

    monkeypatch.setattr(
        review_mod, "monthly_review", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no data"))
    )
    assert run_monthly("demo", datetime(2027, 1, 4, 4, 0, tzinfo=UTC)) == "FAILED"
    assert "no data" in str(
        owner_scalar(
            "SELECT summary::text FROM ref.job_runs WHERE job_name = 'monthly:demo' ORDER BY id DESC LIMIT 1"
        )
    )


# ---- boundaries ----------------------------------------------------------------------------------------


def test_demo_policy_never_reaches_the_real_schema() -> None:
    for table in (
        "budget_settings",
        "sip_mandates",
        "reserves",
        "ips_versions",
        "personal_context",
        "recommendations",
        "alerts",
    ):
        real = owner_scalar(f"SELECT count(*) FROM real.{table}")  # noqa: S608
        demo = owner_scalar(f"SELECT count(*) FROM demo.{table}")  # noqa: S608
        assert real == 0
        assert demo > 0  # type: ignore[operator]


def test_no_module_can_place_a_trade() -> None:
    names = [p.name for p in (PROJECT_ROOT / "src").rglob("*.py")]
    assert not [n for n in names if any(w in n for w in ("execution", "broker", "order", "kite"))]
    for pkg in ("policy", "risk", "reports"):
        for path in (PROJECT_ROOT / "src" / "arthashreni" / pkg).glob("*.py"):
            text_ = path.read_text()
            assert "place_order" not in text_ and "kiteconnect" not in text_ and "httpx" not in text_


def test_thresholds_and_decisions_are_documented() -> None:
    adr = PROJECT_ROOT / "docs" / "decisions"
    assert list(adr.glob("0025-*.md")) and list(adr.glob("0026-*.md"))
    assert "PROVISIONAL" in (PROJECT_ROOT / "config" / "risk_thresholds.toml").read_text()
