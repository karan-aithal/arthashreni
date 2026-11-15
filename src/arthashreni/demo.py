"""Seed the DEMO dataset from the committed synthetic portfolio in demo/portfolio/.

Runs through the normal import pipeline (preview, commit, rebuild lots, reconcile), so it doubles
as an end-to-end check. Re-running is a no-op: every file is recognised by its hash.
"""

from dataclasses import dataclass
from pathlib import Path

from arthashreni.core.config import PROJECT_ROOT
from arthashreni.db.session import session_scope
from arthashreni.ingestion.pipeline import commit_batch, preview_file
from arthashreni.ingestion.setup_csv import load_accounts, load_assets

DEMO_DIR = PROJECT_ROOT / "demo" / "portfolio"
# Corporate actions first: later sales assume the split, bonus and merger have happened.
IMPORT_ORDER = ("corporate_actions.csv", "transactions.csv", "holdings.csv")


@dataclass(frozen=True)
class SeedStep:
    name: str
    outcome: str
    detail: str


def seed_demo(directory: Path = DEMO_DIR) -> list[SeedStep]:
    steps: list[SeedStep] = []
    with session_scope("demo") as db:
        a = load_accounts(db, directory / "accounts.csv")
        n = load_assets(db, "demo", directory / "assets.csv")
    steps.append(SeedStep("accounts+assets", "OK", f"{a} accounts, {n} assets added"))
    for name in IMPORT_ORDER:
        with session_scope("demo") as db:
            res = preview_file(db, "demo", directory / name)
            if res.outcome != "PREVIEW":
                steps.append(SeedStep(name, res.outcome, res.message))
                continue
            assert res.batch_id is not None
            done = commit_batch(db, res.batch_id)
        steps.append(
            SeedStep(name, "COMMITTED", f"inserted {done.inserted}, reconciliation {done.reconciliation}")
        )
    return steps


# ---- DEMO policy (Phase 5): clearly synthetic, written to the demo schema only ----------------------------

DEMO_IPS = {
    "asset_class_bands": {
        "MF": {"low": "0.20", "high": "0.50"},
        "ETF_IN": {"low": "0.15", "high": "0.35"},
        "EQUITY_IN": {"low": "0.10", "high": "0.35"},
        "EQUITY_US": {"low": "0.15", "high": "0.45"},
        "GOLD_ETF": {"low": "0.00", "high": "0.10"},
    },
    "country_bands": {"IN": {"low": "0.55", "high": "0.85"}, "US": {"low": "0.15", "high": "0.45"}},
    "currency_bands": {"INR": {"low": "0.55", "high": "0.85"}, "USD": {"low": "0.15", "high": "0.45"}},
    "rebalance_breach_margin": "0.05",
    "max_monthly_mix_change": "0.10",
    "cooldown_months": 3,
    "max_holding": "0.50",
    "emergency_fund_min_months": "6",
    "planned_expense_window_days": 180,
    "ipo_monthly_sinking_inr": "0",
    "cost_threshold_pct": "0.01",
}


def seed_demo_policy() -> str:
    """Budget, SIP, reserves, approved DEMO IPS, context and two demo IPOs. Re-running changes nothing."""
    import json
    from datetime import date
    from decimal import Decimal

    from sqlalchemy import text

    from arthashreni.ledger.assets import resolve_asset
    from arthashreni.ledger.repository import account_ids
    from arthashreni.policy import store as pstore
    from arthashreni.policy.context import Certainty
    from arthashreni.policy.ips import IPS

    with session_scope("demo") as db:
        if db.execute(text("SELECT 1 FROM ips_versions")).first():
            return "DEMO policy already set up"
        if pstore.budget_for(db, date(2026, 1, 1)) is None:
            pstore.set_budget(db, Decimal("10000"), date(2026, 1, 1))
        pstore.ensure_reserve(db, "Cash reserve", "CASH_RESERVE")
        pstore.ensure_reserve(db, "IPO sinking fund", "IPO_SINKING_FUND", Decimal("30000"))
        a = resolve_asset(db, "demo", "AMFI:900001")
        pstore.add_sip(
            db, a.id, account_ids(db)["COIN-DEMO"], Decimal("2000"), 6, "ZERODHA_COIN", date(2025, 1, 1)
        )
        v = pstore.create_ips_draft(db, IPS.model_validate(DEMO_IPS))
        pstore.approve_ips(db, v, "DEMO IPS, approved automatically by the demo seed; synthetic, not advice")
        for name, value, certainty in (
            ("take_home_income_inr", 120000, Certainty.KNOWN), ("income_stable", True, Certainty.KNOWN),
            ("essential_expenses_inr", 50000, Certainty.KNOWN),
            ("emergency_fund_inr", 400000, Certainty.KNOWN), ("planned_expenses", [], Certainty.KNOWN),
        ):  # fmt: skip
            pstore.set_context(db, name, value, certainty)
        pstore.add_ipo(db, "DEMO-IPO-MAIN", "MAINBOARD", "demo RHP", Decimal("14800"), "UNRATED")
        pstore.add_ipo(db, "DEMO-IPO-SME", "SME", None, None, "UNRATED")
    _ = json
    return (
        "DEMO policy set up: budget 10,000, one SIP, two reserves, approved DEMO IPS, context, two demo IPOs"
    )
