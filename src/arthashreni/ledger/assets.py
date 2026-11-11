"""Asset registry (ref.assets). Each dataset sees only its own assets (DEMO vs REAL)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.core.config import Dataset
from arthashreni.ledger.types import LedgerError


class UnknownAsset(LedgerError):
    pass


@dataclass(frozen=True)
class Asset:
    id: int
    asset_class: str
    name: str
    currency: str
    isin: str | None
    amfi_code: str | None
    ticker: str | None
    exchange_mic: str | None


def add_asset(
    db: Session,
    dataset: Dataset,
    *,
    asset_class: str,
    name: str,
    currency: str,
    country: str,
    isin: str | None = None,
    amfi_code: str | None = None,
    ticker: str | None = None,
    exchange_mic: str | None = None,
    is_sme: bool = False,
    regulated: bool = True,
    mf_plan: str | None = None,
    mf_option: str | None = None,
    fund_house: str | None = None,
    sector: str | None = None,
    maturity_date: date | None = None,
    coupon_rate: Decimal | None = None,
    coupon_frequency_months: int | None = None,
) -> int:
    if is_sme:
        raise LedgerError("SME assets are outside the supported investment universe (ADR-0021)")
    return int(
        db.execute(
            text(
                """INSERT INTO ref.assets (asset_class, name, currency, country, isin, amfi_code, ticker,
                       exchange_mic, regulated, mf_plan, mf_option, fund_house, sector, maturity_date,
                       coupon_rate, coupon_frequency_months, is_demo)
                   VALUES (:asset_class, :name, :currency, :country, :isin, :amfi_code, :ticker,
                       :exchange_mic, :regulated, :mf_plan, :mf_option, :fund_house, :sector,
                       :maturity_date, :coupon_rate, :coupon_frequency_months, :is_demo)
                   RETURNING id"""
            ),
            {
                "asset_class": asset_class,
                "name": name,
                "currency": currency.upper(),
                "country": country.upper(),
                "isin": isin,
                "amfi_code": amfi_code,
                "ticker": ticker,
                "exchange_mic": exchange_mic,
                "regulated": regulated,
                "mf_plan": mf_plan,
                "mf_option": mf_option,
                "fund_house": fund_house,
                "sector": sector,
                "maturity_date": maturity_date,
                "coupon_rate": coupon_rate,
                "coupon_frequency_months": coupon_frequency_months,
                "is_demo": dataset == "demo",
            },
        ).scalar_one()
    )


def resolve_asset(db: Session, dataset: Dataset, ref: str) -> Asset:
    """Resolve `ISIN`, `AMFI:<code>`, `TICKER:<symbol>` or `TICKER:<MIC>:<symbol>`."""
    ref = ref.strip()
    where, params = "", {}
    upper = ref.upper()
    if upper.startswith("AMFI:"):
        where, params = "amfi_code = :v", {"v": ref[5:].strip()}
    elif upper.startswith("TICKER:"):
        parts = ref.split(":")[1:]
        if len(parts) == 2:
            where, params = "exchange_mic = :mic AND ticker = :v", {"mic": parts[0].upper(), "v": parts[1]}
        elif len(parts) == 1:
            where, params = "ticker = :v", {"v": parts[0]}
        else:
            raise UnknownAsset(f"bad ticker reference {ref!r}")
    elif len(ref) == 12 and ref.isalnum():
        where, params = "isin = :v", {"v": upper}
    else:
        raise UnknownAsset(
            f"unrecognised asset reference {ref!r} (use ISIN, AMFI:<code> or TICKER:<MIC>:<symbol>)"
        )

    rows = db.execute(
        text(
            "SELECT id, asset_class, name, currency, isin, amfi_code, ticker, exchange_mic "  # noqa: S608
            # `where` is one of the fixed fragments above; every value is bound
            f"FROM ref.assets WHERE {where} AND is_demo = :demo ORDER BY id"
        ),
        {**params, "demo": dataset == "demo"},
    ).all()
    if not rows:
        raise UnknownAsset(f"no asset registered for {ref!r}")
    if len(rows) > 1:
        raise UnknownAsset(f"{ref!r} matches {len(rows)} assets; give the exchange (TICKER:<MIC>:<symbol>)")
    r = rows[0]
    return Asset(int(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], r[7])
