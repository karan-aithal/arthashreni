"""Ledger against a real database: append-only rules, assets, corrections, lots, reconciliation."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope
from arthashreni.ledger import assets as assets_mod
from arthashreni.ledger.effective import resolve_effective
from arthashreni.ledger.fingerprint import fingerprint
from arthashreni.ledger.models import NewCorporateAction, NewStatementHolding, NewTxn
from arthashreni.ledger.reconciliation import accept_difference, open_differences, reconcile_all
from arthashreni.ledger.repository import (
    account_ids,
    add_account,
    correct_transaction,
    current_book,
    insert_corporate_action,
    insert_statement_holding,
    insert_transaction,
    load_actions,
    load_audit,
    load_effective,
    rebuild_lots,
    void_transaction,
)
from arthashreni.ledger.types import ActionType, LedgerError, NegativeHoldingError, TxnType

D = Decimal
pytestmark = pytest.mark.usefixtures("ledger_db")


@pytest.fixture(scope="module")
def ids(ledger_db: str) -> dict[str, int]:
    out: dict[str, int] = {}
    with session_scope("demo") as db:
        out["acc"] = add_account(db, platform="ZERODHA_KITE", account_kind="BROKER", label="ACC-1")
        out["acc2"] = add_account(db, platform="CAMS", account_kind="MF_FOLIO", label="ACC-2", currency="inr")
        out["eq"] = assets_mod.add_asset(
            db, "demo", asset_class="EQUITY_IN", name="Test Eq", currency="INR", country="IN",
            isin="ZZTESTEQ0001", ticker="TST", exchange_mic="XNSE",
        )  # fmt: skip
        out["mf"] = assets_mod.add_asset(
            db, "demo", asset_class="MF", name="Test MF", currency="INR", country="IN",
            isin="ZZTESTMF0001", amfi_code="111111", mf_plan="DIRECT", mf_option="GROWTH",
        )  # fmt: skip
        out["us"] = assets_mod.add_asset(
            db,
            "demo",
            asset_class="EQUITY_US",
            name="Test US",
            currency="USD",
            country="US",
            ticker="UST",
            exchange_mic="XNAS",
        )
        assets_mod.add_asset(
            db,
            "demo",
            asset_class="EQUITY_IN",
            name="Dup A",
            currency="INR",
            country="IN",
            ticker="DUP",
            exchange_mic="XNSE",
        )
        assets_mod.add_asset(
            db,
            "demo",
            asset_class="EQUITY_US",
            name="Dup B",
            currency="USD",
            country="US",
            ticker="DUP",
            exchange_mic="XNYS",
        )
    return out


def buy(
    ids: dict[str, int], qty: str, on: date, *, asset: str = "eq", account: str = "acc", price: str = "10"
) -> NewTxn:
    q, p = D(qty), D(price)
    return NewTxn(
        asset_id=ids[asset], account_id=ids[account], type=TxnType.BUY, trade_date=on, quantity=q, price=p,
        net_cash_inr=-(q * p), net_cash_orig=-(q * p), source_document="test",
    )  # fmt: skip


def sell(ids: dict[str, int], qty: str, on: date, *, asset: str = "eq", account: str = "acc") -> NewTxn:
    q = D(qty)
    return NewTxn(
        asset_id=ids[asset], account_id=ids[account], type=TxnType.SELL, trade_date=on, quantity=q, price=D(12),
        net_cash_inr=q * 12, net_cash_orig=q * 12, source_document="test",
    )  # fmt: skip


def add(db: Session, txn: NewTxn, tag: str) -> int:
    new_id = insert_transaction(db, txn, fp=fingerprint("t", tag))
    assert new_id is not None
    return new_id


# ---- append-only ------------------------------------------------------------------------------------


def test_ledger_tables_reject_update_delete_truncate_even_for_the_owner(ids: dict[str, int]) -> None:
    with session_scope("demo") as db:
        add(db, buy(ids, "1", date(2024, 1, 1)), "ao")
        batch = _batch(db)
        insert_corporate_action(
            db,
            NewCorporateAction(asset_id=ids["eq"], action_type=ActionType.BONUS, ex_date=date(2024, 1, 2),
                               ratio_from=D(1), ratio_to=D(1), source_document="s"),
            fp="ao-ca",
        )  # fmt: skip
        h, fp = holding(ids, "1", date(2024, 1, 31), "ao-sh")
        insert_statement_holding(db, h, fp=fp, import_batch_id=batch)
        db.execute(
            text(
                """INSERT INTO transaction_imports_raw (batch_id, row_no, item_kind, raw, parse_status)
                   VALUES (:b, 1, 'TRANSACTION', '{}', 'NEW')"""
            ),
            {"b": batch},
        )
    owner = create_engine(get_settings().db_owner_url.get_secret_value())
    for sql in (
        "UPDATE demo.transactions SET fees = 1",
        "DELETE FROM demo.transactions",
        "TRUNCATE demo.transactions CASCADE",
        "UPDATE demo.transaction_imports_raw SET reject_reason = 'x'",
        "DELETE FROM demo.corporate_actions",
        "DELETE FROM demo.statement_holdings",
    ):
        with owner.begin() as conn, pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text(sql))
    owner.dispose()


def test_app_role_has_no_update_or_delete_privilege(ids: dict[str, int]) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), session_scope("demo") as db:
        db.execute(text("UPDATE transactions SET fees = 1"))
    with pytest.raises(ProgrammingError, match="permission denied"), session_scope("demo") as db:
        db.execute(text("DELETE FROM accounts"))


def test_real_schema_is_out_of_reach_for_the_demo_role(ids: dict[str, int]) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), session_scope("demo") as db:
        db.execute(text("SELECT * FROM real.transactions"))


# ---- assets and accounts ------------------------------------------------------------------------------


def test_asset_resolution_by_every_reference_style(ids: dict[str, int]) -> None:
    with session_scope("demo") as db:
        r = lambda ref: assets_mod.resolve_asset(db, "demo", ref).id  # noqa: E731
        assert r("ZZTESTEQ0001") == r("zztesteq0001") == r("TICKER:XNSE:TST") == r("TICKER:TST") == ids["eq"]
        assert r("AMFI:111111") == ids["mf"]
        assert r("TICKER:XNAS:UST") == ids["us"]
        asset = assets_mod.resolve_asset(db, "demo", "AMFI:111111")
        assert (asset.asset_class, asset.currency, asset.amfi_code) == ("MF", "INR", "111111")
        for ref, message in {
            "AMFI:999": "no asset registered",
            "TICKER:XNSE:NOPE": "no asset registered",
            "TICKER:DUP": "matches 2 assets",
            "TICKER:A:B:C": "bad ticker",
            "garbage!": "unrecognised",
        }.items():
            with pytest.raises(assets_mod.UnknownAsset, match=message):
                assets_mod.resolve_asset(db, "demo", ref)


def test_real_dataset_does_not_see_demo_assets(ids: dict[str, int]) -> None:
    with session_scope("demo") as db, pytest.raises(assets_mod.UnknownAsset):
        assets_mod.resolve_asset(db, "real", "ZZTESTEQ0001")


def test_asset_rules(ids: dict[str, int]) -> None:
    with session_scope("demo") as db:
        with pytest.raises(LedgerError, match="SME"):
            assets_mod.add_asset(
                db,
                "demo",
                asset_class="EQUITY_IN",
                name="s",
                currency="INR",
                country="IN",
                isin="ZZSME0000001",
                is_sme=True,
            )
        sgb = assets_mod.add_asset(
            db, "demo", asset_class="SGB", name="SGB 2.5%", currency="INR", country="IN", isin="ZZSGB0000001",
            maturity_date=date(2030, 1, 1), coupon_rate=D("0.025"), coupon_frequency_months=6,
        )  # fmt: skip
        assert sgb > 0
        assert account_ids(db)["ACC-2"] == ids["acc2"]
    with pytest.raises(IntegrityError, match="has_identifier"), session_scope("demo") as db:
        assets_mod.add_asset(db, "demo", asset_class="EQUITY_IN", name="x", currency="INR", country="IN")
    with pytest.raises(IntegrityError, match=r"assets_isin_key|duplicate"), session_scope("demo") as db:
        assets_mod.add_asset(
            db, "demo", asset_class="EQUITY_IN", name="x", currency="INR", country="IN", isin="ZZTESTEQ0001"
        )


# ---- inserts, duplicates, constraints ------------------------------------------------------------------


def test_duplicate_fingerprint_is_a_no_op(ids: dict[str, int]) -> None:
    txn = buy(ids, "3", date(2024, 2, 1))
    with session_scope("demo") as db:
        first = insert_transaction(db, txn, fp="dup-fp")
        again = insert_transaction(db, txn, fp="dup-fp")
    assert first is not None and again is None


def test_database_checks_back_up_the_python_validation(ids: dict[str, int]) -> None:
    insert = """INSERT INTO transactions (account_id, txn_type, trade_date, currency, net_cash_inr, net_cash_orig,
                    source_document, fingerprint {extra_cols}) VALUES (:a, :t, '2024-01-01', 'INR', 0, 0, 's', :fp {extra_vals})"""
    cases = {
        "correction_shape": ("CORRECTION", "", ""),  # correction without supersedes/reason/type
        "txn_type_check": ("NOPE", "", ""),
        "fx_type_with_rate": ("DEPOSIT", ", fx_rate", ", 80"),
    }
    for name, (txn_type, cols, vals) in cases.items():
        with pytest.raises(IntegrityError), session_scope("demo") as db:
            db.execute(
                text(insert.format(extra_cols=cols, extra_vals=vals)),
                {"a": ids["acc"], "t": txn_type, "fp": f"chk-{name}"},
            )
    with (
        pytest.raises(IntegrityError, match=r"supersedes_earlier|correction_shape"),
        session_scope("demo") as db,
    ):
        first = add(db, buy(ids, "1", date(2024, 3, 1)), "chk-self")
        db.execute(
            text(
                """INSERT INTO transactions (account_id, txn_type, corrected_type, supersedes_id, correction_reason,
                       trade_date, currency, net_cash_inr, net_cash_orig, source_document, fingerprint)
                   VALUES (:a, 'CORRECTION', 'BUY', :s, 'r', '2024-03-01', 'INR', 0, 0, 's', 'chk-future')"""
            ),
            {"a": ids["acc"], "s": first + 1000},
        )


def test_model_validation_rules(ids: dict[str, int]) -> None:
    base = dict(
        account_id=1, trade_date=date(2024, 1, 1), net_cash_inr=D(0), net_cash_orig=D(0), source_document="s"
    )
    for bad in (
        dict(type=TxnType.BUY, asset_id=None, quantity=D(1), price=D(1)),
        dict(type=TxnType.BUY, asset_id=1, quantity=D(0), price=D(1)),
        dict(type=TxnType.BUY, asset_id=1, quantity=D(1), price=None),
        dict(type=TxnType.FEE, fees=D(-1)),
        dict(type=TxnType.FEE, currency="US"),
        dict(type=TxnType.FEE, currency="USD"),
        dict(type=TxnType.FEE, fx_rate=D(0), fx_rate_type="BROKER_ACTUAL"),
        dict(type=TxnType.CORRECTION),
        dict(type=TxnType.SPLIT),
        dict(type=TxnType.DEPOSIT, net_cash_orig=D(-5)),
    ):
        with pytest.raises(ValidationError):
            NewTxn(**{**base, **bad})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="ratios"):
        NewCorporateAction(
            asset_id=1,
            action_type=ActionType.SPLIT,
            ex_date=date(2024, 1, 1),
            ratio_from=D(0),
            ratio_to=D(1),
            source_document="s",
        )
    with pytest.raises(ValidationError, match="negative"):
        NewStatementHolding(
            account_id=1, asset_id=1, as_of=date(2024, 1, 1), units=D(-1), source_document="s"
        )


# ---- corrections, lots -----------------------------------------------------------------------------------


def test_correction_void_and_effective_view(ids: dict[str, int]) -> None:
    with session_scope("demo") as db:
        t1 = add(db, buy(ids, "10", date(2024, 4, 1), asset="mf", account="acc2"), "c1")
        t2 = add(db, buy(ids, "5", date(2024, 4, 2), asset="mf", account="acc2"), "c2")
        fixed = correct_transaction(
            db, t1, "keyed 10 instead of 8", {"quantity": "8", "net_cash_inr": "-80", "net_cash_orig": "-80"}
        )
        rebuild_lots(db)
        assert current_book(db).holdings()[(ids["acc2"], ids["mf"])] == D(13)

        rows = load_effective(db)
        assert fixed in {r.id for r in rows} and t1 not in {r.id for r in rows}
        assert next(r for r in rows if r.id == fixed).type is TxnType.BUY  # effective type of the correction
        everything = db.execute(text("SELECT count(*) FROM transactions")).scalar_one()
        assert everything > len(rows)  # the audit table keeps superseded rows

        with pytest.raises(LedgerError, match="already corrected"):
            correct_transaction(db, t1, "again", {"quantity": "7"})
        with pytest.raises(LedgerError, match="needs a reason"):
            correct_transaction(db, t2, "  ", {})
        with pytest.raises(LedgerError, match="does not exist"):
            correct_transaction(db, 999999, "why", {})
        again = correct_transaction(
            db, fixed, "second thoughts", {"quantity": "9", "net_cash_inr": "-90", "net_cash_orig": "-90"}
        )
        void = void_transaction(db, t2, "duplicate entry")
        rebuild_lots(db)
        assert current_book(db).holdings()[(ids["acc2"], ids["mf"])] == D(9)
        effective_ids = {r.id for r in load_effective(db)}
        assert again in effective_ids  # latest correction of t1 is the effective row
        assert void not in effective_ids and t2 not in effective_ids  # a void leaves nothing behind

        # the SQL view and the Python resolver agree on the effective set
        audit = load_audit(db)
        assert [r.id for r in resolve_effective(audit)] == [r.id for r in load_effective(db)]
        assert {t1, t2, fixed} <= {r.id for r in audit}


def test_a_correction_cannot_be_recorded_twice_or_fail_validation(ids: dict[str, int]) -> None:
    with session_scope("demo") as db:
        t = add(db, buy(ids, "4", date(2024, 4, 3), asset="mf", account="acc2"), "c3")
        with pytest.raises(ValidationError):
            correct_transaction(db, t, "bad", {"quantity": "-1"})


def test_rebuild_rejects_an_impossible_ledger(ids: dict[str, int]) -> None:
    with pytest.raises(NegativeHoldingError), session_scope("demo") as db:
        add(db, sell(ids, "1000000", date(2024, 5, 1)), "neg")
        rebuild_lots(db)
    with session_scope("demo") as db:  # the failed transaction rolled back completely
        assert (
            db.execute(
                text("SELECT count(*) FROM transactions WHERE fingerprint = :f"),
                {"f": fingerprint("t", "neg")},
            ).scalar_one()
            == 0
        )


def test_rebuild_persists_lots_and_consumptions(ids: dict[str, int]) -> None:
    with session_scope("demo") as db:
        add(db, sell(ids, "2", date(2024, 6, 1), asset="eq"), "rb-sell")
        book = rebuild_lots(db)
        stored = db.execute(text("SELECT count(*) FROM lots")).scalar_one()
        consumed = db.execute(text("SELECT count(*) FROM lot_consumptions")).scalar_one()
    assert stored == len(book.lots) and consumed == len(book.consumptions) > 0


def test_corporate_actions_load_in_order(ids: dict[str, int]) -> None:
    action = NewCorporateAction(
        asset_id=ids["eq"],
        action_type=ActionType.SPLIT,
        ex_date=date(2024, 7, 1),
        ratio_from=D(1),
        ratio_to=D(2),
        source_document="s",
    )
    with session_scope("demo") as db:
        assert insert_corporate_action(db, action, fp="ca-1") is not None
        assert insert_corporate_action(db, action, fp="ca-1") is None
        assert any(a.action_type is ActionType.SPLIT and a.ratio_to == 2 for a in load_actions(db))


# ---- reconciliation -----------------------------------------------------------------------------------


def holding(
    ids: dict[str, int], units: str, as_of: date, tag: str, *, asset: str = "us", account: str = "acc"
) -> tuple[NewStatementHolding, str]:
    h = NewStatementHolding(
        account_id=ids[account], asset_id=ids[asset], as_of=as_of, units=D(units), source_document="stmt"
    )
    return h, fingerprint("sh", tag)


def test_reconciliation_flags_accepts_and_clears_differences(ids: dict[str, int]) -> None:
    jan, feb = date(2024, 1, 31), date(2024, 2, 29)
    usd = lambda q, on, tag: NewTxn(  # noqa: E731
        asset_id=ids["us"], account_id=ids["acc"], type=TxnType.BUY, trade_date=on, quantity=D(q), price=D(100),
        currency="USD", fx_rate=D(83), fx_rate_type="BROKER_ACTUAL", net_cash_orig=-D(q) * 100, net_cash_inr=-D(q) * 8300,
        source_document="s",
    )  # fmt: skip
    with session_scope("demo") as db:
        insert_transaction(db, usd("5", date(2024, 1, 10), "r1"), fp="rc-1")
        h1, f1 = holding(ids, "5", jan, "match")
        h2, f2 = holding(ids, "7", feb, "mismatch")
        assert insert_statement_holding(db, h1, fp=f1, import_batch_id=_batch(db)) is not None
        assert insert_statement_holding(db, h1, fp=f1, import_batch_id=_batch(db)) is None
        insert_statement_holding(db, h2, fp=f2, import_batch_id=_batch(db))
        counts = reconcile_all(db)
        assert counts["MATCHED"] >= 1 and counts["UNEXPLAINED"] >= 1
        open_ = [d for d in open_differences(db) if d.asset_id == ids["us"]]
        assert (
            len(open_) == 1
            and open_[0].diff == D(-2)
            and open_[0].ledger_units == D(5)
            and open_[0].statement_units == D(7)
        )

        with pytest.raises(LedgerError, match="needs a reason"):
            accept_difference(db, open_[0].reconciliation_id, " ")
        accept_difference(db, open_[0].reconciliation_id, "statement includes a gift from family")
        with pytest.raises(LedgerError, match="not an open"):
            accept_difference(db, open_[0].reconciliation_id, "again")
        assert reconcile_all(db)["ACCEPTED"] >= 1  # same difference stays accepted
        assert not [d for d in open_differences(db) if d.asset_id == ids["us"]]

        insert_transaction(db, usd("1", date(2024, 2, 5), "r2"), fp="rc-2")  # ledger moves: 6 vs 7
        reconcile_all(db)
        again = [d for d in open_differences(db) if d.asset_id == ids["us"]]
        assert len(again) == 1 and again[0].diff == D(-1)  # acceptance does not carry over a new difference

        insert_transaction(db, usd("1", date(2024, 2, 6), "r3"), fp="rc-3")  # 7 vs 7
        reconcile_all(db)
        assert not [d for d in open_differences(db) if d.asset_id == ids["us"]]


def test_only_the_latest_statement_per_holding_can_block(ids: dict[str, int]) -> None:
    with session_scope("demo") as db:
        old, f_old = holding(ids, "99", date(2023, 12, 31), "old", asset="mf", account="acc2")
        new, f_new = holding(ids, "0", date(2024, 12, 31), "new", asset="mf", account="acc2")
        insert_statement_holding(db, old, fp=f_old, import_batch_id=_batch(db))
        insert_statement_holding(db, new, fp=f_new, import_batch_id=_batch(db))
        reconcile_all(db)
        blocking = [
            d for d in open_differences(db) if d.asset_id == ids["mf"] and d.account_id == ids["acc2"]
        ]
        assert all(d.as_of == date(2024, 12, 31) for d in blocking)  # the stale 2023 statement is ignored


def _batch(db: Session) -> int:
    return int(
        db.execute(
            text(
                """INSERT INTO import_batches (source_kind, file_name, file_sha256, status)
                   VALUES ('MANUAL', 'f', md5(random()::text), 'PREVIEW') RETURNING id"""
            )
        ).scalar_one()
    )


@pytest.fixture(autouse=True)
def _noop() -> Iterator[None]:
    yield
