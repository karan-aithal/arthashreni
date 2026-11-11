"""Build adapters for the enabled, consented sources and run them (the daily ingest, G3)."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial

import httpx
from pydantic import SecretStr
from sqlalchemy import text

from arthashreni import __version__
from arthashreni.core.config import Dataset, get_settings
from arthashreni.db.session import session_scope
from arthashreni.ingestion.sources.amfi import AmfiNav
from arthashreni.ingestion.sources.contract import Adapter, Window
from arthashreni.ingestion.sources.fx import Fbil, SbiTt
from arthashreni.ingestion.sources.http import Http
from arthashreni.ingestion.sources.registry import SPECS, SourceSpec, sync_registry
from arthashreni.ingestion.sources.runner import RunResult, record_call, run_adapter
from arthashreni.ingestion.sources.sec import SecCompanyFacts
from arthashreni.ingestion.sources.tiingo import Tiingo

FUNDAMENTALS_EVERY_DAYS = 7  # companyfacts change only when a filing lands
LOOKBACK_DAYS = {
    "amfi_nav": 0,
    "sbi_tt_archive": 14,
    "fbil_ref": 14,
    "tiingo_eod": 10,
    "sec_companyfacts": 1100,
}
AUTOMATED = ("amfi_nav", "sbi_tt_archive", "fbil_ref", "tiingo_eod", "sec_companyfacts")


@dataclass(frozen=True)
class Plan:
    spec: SourceSpec
    adapter: Adapter | None
    window: Window
    skip: str | None = None


def _log_call(dataset: Dataset, sid: str, ok: bool, status: int | None, detail: str) -> None:
    record_call(dataset, sid, ok, status, detail)


def _targets(dataset: Dataset) -> dict[str, list[str]]:
    with session_scope(dataset) as db:
        rows = db.execute(
            text("SELECT amfi_code, ticker, asset_class, cik FROM ref.assets WHERE is_demo = :d"),
            {"d": dataset == "demo"},
        ).all()
    return {
        "amfi": sorted({r[0] for r in rows if r[0]}),
        "us": sorted({r[1] for r in rows if r[1] and r[2] in ("EQUITY_US", "ETF_US")}),
        "cik": sorted({r[3] for r in rows if r[3]}),
    }


def plan(
    dataset: Dataset,
    today: date,
    *,
    only: set[str] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    force: bool = False,
    offline: bool = False,
) -> list[Plan]:
    settings = get_settings()
    targets = _targets(dataset)
    with session_scope(dataset) as db:
        sync_registry(db)
        reg = {
            r.id: r
            for r in db.execute(
                text("SELECT id, enabled, last_success_at FROM ref.data_source_registry")
            ).all()
        }
        used = {
            r[0]: r[1]
            for r in db.execute(
                text(
                    "SELECT source_id, count(*) FROM ref.source_calls "
                    "WHERE called_at >= date_trunc('day', now()) GROUP BY source_id"
                )
            ).all()
        }
    out: list[Plan] = []
    for sid in AUTOMATED:
        if only and sid not in only:
            continue
        spec = SPECS[sid]
        window = Window(today - timedelta(days=LOOKBACK_DAYS[sid]), today)

        def http(headers: dict[str, str] | None = None, sid: str = sid, spec: SourceSpec = spec) -> Http:
            return Http(
                sid,
                client=httpx.Client(transport=transport, follow_redirects=True) if transport else None,
                min_interval=spec.min_interval_seconds,
                headers={"User-Agent": f"arthashreni/{__version__}", **(headers or {})},
                sleep=sleep,
                on_call=partial(_log_call, dataset, sid),
            )

        skip: str | None = None
        adapter: Adapter | None = None
        if not reg[sid].enabled:
            skip = "disabled in the registry"
        elif spec.daily_quota and used.get(sid, 0) >= spec.daily_quota:
            skip = f"daily quota of {spec.daily_quota} calls used"
        elif sid == "amfi_nav":
            skip, adapter = (
                (None, AmfiNav(http(), set(targets["amfi"]))) if targets["amfi"] else ("no MF assets", None)
            )
        elif sid == "sbi_tt_archive":
            adapter = SbiTt(http())
        elif sid == "fbil_ref":
            adapter = Fbil(http())
        elif sid == "tiingo_eod":
            key = SecretStr("offline") if offline else settings.tiingo_api_key
            if not targets["us"]:
                skip = "no US assets"
            elif key is None:
                skip = "ARTHA_TIINGO_API_KEY is not set"
            else:
                adapter = Tiingo(http(), key.get_secret_value(), targets["us"])
        elif sid == "sec_companyfacts":
            ua = "offline" if offline else settings.sec_user_agent
            last = reg[sid].last_success_at
            if not targets["cik"]:
                skip = "no asset has a CIK"
            elif not ua:
                skip = "ARTHA_SEC_USER_AGENT is not set"
            elif last and not force and (today - last.date()).days < FUNDAMENTALS_EVERY_DAYS:
                skip = f"fetched {last.date()}; refreshed every {FUNDAMENTALS_EVERY_DAYS} days"
            else:
                adapter = SecCompanyFacts(http({"User-Agent": ua}), targets["cik"])
        out.append(Plan(spec, adapter, window, skip))
    return out


def run_daily(
    dataset: Dataset,
    today: date,
    *,
    only: set[str] | None = None,
    transport: httpx.BaseTransport | None = None,
    offline: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    force: bool = False,
    windows: dict[str, Window] | None = None,
) -> list[RunResult]:
    """Run every planned source. A failing source never stops the next one (invariant 13)."""
    results: list[RunResult] = []
    for p in plan(dataset, today, only=only, transport=transport, sleep=sleep, force=force, offline=offline):
        if p.adapter is None:
            results.append(RunResult(p.spec.id, "SKIPPED", detail=p.skip or ""))
            continue
        window = (windows or {}).get(p.spec.id, p.window)
        results.append(run_adapter(dataset, p.adapter, window, offline=offline))
    return results
