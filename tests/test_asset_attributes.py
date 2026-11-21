"""Asset theme and mutual-fund category (V32): stored, constrained by the database, set only by the investor,
and picked up by the portfolio report."""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from typer.testing import CliRunner

from arthashreni.cli import app
from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope
from arthashreni.portfolio import load_assets

pytestmark = pytest.mark.usefixtures("ledger_db")
runner = CliRunner()


def run(*args: str, ok: bool = True) -> str:
    result = runner.invoke(app, list(args))
    assert (result.exit_code == 0) == ok, result.output
    return result.output


def asset_row(ref_name: str) -> tuple[str | None, str | None]:
    with session_scope() as db:
        r = db.execute(
            text("SELECT theme, fund_category FROM ref.assets WHERE name = :n"), {"n": ref_name}
        ).one()
    return r[0], r[1]


def test_csv_load_set_and_clear(tmp_path: Path) -> None:
    f = tmp_path / "assets.csv"
    f.write_text(
        "asset_class,name,currency,country,amfi_code,isin,theme,fund_category\n"
        "MF,Debt Fund,INR,IN,500001,,,debt\n"
        "MF,Plain Fund,INR,IN,500002,,,\n"
        "EQUITY_US,AI Stock,USD,US,,ZZAISTOCK001,Artificial intelligence,\n"
    )
    run("asset", "load", str(f))
    assert asset_row("Debt Fund") == (None, "DEBT")  # case-insensitive in the file, stored upper-case
    assert asset_row("Plain Fund") == (None, None)  # unknown stays unknown, never guessed
    assert asset_row("AI Stock") == ("Artificial intelligence", None)

    assert "category=HYBRID" in run("asset", "set", "AMFI:500002", "--category", "hybrid")
    assert asset_row("Plain Fund") == (None, "HYBRID")
    run("asset", "set", "AMFI:500002", "--theme", "Banking")
    assert asset_row("Plain Fund") == ("Banking", "HYBRID")  # setting one leaves the other alone
    run("asset", "set", "AMFI:500002", "--theme", "")
    assert asset_row("Plain Fund") == (None, "HYBRID")  # an empty theme clears it


def test_set_validates_its_input() -> None:
    run("asset", "add", "ETF_IN", "Some ETF", "INR", "IN", "--ticker", "SOMEETF", "--mic", "XNSE")
    run("asset", "set", "TICKER:XNSE:SOMEETF", "--category", "DEBT", ok=False)  # mutual funds only
    run("asset", "set", "TICKER:XNSE:SOMEETF", ok=False)  # nothing to set
    run("asset", "set", "AMFI:500002", "--category", "GOLD", ok=False)
    run("asset", "set", "AMFI:999999999", "--theme", "x", ok=False)  # unknown asset


def test_database_refuses_a_bad_category() -> None:
    engine = create_engine(get_settings().db_owner_url.get_secret_value())
    try:
        for stmt in (
            "UPDATE ref.assets SET fund_category = 'CASH' WHERE name = 'Plain Fund'",
            "UPDATE ref.assets SET fund_category = 'DEBT' WHERE name = 'AI Stock'",  # not a mutual fund
        ):
            with pytest.raises(DBAPIError), engine.begin() as conn:
                conn.execute(text(stmt))
    finally:
        engine.dispose()


def test_assets_stay_insert_only_apart_from_these_columns() -> None:
    with session_scope() as db:
        db.execute(text("UPDATE ref.assets SET theme = 'ok' WHERE name = 'Plain Fund'"))
    with pytest.raises(DBAPIError), session_scope() as db:
        db.execute(text("UPDATE ref.assets SET name = 'renamed' WHERE name = 'Plain Fund'"))


def test_the_portfolio_loader_returns_them() -> None:
    with session_scope() as db:
        ids = {r[0] for r in db.execute(text("SELECT id FROM ref.assets")).all()}
        assets = load_assets(db, ids)
    by_name = {a.name: a for a in assets.values()}
    assert (
        by_name["Debt Fund"].fund_category == "DEBT"
        and by_name["AI Stock"].theme == "Artificial intelligence"
    )
    assert date.today()  # (the loader has no date dependence)
