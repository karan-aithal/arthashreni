"""Tiingo end-of-day prices (personal API key; internal use only). Request shape per Tiingo's docs (V3)."""

import json
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ValidationError

from arthashreni.ingestion.sources.contract import (
    Adapter,
    Observation,
    PriceObs,
    RawPayload,
    SchemaDrift,
    Window,
)
from arthashreni.ingestion.sources.http import Http

URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"


class _Row(BaseModel):
    date: datetime
    close: Decimal
    splitFactor: Decimal | None = None


class Tiingo(Adapter):
    source_id = "tiingo_eod"

    def __init__(self, http: Http, api_key: str, tickers: list[str]) -> None:
        self.http, self.key, self.tickers = http, api_key, tickers

    def fetch(self, window: Window) -> RawPayload:
        # ponytail: one failing ticker fails the run; per-ticker isolation if the watchlist grows
        out = {}
        for t in self.tickers:
            body = self.http.get(
                URL.format(ticker=t),
                params={"startDate": window.start.isoformat(), "endDate": window.end.isoformat()},
                headers={"Authorization": f"Token {self.key}", "Content-Type": "application/json"},
            )
            out[t] = json.loads(body, parse_float=Decimal)
        return RawPayload(
            self.source_id, datetime.now(UTC), window, json.dumps(out, default=str).encode(), 200, "json"
        )

    def parse(self, payload: RawPayload) -> list[Observation]:
        try:
            data = json.loads(payload.body, parse_float=Decimal)
            if not isinstance(data, dict):
                raise SchemaDrift("Tiingo payload is not an object keyed by ticker")
            out: list[Observation] = []
            for ticker, rows in data.items():
                if not isinstance(rows, list):
                    raise SchemaDrift(f"Tiingo response for {ticker} is not a list")
                for raw in rows:
                    r = _Row.model_validate(raw)
                    out.append(
                        PriceObs(
                            asset_ref=f"TICKER:{ticker}",
                            valid_at=r.date.astimezone(UTC).date() if r.date.tzinfo else r.date.date(),
                            close=r.close,
                            currency="USD",
                            price_type="CLOSE",
                            split_factor=r.splitFactor,
                        )
                    )
            return out
        except (ValueError, ValidationError) as exc:
            raise SchemaDrift(f"Tiingo response does not match the expected shape: {str(exc)[:200]}") from exc
