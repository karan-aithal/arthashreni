"""A small hand-made ledger with prices on every flow date: TWR, XIRR and absolute return end to end
(database, price lookups, flows, chain-linking), checked against numbers worked out by hand."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import pyxirr

from arthashreni.db.session import session_scope
from arthashreni.dq.measured import QualityStatus
from arthashreni.ingestion.sources.contract import Window
from arthashreni.ingestion.sources.manual import ManualPrices
from arthashreni.ingestion.sources.registry import sync_registry
from arthashreni.ingestion.sources.runner import run_adapter
from arthashreni.ledger import assets as assets_mod
from arthashreni.ledger.models import NewTxn
from arthashreni.ledger.repository import add_account, insert_transaction
from arthashreni.ledger.types import TxnType
from arthashreni.portfolio import build_report

pytestmark = pytest.mark.usefixtures("ledger_db")
D = Decimal


@pytest.fixture(scope="module")
def world(ledger_db: str, tmp_path_factory: pytest.TempPathFactory) -> None:
    csv = tmp_path_factory.mktemp("prices") / "prices.csv"
    csv.write_text(
        "asset_ref,valid_at,close,currency,price_type\n"
        "TICKER:XNSE:TWR,2025-01-02,100,INR,CLOSE\n"
        "TICKER:XNSE:TWR,2025-02-03,110,INR,CLOSE\n"
        "TICKER:XNSE:TWR,2025-03-03,121,INR,CLOSE\n"
    )
    with session_scope("demo") as db:
        sync_registry(db)
        acc = add_account(db, platform="ZERODHA_KITE", account_kind="BROKER", label="TWR-ACC")
        asset = assets_mod.add_asset(
            db, "demo", asset_class="EQUITY_IN", name="Twr Co", currency="INR", country="IN",
            isin="ZZTWR000001", ticker="TWR", exchange_mic="XNSE",
        )  # fmt: skip
        for i, (day, qty, price, cash) in enumerate(
            [(date(2025, 1, 2), "10", "100", "-1000"), (date(2025, 2, 3), "10", "110", "-1100")]
        ):
            n = NewTxn(
                asset_id=asset, account_id=acc, type=TxnType.BUY, trade_date=day, quantity=D(qty), price=D(price),
                net_cash_inr=D(cash), net_cash_orig=D(cash), source_document="test",
            )  # fmt: skip
            insert_transaction(db, n, fp=f"twr-{i}")
    res = run_adapter(
        "demo", ManualPrices(Path(csv)), Window(date(2025, 1, 1), date(2025, 3, 3)), offline=True
    )
    assert res.status == "OK"


def test_twr_xirr_and_absolute_return_by_hand(world: None) -> None:
    with session_scope("demo") as db:
        r = build_report(db, "demo", date(2025, 3, 3))
    xirr_m, twr_m, abs_m = r.returns
    assert (xirr_m.measure, twr_m.measure, abs_m.measure) == ("XIRR", "TWR", "absolute return")
    # sub-period 1: (2200 - 1100) / 1000 = 1.10 ; sub-period 2: 2420 / 2200 = 1.10 ; TWR = 21%
    assert twr_m.result.value == D("0.21") and twr_m.result.status is QualityStatus.OK
    assert r.totals.value_inr.value == D(2420) and r.invested_inr.value == D(2100)
    assert abs_m.result.value == D(320) / D(2100)  # gain 320 on 2100 paid in
    flows = [(date(2025, 1, 2), -1000.0), (date(2025, 2, 3), -1100.0), (date(2025, 3, 3), 2420.0)]
    expected = pyxirr.xirr([d for d, _ in flows], [a for _, a in flows])
    assert xirr_m.result.value is not None and float(xirr_m.result.value) == pytest.approx(expected, abs=1e-9)
    assert [a.asset for a in r.asset_returns] == ["Twr Co"]
    assert (
        r.asset_returns[0].xirr.result.value == xirr_m.result.value
    )  # one asset: same money-weighted answer


def test_before_the_first_purchase_nothing_is_measurable(world: None) -> None:
    with session_scope("demo") as db:
        r = build_report(db, "demo", date(2025, 1, 1))
    assert r.holdings == () and r.totals.value_inr.value == D(0)
    assert r.returns[1].result.value is None  # TWR needs two valuation dates


def test_a_report_as_of_an_earlier_date_uses_only_what_was_priced_by_then(world: None) -> None:
    with session_scope("demo") as db:
        early = build_report(db, "demo", date(2025, 2, 3))
    # value 2200 on 2025-02-03, so TWR is the first sub-period only: (2200 - 1100) / 1000 - 1 = 10%
    assert early.returns[1].result.value == D("0.1")
    assert early.totals.value_inr.value == D(2200)
