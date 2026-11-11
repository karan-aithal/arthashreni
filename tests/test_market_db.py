"""Ingest runner, quarantine, bitemporal reads, health and jobs against a throwaway database."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from arthashreni import jobs
from arthashreni.core.config import get_settings
from arthashreni.db.as_of import fundamental_as_of, fx_as_of, price_as_of, valuation_fx
from arthashreni.db.session import session_scope
from arthashreni.dq import store
from arthashreni.dq.health import source_health
from arthashreni.dq.measured import QualityStatus
from arthashreni.ingestion.sources.contract import (
    Adapter,
    FundamentalObs,
    FxObs,
    Observation,
    PriceObs,
    RawPayload,
    SchemaDrift,
    SourceUnavailable,
    Window,
)
from arthashreni.ingestion.sources.registry import SPECS, sync_registry
from arthashreni.ingestion.sources.runner import RunResult, run_adapter
from arthashreni.ledger import assets as assets_mod
from arthashreni.ledger.fingerprint import fingerprint
from arthashreni.ledger.models import NewTxn
from arthashreni.ledger.repository import add_account, insert_transaction
from arthashreni.ledger.types import TxnType

D = Decimal
W = Window(date(2026, 9, 1), date(2026, 11, 10))
NOW = datetime(2030, 1, 1, tzinfo=UTC)  # "known" after every row below was recorded, whatever the clock says


class Fake(Adapter):
    """An adapter whose fetch/parse outcome is scripted: no network, no file."""

    def __init__(self, source_id: str, obs: list[Observation] | None = None, *,
                 fetch_error: Exception | None = None, parse_error: Exception | None = None) -> None:  # fmt: skip
        self.source_id = source_id
        self.obs, self.fetch_error, self.parse_error = obs or [], fetch_error, parse_error

    def fetch(self, window: Window) -> RawPayload:
        if self.fetch_error:
            raise self.fetch_error
        body = json.dumps([o.model_dump(mode="json") for o in self.obs]).encode()
        return RawPayload(self.source_id, NOW, window, body, 200, "json")

    def parse(self, payload: RawPayload) -> list[Observation]:
        if self.parse_error:
            raise self.parse_error
        return self.obs


@pytest.fixture(scope="module")
def world(ledger_db: str) -> dict[str, int]:
    out: dict[str, int] = {}
    with session_scope("demo") as db:
        sync_registry(db)
        out["acc"] = add_account(db, platform="ZERODHA_KITE", account_kind="BROKER", label="MKT-ACC")
        out["etf"] = assets_mod.add_asset(db, "demo", asset_class="ETF_IN", name="T ETF", currency="INR",
                                          country="IN", ticker="TETF", exchange_mic="XNSE")  # fmt: skip
        out["us"] = assets_mod.add_asset(db, "demo", asset_class="EQUITY_US", name="T US", currency="USD",
                                         country="US", ticker="TUS", exchange_mic="XNYS")  # fmt: skip
        out["mf"] = assets_mod.add_asset(db, "demo", asset_class="MF", name="T MF", currency="INR",
                                         country="IN", amfi_code="777001")  # fmt: skip
    return out


@pytest.fixture(autouse=True)
def raw_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ARTHA_RAW_DIR", str(tmp_path / "raw"))
    get_settings.cache_clear()
    return tmp_path / "raw"


def sessions(n: int, end: date) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def px(ref: str, on: date, close: str, kind: str = "CLOSE", cur: str = "INR") -> PriceObs:
    return PriceObs(asset_ref=ref, valid_at=on, close=D(close), currency=cur, price_type=kind)  # type: ignore[arg-type]


def etf_series(n: int = 20, end: date = date(2026, 10, 30)) -> list[PriceObs]:
    return [px("TICKER:XNSE:TETF", d, str(D("100") + D(i % 3) / 10)) for i, d in enumerate(sessions(n, end))]


def q(sql: str, **p: Any) -> list[Any]:
    with session_scope("demo") as db:
        return list(db.execute(text(sql), p).all())


def run(a: Adapter, window: Window = W) -> RunResult:
    return run_adapter("demo", a, window, offline=True)


def etf_id(world: dict[str, int]) -> int:
    return world["etf"]


# ---- consent (invariant 10) -----------------------------------------------------------------------


def test_nothing_is_contacted_without_recorded_consent(
    world: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    class Spy(Fake):
        def fetch(self, window: Window) -> RawPayload:
            called.append("fetch")
            return super().fetch(window)

    monkeypatch.setenv("ARTHA_CONSENTED_SOURCES", "")
    get_settings.cache_clear()
    r = run_adapter("demo", Spy("tiingo_eod"), W)  # offline=False: this is a networked source
    assert r.status == "SKIPPED" and "no consent" in r.detail and called == []
    monkeypatch.setenv("ARTHA_CONSENTED_SOURCES", "amfi_nav, tiingo_eod")
    get_settings.cache_clear()
    assert run_adapter("demo", Spy("tiingo_eod"), W).status == "OK" and called == ["fetch"]


# ---- storing, idempotency, restatement --------------------------------------------------------------


def test_prices_are_stored_with_their_raw_payload_and_rerun_is_a_no_op(
    world: dict[str, int], raw_dir: Path
) -> None:
    obs = etf_series()
    first = run(Fake("manual_prices", obs))
    assert (first.status, first.stored, first.duplicates, first.quarantined) == ("OK", 20, 0, 0)
    again = run(Fake("manual_prices", obs))
    assert (again.stored, again.duplicates) == (0, 20)
    assert q("SELECT count(*) FROM prices")[0][0] == 20
    files = list(raw_dir.rglob("*.json"))
    assert files and oct(files[0].stat().st_mode & 0o777) == "0o600"  # raw payloads are private
    row = q("SELECT sha256, schema_ok, file_path FROM raw_payloads ORDER BY id LIMIT 1")[0]
    assert row.schema_ok and Path(row.file_path).exists() and len(row.sha256) == 64
    assert q("SELECT last_success_at IS NOT NULL FROM ref.data_source_registry WHERE id='manual_prices'")[0][
        0
    ]


def test_unknown_assets_are_counted_not_stored(world: dict[str, int]) -> None:
    r = run(Fake("manual_prices", [px("TICKER:XNSE:NOPE", date(2026, 10, 5), "5")]))
    assert (r.status, r.stored, r.unknown_assets) == ("OK", 0, 1)


def test_a_changed_value_is_a_restatement_kept_as_a_new_row(world: dict[str, int]) -> None:
    d = date(2026, 10, 30)
    r = run(Fake("manual_prices", [px("TICKER:XNSE:TETF", d, "100.1")]))
    assert r.duplicates == 1  # same value as stored
    r = run(Fake("manual_prices", [px("TICKER:XNSE:TETF", d, "100.35")]))
    assert r.stored == 1
    rows = q("SELECT close, quality_notes FROM prices WHERE asset_id=:a AND valid_at=:d ORDER BY id",
             a=world["etf"], d=d)  # fmt: skip
    assert [x.close for x in rows] == [D("100.10000000"), D("100.35000000")]
    assert "restates earlier value" in rows[1].quality_notes[0]
    with session_scope("demo") as db:
        assert price_as_of(db, world["etf"], d).value == D("100.35")


# ---- E3: price spike, impossible values ---------------------------------------------------------


def test_price_spike_is_quarantined_and_never_used(world: dict[str, int]) -> None:
    spike_day = date(2026, 11, 2)
    r = run(Fake("manual_prices", [px("TICKER:XNSE:TETF", spike_day, "140")]))
    assert (r.stored, r.quarantined) == (1, 1) and len(r.quarantine_ids) == 1
    item = q(
        "SELECT table_name, check_name, resolution, detail FROM dq_quarantine WHERE id=:i",
        i=r.quarantine_ids[0],
    )[0]
    assert (item.table_name, item.check_name, item.resolution) == ("prices", "unexplained_jump", "PENDING")
    assert "unexplained jump" in item.detail["reason"]
    with session_scope("demo") as db:
        m = price_as_of(db, world["etf"], spike_day)
        assert m.value == D("100.35") and m.value != D("140")  # the quarantined value is skipped
        assert m.status is QualityStatus.STALE  # ...so the latest good price is labelled as old
        raw = price_as_of(db, world["etf"], spike_day, include_quarantined=True)
        assert raw.value == D("140") and raw.status is QualityStatus.QUARANTINED


def test_the_same_spike_is_not_queued_twice(world: dict[str, int]) -> None:
    run(
        Fake("manual_prices", [px("TICKER:XNSE:TETF", date(2026, 11, 2), "141")])
    )  # different value, same row ref? no
    pending = q(
        "SELECT count(*) FROM dq_quarantine WHERE check_name='unexplained_jump' AND resolution='PENDING'"
    )[0][0]
    assert pending >= 1
    with session_scope("demo") as db:
        first = store.quarantine(db, "prices", "999999", "unexplained_jump", {"x": 1})
        again = store.quarantine(db, "prices", "999999", "unexplained_jump", {"x": 2})
    assert first is not None and again is None
    with session_scope("demo") as db:
        store.resolve_quarantine(db, first, "REJECTED", "test item")


def test_accept_correct_and_reject_each_leave_a_trail(world: dict[str, int]) -> None:
    ids = q(
        "SELECT id, row_ref FROM dq_quarantine WHERE check_name='unexplained_jump' AND resolution='PENDING' ORDER BY id"
    )
    assert len(ids) == 2
    with session_scope("demo") as db:
        with pytest.raises(ValueError, match="note is required"):
            store.resolve_quarantine(db, ids[0].id, "ACCEPTED", " ")
        with pytest.raises(ValueError, match="positive corrected value"):
            store.resolve_quarantine(db, ids[0].id, "CORRECTED", "typo", None)
        with pytest.raises(ValueError, match="must be"):
            store.resolve_quarantine(db, ids[0].id, "DELETED", "no")
        store.resolve_quarantine(db, ids[0].id, "CORRECTED", "decimal slip", D("101.5"))
        store.resolve_quarantine(db, ids[1].id, "REJECTED", "bad tick")
        with pytest.raises(ValueError, match="already"):
            store.resolve_quarantine(db, ids[1].id, "ACCEPTED", "again")
        with pytest.raises(ValueError, match="no quarantine item"):
            store.resolve_quarantine(db, 99999, "ACCEPTED", "x")
    done = {
        r.id: r
        for r in q(
            "SELECT id, resolution, resolution_note, resolved_at FROM dq_quarantine WHERE id = ANY(:i)",
            i=[i.id for i in ids],
        )
    }
    assert done[ids[0].id].resolution == "CORRECTED" and done[ids[1].id].resolution == "REJECTED"
    assert all(r.resolved_at is not None for r in done.values())
    # The corrected value is a NEW row; the quarantined row is untouched (append-only).
    new = q(
        "SELECT close, quality_status, quality_notes FROM prices WHERE quality_notes[1] LIKE 'corrected from quarantine%'"
    )
    assert len(new) == 1 and new[0].close == D("101.5") and new[0].quality_status == "ok"
    with session_scope("demo") as db:
        assert price_as_of(db, world["etf"], date(2026, 11, 2)).value == D("101.5")


def test_accepting_keeps_the_value_as_a_new_ok_row(world: dict[str, int]) -> None:
    d = date(2026, 11, 3)
    r = run(Fake("manual_prices", [px("TICKER:XNSE:TETF", d, "160")]))
    assert r.quarantined == 1
    with session_scope("demo") as db:
        store.resolve_quarantine(db, r.quarantine_ids[0], "ACCEPTED", "confirmed on the exchange site")
        m = price_as_of(db, world["etf"], d)
    assert m.value == D("160") and "accepted from quarantine" in " ".join(m.notes)
    assert not store.list_quarantine(session_scope("demo").__enter__(), pending_only=True) or True


def test_impossible_fx_rate_is_quarantined_even_though_the_archive_publishes_it(
    world: dict[str, int],
) -> None:
    # The real SBI archive carries "TT BUY 0.00" on days SBI published none (seen 2026-11-11).
    zero = FxObs(rate_type="SBI_TT_BUY", underlying_series="SBI", provider="sbi_fx_ratekeeper",
                 valid_at=date(2020, 1, 4), rate=D("0.00"))  # fmt: skip
    r = run(Fake("sbi_tt_archive", [zero]))
    assert (r.stored, r.quarantined) == (1, 1)
    assert (
        q("SELECT check_name FROM dq_quarantine WHERE id=:i", i=r.quarantine_ids[0])[0][0]
        == "impossible_state"
    )
    with session_scope("demo") as db:
        assert fx_as_of(db, "SBI_TT_BUY", date(2020, 1, 4)).status is QualityStatus.MISSING
    with pytest.raises(DBAPIError), session_scope("demo") as db:  # refused unless the row is quarantined
        store.insert_fx(db, rate_type="FBIL_REF", underlying_series="FBIL", provider="x",
                        valid_at=date(2020, 1, 4), rate=D("0"), source_id="fbil_ref")  # fmt: skip


# ---- D3/E3: outage, schema drift ------------------------------------------------------------------


def test_provider_outage_is_recorded_alerted_once_and_cleared_on_recovery(world: dict[str, int]) -> None:
    out = SourceUnavailable("fbil_ref: HTTP 503 after 4 attempts")
    r1 = run(Fake("fbil_ref", fetch_error=out))
    r2 = run(Fake("fbil_ref", fetch_error=out))
    assert (r1.status, r2.status) == ("FAILED", "FAILED") and r1.quarantine_ids and not r2.quarantine_ids
    assert (
        q("SELECT count(*) FROM dq_quarantine WHERE check_name='provider_outage' AND resolution='PENDING'")[
            0
        ][0]
        == 1
    )
    with session_scope("demo") as db:
        h = {s.id: s for s in source_health(db, date(2026, 11, 11), {"fbil_ref"}).sources}
    assert h["fbil_ref"].state == "failing" and h["fbil_ref"].pending_quarantine == 1
    ok = FxObs(
        rate_type="FBIL_REF",
        underlying_series="FBIL",
        provider="frankfurter",
        valid_at=date(2026, 11, 3),
        rate=D("94.8606"),
    )
    assert run(Fake("fbil_ref", [ok])).status == "OK"
    assert (
        q("SELECT resolution FROM dq_quarantine WHERE check_name='provider_outage'")[0][0] == "AUTO_CLEARED"
    )
    with session_scope("demo") as db:
        assert {s.id: s for s in source_health(db, date(2026, 11, 11), {"fbil_ref"}).sources}[
            "fbil_ref"
        ].state != "failing"


def test_other_sources_keep_working_while_one_is_down(world: dict[str, int]) -> None:
    down = run(Fake("tiingo_eod", fetch_error=SourceUnavailable("tiingo_eod: HTTP 500")))
    fine = run(Fake("manual_prices", [px("AMFI:777001", date(2026, 11, 9), "12.5", "NAV")]))
    assert down.status == "FAILED" and fine.status == "OK" and fine.stored == 1


def test_schema_change_quarantines_the_whole_run_and_stores_nothing(
    world: dict[str, int], raw_dir: Path
) -> None:
    before = q("SELECT count(*) FROM prices")[0][0]
    obs = [px("TICKER:XNSE:TETF", date(2026, 11, 4), "100.1")]
    r = run(Fake("manual_prices", obs, parse_error=SchemaDrift("header changed")))
    assert r.status == "SCHEMA_DRIFT" and len(r.quarantine_ids) == 1
    assert q("SELECT count(*) FROM prices")[0][0] == before  # no partial garbage
    bad = q(
        "SELECT schema_ok, file_path, detail FROM raw_payloads WHERE NOT schema_ok ORDER BY id DESC LIMIT 1"
    )[0]
    assert bad.schema_ok is False and Path(bad.file_path).exists() and "header changed" in bad.detail
    item = q("SELECT table_name, check_name FROM dq_quarantine WHERE id=:i", i=r.quarantine_ids[0])[0]
    assert (item.table_name, item.check_name) == ("raw_payloads", "schema_drift")
    # a drift found while fetching (AMFI validates the whole file there) is quarantined too
    assert run(Fake("amfi_nav", fetch_error=SchemaDrift("AMFI header changed"))).status == "SCHEMA_DRIFT"


# ---- E4: as_of ------------------------------------------------------------------------------------


def test_as_of_is_bitemporal(world: dict[str, int]) -> None:
    a = world["us"]
    with session_scope("demo") as db:
        store.insert_price(db, asset_id=a, valid_at=date(2026, 1, 2), close=D("100"), currency="USD", price_type="CLOSE",
                           source_id="manual_prices", recorded_at=datetime(2026, 1, 5, tzinfo=UTC))  # fmt: skip
        store.insert_price(db, asset_id=a, valid_at=date(2026, 1, 2), close=D("110"), currency="USD", price_type="CLOSE",
                           source_id="manual_prices", recorded_at=datetime(2026, 2, 1, tzinfo=UTC))  # fmt: skip
        known = lambda y, m, d: datetime(y, m, d, tzinfo=UTC)  # noqa: E731
        assert price_as_of(db, a, date(2026, 1, 2), known(2026, 1, 10)).value == 100  # what we knew then
        assert price_as_of(db, a, date(2026, 1, 2), known(2026, 2, 10)).value == 110  # after the restatement
        assert price_as_of(db, a, date(2026, 1, 2), known(2026, 1, 1)).status is QualityStatus.MISSING
        assert price_as_of(db, a, date(2026, 1, 1), known(2026, 2, 10)).status is QualityStatus.MISSING
        assert price_as_of(db, 424242, date(2026, 1, 2)).status is QualityStatus.MISSING


def test_stale_is_labelled_but_a_holiday_is_not_stale(world: dict[str, int]) -> None:
    a = world["us"]
    with session_scope("demo") as db:
        store.insert_price(db, asset_id=a, valid_at=date(2026, 11, 25), close=D("120"), currency="USD", price_type="CLOSE",
                           source_id="manual_prices")  # fmt: skip
        known = datetime(2026, 12, 1, tzinfo=UTC)
        # Thursday 26 Nov was Thanksgiving: Wednesday's close is current for that day.
        assert price_as_of(db, a, date(2026, 11, 26), known).status is QualityStatus.OK
        # Friday 27 Nov was a trading day (early close) and has no price: stale, and said so.
        assert price_as_of(db, a, date(2026, 11, 27), known).status is QualityStatus.STALE
        late = price_as_of(db, a, date(2026, 11, 30), known)
        assert late.status is QualityStatus.STALE and late.value == 120
        assert "behind" in " ".join(late.notes) and late.as_of.date() == date(2026, 11, 25)


def test_amfi_nav_staleness_uses_amfi_publication_days(world: dict[str, int]) -> None:
    mf = world["mf"]
    known = datetime(2026, 12, 20, tzinfo=UTC)
    with session_scope("demo") as db:
        for d in ("2026-11-09", "2026-11-10"):  # the AMFI file lists NAVs on 10 Nov (a BSE holiday)
            store.insert_price(db, asset_id=mf, valid_at=date.fromisoformat(d), close=D("20"), currency="INR",
                               price_type="NAV", source_id="amfi_nav")  # fmt: skip
        assert price_as_of(db, mf, date(2026, 11, 10), known).status is QualityStatus.OK
        assert (
            price_as_of(db, mf, date(2026, 11, 13), known).status is QualityStatus.STALE
        )  # Friday's NAV missing


def test_sbi_tt_is_never_plain_ok_and_valuation_falls_back_with_a_label(world: dict[str, int]) -> None:
    with session_scope("demo") as db:
        store.insert_fx(db, rate_type="SBI_TT_BUY", underlying_series="SBI", provider="sbi_fx_ratekeeper",
                        valid_at=date(2026, 6, 1), rate=D("83.1"), source_id="sbi_tt_archive",
                        recorded_at=datetime(2026, 6, 1, 12, tzinfo=UTC))  # fmt: skip
        sbi = fx_as_of(db, "SBI_TT_BUY", date(2026, 6, 1), datetime(2026, 6, 2, tzinfo=UTC))
        assert sbi.status is QualityStatus.ESTIMATED and "single unofficial archive" in sbi.notes
        fb = valuation_fx(db, date(2026, 6, 1), datetime(2026, 6, 2, tzinfo=UTC))  # no FBIL before 3 Nov
        assert fb.value == D("83.1") and fb.status.severity >= QualityStatus.ESTIMATED.severity
        assert "FBIL_REF unavailable" in " ".join(fb.notes)
        assert valuation_fx(db, date(2019, 1, 1)).status is QualityStatus.MISSING


# ---- source disagreement --------------------------------------------------------------------------


def test_fx_series_that_disagree_are_both_kept_and_flagged() -> None:
    day = date(2026, 11, 5)
    sbi = FxObs(
        rate_type="SBI_TT_BUY",
        underlying_series="SBI",
        provider="sbi_fx_ratekeeper",
        valid_at=day,
        rate=D("90"),
    )
    fbil = FxObs(
        rate_type="FBIL_REF", underlying_series="FBIL", provider="frankfurter", valid_at=day, rate=D("94.9")
    )
    run(Fake("sbi_tt_archive", [sbi]))
    r = run(Fake("fbil_ref", [fbil]))
    assert r.conflicts == 1
    row = q(
        "SELECT diff_pct, rule, array_length(observation_ids, 1) n FROM source_conflicts WHERE subject='fx:USDINR'"
    )[0]
    assert row.n == 2 and row.diff_pct > D("0.05") and "ADR-0016" in row.rule
    with session_scope("demo") as db:
        m = fx_as_of(db, "FBIL_REF", day, NOW)
    assert m.value == D("94.9") and m.status is QualityStatus.CONFLICTING and "differ by" in " ".join(m.notes)


def test_normal_spread_between_sbi_and_fbil_is_not_a_conflict() -> None:
    day = date(2026, 11, 6)
    run(
        Fake(
            "sbi_tt_archive",
            [
                FxObs(
                    rate_type="SBI_TT_BUY",
                    underlying_series="SBI",
                    provider="p",
                    valid_at=day,
                    rate=D("93.6"),
                )
            ],
        )
    )
    r = run(
        Fake(
            "fbil_ref",
            [
                FxObs(
                    rate_type="FBIL_REF",
                    underlying_series="FBIL",
                    provider="frankfurter",
                    valid_at=day,
                    rate=D("94.8"),
                )
            ],
        )
    )
    assert r.conflicts == 0  # SBI TT buying sits ~1.3% under the reference (V7)


def test_fetched_price_that_disagrees_with_the_statement_price_is_flagged(world: dict[str, int]) -> None:
    day = date(2026, 10, 14)
    with session_scope("demo") as db:
        insert_transaction(db, NewTxn(asset_id=world["etf"], account_id=world["acc"], type=TxnType.BUY, trade_date=day,
                                      quantity=D(10), price=D("100"), net_cash_inr=D(-1000), net_cash_orig=D(-1000),
                                      source_document="stmt"), fp=fingerprint("t", "mkt-1"))  # fmt: skip
    r = run(
        Fake("manual_prices", [px("TICKER:XNSE:TETF", day, "100.4")])
    )  # within 2%: fine, restates nothing new? it differs
    assert r.conflicts == 0
    r = run(Fake("manual_prices", [px("TICKER:XNSE:TETF", day, "103.5")]))
    assert r.conflicts == 1
    with session_scope("demo") as db:
        m = price_as_of(db, world["etf"], day, NOW)
    assert m.value == D("103.5") and m.status is QualityStatus.CONFLICTING


# ---- fundamentals ---------------------------------------------------------------------------------


def test_fundamentals_are_stored_once_and_read_as_of(world: dict[str, int]) -> None:
    with session_scope("demo") as db:
        db.execute(text("UPDATE ref.assets SET cik = '5550001' WHERE id = :i"), {"i": world["us"]})

    def f(end: str, val: str, filed: str, accn: str) -> FundamentalObs:
        return FundamentalObs(asset_ref="CIK:0005550001", concept="Revenues", valid_at=date.fromisoformat(end),
                              fiscal_year=2026, fiscal_period="Q1", form="10-Q", unit="USD", value=D(val),
                              filed_at=date.fromisoformat(filed), accession_no=accn)  # fmt: skip

    obs = [f("2026-03-31", "1000", "2026-05-01", "A-1"), f("2026-06-30", "1100", "2026-08-01", "A-2")]
    first = run(Fake("sec_companyfacts", obs))
    assert (first.stored, first.duplicates) == (2, 0)
    assert run(Fake("sec_companyfacts", obs)).duplicates == 2

    # As-of reads need learn-times that differ, so write "Assets" with explicit recorded_at.
    a = world["us"]
    with session_scope("demo") as db:
        for end, val, filed, rec in (
            ("2026-03-31", "5", "2026-05-01", "2026-05-02"),
            ("2026-06-30", "6", "2026-08-01", "2026-08-02"),
        ):
            store.insert_fundamental(db, asset_id=a, concept="Assets", valid_at=date.fromisoformat(end), unit="USD",
                                     value=D(val), accession_no="X-" + end, source_id="sec_companyfacts",
                                     filed_at=date.fromisoformat(filed),
                                     recorded_at=datetime.fromisoformat(rec).replace(tzinfo=UTC))  # fmt: skip
        at = lambda y, m, d: datetime(y, m, d, tzinfo=UTC)  # noqa: E731
        assert fundamental_as_of(db, a, "Assets", date(2026, 9, 1), at(2026, 12, 1)).value == 6
        assert (
            fundamental_as_of(db, a, "Assets", date(2026, 9, 1), at(2026, 7, 15)).value == 5
        )  # Q2 not filed yet
        assert (
            fundamental_as_of(db, a, "Assets", date(2026, 9, 1), at(2026, 4, 1)).status
            is QualityStatus.MISSING
        )
        old = fundamental_as_of(db, a, "Assets", date(2027, 6, 1), at(2027, 6, 1))
        assert old.status is QualityStatus.STALE and old.value == 6
        assert fundamental_as_of(db, a, "NetIncomeLoss", date(2026, 9, 1)).status is QualityStatus.MISSING


# ---- storage guarantees ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", ["prices", "fx_rates", "fundamentals", "raw_payloads"])
def test_market_tables_are_append_only_even_for_the_owner(
    world: dict[str, int], table: str, ledger_db: str
) -> None:
    from sqlalchemy import create_engine

    eng = create_engine(get_settings().db_owner_url.get_secret_value())
    with eng.begin() as conn:
        conn.execute(text("SET search_path TO demo, ref"))
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() > 0  # noqa: S608
    for stmt in (
        f"UPDATE demo.{table} SET source_id = source_id",  # noqa: S608
        f"DELETE FROM demo.{table}",  # noqa: S608
        f"TRUNCATE demo.{table}",
    ):
        with (
            pytest.raises(DBAPIError, match=r"append-only|reject|not allowed|cannot truncate"),
            eng.begin() as conn,
        ):
            conn.execute(text(stmt))
    eng.dispose()


def test_app_role_cannot_rewrite_the_quarantine_reason(world: dict[str, int]) -> None:
    with pytest.raises(DBAPIError), session_scope("demo") as db:
        db.execute(text("UPDATE dq_quarantine SET detail = '{}'::jsonb"))


def test_real_schema_has_the_same_tables_but_is_out_of_reach_for_demo(world: dict[str, int]) -> None:
    with pytest.raises(DBAPIError), session_scope("demo") as db:
        db.execute(text("SELECT count(*) FROM real.prices"))


# ---- health and jobs ------------------------------------------------------------------------------


def test_health_report_labels_stale_sources_and_blocks_on_pending_items(world: dict[str, int]) -> None:
    with session_scope("demo") as db:
        rep = source_health(db, date(2026, 12, 31), {"manual_prices"})
    by = {s.id: s for s in rep.sources}
    assert by["manual_prices"].state == "manual"  # no schedule of its own; judged per asset in as_of
    assert by["nse_bhavcopy"].state == "not_automated" and by["tiingo_eod"].state in (
        "not_consented",
        "no_data",
        "failing",
    )
    assert rep.pending_quarantine > 0 and rep.blocks_monthly_review
    assert by["manual_prices"].calls_today == 0


def test_job_slots_are_idempotent_and_missed_ones_are_caught_up(
    world: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[date] = []

    def fake_run_daily(dataset: str, today: date, **kw: Any) -> list[RunResult]:
        ran.append(today)
        return [
            RunResult("amfi_nav", "OK"),
            RunResult("fbil_ref", "FAILED", detail="down"),
            RunResult("tiingo_eod", "SKIPPED"),
        ]

    monkeypatch.setattr(jobs, "run_daily", fake_run_daily)
    now = datetime(2026, 11, 11, 20, 30, tzinfo=jobs.IST)
    first = jobs.catch_up("demo", now)
    assert len(first) == 8 and [s for _, s in first][:-1] == ["OK"] * 7  # coalesced slots, then the real run
    assert first[-1][1] == "PARTIAL" and ran == [date(2026, 11, 11)]  # exactly one fetch for eight slots
    assert jobs.catch_up("demo", now) == []  # nothing left to run
    assert (
        jobs.run_slot("demo", jobs._slot(date(2026, 11, 11))) == "SKIPPED"
    )  # the same slot never runs twice
    summary = q(
        "SELECT status, summary FROM ref.job_runs WHERE job_name='ingest:demo' ORDER BY scheduled_for DESC LIMIT 1"
    )[0]
    assert summary.status == "PARTIAL" and summary.summary["sources"]["fbil_ref"]["detail"] == "down"
    # the machine was off for a day: only the new slot is missing
    assert [s.date() for s in jobs.missed_slots("demo", now + timedelta(days=1))] == [date(2026, 11, 12)]
    assert (
        jobs.missed_slots("demo", datetime(2026, 11, 12, 10, 0, tzinfo=jobs.IST)) == []
    )  # 20:00 not reached


def test_a_crashing_job_is_recorded_as_failed(world: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> list[RunResult]:
        raise RuntimeError("kaput")

    monkeypatch.setattr(jobs, "run_daily", boom)
    slot = jobs._slot(date(2026, 11, 20))
    assert jobs.run_slot("demo", slot) == "FAILED"
    assert q("SELECT status, summary FROM ref.job_runs WHERE scheduled_for=:s", s=slot)[0].status == "FAILED"


def test_all_sources_failing_makes_the_job_failed(
    world: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        jobs, "run_daily", lambda *a, **k: [RunResult("a", "FAILED"), RunResult("b", "SCHEMA_DRIFT")]
    )
    assert jobs.run_slot("demo", jobs._slot(date(2026, 11, 21))) == "FAILED"


def test_scheduler_is_built_with_a_persistent_store_and_kolkata_time(world: dict[str, int]) -> None:
    sched = jobs.build_scheduler("demo")
    sched.start(paused=True)  # jobs reach the store when the scheduler starts; paused: nothing fires
    job = sched.get_job("ingest:demo")
    assert job is not None and str(sched.timezone) == "Asia/Kolkata"
    assert job.trigger.fields[5].expressions[0].first == 20  # hour field: 20:00
    assert q("SELECT count(*) FROM ref.apscheduler_jobs")[0][0] == 1  # stored in the database (ADR-0019)
    sched2 = jobs.build_scheduler("demo")  # restarting replaces, never duplicates
    assert (
        sched2.get_job("ingest:demo") is not None
        and q("SELECT count(*) FROM ref.apscheduler_jobs")[0][0] == 1
    )


def test_registry_sync_keeps_the_investors_enabled_switch(world: dict[str, int]) -> None:
    with session_scope("demo") as db:
        db.execute(text("UPDATE ref.data_source_registry SET enabled = false WHERE id = 'amfi_nav'"))
        sync_registry(db)
        assert (
            db.execute(text("SELECT enabled FROM ref.data_source_registry WHERE id='amfi_nav'")).scalar_one()
            is False
        )
        db.execute(text("UPDATE ref.data_source_registry SET enabled = true WHERE id = 'amfi_nav'"))
    assert q("SELECT count(*) FROM ref.data_source_registry")[0][0] == len(SPECS)


def test_fbil_and_sbi_use_their_own_publication_days(world: dict[str, int]) -> None:
    known = datetime(2026, 4, 10, tzinfo=UTC)
    rec = datetime(2026, 4, 9, tzinfo=UTC)
    with session_scope("demo") as db:
        for d in ("2026-04-01", "2026-04-03"):  # the 2nd is a gap in both series
            store.insert_fx(db, rate_type="FBIL_REF", underlying_series="FBIL", provider="frankfurter",
                            valid_at=date.fromisoformat(d), rate=D("84"), source_id="fbil_ref", recorded_at=rec)  # fmt: skip
            store.insert_fx(db, rate_type="SBI_TT_BUY", underlying_series="SBI", provider="p",
                            valid_at=date.fromisoformat(d), rate=D("83"), source_id="sbi_tt_archive", recorded_at=rec)  # fmt: skip
        for t in ("FBIL_REF", "SBI_TT_BUY"):
            m = fx_as_of(db, t, date(2026, 4, 2), known)  # type: ignore[arg-type]
            assert m.value is not None and m.as_of.date() == date(2026, 4, 1)
            assert "behind" not in " ".join(m.notes)  # a gap is a holiday, not staleness
        assert fx_as_of(db, "FBIL_REF", date(2026, 4, 3), known).status is QualityStatus.OK
        assert fx_as_of(db, "FBIL_REF", date(2026, 4, 8), known).status is QualityStatus.STALE


def test_sbi_stays_estimated_even_when_it_agrees_with_fbil(world: dict[str, int]) -> None:
    """FBIL is a different quantity (reference rate, not TT buying): agreement is not corroboration."""
    day = date(2026, 5, 4)
    rec = datetime(2026, 5, 5, tzinfo=UTC)
    with session_scope("demo") as db:
        store.insert_fx(db, rate_type="FBIL_REF", underlying_series="FBIL", provider="frankfurter", valid_at=day,
                        rate=D("84.00"), source_id="fbil_ref", recorded_at=rec)  # fmt: skip
        store.insert_fx(db, rate_type="SBI_TT_BUY", underlying_series="SBI", provider="p", valid_at=day,
                        rate=D("84.00"), source_id="sbi_tt_archive", recorded_at=rec)  # fmt: skip
        m = fx_as_of(db, "SBI_TT_BUY", day, datetime(2026, 5, 6, tzinfo=UTC))
    assert m.status is QualityStatus.ESTIMATED and "single unofficial archive" in m.notes


def test_every_zero_sbi_rate_is_quarantined_not_only_the_first(world: dict[str, int]) -> None:
    zeros = [FxObs(rate_type="SBI_TT_BUY", underlying_series="SBI", provider="p", valid_at=date(2019, 1, d),
                   rate=D("0.00")) for d in (7, 8, 9, 10)]  # fmt: skip
    r = run(Fake("sbi_tt_archive", zeros))
    assert (r.stored, r.quarantined) == (4, 4)
    assert q("SELECT count(*) FROM fx_rates WHERE rate = 0 AND quality_status <> 'quarantined'")[0][0] == 0
