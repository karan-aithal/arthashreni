"""Adapter contract (D3): `fetch(window) -> RawPayload`, `parse(RawPayload) -> list[Observation]`."""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SourceError(Exception):
    """Base class for adapter failures."""


class SourceUnavailable(SourceError):
    """Network error, timeout, 5xx or 429 after retries: a provider outage."""


class SchemaDrift(SourceError):
    """The payload no longer has the expected shape: the whole run is quarantined (D3)."""


class NotConsented(SourceError):
    """The investor has not consented to contacting this source (invariant 10)."""


@dataclass(frozen=True)
class Window:
    start: date
    end: date


@dataclass(frozen=True)
class RawPayload:
    source_id: str
    fetched_at: datetime
    window: Window
    body: bytes
    http_status: int | None = 200
    suffix: str = "bin"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


class _Obs(BaseModel):
    model_config = ConfigDict(frozen=True)


class PriceObs(_Obs):
    kind: Literal["price"] = "price"
    asset_ref: str  # AMFI:<code> or TICKER:<symbol>
    valid_at: date
    close: Decimal
    currency: str
    price_type: Literal["CLOSE", "NAV", "STATEMENT"]
    split_factor: Decimal | None = None


class FxObs(_Obs):
    kind: Literal["fx"] = "fx"
    rate_type: Literal["SBI_TT_BUY", "FBIL_REF", "BROKER_ACTUAL"]
    underlying_series: Literal["FBIL", "SBI", "BROKER"]
    provider: str
    valid_at: date
    observed_at: datetime | None = None
    rate: Decimal
    pair: str = "USDINR"


class FundamentalObs(_Obs):
    kind: Literal["fundamental"] = "fundamental"
    asset_ref: str  # CIK:<10 digits>
    concept: str
    valid_at: date  # period end
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    form: str | None = None
    unit: str
    value: Decimal
    filed_at: date | None = None
    accession_no: str


Observation = PriceObs | FxObs | FundamentalObs


class Adapter(ABC):
    """One source. Subclasses hold their own targets and an `Http` client (rate limit, retries)."""

    source_id: str

    @abstractmethod
    def fetch(self, window: Window) -> RawPayload: ...

    @abstractmethod
    def parse(self, payload: RawPayload) -> list[Observation]: ...
