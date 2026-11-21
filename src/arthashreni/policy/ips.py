"""Investment Policy Statement (B5). A versioned object the investor writes and approves; the system may
propose a draft but never treats a draft as approved. Bands are fractions of the portfolio's value."""

from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ONE = Decimal(1)


class Band(BaseModel):
    model_config = ConfigDict(frozen=True)
    low: Decimal = Field(ge=0, le=1)
    high: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.low > self.high:
            raise ValueError("band low is above band high")
        return self

    @property
    def mid(self) -> Decimal:
        return (self.low + self.high) / 2


class AdvisoryWeights(BaseModel):
    """Illustrative hypotheses (F3.4): they cannot be statistically validated on one investor's monthly
    decisions. They never override the hard gates, vetoes, bands or the budget."""

    model_config = ConfigDict(frozen=True)
    market: Decimal = Decimal(25)
    quality: Decimal = Decimal(25)
    fit: Decimal = Decimal(20)
    valuation: Decimal = Decimal(15)
    context: Decimal = Decimal(15)

    @model_validator(mode="after")
    def _hundred(self) -> Self:
        if sum(self.model_dump().values()) != 100:
            raise ValueError("advisory weights must add up to 100")
        return self


class IPS(BaseModel):
    model_config = ConfigDict(frozen=True)
    asset_class_bands: dict[str, Band]
    country_bands: dict[str, Band] = {}
    currency_bands: dict[str, Band] = {}
    rebalance_breach_margin: Decimal = Field(default=Decimal("0.05"), ge=0, le=1)  # sales only beyond this
    max_monthly_mix_change: Decimal = Field(
        default=Decimal("0.05"), gt=0, le=1
    )  # half the sum of |weight change|
    cooldown_months: int = Field(default=3, ge=0)
    max_holding: Decimal | None = Field(default=None, gt=0, le=1)  # one stock, fund or ETF
    max_fund_house: Decimal | None = Field(default=None, gt=0, le=1)
    max_sector: Decimal | None = Field(default=None, gt=0, le=1)
    max_theme: Decimal | None = Field(default=None, gt=0, le=1)  # enforced for assets with a theme
    emergency_fund_min_months: Decimal = Field(default=Decimal(6), ge=0)
    planned_expense_window_days: int = Field(default=180, ge=0)
    leftover_rule: Literal["RESERVE_NO_INFLATE"] = "RESERVE_NO_INFLATE"
    sale_proceeds_to: Literal["RESERVE", "BUDGET"] = "RESERVE"
    ipo_monthly_sinking_inr: Decimal = Field(default=Decimal(0), ge=0)
    cost_threshold_pct: Decimal = Field(default=Decimal("0.01"), ge=0)
    advisory_weights: AdvisoryWeights = AdvisoryWeights()

    @model_validator(mode="after")
    def _feasible(self) -> Self:
        for name, bands in (
            ("asset class", self.asset_class_bands),
            ("country", self.country_bands),
            ("currency", self.currency_bands),
        ):
            if bands and not sum((b.low for b in bands.values()), Decimal(0)) <= ONE <= sum(
                (b.high for b in bands.values()), Decimal(0)
            ):
                raise ValueError(f"{name} bands cannot add up to 100%")
        if not self.asset_class_bands:
            raise ValueError("an IPS needs asset class bands")
        return self


def ips_from_json(body: dict[str, Any]) -> IPS:
    return IPS.model_validate(body)
