from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from arthashreni import backup
from arthashreni.core.config import get_settings


@pytest.fixture
def settings_with_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    identity = tmp_path / "identity.txt"
    public = backup.generate_identity(identity)
    data = tmp_path / "data"
    (data / "imports").mkdir(parents=True)
    (data / "imports" / "statement.csv").write_text("a,b\n1,2\n")
    monkeypatch.setenv("ARTHA_BACKUP_RECIPIENT", public)
    monkeypatch.setenv("ARTHA_DATA_DIR", str(data))
    monkeypatch.setenv("ARTHA_BACKUP_DIR", str(data / "backups"))
    get_settings.cache_clear()
    yield identity
    get_settings.cache_clear()


def test_identity_file_is_private(settings_with_key: Path) -> None:
    assert settings_with_key.stat().st_mode & 0o777 == 0o600


def test_encrypted_backup_restores_with_matching_counts(settings_with_key: Path, tmp_path: Path) -> None:
    s = get_settings()
    engine = create_engine(s.db_owner_url.get_secret_value())
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS demo.backup_probe (n int)"))
        conn.execute(text("INSERT INTO demo.backup_probe SELECT generate_series(1, 25)"))
    engine.dispose()

    archive = backup.create_backup(s)
    assert archive.read_bytes().startswith(b"age-encryption.org/v1")
    assert archive.stat().st_mode & 0o777 == 0o600
    assert b"statement.csv" not in archive.read_bytes()  # contents are encrypted

    out = tmp_path / "restored"
    result = backup.restore_backup(s, archive, settings_with_key, data_out=out, keep=False)
    assert result.tables_checked >= 3
    assert result.data_files == 1
    assert (out / "data" / "imports" / "statement.csv").read_text() == "a,b\n1,2\n"


def test_wrong_key_cannot_decrypt(settings_with_key: Path, tmp_path: Path) -> None:
    archive = backup.create_backup(get_settings())
    other = tmp_path / "other.txt"
    backup.generate_identity(other)
    with pytest.raises(Exception, match=r"(?i)decrypt|no identity|matching"):
        backup.restore_backup(get_settings(), archive, other)


def test_restore_test_without_key() -> None:
    result = backup.restore_test(get_settings())
    assert result.tables_checked >= 3


def test_refuses_main_database(settings_with_key: Path) -> None:
    s = get_settings()
    archive = backup.create_backup(s)
    main = backup.PgTarget.from_sqlalchemy(s.db_owner_url.get_secret_value()).database
    with pytest.raises(backup.BackupError, match="main database"):
        backup.restore_backup(s, archive, settings_with_key, target_db=main)


def test_backup_needs_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTHA_BACKUP_RECIPIENT", "")
    get_settings.cache_clear()
    with pytest.raises(backup.BackupError, match="RECIPIENT"):
        backup.create_backup(get_settings())
    get_settings.cache_clear()
