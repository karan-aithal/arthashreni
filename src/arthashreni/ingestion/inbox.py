"""Inbox scanner (D4): previews every new file in data/imports/inbox/.

Layout: files in the inbox root name their own account (CSV templates, CAS). Files for sources whose
export does not name an account (Zerodha tradebook, INDmoney) go in a sub-folder named after the
account label, e.g. inbox/ZERODHA-KITE/tradebook.csv. PDFs need a password, so they are listed and
imported with `arthashreni import file`. Processed files move to data/imports/processed/.
This is a polling scan; the scheduler (Phase 3) calls it on a timer.
"""

import shutil
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from arthashreni.core.config import Dataset
from arthashreni.db.session import session_scope
from arthashreni.ingestion.pipeline import preview_file

SUPPORTED = {".csv", ".xlsx", ".pdf"}


@dataclass(frozen=True)
class ScanItem:
    file: str
    outcome: str  # PREVIEW | ALREADY_IMPORTED | NEEDS_INPUT | NEEDS_PASSWORD | ERROR
    message: str = ""
    batch_id: int | None = None


def scan_inbox(dataset: Dataset, inbox: Path, processed: Path) -> list[ScanItem]:
    results: list[ScanItem] = []
    for path in sorted(p for p in inbox.rglob("*") if p.is_file()):
        if path.name.startswith(".") or path.suffix.lower() not in SUPPORTED:
            continue
        rel = str(path.relative_to(inbox))
        if path.suffix.lower() == ".pdf":
            results.append(
                ScanItem(rel, "NEEDS_PASSWORD", "run `arthashreni import file` and enter the password")
            )
            continue
        account = path.parent.name if path.parent != inbox else None
        try:
            with session_scope(dataset) as db:
                res = preview_file(db, dataset, path, account=account)
        except Exception as exc:  # one bad file must not stop the scan
            results.append(ScanItem(rel, "ERROR", f"{type(exc).__name__}: {exc}"))
            continue
        results.append(ScanItem(rel, res.outcome, res.message, res.batch_id))
        if res.outcome in {"PREVIEW", "ALREADY_IMPORTED"}:
            processed.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            shutil.move(path, processed / f"{stamp}-{path.name}")
    return results


def watch_inbox(
    dataset: Dataset,
    inbox: Path,
    processed: Path,
    *,
    interval: float = 30.0,
    iterations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[ScanItem]:
    """Poll the inbox forever (or `iterations` times), yielding each new result as it appears.

    Files that need the investor (unrecognised, needs a password) are reported once, not on every poll.
    """
    reported: set[tuple[str, str]] = set()
    n = 0
    while iterations is None or n < iterations:
        for item in scan_inbox(dataset, inbox, processed):
            key = (item.file, item.outcome)
            if item.outcome in {"NEEDS_INPUT", "NEEDS_PASSWORD", "ERROR"} and key in reported:
                continue
            reported.add(key)
            yield item
        n += 1
        if iterations is None or n < iterations:
            sleep(interval)
