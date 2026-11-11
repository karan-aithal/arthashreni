"""Settings from environment / .env. No secrets have defaults."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]

Dataset = Literal["demo", "real"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARTHA_", env_file=PROJECT_ROOT / ".env", extra="ignore")

    db_owner_url: SecretStr
    db_demo_url: SecretStr
    db_real_url: SecretStr
    dataset: Dataset = "demo"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    allow_non_local_bind: bool = False
    session_hours: int = 12

    data_dir: Path = PROJECT_ROOT / "data"
    backup_dir: Path = PROJECT_ROOT / "data" / "backups"
    # age X25519 public key (age1...). Only the public key lives on this machine (ADR-0014).
    backup_recipient: str | None = None
    restore_test_db: str = "arthashreni_restore_test"

    log_level: str = "INFO"

    # External data (Phase 3). Nothing is fetched from a source unless it is listed in `consented_sources`
    # (comma-separated registry ids): requests reveal which instruments the investor follows (invariant 10).
    consented_sources: str = ""
    tiingo_api_key: SecretStr | None = None
    sec_user_agent: str | None = None  # "Name contact@example.com"; sent to the SEC only (D1)
    raw_dir: Path = PROJECT_ROOT / "data" / "raw"
    ingest_time: str = "20:00"  # Asia/Kolkata, daily ingest (G3)

    def consented(self) -> set[str]:
        return {x.strip() for x in self.consented_sources.split(",") if x.strip()}

    def app_url(self, dataset: Dataset | None = None) -> str:
        ds = dataset or self.dataset
        url = self.db_demo_url if ds == "demo" else self.db_real_url
        return url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from env
