"""A holding bought on a flow date but not yet priced there: TWR must refuse, XIRR stays available."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

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


def test_twr_is_missing_when_a_holding_has_no_price_on_a_flow_date(tmp_path: Path) -> None:
    csv = tmp_path / "prices.csv"
    csv.write_text(
        "asset_ref,valid_at,close,currency,price_type\n"
        "TICKER:XNSE:PA,2025-01-02,100,INR,CLOSE\n"
        "TICKER:XNSE:PA,2025-02-03,110,INR,CLOSE\n"
        "TICKER:XNSE:PA,2025-03-03,121,INR,CLOSE\n"
        "TICKER:XNSE:PB,2025-03-03,60,INR,CLOSE\n"  # B is first priced a month after it was bought
    )
    with session_scope("demo") as db:
        sync_registry(db)
        acc = add_account(db, platform="ZERODHA_KITE", account_kind="BROKER", label="PARTIAL-ACC")
        ids = [
            assets_mod.add_asset(
                db,
                "demo",
                asset_class="EQUITY_IN",
                name=name,
                currency="INR",
                country="IN",
                isin=f"ZZPART00000{i}",
                ticker=ticker,
                exchange_mic="XNSE",
            )
            for i, (name, ticker) in enumerate([("Part A", "PA"), ("Part B", "PB")])
        ]
        buys = [(ids[0], date(2025, 1, 2), "10", "100"), (ids[1], date(2025, 2, 3), "5", "50")]
        for i, (asset, day, qty, price) in enumerate(buys):
            cash = D(qty) * D(price)
            insert_transaction(
                db,
                NewTxn(
                    asset_id=asset,
                    account_id=acc,
                    type=TxnType.BUY,
                    trade_date=day,
                    quantity=D(qty),
                    price=D(price),
                    net_cash_inr=-cash,
                    net_cash_orig=-cash,
                    source_document="test",
                ),
                fp=f"part-{i}",
            )
    res = run_adapter("demo", ManualPrices(csv), Window(date(2025, 1, 1), date(2025, 3, 3)), offline=True)
    assert res.status == "OK"
    with session_scope("demo") as db:
        r = build_report(db, "demo", date(2025, 3, 3))
    xirr_m, twr_m, _ = r.returns
    assert r.totals.unvalued == ()  # everything is priced on the report date itself
    assert twr_m.result.value is None and twr_m.result.status is QualityStatus.MISSING
    assert "valuation left out holdings with no price on 2025-02-03" in twr_m.result.notes[-1]
    assert xirr_m.result.value is not None  # the money-weighted figure does not chain, so it stays
