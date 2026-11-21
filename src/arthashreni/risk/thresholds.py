"""Risk thresholds and combination rules, read from config/risk_thresholds.toml (every row has a rationale)."""

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum
from functools import lru_cache
from pathlib import Path

from arthashreni.core.config import PROJECT_ROOT

CONFIG = PROJECT_ROOT / "config" / "risk_thresholds.toml"


class Level(IntEnum):
    NORMAL = 0
    CAUTION = 1
    HIGH_RISK = 2
    SEVERE_STRESS = 3


@dataclass(frozen=True)
class Threshold:
    id: str
    unit: str
    direction: str  # above | below
    caution: Decimal
    high: Decimal
    severe: Decimal
    rationale: str
    backtested: bool


@dataclass(frozen=True)
class Rules:
    severe_stress_if_severe: int
    high_risk_if_severe: int
    high_risk_if_high: int
    caution_if_high: int
    caution_if_caution: int
    alert_min_level: Level
    market_wide_ratio: Decimal
    asset_specific_ratio: Decimal
    min_correlation: Decimal


@dataclass(frozen=True)
class Config:
    thresholds: dict[str, Threshold]
    rules: Rules


def _d(v: object) -> Decimal:
    return Decimal(str(v))


def parse(text: str) -> Config:
    raw = tomllib.loads(text)
    ths: dict[str, Threshold] = {}
    for k, t in raw["signal"].items():
        th = Threshold(
            k,
            t["unit"],
            t["direction"],
            _d(t["caution"]),
            _d(t["high"]),
            _d(t["severe"]),
            t["rationale"],
            bool(t["backtested"]),
        )
        ordered = (
            th.caution <= th.high <= th.severe
            if th.direction == "above"
            else th.caution >= th.high >= th.severe
        )
        if th.direction not in ("above", "below") or not ordered or not th.rationale.strip():
            raise ValueError(f"threshold {k} is malformed or has no rationale")
        ths[k] = th
    lr, dc = raw["level_rules"], raw["decline"]
    rules = Rules(
        lr["severe_stress_if_severe"],
        lr["high_risk_if_severe"],
        lr["high_risk_if_high"],
        lr["caution_if_high"],
        lr["caution_if_caution"],
        Level[lr["alert_min_level"]],
        _d(dc["market_wide_ratio"]),
        _d(dc["asset_specific_ratio"]),
        _d(dc["min_correlation"]),
    )
    return Config(ths, rules)


@lru_cache
def load(path: Path = CONFIG) -> Config:
    return parse(path.read_text())
