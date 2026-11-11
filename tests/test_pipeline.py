"""Import workflow against a real database: preview, duplicates, blocking, commit, inbox."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from arthashreni.db.session import session_scope
from arthashreni.ingestion import cas, csv_template, indmoney
from arthashreni.ingestion.inbox import scan_inbox
from arthashreni.ingestion.pipeline import (
    batch_rows,
    commit_batch,
    file_sha256,
    preview_file,
    reject_batch,
)
from arthashreni.ingestion.setup_csv import load_accounts, load_assets
from arthashreni.ledger.assets import resolve_asset
from arthashreni.ledger.reconciliation import open_differences
from arthashreni.ledger.repository import current_book
from arthashreni.ledger.types import LedgerError

pytestmark = pytest.mark.usefixtures("ledger_db")
D = Decimal


@pytest.fixture(scope="module", autouse=True)
def setup(ledger_db: str, tmp_path_factory: pytest.TempPathFactory) -> None:
    d = tmp_path_factory.mktemp("setup")
    (d / "accounts.csv").write_text(
        "label,platform,account_kind,currency\nBRK,ZERODHA_KITE,BROKER,INR\nUSB,INDMONEY,BROKER,USD\nFOLIO-*****6789,CAMS,MF_FOLIO,INR\n"
    )
    (d / "assets.csv").write_text(
        "asset_class,name,currency,country,isin,amfi_code,ticker,exchange_mic\n"
        "EQUITY_IN,Eq One,INR,IN,ZZPIPEEQ0001,,PIPE,XNSE\n"
        "MF,Fund One,INR,IN,ZZPIPEMF0001,555,,\n"
        "EQUITY_US,Us One,USD,US,,,PUS,XNAS\n"
    )
    with session_scope("demo") as db:
        assert load_accounts(db, d / "accounts.csv") == 3
        assert load_assets(db, "demo", d / "assets.csv") == 3
        assert load_accounts(db, d / "accounts.csv") == 0 and load_assets(db, "demo", d / "assets.csv") == 0


def csv(path: Path, header: str, *rows: str) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n")
    return path


def txns(path: Path, *rows: str) -> Path:
    return csv(path, csv_template.TXN_COLUMNS, *rows)


def holding_units(account: str = "BRK") -> D:  # type: ignore[valid-type]
    with session_scope("demo") as db:
        asset = resolve_asset(db, "demo", "ZZPIPEEQ0001").id
        acc = db.execute(text("SELECT id FROM accounts WHERE label = :l"), {"l": account}).scalar_one()
        return current_book(db).holdings().get((acc, asset), D(0))


def test_preview_commit_and_idempotent_reimport(tmp_path: Path) -> None:
    f = txns(
        tmp_path / "a.csv",
        "BRK,ZZPIPEEQ0001,BUY,2025-01-02,,10,100,INR,,,1,0,0,,,a1,",
        "BRK,ZZPIPEEQ0001,BUY,2025-01-02,,10,100,INR,,,1,0,0,,,a1,",  # identical twin trade in the same file
        "BRK,ZZPIPEEQ0001,SELL,2025-01-09,,5,110,INR,,,1,0,0,,,a2,",
    )
    with session_scope("demo") as db:
        res = preview_file(db, "demo", f)
        assert res.outcome == "PREVIEW" and res.counts == {"NEW": 3} and res.blocking == []
        assert (
            db.execute(text("SELECT count(*) FROM transactions WHERE source_document = 'a.csv'")).scalar_one()
            == 0
        )  # nothing yet
        assert len(batch_rows(db, res.batch_id)) == 3  # type: ignore[arg-type]
        done = commit_batch(db, res.batch_id)  # type: ignore[arg-type]
        assert done.inserted["TRANSACTION"] == 3 and done.skipped == 0
        status = db.execute(
            text("SELECT status, approved_at IS NOT NULL FROM import_batches WHERE id = :i"),
            {"i": res.batch_id},
        ).one()
        assert tuple(status) == ("COMMITTED", True)
        again = preview_file(db, "demo", f)
        assert again.outcome == "ALREADY_IMPORTED" and again.batch_id == res.batch_id
        with pytest.raises(LedgerError, match="not PREVIEW"):
            commit_batch(db, res.batch_id)  # type: ignore[arg-type]
    assert holding_units() == D(15)


def test_overlapping_file_only_adds_new_rows(tmp_path: Path) -> None:
    f = txns(
        tmp_path / "b.csv",
        "BRK,ZZPIPEEQ0001,BUY,2025-01-02,,10,100,INR,,,1,0,0,,,a1,",  # already in the ledger (twice, same key)
        "BRK,ZZPIPEEQ0001,BUY,2025-01-02,,10,100,INR,,,1,0,0,,,a1,",
        "BRK,ZZPIPEEQ0001,BUY,2025-02-02,,3,100,INR,,,0,0,0,,,a3,",
    )
    with session_scope("demo") as db:
        res = preview_file(db, "demo", f)
        assert res.counts == {"DUPLICATE": 2, "NEW": 1}
        done = commit_batch(db, res.batch_id)  # type: ignore[arg-type]
        assert done.inserted["TRANSACTION"] == 1
    assert holding_units() == D(18)


def test_selling_more_than_held_blocks_the_batch_at_preview(tmp_path: Path) -> None:
    f = txns(tmp_path / "c.csv", "BRK,ZZPIPEEQ0001,SELL,2025-03-01,,1000,10,INR,,,0,0,0,,,c1,")
    with session_scope("demo") as db:
        res = preview_file(db, "demo", f)
        assert any("ledger check" in b and "negative" in b for b in res.blocking)
        with pytest.raises(LedgerError, match="blocking"):
            commit_batch(db, res.batch_id)  # type: ignore[arg-type]
        reject_batch(db, res.batch_id)  # type: ignore[arg-type]
        with pytest.raises(LedgerError, match="not a PREVIEW"):
            reject_batch(db, res.batch_id)  # type: ignore[arg-type]
    assert holding_units() == D(18)


def test_rejected_rows_need_explicit_acceptance_and_a_rejected_file_can_return(tmp_path: Path) -> None:
    f = txns(
        tmp_path / "d.csv",
        "BRK,ZZPIPEEQ0001,BUY,2025-03-02,,1,100,INR,,,0,0,0,,,d1,",
        "BRK,ZZPIPEUNKNOWN,BUY,2025-03-03,,1,100,INR,,,0,0,0,,,d2,",
    )
    with session_scope("demo") as db:
        res = preview_file(db, "demo", f)
        assert res.counts == {"NEW": 1, "REJECTED": 1}
        with pytest.raises(LedgerError, match="left out"):
            commit_batch(db, res.batch_id)  # type: ignore[arg-type]
        reject_batch(db, res.batch_id)  # type: ignore[arg-type]
        retry = preview_file(db, "demo", f)  # same bytes, allowed once the first batch was rejected
        assert retry.outcome == "PREVIEW" and retry.batch_id != res.batch_id
        done = commit_batch(db, retry.batch_id, accept_skipped=True)  # type: ignore[arg-type]
        assert done.inserted["TRANSACTION"] == 1 and done.skipped == 1
    assert holding_units() == D(19)


def test_needs_input_outcomes(tmp_path: Path) -> None:
    other = csv(tmp_path / "other.csv", "foo,bar", "1,2")
    trade = csv(
        tmp_path / "tb.csv",
        "isin,trade_date,trade_type,quantity,price,trade_id",
        "ZZPIPEEQ0001,2025-01-01,buy,1,1,t1",
    )
    hold = csv(tmp_path / "ch.csv", "isin,quantity_available,average_price", "ZZPIPEEQ0001,1,1")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with session_scope("demo") as db:
        assert "unrecognised" in preview_file(db, "demo", other).message
        assert "--account" in preview_file(db, "demo", trade).message
        assert "--as-of" in preview_file(db, "demo", hold, account="BRK").message
        assert "password" in preview_file(db, "demo", pdf).message
        assert (
            db.execute(
                text(
                    "SELECT count(*) FROM import_batches WHERE file_name IN ('other.csv','tb.csv','ch.csv','x.pdf')"
                )
            ).scalar_one()
            == 0
        )


def test_oversized_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from arthashreni.ingestion import pipeline

    big = csv(tmp_path / "big.csv", "a", "1")
    monkeypatch.setattr(pipeline, "MAX_FILE_BYTES", 1)
    with pytest.raises(Exception, match="larger"):
        file_sha256(big)


def test_console_tradebook_holdings_and_reconciliation(tmp_path: Path) -> None:
    tb = csv(
        tmp_path / "tradebook.csv",
        "symbol,isin,trade_date,exchange,segment,series,trade_type,quantity,price,trade_id,order_id",
        "PIPE,ZZPIPEEQ0001,2025-04-01,NSE,EQ,EQ,buy,4,100,tt1,o1",
    )
    hold = csv(
        tmp_path / "holdings.csv", "symbol,isin,quantity_available,average_price", "PIPE,ZZPIPEEQ0001,23,100"
    )
    with session_scope("demo") as db:
        r1 = preview_file(db, "demo", tb, account="BRK")
        commit_batch(db, r1.batch_id)  # type: ignore[arg-type]
        r2 = preview_file(db, "demo", hold, account="BRK", as_of=date(2025, 4, 30))
        done = commit_batch(db, r2.batch_id)  # type: ignore[arg-type]
        assert done.reconciliation["MATCHED"] >= 1 and not done.open_differences  # 19 + 4 = 23
    assert holding_units() == D(23)


def test_statement_that_disagrees_is_reported_as_an_open_difference(tmp_path: Path) -> None:
    hold = csv(tmp_path / "wrong.csv", csv_template.HOLDING_COLUMNS, "BRK,ZZPIPEEQ0001,2025-05-31,99")
    with session_scope("demo") as db:
        res = preview_file(db, "demo", hold)
        done = commit_batch(db, res.batch_id)  # type: ignore[arg-type]
        assert len(done.open_differences) == 1
        assert done.open_differences[0].diff == D(-76)
        assert len(open_differences(db)) == 1


def test_indmoney_import_with_fx(tmp_path: Path) -> None:
    f = csv(
        tmp_path / "ind.csv", indmoney.SAMPLE_HEADER,
        "2025-04-01,PUS,NASDAQ,BUY,2,50,0.5,0,,83,i1",
        "2025-04-02,,,DEPOSIT,,,,,1000,83,i2",
    )  # fmt: skip
    with session_scope("demo") as db:
        res = preview_file(db, "demo", f, account="USB")
        assert res.counts == {"NEW": 2} and any("ASSUMED" in w for w in res.warnings)
        commit_batch(db, res.batch_id)  # type: ignore[arg-type]
        row = db.execute(
            text(
                "SELECT net_cash_inr, fx_rate_type FROM transactions WHERE source_document = 'ind.csv' ORDER BY id"
            )
        ).first()
    assert row is not None and row[0] == D("-8341.5000") and row[1] == "BROKER_ACTUAL"


def test_corporate_actions_apply_before_later_sales(tmp_path: Path) -> None:
    ca_file = csv(tmp_path / "ca.csv", csv_template.ACTION_COLUMNS, "ZZPIPEEQ0001,SPLIT,2025-06-01,1,2,")
    sale = txns(tmp_path / "late.csv", "BRK,ZZPIPEEQ0001,SELL,2025-07-01,,40,10,INR,,,0,0,0,,,late1,")
    with session_scope("demo") as db:
        blocked = preview_file(db, "demo", sale)  # 23 held; 40 only possible after the split
        assert blocked.blocking
        reject_batch(db, blocked.batch_id)  # type: ignore[arg-type]
        a = preview_file(db, "demo", ca_file)
        commit_batch(db, a.batch_id)  # type: ignore[arg-type]
        ok = preview_file(db, "demo", sale)
        assert not ok.blocking
        commit_batch(db, ok.batch_id)  # type: ignore[arg-type]
    assert holding_units() == D(6)  # 23 * 2 - 40


def test_cas_import_end_to_end_with_blocking_on_unit_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_parsers import cas_data, scheme, txn

    good = cas_data(
        [
            scheme(
                [txn("2025-01-06", "PURCHASE_SIP", "500.00", "10.000", "50.0000")],
                close="10.000",
                isin="ZZPIPEMF0001",
            )
        ]
    )
    bad = cas_data(
        [
            scheme(
                [txn("2025-01-06", "PURCHASE", "500.00", "99.000", "50.0000")],
                close="99.000",
                isin="ZZPIPEMF0001",
            )
        ]
    )
    monkeypatch.setattr(cas, "read_cas", lambda path, password: bad)
    pdf = tmp_path / "cas.pdf"
    pdf.write_bytes(b"%PDF-1.4 bad")
    with session_scope("demo") as db:
        res = preview_file(db, "demo", pdf, password="secret")
        assert res.outcome == "PREVIEW" and any("UNITS_MISMATCH" in b for b in res.blocking)
        with pytest.raises(LedgerError, match="blocking"):
            commit_batch(db, res.batch_id)  # type: ignore[arg-type]
        reject_batch(db, res.batch_id)  # type: ignore[arg-type]

        monkeypatch.setattr(cas, "read_cas", lambda path, password: good)
        res2 = preview_file(db, "demo", pdf, password="secret")
        assert res2.blocking == []
        done = commit_batch(db, res2.batch_id)  # type: ignore[arg-type]
        assert done.reconciliation["MATCHED"] >= 1
        stored = db.execute(
            text("SELECT source_kind, summary ->> 'blocking' FROM import_batches WHERE id = :i"),
            {"i": res2.batch_id},
        ).one()
    assert stored[0] == "CAS_CAMS"
    assert "secret" not in str(stored)  # the password is never stored


def test_inbox_scan(tmp_path: Path) -> None:
    inbox, processed = tmp_path / "inbox", tmp_path / "processed"
    (inbox / "USB").mkdir(parents=True)
    txns(inbox / "root.csv", "BRK,ZZPIPEEQ0001,BUY,2025-08-01,,1,100,INR,,,0,0,0,,,in1,")
    csv(inbox / "USB" / "ind2.csv", indmoney.SAMPLE_HEADER, "2025-08-02,PUS,NASDAQ,BUY,1,50,0,0,,83,i9")
    csv(inbox / "mystery.csv", "foo,bar", "1,2")
    (inbox / "stmt.pdf").write_bytes(b"%PDF")
    (inbox / ".hidden.csv").write_text("x")
    (inbox / "notes.txt").write_text("x")
    broken = csv(
        inbox / "broken.csv",
        csv_template.TXN_COLUMNS,
        "BRK,ZZPIPEEQ0001,BUY,2025-08-01,,1,100,INR,,,0,0,0,,,in1,",
    )
    broken.write_bytes(broken.read_bytes().replace(b"BRK", b"\xff\xfe"))  # not valid UTF-8

    items = {i.file: i for i in scan_inbox("demo", inbox, processed)}
    assert items["root.csv"].outcome == "PREVIEW" and items["root.csv"].batch_id
    assert items[str(Path("USB") / "ind2.csv")].outcome == "PREVIEW"
    assert items["mystery.csv"].outcome == "NEEDS_INPUT"
    assert items["stmt.pdf"].outcome == "NEEDS_PASSWORD"
    assert items["broken.csv"].outcome == "ERROR"
    assert ".hidden.csv" not in items and "notes.txt" not in items
    assert not (inbox / "root.csv").exists() and len(list(processed.iterdir())) == 2
    assert (inbox / "mystery.csv").exists() and (inbox / "broken.csv").exists()  # left for the investor

    again = {i.file: i for i in scan_inbox("demo", inbox, processed)}
    assert "root.csv" not in again  # already moved
    dup = txns(inbox / "root-again.csv", "BRK,ZZPIPEEQ0001,BUY,2025-08-01,,1,100,INR,,,0,0,0,,,in1,")
    dup.write_bytes((processed / next(p.name for p in processed.iterdir() if "root" in p.name)).read_bytes())
    items3 = {i.file: i for i in scan_inbox("demo", inbox, processed)}
    assert items3["root-again.csv"].outcome == "ALREADY_IMPORTED"


def test_watch_polls_and_reports_each_problem_file_once(tmp_path: Path) -> None:
    from arthashreni.ingestion.inbox import watch_inbox

    inbox, processed = tmp_path / "in", tmp_path / "done"
    inbox.mkdir()
    csv(inbox / "odd.csv", "foo,bar", "1,2")
    sleeps: list[float] = []
    seen = list(watch_inbox("demo", inbox, processed, interval=7, iterations=3, sleep=sleeps.append))
    assert [(i.file, i.outcome) for i in seen] == [
        ("odd.csv", "NEEDS_INPUT")
    ]  # not repeated on polls 2 and 3
    assert sleeps == [7, 7]
    txns(inbox / "late.csv", "BRK,ZZPIPEEQ0001,BUY,2025-09-01,,1,100,INR,,,0,0,0,,,w1,")
    later = list(watch_inbox("demo", inbox, processed, iterations=1))
    assert {i.file: i.outcome for i in later} == {"odd.csv": "NEEDS_INPUT", "late.csv": "PREVIEW"}
