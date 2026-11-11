"""Phase 2 gate: the golden DEMO portfolio imports, reconciles with zero unexplained differences,
a repeat import is a no-op, and every figure equals the independently derived expected values."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from arthashreni.db.session import session_scope
from arthashreni.demo import DEMO_DIR, seed_demo
from arthashreni.ingestion.pipeline import commit_batch, preview_file
from arthashreni.ledger.reconciliation import open_differences, reconcile_all
from arthashreni.ledger.repository import current_book, load_audit, rebuild_lots

pytestmark = pytest.mark.usefixtures("ledger_db")
EXPECTED = json.loads((Path(__file__).parent / "golden" / "expected.json").read_text())


@pytest.fixture(scope="module")
def seeded(ledger_db: str):  # type: ignore[no-untyped-def]
    return seed_demo()


def test_seed_commits_everything(seeded) -> None:  # type: ignore[no-untyped-def]
    assert [s.outcome for s in seeded] == ["OK", "COMMITTED", "COMMITTED", "COMMITTED"]
    with session_scope("demo") as db:
        for table, expected in EXPECTED["row_counts"].items():
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == expected  # noqa: S608


def test_reconciles_with_zero_unexplained_differences(seeded) -> None:  # type: ignore[no-untyped-def]
    with session_scope("demo") as db:
        assert open_differences(db) == []
        counts = reconcile_all(db)
        statuses = db.execute(text("SELECT status, count(*) FROM reconciliations GROUP BY status")).all()
    assert counts == {"MATCHED": 7, "UNEXPLAINED": 0, "ACCEPTED": 0}
    assert dict(statuses) == {"MATCHED": 7}


def test_repeat_import_is_a_no_op(seeded) -> None:  # type: ignore[no-untyped-def]
    with session_scope("demo") as db:
        before = db.execute(
            text("SELECT (SELECT count(*) FROM transactions), (SELECT count(*) FROM import_batches)")
        ).one()
    again = seed_demo()
    assert [s.outcome for s in again] == ["OK", "ALREADY_IMPORTED", "ALREADY_IMPORTED", "ALREADY_IMPORTED"]
    assert again[0].detail == "0 accounts, 0 assets added"
    with session_scope("demo") as db:
        after = db.execute(
            text("SELECT (SELECT count(*) FROM transactions), (SELECT count(*) FROM import_batches)")
        ).one()
    assert tuple(before) == tuple(after)


def test_same_rows_in_a_renamed_file_are_all_duplicates(seeded, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    copy = tmp_path / "transactions-copy.csv"
    copy.write_text((DEMO_DIR / "transactions.csv").read_text() + "\n")  # different bytes, same rows
    with session_scope("demo") as db:
        res = preview_file(db, "demo", copy)
        assert res.outcome == "PREVIEW" and res.counts == {"DUPLICATE": 23}
        done = commit_batch(db, res.batch_id)  # type: ignore[arg-type]
    assert done.inserted == {"TRANSACTION": 0, "CORPORATE_ACTION": 0, "STATEMENT_HOLDING": 0}


def test_holdings_lots_and_realised_figures_match_the_independent_derivation(seeded) -> None:  # type: ignore[no-untyped-def]
    with session_scope("demo") as db:
        names = {
            (int(a), int(s)): f"{al}|{ref}"
            for a, s, al, ref in db.execute(
                text(
                    """SELECT ac.id, a.id, ac.label,
                              COALESCE('AMFI:' || a.amfi_code, 'TICKER:' || a.exchange_mic || ':' || a.ticker)
                       FROM accounts ac CROSS JOIN ref.assets a"""
                )
            )
        }
        book = rebuild_lots(db)
        stored_lots = db.execute(
            text(
                """SELECT ac.label, COALESCE('AMFI:' || a.amfi_code, 'TICKER:' || a.exchange_mic || ':' || a.ticker),
                          l.acquired_on, l.original_qty, l.remaining_qty, l.cost_inr, l.remaining_cost_inr, l.is_bonus
                   FROM lots l JOIN accounts ac ON ac.id = l.account_id JOIN ref.assets a ON a.id = l.asset_id
                   ORDER BY ac.label, 2, l.acquired_on"""
            )
        ).all()
        stored_cons = db.execute(
            text(
                """SELECT ac.label, COALESCE('AMFI:' || a.amfi_code, 'TICKER:' || a.exchange_mic || ':' || a.ticker),
                          t.trade_date, l.acquired_on, c.qty, c.cost_inr_consumed, c.proceeds_inr, c.holding_days
                   FROM lot_consumptions c JOIN lots l ON l.id = c.lot_id
                   JOIN transactions t ON t.id = c.disposal_transaction_id
                   JOIN accounts ac ON ac.id = l.account_id JOIN ref.assets a ON a.id = l.asset_id"""
            )
        ).all()
        audit = load_audit(db)

    assert {names[k]: v for k, v in book.holdings().items()} == {
        k: Decimal(v) for k, v in EXPECTED["holdings"].items()
    }

    got_lots = [(r[0], r[1], str(r[2]), r[3], r[4], r[5], r[6], r[7]) for r in stored_lots]
    want_lots = sorted(
        (x["account"], x["asset"], x["acquired_on"], Decimal(x["original_qty"]), Decimal(x["remaining_qty"]),
         Decimal(x["cost_inr"]), Decimal(x["remaining_cost_inr"]), x["is_bonus"])
        for x in EXPECTED["lots"]
    )  # fmt: skip
    assert sorted(got_lots) == want_lots

    got_cons = sorted((r[0], r[1], str(r[2]), str(r[3]), r[4], r[5], r[6], r[7]) for r in stored_cons)
    want_cons = sorted(
        (x["account"], x["asset"], x["disposal_date"], x["acquired_on"], Decimal(x["qty"]),
         Decimal(x["cost_inr_consumed"]), Decimal(x["proceeds_inr"]), x["holding_days"])
        for x in EXPECTED["consumptions"]
    )  # fmt: skip
    assert got_cons == want_cons

    cost = sum((c.cost_inr_consumed for c in book.consumptions), Decimal(0))
    proceeds = sum((c.proceeds_inr for c in book.consumptions), Decimal(0))
    assert (str(cost), str(proceeds), str(proceeds - cost)) == (
        EXPECTED["realised"]["cost_inr"], EXPECTED["realised"]["proceeds_inr"], EXPECTED["realised"]["gain_inr"]
    )  # fmt: skip
    assert (
        str(sum((lot.remaining_cost_inr for lot in book.lots), Decimal(0))) == EXPECTED["remaining_cost_inr"]
    )

    by_account: dict[str, Decimal] = {}
    label = {a: n.split("|")[0] for (a, _), n in names.items()}
    for t in audit:
        by_account[label[t.account_id]] = by_account.get(label[t.account_id], Decimal(0)) + t.net_cash_inr
    assert {k: str(v) for k, v in by_account.items()} == EXPECTED["net_cash_inr_by_account"]


def test_book_from_the_ledger_equals_the_stored_cache(seeded) -> None:  # type: ignore[no-untyped-def]
    with session_scope("demo") as db:
        built = current_book(db)
        stored = db.execute(text("SELECT count(*) FROM lots")).scalar_one()
    assert len(built.lots) == stored == len(EXPECTED["lots"])
