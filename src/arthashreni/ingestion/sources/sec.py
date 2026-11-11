"""SEC EDGAR XBRL `companyfacts` (declared User-Agent, <= 2 req/s): whitelisted fundamentals only."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ValidationError

from arthashreni.ingestion.sources.contract import (
    Adapter,
    FundamentalObs,
    Observation,
    RawPayload,
    SchemaDrift,
    Window,
)
from arthashreni.ingestion.sources.http import Http

URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
# "Download only what you need" (SEC): a small set of statement concepts, not the whole filing.
CONCEPTS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "EarningsPerShareDiluted",
    "NetCashProvidedByUsedInOperatingActivities",
)


class _Fact(BaseModel):
    end: date
    val: Decimal
    accn: str
    fy: int | None = None
    fp: str | None = None
    form: str | None = None
    filed: date | None = None


class SecCompanyFacts(Adapter):
    source_id = "sec_companyfacts"

    def __init__(self, http: Http, ciks: list[str]) -> None:
        self.http, self.ciks = http, ciks

    def fetch(self, window: Window) -> RawPayload:
        out = {}
        for cik in self.ciks:
            body = self.http.get(URL.format(cik=cik.zfill(10)))
            out[cik.zfill(10)] = json.loads(body, parse_float=Decimal)
        return RawPayload(
            self.source_id, datetime.now(UTC), window, json.dumps(out, default=str).encode(), 200, "json"
        )

    def parse(self, payload: RawPayload) -> list[Observation]:
        try:
            data = json.loads(payload.body, parse_float=Decimal)
            if not isinstance(data, dict):
                raise SchemaDrift("SEC payload is not an object keyed by CIK")
            out: list[Observation] = []
            for cik, doc in data.items():
                gaap = doc["facts"]["us-gaap"]
                for concept in CONCEPTS:
                    for unit, facts in gaap.get(concept, {}).get("units", {}).items():
                        for raw in facts:
                            f = _Fact.model_validate(raw)
                            if f.end < payload.window.start:
                                continue
                            out.append(
                                FundamentalObs(
                                    asset_ref=f"CIK:{cik}",
                                    concept=concept,
                                    valid_at=f.end,
                                    fiscal_year=f.fy,
                                    fiscal_period=f.fp,
                                    form=f.form,
                                    unit=unit,
                                    value=f.val,
                                    filed_at=f.filed,
                                    accession_no=f.accn,
                                )
                            )
            return out
        except (KeyError, TypeError, AttributeError, ValueError, ValidationError) as exc:
            raise SchemaDrift(
                f"SEC companyfacts does not match the expected shape: {str(exc)[:200]}"
            ) from exc
