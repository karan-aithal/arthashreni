"""Command-line interface: `arthashreni ...`."""

import getpass
import logging
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import create_engine, text

from arthashreni.core.config import get_settings
from arthashreni.core.logging import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False)
user_app = typer.Typer(no_args_is_help=True, help="Local user account.")
backup_app = typer.Typer(no_args_is_help=True, help="Encrypted backups (ADR-0014).")
app.add_typer(user_app, name="user")
app.add_typer(backup_app, name="backup")

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
