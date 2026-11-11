"""Phase 2 CLI commands through Typer's runner, against a throwaway database."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arthashreni.cli import app
from arthashreni.core.config import get_settings
from arthashreni.ingestion import cas
from arthashreni.ingestion.csv_template import HOLDING_COLUMNS, TXN_COLUMNS

pytestmark = pytest.mark.usefixtures("ledger_db")
runner = CliRunner()


def run(*args: str, ok: bool = True, input: str | None = None) -> str:
    result = runner.invoke(app, list(args), input=input)
    assert (result.exit_code == 0) == ok, result.output
    return result.output


def test_setup_and_import_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = tmp_path / "accounts.csv"
    accounts.write_text("label,platform,account_kind,currency\nCLI-ACC,OTHER,BROKER,INR\n")
    assets = tmp_path / "assets.csv"
    assets.write_text("asset_class,name,currency,country,isin\nEQUITY_IN,Cli Eq,INR,IN,ZZCLIEQ00001\n")
    assert "1 accounts added" in run("account", "load", str(accounts))
    assert "1 assets added" in run("asset", "load", str(assets))
    run("account", "add", "CLI-2", "--platform", "CAMS", "--kind", "MF_FOLIO", "--masked", "****1234")
    assert "CLI-ACC" in run("account", "list") and "CLI-2" in run("account", "list")
    assert "asset added" in run("asset", "add", "MF", "Cli Fund", "INR", "IN", "--amfi", "424242")
    run("asset", "add", "ETF_IN", "x", "INR", "IN", ok=False)  # no identifier

    out = run("import", "template", "transactions")
    assert out.splitlines()[0] == TXN_COLUMNS
    assert run("import", "template", "holdings").splitlines()[0] == HOLDING_COLUMNS
    run("import", "template", "nope", ok=False)

    f = tmp_path / "t.csv"
    f.write_text(
        f"{TXN_COLUMNS}\nCLI-ACC,ZZCLIEQ00001,BUY,2025-01-01,,5,10,INR,,,0,0,0,,,z1,\nCLI-ACC,NOPE0000000X,BUY,2025-01-02,,1,1,INR,,,0,0,0,,,z2,\n"
    )
    out = run("import", "file", str(f))
    assert "batch" in out and "REJECTED" in out and "Review, then" in out
    batch = out.split("batch ")[1].split()[0]
    assert "REJECTED" in run("import", "show", batch)
    refuse = run("import", "commit", batch, ok=False)
    assert "not committed" in refuse or "left out" in refuse
    assert "committed batch" in run("import", "commit", batch, "--accept-skipped")
    assert "ALREADY_IMPORTED" in run("import", "file", str(f))
    run("import", "show", "9999", ok=False)

    assert "5.00000000" in run("ledger", "holdings")
    assert "superseded by correction" in run(
        "ledger",
        "correct",
        "1",
        "--reason",
        "typo",
        "--set",
        "quantity=4",
        "--set",
        "net_cash_inr=-40",
        "--set",
        "net_cash_orig=-40",
    )
    assert "4.00000000" in run("ledger", "holdings")
    run("ledger", "correct", "1", "--reason", "again", "--set", "quantity=3", ok=False)  # already corrected
    run("ledger", "correct", "1000", "--reason", "x", "--set", "quantity=3", ok=False)
    run("ledger", "correct", "2", "--reason", "x", "--set", "quantity=-3", ok=False)  # fails validation
    assert "voided" in run("ledger", "void", "2", "--reason", "not mine")
    run("ledger", "void", "2", "--reason", "twice", ok=False)

    h = tmp_path / "h.csv"
    h.write_text(f"{HOLDING_COLUMNS}\nCLI-ACC,ZZCLIEQ00001,2025-02-01,7\n")
    prev = run("import", "file", str(h))
    hb = prev.split("batch ")[1].split()[0]
    out = run("import", "commit", hb)
    assert "UNEXPLAINED" in out
    assert "1 unexplained" in run("reconcile", "open")
    report = run("reconcile", "report")
    assert "UNEXPLAINED" in report and "ledger" in report and "1 statement holding(s)" in report
    assert "UNEXPLAINED" in run("reconcile", "run")
    rec_id = out.split("UNEXPLAINED #")[1].split(":")[0]
    run("reconcile", "accept", rec_id, "--reason", "", ok=False)
    assert "accepted" in run("reconcile", "accept", rec_id, "--reason", "known gift")
    assert "0 unexplained" in run("reconcile", "open")
    assert "ACCEPTED" in run("reconcile", "report") and "known gift" in run("reconcile", "report")

    rej = tmp_path / "again.csv"
    rej.write_text(f"{TXN_COLUMNS}\nCLI-ACC,ZZCLIEQ00001,BUY,2025-03-01,,1,1,INR,,,0,0,0,,,z9,\n")
    rb = run("import", "file", str(rej)).split("batch ")[1].split()[0]
    assert "rejected" in run("import", "reject", rb)

    # PDF path asks for the password without echoing it and passes it to the CAS reader
    seen: list[str] = []
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "pdf-secret")

    def fake_read(path: Path, password: str):  # type: ignore[no-untyped-def]
        seen.append(password)
        raise cas.ImportRowError("unreadable test pdf")

    monkeypatch.setattr(cas, "read_cas", fake_read)
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"%PDF")
    assert runner.invoke(app, ["import", "file", str(pdf)]).exit_code != 0
    assert seen == ["pdf-secret"]


def test_scan_and_demo_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTHA_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    assert "inbox is empty" in run("import", "scan")
    (tmp_path / "imports" / "inbox" / "x.csv").write_text("foo,bar\n1,2\n")
    assert "NEEDS_INPUT" in run("import", "scan")
    seeded = run("demo", "seed")
    assert seeded.count("COMMITTED") == 3
    assert "ALREADY_IMPORTED" in run("demo", "seed")
    monkeypatch.setenv("ARTHA_DATASET", "real")
    get_settings.cache_clear()
    run("demo", "seed", ok=False)
    get_settings.cache_clear()
