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
