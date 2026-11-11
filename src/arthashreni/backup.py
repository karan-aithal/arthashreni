"""Encrypted backup and restore (ADR-0014).

Archive = tar of {manifest.json, db.dump (pg_dump -Fc), data/...}, encrypted with
age to an X25519 public key. Only the public key is needed to back up; the
private identity is needed only to restore and should live off this machine.
"""

import io
import json
import os
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pyrage
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from arthashreni.core.config import Settings

SCHEMAS = ("ref", "demo", "real")


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class PgTarget:
    """libpq connection pieces; the password goes via PGPASSWORD, never argv."""

    url: URL

    @classmethod
    def from_sqlalchemy(cls, url: str) -> "PgTarget":
        return cls(make_url(url))

    def with_db(self, name: str) -> "PgTarget":
        return PgTarget(self.url.set(database=name))

    @property
    def database(self) -> str:
        return self.url.database or ""

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            PGHOST=self.url.host or "127.0.0.1",
            PGPORT=str(self.url.port or 5432),
            PGUSER=self.url.username or "",
            PGPASSWORD=self.url.password or "",
            PGDATABASE=self.database,
        )
        return env

    def connect(self, autocommit: bool = False) -> psycopg.Connection[tuple[object, ...]]:
        return psycopg.connect(
            host=self.url.host or "127.0.0.1",
            port=self.url.port or 5432,
            user=self.url.username,
            password=self.url.password,
            dbname=self.database,
            autocommit=autocommit,
        )


def _run(cmd: list[str], target: PgTarget) -> None:
    result = subprocess.run(cmd, env=target.env(), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise BackupError(f"{cmd[0]} failed: {result.stderr.strip()[:500]}")


def table_counts(target: PgTarget) -> dict[str, int]:
    """Row count of every table in the project schemas."""
    with target.connect() as conn:
        tables = conn.execute(
            "SELECT schemaname, tablename FROM pg_tables WHERE schemaname = ANY(%s) ORDER BY 1, 2",
            (list(SCHEMAS),),
        ).fetchall()
        counts: dict[str, int] = {}
        for schema, table in tables:
            q = sql.SQL("SELECT count(*) FROM {}.{}").format(
                sql.Identifier(str(schema)), sql.Identifier(str(table))
            )
            row = conn.execute(q).fetchone()
            counts[f"{schema}.{table}"] = int(str(row[0])) if row else 0
    return counts


def _dump(target: PgTarget, out: Path) -> None:
    _run(["pg_dump", "--format=custom", "--file", str(out)], target)


def _recreate_db(owner: PgTarget, name: str) -> None:
    if name == owner.database:
        raise BackupError(f"refusing to overwrite the main database {name!r}")
    with owner.with_db("postgres").connect(autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def _restore_dump(owner: PgTarget, dump: Path, name: str) -> PgTarget:
    _recreate_db(owner, name)
    target = owner.with_db(name)
    _run(["pg_restore", "--exit-on-error", "--no-owner", "--dbname", name, str(dump)], target)
    return target


def drop_db(owner: PgTarget, name: str) -> None:
    if name == owner.database:
        raise BackupError(f"refusing to drop the main database {name!r}")
    with owner.with_db("postgres").connect(autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def generate_identity(out: Path) -> str:
    """Write a new age identity (private key) with mode 600; return its public key."""
    identity = pyrage.x25519.Identity.generate()
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(str(identity) + "\n")
    return str(identity.to_public())


def create_backup(settings: Settings, now: datetime | None = None) -> Path:
    if not settings.backup_recipient:
        raise BackupError("ARTHA_BACKUP_RECIPIENT (age public key) is not set")
    recipient = pyrage.x25519.Recipient.from_str(settings.backup_recipient.strip())
    owner = PgTarget.from_sqlalchemy(settings.db_owner_url.get_secret_value())
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")

    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "db.dump"
        _dump(owner, dump)
        manifest = {
            "created_at": stamp,
            "database": owner.database,
            "table_counts": table_counts(owner),
        }
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            raw = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
            tar.add(dump, arcname="db.dump")
            backup_dir = settings.backup_dir.resolve()
            if settings.data_dir.is_dir():
                for path in sorted(settings.data_dir.rglob("*")):
                    if path.is_file() and backup_dir not in path.resolve().parents:
                        tar.add(path, arcname=f"data/{path.relative_to(settings.data_dir)}")
        # ponytail: whole archive in memory; fine for a personal portfolio, stream if it passes ~1 GB
        encrypted = pyrage.encrypt(buf.getvalue(), [recipient])

    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    out = settings.backup_dir / f"arthashreni-{stamp}.tar.age"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(encrypted)
    return out


@dataclass(frozen=True)
class RestoreResult:
    database: str
    tables_checked: int
    data_files: int


def restore_backup(
    settings: Settings,
    archive: Path,
    identity_file: Path,
    target_db: str | None = None,
    data_out: Path | None = None,
    keep: bool = True,
) -> RestoreResult:
    """Decrypt, restore into a separate database, and check row counts against the manifest."""
    identity = pyrage.x25519.Identity.from_str(identity_file.read_text().strip())
    plain = pyrage.decrypt(archive.read_bytes(), [identity])
    owner = PgTarget.from_sqlalchemy(settings.db_owner_url.get_secret_value())
    name = target_db or settings.restore_test_db

    with tempfile.TemporaryDirectory() as tmp, tarfile.open(fileobj=io.BytesIO(plain)) as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
        tar.extract("db.dump", tmp, filter="data")
        data_members = [m for m in tar.getmembers() if m.name.startswith("data/") and m.isfile()]
        if data_out is not None:
            for m in data_members:
                tar.extract(m, data_out, filter="data")
        target = _restore_dump(owner, Path(tmp) / "db.dump", name)

    restored = table_counts(target)
    expected: dict[str, int] = manifest["table_counts"]
    if restored != expected:
        diff = {
            k: (expected.get(k), restored.get(k))
            for k in expected.keys() | restored.keys()
            if expected.get(k) != restored.get(k)
        }
        raise BackupError(f"row counts differ after restore (expected, restored): {diff}")
    if not keep:
        drop_db(owner, name)
    return RestoreResult(database=name, tables_checked=len(expected), data_files=len(data_members))


def restore_test(settings: Settings) -> RestoreResult:
    """Weekly check that needs no private key: dump, restore into scratch DB, compare, drop."""
    owner = PgTarget.from_sqlalchemy(settings.db_owner_url.get_secret_value())
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "db.dump"
        expected = table_counts(owner)
        _dump(owner, dump)
        target = _restore_dump(owner, dump, settings.restore_test_db)
    restored = table_counts(target)
    drop_db(owner, settings.restore_test_db)
    if restored != expected:
        raise BackupError("row counts differ between live and restored database")
    return RestoreResult(database=settings.restore_test_db, tables_checked=len(expected), data_files=0)
