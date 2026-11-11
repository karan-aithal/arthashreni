"""Load accounts and assets from CSV (setup data, not statements). Idempotent."""

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.core.config import Dataset
from arthashreni.ledger.assets import add_asset
from arthashreni.ledger.repository import account_ids, add_account

ASSET_COLUMNS = (
    "asset_class,name,currency,country,isin,amfi_code,ticker,exchange_mic,mf_plan,mf_option,"
    "fund_house,sector,maturity_date,coupon_rate,coupon_frequency_months"
)
ACCOUNT_COLUMNS = "label,platform,account_kind,currency,folio_or_ref_masked"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(f)]


def load_accounts(db: Session, path: Path) -> int:
    existing = account_ids(db)
    added = 0
    for r in _rows(path):
        if r["label"] in existing:
            continue
        add_account(
            db,
            platform=r["platform"],
            account_kind=r["account_kind"],
            label=r["label"],
            currency=r.get("currency") or "INR",
            folio_or_ref_masked=r.get("folio_or_ref_masked") or None,
        )
        added += 1
    return added


def load_assets(db: Session, dataset: Dataset, path: Path) -> int:
    added = 0
    for r in _rows(path):
        found = db.execute(
            text(
                """SELECT 1 FROM ref.assets WHERE is_demo = :demo AND
                   ((:isin <> '' AND isin = :isin) OR (:amfi <> '' AND amfi_code = :amfi)
                    OR (:ticker <> '' AND ticker = :ticker AND exchange_mic = :mic))"""
            ),
            {"demo": dataset == "demo", "isin": r.get("isin", ""), "amfi": r.get("amfi_code", ""),
             "ticker": r.get("ticker", ""), "mic": r.get("exchange_mic", "")},
        ).first()  # fmt: skip
        if found:
            continue
        add_asset(
            db,
            dataset,
            asset_class=r["asset_class"],
            name=r["name"],
            currency=r["currency"],
            country=r["country"],
            isin=r.get("isin") or None,
            amfi_code=r.get("amfi_code") or None,
            ticker=r.get("ticker") or None,
            exchange_mic=r.get("exchange_mic") or None,
            mf_plan=r.get("mf_plan") or None,
            mf_option=r.get("mf_option") or None,
            fund_house=r.get("fund_house") or None,
            sector=r.get("sector") or None,
            maturity_date=date.fromisoformat(r["maturity_date"]) if r.get("maturity_date") else None,
            coupon_rate=Decimal(r["coupon_rate"]) if r.get("coupon_rate") else None,
            coupon_frequency_months=int(r["coupon_frequency_months"])
            if r.get("coupon_frequency_months")
            else None,
        )
        added += 1
    return added
