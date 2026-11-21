"""Phase 5 database rules (ADR-0009, ADR-0025): over-allocation is a database error, one rupee has one use, reserves
never go negative, an approved IPS is frozen, history is append-only, and two concurrent allocations of one month
leave one winner and exact totals."""

import threading
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope
from arthashreni.demo import DEMO_IPS, seed_demo, seed_demo_policy
from arthashreni.demo_market import DEMO_TODAY, load_demo_market
from arthashreni.policy import store as pstore
from arthashreni.policy.context import Certainty
from arthashreni.policy.ips import IPS
from arthashreni.review import monthly_review, run_risk

pytestmark = pytest.mark.usefixtures("ledger_db")
D = Decimal


@pytest.fixture(scope="module", autouse=True)
def world(ledger_db: str, tmp_path_factory: pytest.TempPathFactory) -> None:
    mp = pytest.MonkeyPatch()
    mp.setenv("ARTHA_RAW_DIR", str(tmp_path_factory.mktemp("raw")))
    get_settings.cache_clear()
    seed_demo()
    load_demo_market()
    seed_demo_policy()
    yield  # type: ignore[misc]
    mp.undo()
    get_settings.cache_clear()


def review(period: date):  # type: ignore[no-untyped-def]
    with session_scope("demo") as db:
        return monthly_review(db, "demo", period, DEMO_TODAY)


def sql(statement: str, **params: object) -> None:
    with session_scope("demo") as db:
        db.execute(text(statement), params)


def one(statement: str, **params: object) -> object:
    with session_scope("demo") as db:
        return db.execute(text(statement), params).scalar_one()


# ---- budget, SIPs, context -----------------------------------------------------------------------------


def test_demo_policy_seed_is_idempotent() -> None:
    assert seed_demo_policy() == "DEMO policy already set up"
    assert one("SELECT count(*) FROM budget_settings") == 1


def test_budget_history_is_kept_and_the_effective_one_is_used() -> None:
    with session_scope("demo") as db:
        assert pstore.budget_for(db, date(2026, 11, 1)) == (1, D("10000.00"))
        assert pstore.budget_for(db, date(2025, 12, 1)) is None
        new_id = pstore.set_budget(db, D("12000"), date(2027, 1, 1))
        assert pstore.budget_for(db, date(2026, 12, 1)) == (1, D("10000.00"))
        assert pstore.budget_for(db, date(2027, 1, 1)) == (new_id, D("12000.00"))
        pstore.set_budget(
            db, D("11000"), date(2027, 1, 1)
        )  # a correction on the same date: the newer row wins
        assert pstore.budget_for(db, date(2027, 2, 1))[1] == D("11000.00")  # type: ignore[index]
    assert one("SELECT count(*) FROM budget_settings") == 3


def test_budget_settings_are_append_only() -> None:
    with pytest.raises(Exception, match="append-only|not allowed|permission"):
        sql("UPDATE budget_settings SET monthly_budget_inr = 1")
    with pytest.raises(Exception, match="append-only|not allowed|permission"):
        sql("DELETE FROM budget_settings")


def test_a_budget_must_be_positive() -> None:
    with pytest.raises(Exception, match="monthly_budget_inr"):
        sql("INSERT INTO budget_settings (monthly_budget_inr, effective_from) VALUES (0, '2026-01-01')")


def test_sip_change_is_a_new_row_that_supersedes_the_old_one() -> None:
    with session_scope("demo") as db:
        first = pstore.load_mandates(db)[0]
        new_id = pstore.change_sip(db, first.id, amount=D("2500"))
        latest = pstore.load_mandates(db)
        assert [m.id for m in latest] == [new_id] and latest[0].amount_inr == D("2500.00")
        with pytest.raises(pstore.PolicyError, match="not the latest"):
            pstore.change_sip(db, first.id, amount=D(1))
        ended = pstore.change_sip(db, new_id, status="ENDED", end_date=date(2026, 12, 31))
        assert pstore.load_mandates(db)[0].status == "ENDED" and pstore.load_mandates(db)[0].id == ended
    assert one("SELECT count(*) FROM sip_mandates") == 3
    with session_scope("demo") as db:  # restore an active mandate for the later tests
        m = pstore.load_mandates(db)[0]
        pstore.add_sip(
            db, m.asset_id, m.account_id, D("2000"), 6, m.platform, date(2025, 1, 1), supersedes_id=m.id
        )


def test_context_keeps_history_and_reads_the_latest() -> None:
    with session_scope("demo") as db:
        pstore.set_context(db, "emergency_fund_inr", 1, Certainty.ASSUMED)
        pstore.set_context(db, "emergency_fund_inr", 400000, Certainty.KNOWN)
        ctx = pstore.load_context(db)
    assert (
        ctx["emergency_fund_inr"].value == 400000 and ctx["emergency_fund_inr"].certainty is Certainty.KNOWN
    )
    assert one("SELECT count(*) FROM personal_context WHERE field = 'emergency_fund_inr'") == 3
    with pytest.raises(Exception, match="certainty"):
        sql("INSERT INTO personal_context (field, value, certainty) VALUES ('x', '1', 'MAYBE')")


# ---- IPS ------------------------------------------------------------------------------------------------


def test_a_draft_is_never_used_and_approval_supersedes_the_old_version() -> None:
    with session_scope("demo") as db:
        row = pstore.approved_ips(db)
        assert row is not None and row[1] == 1
        draft = IPS.model_validate({**DEMO_IPS, "cooldown_months": 6})
        v = pstore.create_ips_draft(db, draft)
        assert v == 2 and pstore.approved_ips(db)[1] == 1  # type: ignore[index]  # the draft changes nothing
        with pytest.raises(pstore.PolicyError, match="needs a note"):
            pstore.approve_ips(db, v, "  ")
        pstore.approve_ips(db, v, "reads well")
        assert pstore.approved_ips(db)[1] == 2  # type: ignore[index]
        with pytest.raises(pstore.PolicyError, match="not a draft"):
            pstore.approve_ips(db, 1, "again")
        with pytest.raises(pstore.PolicyError, match="not a draft"):
            pstore.approve_ips(db, 99, "missing")
    assert one("SELECT status FROM ips_versions WHERE version = 1") == "SUPERSEDED"
    assert one("SELECT count(*) FROM ips_versions WHERE status = 'APPROVED'") == 1
    with session_scope("demo") as db:  # back to version 1's content for the later tests
        v3 = pstore.create_ips_draft(db, IPS.model_validate(DEMO_IPS))
        pstore.approve_ips(db, v3, "restore")


def test_the_database_freezes_approved_ips_bodies_and_refuses_bad_transitions() -> None:
    owner = create_engine(get_settings().db_owner_url.get_secret_value())
    try:
        with owner.begin() as conn:
            conn.execute(text("SET search_path TO demo, ref"))
            with pytest.raises(Exception, match="cannot be edited"), conn.begin_nested():
                conn.execute(text("UPDATE ips_versions SET body = '{}' WHERE status = 'APPROVED'"))
            with pytest.raises(Exception, match="cannot go from"), conn.begin_nested():
                conn.execute(text("UPDATE ips_versions SET status = 'DRAFT' WHERE status = 'SUPERSEDED'"))
            with pytest.raises(Exception, match="never deleted|permission denied"), conn.begin_nested():
                conn.execute(text("DELETE FROM ips_versions"))
    finally:
        owner.dispose()


def test_only_one_ips_can_be_approved() -> None:
    owner = create_engine(get_settings().db_owner_url.get_secret_value())
    try:
        with owner.begin() as conn:
            conn.execute(text("SET search_path TO demo, ref"))
            conn.execute(text("INSERT INTO ips_versions (version, status, body) VALUES (99, 'DRAFT', '{}')"))
            with pytest.raises(Exception, match="ips_one_approved"), conn.begin_nested():
                conn.execute(
                    text(
                        "UPDATE ips_versions SET status = 'APPROVED', approved_at = now() WHERE version = 99"
                    )
                )
    finally:
        owner.dispose()


# ---- reserves -------------------------------------------------------------------------------------------


def test_reserve_balance_never_goes_negative() -> None:
    with session_scope("demo") as db:
        rid = pstore.ensure_reserve(db, "Cash reserve", "CASH_RESERVE")
        assert pstore.ensure_reserve(db, "Cash reserve", "CASH_RESERVE") == rid
        pstore.reserve_add(db, rid, "TRANSFER_IN", D("1000"), date(2026, 11, 1))
        pstore.reserve_add(db, rid, "DIVIDEND_IN", D("200.50"), date(2026, 11, 2))
        pstore.reserve_add(db, rid, "SALE_PROCEEDS_IN", D("99.50"), date(2026, 11, 3))
        assert next(b for b in pstore.reserve_balances(db) if b.id == rid).available_inr == D("1300.00")
    with pytest.raises(Exception, match="would go negative"), session_scope("demo") as db:
        pstore.reserve_add(db, rid, "IPO_BLOCK", D("1300.01"), date(2026, 11, 4), note="x")
    assert one("SELECT count(*) FROM reserve_ledger") == 3  # the refused entry is not there
    with pytest.raises(Exception, match="approved_line_id|check"):
        sql(
            "INSERT INTO reserve_ledger (reserve_id, entry_date, kind, amount_inr) VALUES (:r, '2026-11-04', 'USE', 1)",
            r=rid,
        )
    with pytest.raises(Exception, match="amount_inr"):
        sql(
            "INSERT INTO reserve_ledger (reserve_id, entry_date, kind, amount_inr) VALUES (:r, '2026-11-04', 'TRANSFER_IN', -5)",
            r=rid,
        )
    with pytest.raises(Exception, match="append-only|not allowed|permission denied"):
        sql("UPDATE reserve_ledger SET amount_inr = 1")


def test_ipo_block_allotment_and_refund_are_separate_events() -> None:
    with session_scope("demo") as db:
        rid = pstore.ensure_reserve(db, "IPO sinking fund", "IPO_SINKING_FUND")
        pstore.reserve_add(db, rid, "TRANSFER_IN", D("40000"), date(2026, 11, 1))
        with pytest.raises(pstore.PolicyError, match="research classification is UNRATED"):
            pstore.ipo_block(
                db, "DEMO-IPO-MAIN", rid, D("14800"), date(2026, 11, 5)
            )  # unrated: not allowed to apply
    with session_scope("demo") as db:
        db.execute(
            text("UPDATE ipo_applications SET research_class = 'ALLOWED' WHERE ipo_ref = 'DEMO-IPO-MAIN'")
        )
    with session_scope("demo") as db:
        rid = pstore.ensure_reserve(db, "IPO sinking fund", "IPO_SINKING_FUND")
        with pytest.raises(pstore.PolicyError, match="cannot apply"):
            pstore.ipo_block(
                db, "DEMO-IPO-MAIN", rid, D("100"), date(2026, 11, 5)
            )  # below the minimum application
        with pytest.raises(pstore.PolicyError, match="cannot apply"):
            pstore.ipo_block(db, "DEMO-IPO-SME", rid, D("999999"), date(2026, 11, 5))  # SME is never eligible
        pstore.ipo_block(db, "DEMO-IPO-MAIN", rid, D("29600"), date(2026, 11, 5))  # two lots
        bal = next(b for b in pstore.reserve_balances(db) if b.id == rid)
        assert (bal.available_inr, bal.blocked_inr) == (
            D("10400.00"),
            D("29600.00"),
        )  # blocked stays committed
        with pytest.raises(pstore.PolicyError, match="cannot apply"):
            pstore.ipo_block(db, "DEMO-IPO-MAIN", rid, D("14800"), date(2026, 11, 6))  # already applied
        with pytest.raises(pstore.PolicyError, match="between 0 and the blocked"):
            pstore.ipo_settle(db, "DEMO-IPO-MAIN", rid, D("29600.01"), date(2026, 11, 12))
        assert pstore.ipo_settle(db, "DEMO-IPO-MAIN", rid, D("14800"), date(2026, 11, 12)) == "PARTIAL"
        bal = next(b for b in pstore.reserve_balances(db) if b.id == rid)
        assert (bal.available_inr, bal.blocked_inr) == (
            D("25200.00"),
            D(0),
        )  # 14,800 refunded, 14,800 allotted
        with pytest.raises(pstore.PolicyError, match="not APPLIED"):
            pstore.ipo_settle(db, "DEMO-IPO-MAIN", rid, D(0), date(2026, 11, 13))
        with pytest.raises(pstore.PolicyError, match="no IPO"):
            pstore.ipo_settle(db, "NOPE", rid, D(0), date(2026, 11, 13))
    assert one("SELECT status FROM ipo_applications WHERE ipo_ref = 'DEMO-IPO-SME'") == "EXCLUDED"
    with pytest.raises(Exception, match="check"):  # an SME IPO cannot be anything but EXCLUDED
        sql(
            "INSERT INTO ipo_applications (ipo_ref, board, min_lot_value_inr, status) VALUES ('S2', 'SME', 1, 'APPLIED')"
        )


def test_the_database_refuses_to_release_more_ipo_money_than_was_blocked() -> None:
    with session_scope("demo") as db:
        rid = pstore.ensure_reserve(db, "IPO sinking fund", "IPO_SINKING_FUND")
    with pytest.raises(Exception, match="more IPO money than is blocked"), session_scope("demo") as db:
        pstore.reserve_add(db, rid, "IPO_RELEASE", D(1), date(2026, 11, 14), note="x")


# ---- envelope, allocation, concurrency ------------------------------------------------------------------


def test_the_database_rejects_an_envelope_that_does_not_reconcile() -> None:
    with pytest.raises(Exception, match="envelope_reconciles"):
        sql("""INSERT INTO budget_periods (period, budget_setting_id, opening_budget_inr, committed_sip_inr, unallocated_inr)
               VALUES ('2025-12-01', 1, 10000, 2000, 7999.99)""")
    with pytest.raises(Exception, match="check"):  # not the first of the month
        sql("""INSERT INTO budget_periods (period, budget_setting_id, opening_budget_inr, committed_sip_inr, unallocated_inr)
               VALUES ('2025-12-02', 1, 10000, 0, 10000)""")
    with pytest.raises(Exception, match="new_allocations_inr"):  # a negative component is over-allocation
        sql("""INSERT INTO budget_periods (period, budget_setting_id, opening_budget_inr, committed_sip_inr,
                   new_allocations_inr, unallocated_inr) VALUES ('2025-12-01', 1, 10000, 0, -1, 10001)""")


def test_lines_that_do_not_add_up_to_the_period_totals_fail_at_commit() -> None:
    aid = one("SELECT id FROM ref.assets WHERE ticker = 'DEMOETF' AND is_demo")
    with pytest.raises(Exception, match="do not add up"):
        with session_scope("demo") as db:
            db.execute(
                text("""INSERT INTO budget_periods (period, budget_setting_id, opening_budget_inr, committed_sip_inr,
                                   unallocated_inr) VALUES ('2025-11-01', 1, 10000, 0, 10000)""")
            )
            db.execute(
                text(
                    "INSERT INTO envelope_lines (period, kind, amount_inr, asset_id) VALUES ('2025-11-01', 'NEW_ALLOCATION', 500, :a)"
                ),
                {"a": aid},
            )  # a line, but the period still says 0 new money
    assert one("SELECT count(*) FROM budget_periods WHERE period = '2025-11-01'") == 0


def test_allocation_stores_everything_once_and_freezes_the_month() -> None:
    m = review(date(2026, 9, 1))
    with session_scope("demo") as db:
        rid = pstore.allocate_period(db, m.recommendation, m.ips_row_id)
    env = m.recommendation.envelope
    assert one("SELECT status FROM budget_periods WHERE period = '2026-09-01'") == "ALLOCATED"
    assert one("SELECT count(*) FROM envelope_lines WHERE period = '2026-09-01'") == len(env.lines)
    assert one("SELECT count(*) FROM recommendation_lines WHERE recommendation_id = :r", r=rid) == len(
        m.recommendation.lines
    )
    total = one(
        "SELECT committed_sip_inr + new_allocations_inr + reserve_transfers_inr + unallocated_inr FROM budget_periods WHERE period = '2026-09-01'"
    )
    assert total == env.opening_budget_inr
    with session_scope("demo") as db:
        stored = pstore.load_recommendation(db, date(2026, 9, 1))
    assert stored is not None and stored[1] == m.recommendation and stored[0] == rid
    with pytest.raises(pstore.PeriodAllocated), session_scope("demo") as db:
        pstore.allocate_period(
            db, m.recommendation, m.ips_row_id
        )  # the second allocation of a month is refused
    assert one("SELECT count(*) FROM envelope_lines WHERE period = '2026-09-01'") == len(
        env.lines
    )  # nothing doubled


def test_an_allocated_period_cannot_be_changed_or_given_more_lines() -> None:
    aid = one("SELECT id FROM ref.assets WHERE ticker = 'DEMOETF' AND is_demo")
    with pytest.raises(Exception, match="already allocated"):
        sql("UPDATE budget_periods SET unallocated_inr = unallocated_inr WHERE period = '2026-09-01'")
    with pytest.raises(Exception, match="never deleted|permission denied"):
        sql("DELETE FROM budget_periods WHERE period = '2026-09-01'")
    with pytest.raises(Exception, match="already allocated"):
        sql(
            "INSERT INTO envelope_lines (period, kind, amount_inr, asset_id) VALUES ('2026-09-01', 'NEW_ALLOCATION', 1, :a)",
            a=aid,
        )
    with pytest.raises(Exception, match="append-only|not allowed|permission denied"):
        sql("UPDATE envelope_lines SET amount_inr = 1")


def test_an_envelope_line_can_back_only_one_recommendation_line() -> None:
    line = one("SELECT envelope_line_id FROM recommendation_lines WHERE envelope_line_id IS NOT NULL LIMIT 1")
    rid = one("SELECT recommendation_id FROM recommendation_lines WHERE envelope_line_id = :e", e=line)
    amount = one("SELECT amount_inr FROM envelope_lines WHERE id = :e", e=line)
    with pytest.raises(Exception, match="recommendation_lines_envelope_line_id_key"):
        sql(
            "INSERT INTO recommendation_lines (recommendation_id, envelope_line_id, action, amount_inr) VALUES (:r, :e, 'accumulate', :a)",
            r=rid,
            e=line,
            a=amount,
        )
    with pytest.raises(Exception, match="differs from its envelope line"):
        sql(
            "INSERT INTO recommendation_lines (recommendation_id, envelope_line_id, action, amount_inr) VALUES (:r, :e, 'accumulate', :a)",
            r=rid,
            e=line,
            a=D(amount) + 1,
        )  # type: ignore[arg-type]
    with pytest.raises(Exception, match="check"):  # money without an envelope line
        sql(
            "INSERT INTO recommendation_lines (recommendation_id, action, amount_inr) VALUES (:r, 'accumulate', 5)",
            r=rid,
        )
    with pytest.raises(Exception, match="check"):  # human approval cannot be switched off
        sql("""INSERT INTO recommendations (period, schema_version, payload, confidence, risk_level, human_approval_required)
               VALUES ('2026-09-01', '1.0', '{}', 'low', 'NORMAL', false)""")


def test_allocation_needs_the_budget_to_match_the_setting() -> None:
    m = review(date(2026, 8, 1))
    wrong = m.recommendation.model_copy(update={"budget_inr": D("9999")})
    bad_env = m.recommendation.envelope
    assert wrong.budget_inr != bad_env.opening_budget_inr  # the validator is not re-run by model_copy
    with pytest.raises(pstore.PolicyError, match="budget setting"), session_scope("demo") as db:
        fake = bad_env.model_copy(update={"opening_budget_inr": D("9999")})
        pstore.allocate_period(db, m.recommendation.model_copy(update={"envelope": fake}), m.ips_row_id)
    assert one("SELECT count(*) FROM budget_periods WHERE period = '2026-08-01'") == 0


def test_two_concurrent_allocations_of_one_month_leave_one_winner_and_exact_totals() -> None:
    for month in (3, 4, 5, 6):
        period = date(2026, month, 1)
        m = review(period)
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def attempt() -> None:
            try:
                with session_scope("demo") as db:
                    barrier.wait(timeout=10)
                    pstore.allocate_period(db, m.recommendation, m.ips_row_id)
                outcomes.append("ok")
            except pstore.PeriodAllocated:
                outcomes.append("refused")
            except (
                Exception
            ) as exc:  # the constraint triggers are the backstop; any failure still means one winner
                outcomes.append(f"error: {str(exc)[:80]}")

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert sorted(o.split(":")[0] for o in outcomes) in (["error", "ok"], ["ok", "refused"]), outcomes
        assert outcomes.count("ok") == 1
        n = len(m.recommendation.envelope.lines)
        assert one("SELECT count(*) FROM envelope_lines WHERE period = :p", p=period) == n
        assert one("SELECT count(*) FROM recommendations WHERE period = :p", p=period) == 1
        env = m.recommendation.envelope
        row = one(
            "SELECT committed_sip_inr + new_allocations_inr + reserve_transfers_inr + unallocated_inr FROM budget_periods WHERE period = :p",
            p=period,
        )
        assert row == env.opening_budget_inr


# ---- decisions and leftover -----------------------------------------------------------------------------


def test_decisions_are_recorded_and_execute_nothing() -> None:
    with session_scope("demo") as db:
        rec_id, rec = pstore.load_recommendation(db, date(2026, 9, 1))  # type: ignore[misc]
    line = one(
        "SELECT id FROM recommendation_lines WHERE recommendation_id = :r ORDER BY id LIMIT 1", r=rec_id
    )
    with session_scope("demo") as db:
        with pytest.raises(pstore.PolicyError, match="needs a reason"):
            pstore.record_decision(db, rec_id, int(line), "REJECT", " ", date(2026, 11, 12))  # type: ignore[arg-type]
    with session_scope("demo") as db:
        pstore.record_decision(db, rec_id, int(line), "DEFER", "want to think", date(2026, 11, 12))  # type: ignore[arg-type]
        pstore.record_decision(db, rec_id, None, "REJECT", "whole month rejected", date(2026, 11, 12))
    assert one("SELECT count(*) FROM decisions") == 2
    with pytest.raises(Exception, match="check"):
        sql(
            "INSERT INTO decisions (recommendation_id, decision, reason) VALUES (:r, 'EXECUTE', 'x')",
            r=rec_id,
        )
    with pytest.raises(Exception, match="append-only|not allowed|permission denied"):
        sql("UPDATE decisions SET reason = 'edited'")
    assert rec.human_approval_required is True


def test_approving_a_reserve_transfer_books_it_once_and_leftover_sweeps_are_idempotent() -> None:
    with session_scope("demo") as db:
        pstore.set_context(
            db, "emergency_fund_inr", 100000, Certainty.KNOWN
        )  # below 6 months: liquidity first
    m = review(date(2026, 10, 1))
    with session_scope("demo") as db:
        pstore.set_context(db, "emergency_fund_inr", 400000, Certainty.KNOWN)
        rec_id = pstore.allocate_period(db, m.recommendation, m.ips_row_id)
    assert any(e.kind.value == "RESERVE_TRANSFER" for e in m.recommendation.envelope.lines)
    transfer = one(
        "SELECT l.id FROM recommendation_lines l JOIN envelope_lines e ON e.id = l.envelope_line_id WHERE e.kind = 'RESERVE_TRANSFER' AND l.recommendation_id = :r LIMIT 1",
        r=rec_id,
    )
    before = one("SELECT coalesce(sum(amount_inr), 0) FROM reserve_ledger WHERE kind = 'TRANSFER_IN'")
    with session_scope("demo") as db:
        pstore.record_decision(
            db, rec_id, int(transfer), "APPROVE", "yes, build the cushion", date(2026, 11, 12)
        )  # type: ignore[arg-type]
    amount = one(
        "SELECT e.amount_inr FROM envelope_lines e JOIN recommendation_lines l ON l.envelope_line_id = e.id WHERE l.id = :l",
        l=transfer,
    )
    assert one("SELECT coalesce(sum(amount_inr), 0) FROM reserve_ledger WHERE kind = 'TRANSFER_IN'") == D(
        before
    ) + D(amount)  # type: ignore[arg-type]
    with pytest.raises(pstore.PolicyError, match="already approved"):
        with session_scope("demo") as db:
            pstore.record_decision(db, rec_id, int(transfer), "APPROVE", "again", date(2026, 11, 12))  # type: ignore[arg-type]
    with session_scope("demo") as db:
        cash = pstore.ensure_reserve(db, "Cash reserve", "CASH_RESERVE")
        left = pstore.sweep_leftover(db, date(2026, 10, 1), cash, date(2026, 11, 12))
        again = pstore.sweep_leftover(db, date(2026, 10, 1), cash, date(2026, 11, 12))
        assert left == again == D(m.recommendation.envelope.unallocated_inr)
        with pytest.raises(pstore.PolicyError, match="only an allocated month"):
            pstore.sweep_leftover(db, date(2026, 1, 1), cash, date(2026, 11, 12))
    swept = one("SELECT count(*) FROM reserve_ledger WHERE sweep_period = '2026-10-01'")
    assert swept == (1 if left > 0 else 0)
    # leftovers never inflate the next month: a new month still opens at the configured budget
    assert review(date(2026, 11, 1)).recommendation.budget_inr == D("10000.00")


# ---- risk store ------------------------------------------------------------------------------------------


def test_risk_run_is_stored_and_alerts_are_deduplicated_across_days() -> None:
    with session_scope("demo") as db:
        first = run_risk(db, "demo", DEMO_TODAY, persist=True)
    assert first.diff.new and not first.diff.unchanged
    n_alerts = one("SELECT count(*) FROM alerts")
    assert one("SELECT count(*) FROM risk_signals WHERE as_of = :d", d=DEMO_TODAY) == len(first.signals)
    assert one("SELECT level FROM risk_levels ORDER BY id DESC LIMIT 1") == first.level.level.name
    with session_scope("demo") as db:
        again = run_risk(db, "demo", DEMO_TODAY, persist=True)
    assert again.diff.new == () and len(again.diff.unchanged) == len(first.diff.new)  # same condition: silent
    assert one("SELECT count(*) FROM alerts") == n_alerts
    with session_scope("demo") as db:
        from arthashreni.risk.store import latest_level

        got = latest_level(db)
    assert got is not None and got[1] is first.level.level and got[2]["total"] == first.level.total
    body = one("SELECT body->>'if_nothing' FROM alerts LIMIT 1")
    assert "Nothing is executed" in str(body)


def test_a_cleared_alert_closes_and_reopens_when_the_condition_returns() -> None:
    from arthashreni.risk.alerts import AlertDiff
    from arthashreni.risk.store import apply_alert_diff, open_alert_keys

    with session_scope("demo") as db:
        keys = open_alert_keys(db)
        assert keys
        key = sorted(keys)[0]
        apply_alert_diff(db, AlertDiff((), (), (key,)))
        assert key not in open_alert_keys(db)
    with session_scope("demo") as db:
        run = run_risk(db, "demo", DEMO_TODAY, persist=True)
    assert any(a.key == key for a in run.diff.new)  # raised again, as a new occurrence
    with session_scope("demo") as db:
        assert key in open_alert_keys(db)
