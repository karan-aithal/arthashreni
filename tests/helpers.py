"""Builders for ledger unit tests."""

from datetime import date
from decimal import Decimal

from arthashreni.ledger.types import ActionType, CorporateAction, TxnRow, TxnType

D = Decimal


def tx(
    id: int,
    type: TxnType,
    on: date,
    qty: str | None = None,
    price: str | None = None,
    *,
    asset: int | None = 1,
    account: int = 1,
    fees: str = "0",
    stamp: str = "0",
    taxes: str = "0",
    fx: str | None = None,
    cash: str = "0",
    corrected_type: TxnType | None = None,
    supersedes: int | None = None,
) -> TxnRow:
    return TxnRow(
        id=id,
        asset_id=asset,
        account_id=account,
        type=type,
        trade_date=on,
        quantity=None if qty is None else D(qty),
        price=None if price is None else D(price),
        currency="INR" if fx is None else "USD",
        fx_rate=None if fx is None else D(fx),
        fx_rate_type=None if fx is None else "BROKER_ACTUAL",
        fees=D(fees),
        taxes=D(taxes),
        stamp_duty=D(stamp),
        net_cash_inr=D(cash),
        net_cash_orig=D(cash),
        corrected_type=corrected_type,
        supersedes_id=supersedes,
    )


def ca(
    id: int,
    kind: ActionType,
    on: date,
    frm: str = "1",
    to: str = "1",
    asset: int = 1,
    successor: int | None = None,
) -> CorporateAction:
    return CorporateAction(id, asset, kind, on, D(frm), D(to), successor)


from arthashreni.ingestion.common import ImportRowError  # noqa: E402
from arthashreni.ledger.assets import Asset, UnknownAsset  # noqa: E402


class FakeResolver:
    """In-memory Resolver: refs map to assets, labels to account ids."""

    def __init__(self) -> None:
        self.assets: dict[str, Asset] = {}
        self.accounts: dict[str, int] = {}
        self._next = 1

    def add_asset(
        self, ref: str, cls: str = "EQUITY_IN", currency: str = "INR", name: str | None = None
    ) -> int:
        asset = Asset(self._next, cls, name or ref, currency, None, None, None, None)
        self.assets[ref] = asset
        self._next += 1
        return asset.id

    def add_account(self, label: str) -> int:
        self.accounts[label] = len(self.accounts) + 1
        return self.accounts[label]

    def asset(self, ref: str) -> Asset:
        if ref not in self.assets:
            raise UnknownAsset(f"no asset registered for {ref!r}")
        return self.assets[ref]

    def account(self, label: str) -> int:
        if label not in self.accounts:
            raise ImportRowError(f"unknown account {label!r}")
        return self.accounts[label]
