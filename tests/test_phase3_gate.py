"""Phase 3 gate: a provider outage, a schema change and a price spike are each caught and quarantined,
stale data is labelled, and the DEMO market data loads through the real adapters (no network).
Also the CLI and the source-health API."""

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from arthashreni.api.app import app as api
from arthashreni.api.auth import hash_password
from arthashreni.cli import app
from arthashreni.core.config import get_settings
from arthashreni.db.as_of import fx_as_of, price_as_of, valuation_fx
from arthashreni.db.session import session_scope
from arthashreni.demo import seed_demo
from arthashreni.demo_market import DEMO_MARKET_DIR, DEMO_TODAY, FixtureTransport, load_demo_market
from arthashreni.dq.health import source_health
from arthashreni.dq.measured import QualityStatus
from arthashreni.ingestion.sources.contract import Window
from arthashreni.ingestion.sources.ingest import run_daily

runner = CliRunner()
# pragma: allowlist nextline secret
PASSWORD = "correct horse battery"
D = Decimal


@pytest.fixture(scope="module", autouse=True)
def demo_world(ledger_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    mp = pytest.MonkeyPatch()
    mp.setenv("ARTHA_RAW_DIR", str(tmp_path_factory.mktemp("raw")))
    get_settings.cache_clear()
    seed_demo()
    results = load_demo_market()
    assert {r.source_id: r.status for r in results} == {
        "manual_prices": "OK", "amfi_nav": "OK", "sbi_tt_archive": "OK", "fbil_ref": "OK",
        "tiingo_eod": "OK", "sec_companyfacts": "OK",
    }  # fmt: skip
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


def run_only(source: str, transport: httpx.BaseTransport, **kw: object):  # type: ignore[no-untyped-def]
    win = {source: Window(date(2026, 9, 1), DEMO_TODAY)}
    return run_daily("demo", DEMO_TODAY, only={source}, transport=transport, offline=True,
                     sleep=lambda _s: None, force=True, windows=win, **kw)[0]  # type: ignore[arg-type]  # fmt: skip


class Patched(FixtureTransport):
    """The demo fixtures, with one URL answered differently."""

    def __init__(self, needle: str, response: httpx.Response | str) -> None:
        super().__init__()
        self.needle, self.response = needle, response

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.needle in str(request.url):
            if isinstance(self.response, str):
                return httpx.Response(200, content=self.response.encode(), request=request)
            return httpx.Response(self.response.status_code, request=request)
        return super().handle_request(request)


def pending() -> list[str]:
    with session_scope("demo") as db:
        return [
            r[0] for r in db.execute(text("SELECT check_name FROM dq_quarantine WHERE resolution='PENDING'"))
        ]


# ---- DEMO data ------------------------------------------------------------------------------------


def test_demo_market_loaded_cleanly_and_is_idempotent() -> None:
    with session_scope("demo") as db:
        counts = {t: db.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in  # noqa: S608
                  ("prices", "fx_rates", "fundamentals", "raw_payloads")}  # fmt: skip
    assert counts["fx_rates"] == 44 and counts["fundamentals"] == 3 and counts["prices"] == 150 + 1 + 26
    again = load_demo_market()
    assert all(r.stored == 0 and r.status == "OK" for r in again)
    assert pending() == []  # a clean DEMO load queues nothing


def test_demo_values_read_back_with_status(ledger_db: str) -> None:
    with session_scope("demo") as db:
        aid = db.execute(text("SELECT id FROM ref.assets WHERE ticker='DTECH'")).scalar_one()
        mf = db.execute(text("SELECT id FROM ref.assets WHERE amfi_code='900001'")).scalar_one()
        ok = price_as_of(db, mf, DEMO_TODAY)
        assert (
            ok.status is QualityStatus.OK and ok.as_of.date() == DEMO_TODAY and ok.source_ids == ("amfi_nav",)
        )
        # DTECH's newest close is 9 Nov: current for 9 Nov, stale a few days later.
        assert price_as_of(db, aid, date(2026, 11, 9)).status is QualityStatus.OK
        late = price_as_of(db, aid, date(2026, 11, 13), datetime(2026, 11, 14, tzinfo=UTC))
        assert late.status is QualityStatus.STALE and any("behind" in n for n in late.notes)
        fb = fx_as_of(db, "FBIL_REF", DEMO_TODAY)
        assert fb.status is QualityStatus.OK and fb.value is not None and fb.value > D("80")
        assert valuation_fx(db, DEMO_TODAY).value == fb.value
        tt = fx_as_of(db, "SBI_TT_BUY", DEMO_TODAY)
        assert tt.status is QualityStatus.ESTIMATED and tt.value is not None and tt.value < fb.value


def test_demo_isolation_real_schema_has_no_market_rows() -> None:
    engine = create_engine(get_settings().db_owner_url.get_secret_value())
    with engine.connect() as conn:
        for t in ("prices", "fx_rates", "fundamentals", "raw_payloads", "dq_quarantine"):
            assert conn.execute(text(f"SELECT count(*) FROM real.{t}")).scalar_one() == 0  # noqa: S608
    engine.dispose()


def test_health_after_a_clean_load_does_not_block_the_monthly_review() -> None:
    with session_scope("demo") as db:
        rep = source_health(db, DEMO_TODAY, set())
    assert rep.pending_quarantine == 0 and rep.ledger_differences == 0 and not rep.blocks_monthly_review


# ---- the three gate scenarios ----------------------------------------------------------------------


def test_gate_provider_outage() -> None:
    before = pending()
    r = run_only("tiingo_eod", Patched("api.tiingo.com", httpx.Response(503)))
    assert r.status == "FAILED" and "503" in r.detail
    assert pending() == [*before, "provider_outage"]
    with session_scope("demo") as db:
        health = {s.id: s for s in source_health(db, DEMO_TODAY, {"tiingo_eod"}).sources}["tiingo_eod"]
        assert health.state == "failing" and health.pending_quarantine == 1
        calls = db.execute(
            text("SELECT count(*) FROM ref.source_calls WHERE source_id='tiingo_eod' AND NOT ok")
        ).scalar_one()
        assert calls == 4  # one try plus three retries, each recorded without a URL or symbol
        assert (
            db.execute(text("SELECT count(*) FROM prices WHERE source_id='tiingo_eod'")).scalar_one() == 26
        )  # nothing lost
    other = run_only("fbil_ref", FixtureTransport())
    assert other.status == "OK"  # the rest of the application keeps going
    ok = run_only("tiingo_eod", FixtureTransport())
    assert ok.status == "OK" and "provider_outage" not in pending()  # recovery clears the alert


def test_gate_schema_change() -> None:
    nav = (DEMO_MARKET_DIR / "NAVAll.txt").read_text().replace("Net Asset Value;Date", "NAV;As On")
    r = run_only("amfi_nav", Patched("NAVAll.txt", nav))
    assert r.status == "SCHEMA_DRIFT" and "header changed" in r.detail
    fbil = json.dumps({"rates": {"2026-11-09": {"INR": 94.0}}})  # a new envelope instead of a list
    r2 = run_only("fbil_ref", Patched("api.frankfurter.dev", fbil))
    assert r2.status == "SCHEMA_DRIFT" and r2.stored == 0
    assert pending().count("schema_drift") == 2
    with session_scope("demo") as db:
        bad = db.execute(text("SELECT count(*) FROM raw_payloads WHERE NOT schema_ok")).scalar_one()
        assert bad == 2
        assert (
            db.execute(text("SELECT count(*) FROM fx_rates")).scalar_one() == 44
        )  # no partial garbage stored
    assert (
        run_only("sbi_tt_archive", Patched("sbi-fx-ratekeeper", "DATE,RATE\n2026-11-09,93\n")).status
        == "SCHEMA_DRIFT"
    )


def test_gate_price_spike(tmp_path: Path) -> None:
    rows = json.loads((DEMO_MARKET_DIR / "tiingo_DTECH.json").read_text())
    last = rows[-1]
    spike = {
        **last,
        "date": "2026-11-10T00:00:00.000Z",
        "close": last["close"] * 2,
        "adjClose": last["close"] * 2,
    }
    r = run_only("tiingo_eod", Patched("api.tiingo.com", json.dumps([*rows, spike])))
    assert r.status == "OK" and r.stored == 1 and r.quarantined == 1
    assert "unexplained_jump" in pending()
    with session_scope("demo") as db:
        aid = db.execute(text("SELECT id FROM ref.assets WHERE ticker='DTECH'")).scalar_one()
        m = price_as_of(db, aid, DEMO_TODAY)
        assert m.value == D(str(last["close"])) and m.status is QualityStatus.STALE  # the spike is never used
        assert source_health(
            db, DEMO_TODAY, set()
        ).blocks_monthly_review  # and the review waits for a decision
    # the investor rejects it: logged with a reason, monthly review unblocked
    out = runner.invoke(app, ["quarantine", "list"])
    assert "unexplained_jump" in out.output
    line = next(ln for ln in out.output.splitlines() if "unexplained_jump" in ln)
    qid = int(line.split("#")[1].split()[0])
    assert (
        runner.invoke(
            app, ["quarantine", "reject", str(qid), "--note", "fat-finger in the test fixture"]
        ).exit_code
        == 0
    )
    assert "unexplained_jump" not in pending()


def test_a_split_on_the_day_explains_the_move(tmp_path: Path) -> None:
    rows = json.loads((DEMO_MARKET_DIR / "tiingo_DTECH.json").read_text())
    last = rows[-1]
    split = {**last, "date": "2026-11-10T00:00:00.000Z", "close": last["close"] / 2, "splitFactor": 2.0}
    r = run_only("tiingo_eod", Patched("api.tiingo.com", json.dumps([*rows, split])))
    assert r.quarantined == 0 and r.stored == 1


# ---- CLI --------------------------------------------------------------------------------------------


def cli(*args: str, ok: bool = True) -> str:
    result = runner.invoke(app, list(args))
    assert (result.exit_code == 0) == ok, result.output
    return result.output


def test_cli_reads_and_review_commands() -> None:
    assert "[ok]" in cli("market", "price", "AMFI:900001", "--date", "2026-11-10")
    assert "[stale]" in cli("market", "price", "TICKER:XNSE:DSTK", "--date", "2026-11-11")
    cli("market", "price", "TICKER:NOPE", ok=False)
    assert "[estimated]" in cli("market", "fx", "SBI_TT_BUY", "--date", "2026-11-10")
    cli("market", "fx", "USDINR", ok=False)  # the rate type has no default
    out = cli("market", "sources")
    assert "fbil_ref" in out and "nse_bhavcopy" in out and "pending quarantine" in out
    assert "item(s)" in cli("quarantine", "list", "--all")
    cli("quarantine", "accept", "99999", "--note", "x", ok=False)
    cli("quarantine", "correct", "99999", "--value", "1", "--note", "x", ok=False)
    cli("asset", "cik", "TICKER:DTECH", "0009999999")


def test_cli_manual_imports_and_ingest_without_consent(tmp_path: Path) -> None:
    p = tmp_path / "p.csv"
    p.write_text(
        "asset_ref,valid_at,close,currency,price_type\nTICKER:XNSE:DEMOETF,2026-11-10,251.5,INR,CLOSE\n"
    )
    assert "stored 1" in cli("market", "import-prices", str(p))
    f = tmp_path / "f.csv"
    f.write_text("rate_type,valid_at,rate\nFBIL_REF,2026-11-11,85.1\n")
    assert "stored 1" in cli("market", "import-fx", str(f))
    out = cli("market", "ingest")  # ARTHA_CONSENTED_SOURCES is empty: nothing may be contacted
    assert "SKIPPED" in out and "FAILED" not in out
    assert "no consent" in out or "disabled" in out or "no " in out


def test_cli_demo_market_is_idempotent_and_refuses_real(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "duplicates" in cli("demo", "market")
    monkeypatch.setenv("ARTHA_DATASET", "real")
    get_settings.cache_clear()
    try:
        cli("demo", "market", ok=False)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_scheduler_catch_up_command() -> None:
    out = cli("scheduler", "catch-up")
    assert "IST" in out or out.strip() == ""


# ---- API --------------------------------------------------------------------------------------------


def test_source_health_endpoint_requires_login_and_reports() -> None:
    c = TestClient(api)
    assert c.get("/sources/health").status_code == 401
    assert c.get("/quarantine").status_code == 401
    r = c.post("/auth/login", json={"username": "demo", "password": PASSWORD})
    assert r.status_code == 200, r.text
    h = c.get("/sources/health")
    assert h.status_code == 200
    body = h.json()
    ids = {s["id"]: s for s in body["sources"]}
    assert body["dataset"] == "DEMO" and set(ids) >= {
        "amfi_nav",
        "fbil_ref",
        "tiingo_eod",
        "sec_companyfacts",
    }
    assert ids["nse_bhavcopy"]["state"] == "not_automated" and ids["nse_bhavcopy"]["enabled"] is False
    assert ids["fbil_ref"]["latest_valid_at"] == "2026-11-10"
    assert "blocks_monthly_review" in body
    q = c.get("/quarantine", params={"pending_only": False})
    assert q.status_code == 200 and isinstance(q.json(), list) and q.json()[0]["table_name"]
