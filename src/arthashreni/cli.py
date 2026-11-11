"""Command-line interface: `arthashreni ...`."""

import getpass
import logging
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import create_engine, text

from arthashreni.core.config import get_settings
from arthashreni.core.logging import configure_logging
from arthashreni.db.session import session_scope
from arthashreni.ingestion.pipeline import PreviewResult
from arthashreni.ledger.types import LedgerError

app = typer.Typer(no_args_is_help=True, add_completion=False)
user_app = typer.Typer(no_args_is_help=True, help="Local user account.")
backup_app = typer.Typer(no_args_is_help=True, help="Encrypted backups (ADR-0014).")
account_app = typer.Typer(no_args_is_help=True, help="Accounts (broker accounts, folios, demat).")
asset_app = typer.Typer(no_args_is_help=True, help="Asset registry.")
import_app = typer.Typer(no_args_is_help=True, help="Statement imports: preview, then commit (C4).")
ledger_app = typer.Typer(no_args_is_help=True, help="Ledger inspection and corrections.")
reconcile_app = typer.Typer(no_args_is_help=True, help="Ledger vs statement reconciliation.")
demo_app = typer.Typer(no_args_is_help=True, help="DEMO dataset.")
for _name, _sub in (
    ("user", user_app), ("backup", backup_app), ("account", account_app), ("asset", asset_app),
    ("import", import_app), ("ledger", ledger_app), ("reconcile", reconcile_app), ("demo", demo_app),
):  # fmt: skip
    app.add_typer(_sub, name=_name)

log = logging.getLogger("arthashreni.cli")


@app.callback()
def _main() -> None:
    configure_logging(get_settings().log_level)


@app.command()
def serve() -> None:
    """Run the API (localhost only unless explicitly allowed)."""
    import uvicorn

    s = get_settings()
    if s.api_host not in ("127.0.0.1", "localhost", "::1"):
        if not s.allow_non_local_bind:
            raise typer.BadParameter(
                f"refusing to bind {s.api_host}; set ARTHA_ALLOW_NON_LOCAL_BIND=true to override"
            )
        log.warning("API bound to non-local address %s", s.api_host)
    uvicorn.run("arthashreni.api.app:app", host=s.api_host, port=s.api_port, log_config=None)


@user_app.command("init")
def user_init(
    username: Annotated[str, typer.Option(prompt=True)],
    reset: Annotated[bool, typer.Option(help="Replace the existing user's password.")] = False,
) -> None:
    """Create the single local user (runs as the owner role)."""
    from arthashreni.api.auth import hash_password

    password = getpass.getpass("Password (min 12 chars): ")
    if len(password) < 12:
        raise typer.BadParameter("password must be at least 12 characters")
    if getpass.getpass("Repeat password: ") != password:
        raise typer.BadParameter("passwords do not match")
    engine = create_engine(get_settings().db_owner_url.get_secret_value())
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM ref.local_user")).first() is not None
        if exists and not reset:
            raise typer.BadParameter("user already exists; use --reset to change the password")
        conn.execute(
            text(
                "INSERT INTO ref.local_user (id, username, password_hash) VALUES (1, :u, :h) "
                "ON CONFLICT (id) DO UPDATE SET username = :u, password_hash = :h"
            ),
            {"u": username, "h": hash_password(password)},
        )
        conn.execute(text("DELETE FROM ref.auth_sessions"))  # log out everywhere
    typer.echo(f"local user {username!r} saved")


@backup_app.command("keygen")
def backup_keygen(out: Annotated[Path, typer.Option(help="Where to write the private identity.")]) -> None:
    """Create an age key pair. Keep the identity file OFF this machine."""
    from arthashreni.backup import generate_identity

    public = generate_identity(out)
    typer.echo(f"identity (private, mode 600): {out}")
    typer.echo(f"public key, put in .env as ARTHA_BACKUP_RECIPIENT:\n{public}")


@backup_app.command("create")
def backup_create() -> None:
    """Dump the database and data/ into an encrypted archive."""
    from arthashreni.backup import create_backup

    out = create_backup(get_settings())
    typer.echo(f"backup written: {out}")


@backup_app.command("restore")
def backup_restore(
    archive: Path,
    identity: Annotated[Path, typer.Option(help="Private age identity file.")],
    target_db: Annotated[str | None, typer.Option(help="Database to restore into.")] = None,
    data_out: Annotated[Path | None, typer.Option(help="Directory to extract data/ files into.")] = None,
    keep: Annotated[bool, typer.Option(help="Keep the restored database afterwards.")] = True,
) -> None:
    """Restore an archive into a separate database and verify row counts. Never touches the main DB."""
    from arthashreni.backup import restore_backup

    r = restore_backup(get_settings(), archive, identity, target_db, data_out, keep)
    typer.echo(f"restored into {r.database}: {r.tables_checked} tables match, {r.data_files} data files")


@backup_app.command("restore-test")
def backup_restore_test() -> None:
    """Dump live DB, restore into the scratch DB, compare row counts, drop it (no key needed)."""
    from arthashreni.backup import restore_test

    r = restore_test(get_settings())
    typer.echo(f"restore test ok: {r.tables_checked} tables match in {r.database}")


# ---- Phase 2: accounts, assets, imports, ledger -------------------------------------------------


@account_app.command("add")
def account_add(
    label: str,
    platform: Annotated[
        str, typer.Option(help="INDMONEY, ZERODHA_COIN, ZERODHA_KITE, CAMS, KFINTECH, OTHER")
    ],
    kind: Annotated[str, typer.Option(help="BROKER, MF_FOLIO, DEMAT, BANK, RESERVE")],
    currency: str = "INR",
    masked: Annotated[str | None, typer.Option(help="Account number, last 4 characters only.")] = None,
) -> None:
    from arthashreni.ledger.repository import add_account

    with session_scope() as db:
        new_id = add_account(db, platform=platform, account_kind=kind, label=label, currency=currency,
                             folio_or_ref_masked=masked)  # fmt: skip
    typer.echo(f"account {label!r} added (id {new_id})")


@account_app.command("list")
def account_list() -> None:
    from arthashreni.ledger.repository import account_ids

    with session_scope() as db:
        for label, acc_id in sorted(account_ids(db).items()):
            typer.echo(f"{acc_id:>4}  {label}")


@account_app.command("load")
def account_load(csv_file: Path) -> None:
    from arthashreni.ingestion.setup_csv import ACCOUNT_COLUMNS, load_accounts

    typer.echo(f"columns: {ACCOUNT_COLUMNS}")
    with session_scope() as db:
        typer.echo(f"{load_accounts(db, csv_file)} accounts added")


@asset_app.command("load")
def asset_load(csv_file: Path) -> None:
    from arthashreni.ingestion.setup_csv import ASSET_COLUMNS, load_assets

    typer.echo(f"columns: {ASSET_COLUMNS}")
    with session_scope() as db:
        typer.echo(f"{load_assets(db, get_settings().dataset, csv_file)} assets added")


@asset_app.command("add")
def asset_add(
    asset_class: str, name: str, currency: str, country: str,
    isin: str | None = None, amfi: str | None = None, ticker: str | None = None, mic: str | None = None,
) -> None:  # fmt: skip
    from arthashreni.ledger.assets import add_asset

    with session_scope() as db:
        new_id = add_asset(
            db,
            get_settings().dataset,
            asset_class=asset_class,
            name=name,
            currency=currency,
            country=country,
            isin=isin,
            amfi_code=amfi,
            ticker=ticker,
            exchange_mic=mic,
        )
    typer.echo(f"asset added (id {new_id})")


@import_app.command("template")
def import_template(
    kind: Annotated[str, typer.Argument(help="transactions | corporate-actions | holdings")],
) -> None:
    """Print a CSV template (header and one example row)."""
    from arthashreni.ingestion.csv_template import TEMPLATES

    if kind not in TEMPLATES:
        raise typer.BadParameter(f"choose one of {', '.join(TEMPLATES)}")
    header, example = TEMPLATES[kind]
    typer.echo(header)
    typer.echo(example)


def _print_preview(res: PreviewResult) -> None:
    typer.echo(f"{res.outcome}: {res.message}".rstrip(": "))
    if res.batch_id is not None:
        typer.echo(f"batch {res.batch_id}  counts {res.counts}")
    for w in res.warnings:
        typer.echo(f"  warning: {w}")
    for b in res.blocking:
        typer.echo(f"  BLOCKING: {b}")
    shown = [r for r in res.rows if r["status"] != "NEW"][:25]
    for r in shown:
        typer.echo(f"  row {r['row_no']:>4} {r['kind']:<17} {r['status']:<10} {r['reason']}")
    if res.outcome == "PREVIEW":
        typer.echo(
            f"Review, then: arthashreni import commit {res.batch_id}   (or: import reject {res.batch_id})"
        )


@import_app.command("file")
def import_file(
    path: Path,
    account: Annotated[
        str | None, typer.Option(help="Account label, for sources whose file does not name one.")
    ] = None,
    as_of: Annotated[str | None, typer.Option(help="Holdings date, YYYY-MM-DD.")] = None,
) -> None:
    """Parse a file and show a preview. Nothing is written to the ledger yet."""
    from arthashreni.ingestion.pipeline import preview_file

    password = None
    if path.suffix.lower() == ".pdf":
        password = getpass.getpass("CAS PDF password (used for this import only, never stored): ")
    with session_scope() as db:
        res = preview_file(db, get_settings().dataset, path, account=account,
                           as_of=date.fromisoformat(as_of) if as_of else None, password=password)  # fmt: skip
    _print_preview(res)


@import_app.command("scan")
def import_scan() -> None:
    """Preview every new file in data/imports/inbox/."""
    from arthashreni.ingestion.inbox import scan_inbox

    s = get_settings()
    inbox = s.data_dir / "imports" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    items = scan_inbox(s.dataset, inbox, s.data_dir / "imports" / "processed")
    if not items:
        typer.echo("inbox is empty")
    for i in items:
        suffix = f" (batch {i.batch_id})" if i.batch_id else ""
        typer.echo(f"{i.outcome:<17} {i.file}{suffix}  {i.message}")


@import_app.command("watch")
def import_watch(interval: Annotated[float, typer.Option(help="Seconds between scans.")] = 30.0) -> None:
    """Keep scanning data/imports/inbox/ and preview new files as they arrive (Ctrl-C to stop)."""
    from arthashreni.ingestion.inbox import watch_inbox

    s = get_settings()
    inbox = s.data_dir / "imports" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    typer.echo(f"watching {inbox} every {interval:g}s")
    try:
        for i in watch_inbox(s.dataset, inbox, s.data_dir / "imports" / "processed", interval=interval):
            suffix = f" (batch {i.batch_id})" if i.batch_id else ""
            typer.echo(f"{i.outcome:<17} {i.file}{suffix}  {i.message}")
    except KeyboardInterrupt:
        typer.echo("stopped")


@import_app.command("show")
def import_show(batch_id: int) -> None:
    from arthashreni.ingestion.pipeline import batch_rows

    with session_scope() as db:
        row = db.execute(
            text("SELECT source_kind, file_name, status, summary FROM import_batches WHERE id = :i"),
            {"i": batch_id},
        ).first()
        if row is None:
            raise typer.BadParameter(f"no batch {batch_id}")
        typer.echo(f"batch {batch_id}: {row[0]} {row[1]} [{row[2]}]  {row[3]}")
        for r in batch_rows(db, batch_id):
            if r["status"] != "NEW":
                typer.echo(f"  row {r['row_no']:>4} {r['status']:<10} {r['reason']}")


@import_app.command("commit")
def import_commit(
    batch_id: int,
    accept_skipped: Annotated[
        bool, typer.Option(help="Commit even though rejected/ambiguous rows are left out.")
    ] = False,
) -> None:
    """Approve a previewed batch: write it to the ledger, rebuild lots, reconcile."""
    from arthashreni.ingestion.pipeline import commit_batch

    try:
        with session_scope() as db:
            done = commit_batch(db, batch_id, accept_skipped=accept_skipped)
    except LedgerError as exc:
        typer.echo(f"not committed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"committed batch {batch_id}: inserted {done.inserted}, left out {done.skipped}")
    typer.echo(f"reconciliation: {done.reconciliation}")
    for d in done.open_differences:
        typer.echo(f"  UNEXPLAINED #{d.reconciliation_id}: account {d.account_id} asset {d.asset_id} "
                   f"as of {d.as_of}: ledger {d.ledger_units} vs statement {d.statement_units}")  # fmt: skip


@import_app.command("reject")
def import_reject(batch_id: int) -> None:
    from arthashreni.ingestion.pipeline import reject_batch

    with session_scope() as db:
        reject_batch(db, batch_id)
    typer.echo(f"batch {batch_id} rejected; its file can be imported again")


@ledger_app.command("holdings")
def ledger_holdings() -> None:
    """Units per account and asset from the effective ledger (corporate actions applied)."""
    from arthashreni.ledger.repository import current_book

    with session_scope() as db:
        names = {
            (int(r[0]), int(r[1])): f"{r[2]} / {r[3]}"
            for r in db.execute(
                text("SELECT ac.id, a.id, ac.label, a.name FROM accounts ac CROSS JOIN ref.assets a")
            )
        }
        for (acc, asset), units in sorted(current_book(db).holdings().items()):
            typer.echo(f"{units:>16}  {names.get((acc, asset), (acc, asset))}")


@ledger_app.command("correct")
def ledger_correct(
    txn_id: int,
    reason: Annotated[str, typer.Option(help="Why the original row was wrong.")],
    set_: Annotated[list[str], typer.Option("--set", help="field=value, repeatable.")],
) -> None:
    """Append a CORRECTION that supersedes a transaction (the original row is kept)."""
    from arthashreni.ledger.repository import correct_transaction, rebuild_lots

    changes = dict(item.split("=", 1) for item in set_)
    try:
        with session_scope() as db:
            new_id = correct_transaction(db, txn_id, reason, changes)
            rebuild_lots(db)
    except (LedgerError, ValueError) as exc:
        typer.echo(f"not corrected: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"transaction {txn_id} superseded by correction {new_id}")


@ledger_app.command("void")
def ledger_void(txn_id: int, reason: Annotated[str, typer.Option()]) -> None:
    """Cancel a transaction (a correction with zero quantity and zero cash)."""
    from arthashreni.ledger.repository import rebuild_lots, void_transaction

    try:
        with session_scope() as db:
            new_id = void_transaction(db, txn_id, reason)
            rebuild_lots(db)
    except (LedgerError, ValueError) as exc:
        typer.echo(f"not voided: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"transaction {txn_id} voided by correction {new_id}")


@reconcile_app.command("run")
def reconcile_run() -> None:
    from arthashreni.ledger.reconciliation import open_differences, reconcile_all

    with session_scope() as db:
        typer.echo(reconcile_all(db))
        for d in open_differences(db):
            typer.echo(
                f"  UNEXPLAINED #{d.reconciliation_id}: ledger {d.ledger_units} vs statement "
                f"{d.statement_units} (account {d.account_id}, asset {d.asset_id}, {d.as_of})"
            )


@reconcile_app.command("report")
def reconcile_report() -> None:
    """Every statement holding against the ledger, with its status."""
    from arthashreni.ledger.reconciliation import reconciliation_report

    with session_scope() as db:
        lines = reconciliation_report(db)
    for r in lines:
        note = f"  ({r.reason})" if r.reason else ""
        typer.echo(
            f"{r.as_of}  {r.status:<11} ledger {r.ledger_units:>16} statement {r.statement_units:>16} "
            f"diff {r.diff:>12}  {r.account} / {r.asset}{note}"
        )
    typer.echo(f"{len(lines)} statement holding(s)")


@reconcile_app.command("open")
def reconcile_open() -> None:
    """List unexplained differences; any of these blocks the monthly review."""
    from arthashreni.ledger.reconciliation import open_differences

    with session_scope() as db:
        items = open_differences(db)
    typer.echo(f"{len(items)} unexplained difference(s)")
    for d in items:
        typer.echo(
            f"  #{d.reconciliation_id}: ledger {d.ledger_units} vs statement {d.statement_units} "
            f"(account {d.account_id}, asset {d.asset_id}, {d.as_of})"
        )


@reconcile_app.command("accept")
def reconcile_accept(reconciliation_id: int, reason: Annotated[str, typer.Option()]) -> None:
    from arthashreni.ledger.reconciliation import accept_difference

    try:
        with session_scope() as db:
            accept_difference(db, reconciliation_id, reason)
    except LedgerError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"difference #{reconciliation_id} accepted with a recorded reason")


@demo_app.command("seed")
def demo_seed() -> None:
    """Load the synthetic DEMO portfolio through the normal import pipeline (safe to re-run)."""
    from arthashreni.demo import seed_demo

    if get_settings().dataset != "demo":
        raise typer.BadParameter("set ARTHA_DATASET=demo; the demo portfolio never goes into REAL")
    for step in seed_demo():
        typer.echo(f"{step.outcome:<17} {step.name}  {step.detail}")
