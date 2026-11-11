"""Documented benchmarks (F1). A mapping from what the investor holds to the index it should be judged
against, with the reason. No index level is fetched automatically: no compliant free source for index
levels is in the source registry (D1), so a benchmark's series must be supplied as manual price rows of
an asset whose ticker is `ticker`. Without them the comparison is `missing`, never substituted."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Benchmark:
    id: str
    name: str
    ticker: str  # ticker of the asset row that carries the index series (manual CSV import)
    asset_classes: frozenset[str]
    rationale: str


BENCHMARKS: tuple[Benchmark, ...] = (
    Benchmark(
        "NIFTY50_TRI", "Nifty 50 Total Return Index", "NIFTY50TRI",
        frozenset({"MF", "ETF_IN", "EQUITY_IN"}),
        "Broad large-cap Indian equity; total-return because fund NAVs already include dividends.",
    ),
    Benchmark(
        "SP500_TRI", "S&P 500 Total Return Index (USD)", "SP500TR",
        frozenset({"EQUITY_US", "ETF_US"}),
        "Broad US large-cap equity in USD; compare in USD first, then read the FX effect separately.",
    ),
    Benchmark(
        "GOLD_INR", "Gold price in INR", "GOLDINR",
        frozenset({"GOLD_ETF", "SGB"}),
        "Spot gold in INR; gold ETFs and bonds track it before expenses.",
    ),
    Benchmark(
        "SILVER_INR", "Silver price in INR", "SILVERINR",
        frozenset({"SILVER_ETF"}),
        "Spot silver in INR; silver ETFs track it before expenses.",
    ),
)  # fmt: skip


def benchmark_for(asset_class: str) -> Benchmark | None:
    return next((b for b in BENCHMARKS if asset_class in b.asset_classes), None)
