"""Monthly decision engine (F3). Mostly rules, scores advisory. Order of steps:

1. hard gates: approved IPS, clean reconciliation, liquidity (emergency fund, planned expense, income stability),
   usable data per asset; any failure means "insufficient evidence" or "build liquidity" for that part of the budget;
2. policy allocation: new money goes to assets in classes below their IPS band, within the band highs, concentration
   limits, the monthly mix-change limit and the cooldown, in tiers (below band, then below mid) so a score never
   outranks a band;
3. risk vetoes: a severe asset-specific signal, a failed thesis or an exhausted concentration limit means no new money;
4. advisory score: orders candidates inside a tier; it cannot override steps 1-3;
5. cost rule (B4) and budget envelope (B1) last; the result must pass the envelope validator.

Committed SIPs are deducted first and counted as money already moving into their assets. Selling is never implied:
a band breached beyond the margin yields `review` (or `reduce` only when the investor flagged the thesis as failed)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import Any

from arthashreni.dq.confidence import ConfidenceLevel
from arthashreni.dq.measured import QualityStatus, exact
from arthashreni.policy.context import Context, Liquidity, Verdict, liquidity
from arthashreni.policy.cost import CostBook, CostVerdict, check_cost
from arthashreni.policy.envelope import PAISE, EnvelopeLine, Kind, OverAllocated, build_envelope
from arthashreni.policy.ips import IPS, Band
from arthashreni.policy.recommendation import Action, RecLine, Recommendation
from arthashreni.policy.score import advisory_score
from arthashreni.risk.thresholds import Level

ZERO, INF = Decimal(0), Decimal("Infinity")
RISKY = frozenset(
    {"EQUITY_IN", "EQUITY_US", "ETF_IN", "ETF_US", "MF", "SILVER_ETF"}
)  # MF assumed equity (V32)
BAD_DATA = frozenset(
    {QualityStatus.STALE, QualityStatus.MISSING, QualityStatus.CONFLICTING, QualityStatus.QUARANTINED}
)
MARKET_SCORE = {
    Level.NORMAL: Decimal(1),
    Level.CAUTION: Decimal("0.75"),
    Level.HIGH_RISK: Decimal("0.5"),
    Level.SEVERE_STRESS: Decimal("0.25"),
}
DEMO_LABEL = "DEMO: synthetic data, not advice; nothing is executed"
REAL_LABEL = "Decision support only; the investor decides; nothing is executed"


@dataclass(frozen=True)
class Holding:
    """A held asset that can receive new money."""

    asset_id: int
    name: str
    asset_class: str
    country: str
    currency: str
    platform: str
    value_inr: Decimal
    price_status: QualityStatus
    price_as_of: date | None = None
    sector: str | None = None
    fund_house: str | None = None
    thesis_failed: bool = False
    risk_veto: tuple[str, ...] = ()
    components: dict[str, Decimal | None] = field(
        default_factory=dict
    )  # advisory inputs (quality, valuation)


@dataclass(frozen=True)
class PastMove:
    period: date
    asset_class: str
    direction: str  # up (accumulated) | down (reduced)


@dataclass(frozen=True)
class Reserve:
    id: int
    name: str
    available_inr: Decimal


@dataclass(frozen=True)
class DecisionInput:
    period: date
    today: date
    dataset: str  # DEMO | REAL
    opening_budget_inr: Decimal
    sip_lines: tuple[EnvelopeLine, ...]
    holdings: tuple[Holding, ...]
    reconciliation_clean: bool
    confidence: ConfidenceLevel
    confidence_reasons: tuple[str, ...]
    risk_level: Level
    ips: IPS | None
    ips_version: int | None
    context: Context
    costs: CostBook
    cash_reserve: Reserve | None = None
    ipo_reserve: Reserve | None = None
    history: tuple[PastMove, ...] = ()
    reserve_use: tuple[
        tuple[int, Decimal], ...
    ] = ()  # (asset id, rupees) the investor asked to spend from the reserve
    scenarios: tuple[dict[str, Any], ...] = ()
    sources: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()  # e.g. signals with no data


def _floor(x: Decimal) -> Decimal:
    return x.quantize(PAISE, ROUND_DOWN) if x.is_finite() else x


def _cap_room(group_value: Decimal, cap: Decimal, total: Decimal) -> Decimal:
    """Most that can be added to a group so its weight stays within `cap` of the portfolio after the addition."""
    if cap >= 1:
        return INF
    return max(ZERO, (cap * total - group_value) / (1 - cap))


def _months_between(a: date, b: date) -> int:
    return abs((a.year - b.year) * 12 + a.month - b.month)


class _Plan:
    """Running values: what is held now plus what this month's plan has already sent in."""

    def __init__(self, inp: DecisionInput, ips: IPS) -> None:
        self.inp, self.ips = inp, ips
        self.val = {h.asset_id: h.value_inr for h in inp.holdings}
        self.v0 = sum(self.val.values(), ZERO)
        self.cls0: dict[str, Decimal] = {}
        for h in inp.holdings:
            self.cls0[h.asset_class] = self.cls0.get(h.asset_class, ZERO) + h.value_inr
        self.added = ZERO

    def put(self, asset_id: int, x: Decimal) -> None:
        if asset_id in self.val:
            self.val[asset_id] += x
        self.added += x

    def group(self, pick: Any) -> Decimal:
        return sum((self.val[h.asset_id] for h in self.inp.holdings if pick(h)), ZERO)

    def class_value(self, cls: str) -> Decimal:
        return self.group(lambda g: g.asset_class == cls)

    def total(self) -> Decimal:
        return self.v0 + self.added

    def turnover(self, h: Holding, x: Decimal) -> Decimal:
        """Half the sum of absolute class-weight changes against the start of the month, if `x` goes to `h`."""
        total = self.total() + x
        classes = {h2.asset_class for h2 in self.inp.holdings}
        return (
            sum(
                (
                    abs(
                        (self.group(lambda g, c=c: g.asset_class == c) + (x if c == h.asset_class else ZERO))
                        / total
                        - self.cls0.get(c, ZERO) / self.v0
                    )
                    for c in classes
                ),
                ZERO,
            )
            / 2
        )

    def room(
        self,
        h: Holding,
        band: Band,
        liq: Liquidity,
        risky0: Decimal,
        ceiling: Decimal,
        to_target: bool = True,
    ) -> tuple[Decimal, str]:
        """Largest addition to `h` (at most `ceiling`, the money available) that keeps every limit, and the limit
        that binds. The ceiling matters when no limit applies: the room is then unbounded and cannot be tested."""
        ips, total = self.ips, self.total()
        limits: list[tuple[Decimal, str]] = [
            (
                _cap_room(self.group(lambda g: g.asset_class == h.asset_class), band.mid, total)
                if to_target
                else INF,
                "class target",
            ),
            (
                _cap_room(self.group(lambda g: g.asset_class == h.asset_class), band.high, total),
                "class band high",
            ),
        ]
        if h.country in ips.country_bands:
            limits.append(
                (
                    _cap_room(
                        self.group(lambda g: g.country == h.country), ips.country_bands[h.country].high, total
                    ),
                    "country band",
                )
            )
        if h.currency in ips.currency_bands:
            limits.append(
                (
                    _cap_room(
                        self.group(lambda g: g.currency == h.currency),
                        ips.currency_bands[h.currency].high,
                        total,
                    ),
                    "currency band",
                )
            )
        if ips.max_holding is not None:
            limits.append((_cap_room(self.val[h.asset_id], ips.max_holding, total), "holding limit"))
        if ips.max_fund_house is not None and h.fund_house:
            limits.append(
                (
                    _cap_room(self.group(lambda g: g.fund_house == h.fund_house), ips.max_fund_house, total),
                    "fund-house limit",
                )
            )
        if ips.max_sector is not None and h.sector:
            limits.append(
                (_cap_room(self.group(lambda g: g.sector == h.sector), ips.max_sector, total), "sector limit")
            )
        if liq.income_unstable and h.asset_class in RISKY:
            limits.append(
                (
                    _cap_room(self.group(lambda g: g.asset_class in RISKY), risky0, total),
                    "risk level may not rise",
                )
            )
        x, why = min(limits, key=lambda t: t[0])
        x = min(x, ceiling)
        x = _floor(x)
        if x > 0 and self.turnover(h, x) > ips.max_monthly_mix_change:
            lo, hi = ZERO, x
            for _ in range(80):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if self.turnover(h, mid) <= ips.max_monthly_mix_change else (lo, mid)
            x, why = _floor(lo), "monthly mix-change limit"
            if x > 0 and self.turnover(h, x) > ips.max_monthly_mix_change:
                x = ZERO
        return x, why


def _exclusions(h: Holding, inp: DecisionInput, ips: IPS, liq: Liquidity) -> list[str]:
    out: list[str] = []
    if h.price_status in BAD_DATA:
        out.append(f"insufficient evidence: the price is {h.price_status.value}")
    out += list(h.risk_veto)
    if h.thesis_failed:
        out.append("the investor flagged the thesis as failed")
    if h.asset_class not in ips.asset_class_bands:
        out.append(f"the IPS has no band for {h.asset_class}")
    for m in inp.history:
        if (
            m.asset_class == h.asset_class
            and m.direction == "down"
            and _months_between(inp.period, m.period) < ips.cooldown_months
        ):
            out.append(
                f"cooldown: {h.asset_class} was reduced in {m.period:%Y-%m}, less than {ips.cooldown_months} months ago"
            )
    if liq.expense_window is Verdict.FAIL and h.asset_class in RISKY:
        out.append("a large planned expense is near: no new risk money")
    return out


@exact
def decide(inp: DecisionInput) -> Recommendation:
    ips = inp.ips
    opening = inp.opening_budget_inr
    free = opening - sum((s.amount_inr for s in inp.sip_lines), ZERO)
    if free < 0:
        raise OverAllocated(f"committed SIPs {opening - free} exceed the monthly budget {opening}")
    warnings: list[str] = list(inp.risk_notes)
    missing: list[str] = []
    drafts: list[tuple[EnvelopeLine | None, RecLine]] = []
    moves: list[dict[str, Any]] = []
    by_id = {h.asset_id: h for h in inp.holdings}
    conf = inp.confidence.value
    assumptions = [
        f"Leftover-cash rule: {ips.leftover_rule} (IPS v{inp.ips_version})"
        if ips and inp.ips_version
        else "Leftover-cash rule: default assumption (IPS not approved)"
    ]
    base_evidence = (f"risk_level:{inp.period.isoformat()}",) + (
        (f"ips:v{inp.ips_version}",) if inp.ips_version else ()
    )

    def line(
        action: Action,
        amount: Decimal,
        subject: str,
        reasons: list[str],
        env: EnvelopeLine | None = None,
        h: Holding | None = None,
        score: dict[str, Any] | None = None,
        freshness: str | None = None,
    ) -> None:
        data = h.price_status if h else None
        fresh = freshness or (data.value if data else "ok")
        evidence = (
            *base_evidence,
            *((f"price:{h.asset_id}:{h.price_as_of}",) if h and h.price_as_of else ()),
        )
        drafts.append(
            (
                env,
                RecLine(
                    action=action,
                    amount_inr=amount,
                    subject=subject,
                    asset_id=h.asset_id if h else (env.asset_id if env else None),
                    reasons=tuple(reasons),
                    evidence_ids=evidence,
                    risks=("market value can fall",)
                    + ((f"risk level is {inp.risk_level.name}",) if inp.risk_level >= Level.CAUTION else ()),
                    assumptions=tuple(assumptions),
                    invalidation_conditions=(
                        "the price data becomes stale, conflicting or missing",
                        "the IPS or personal context changes",
                    ),
                    data_freshness=fresh,
                    confidence="low" if data in BAD_DATA else conf,
                    score=score,
                ),
            )
        )

    # committed SIPs: pre-deducted (B1); a vetoed asset keeps its mandate but is flagged for review
    for s in inp.sip_lines:
        h = by_id.get(s.asset_id) if s.asset_id is not None else None
        line(
            Action.CONTINUE_SIP,
            s.amount_inr,
            h.name if h else f"asset {s.asset_id}",
            ["running SIP is pre-committed (B1)"],
            s,
            h,
        )
        if h and (h.risk_veto or h.thesis_failed):
            line(
                Action.REVIEW,
                ZERO,
                h.name,
                [
                    "this asset has a veto; review the running SIP (nothing is cancelled automatically)",
                    *h.risk_veto,
                ],
                None,
                h,
            )

    # ---- step 1: hard gates
    blocked: list[str] = []
    liq: Liquidity | None = None
    if ips is None:
        blocked.append("no approved IPS: no personalised allocation")
        missing.append("approved Investment Policy Statement")
    else:
        liq = liquidity(inp.context, ips, inp.today)
        warnings += list(liq.notes)
        assumptions += list(liq.assumptions)
        if liq.emergency is Verdict.UNKNOWN:
            blocked.append("insufficient evidence: emergency fund or essential expenses unknown")
            missing += ["emergency_fund_inr", "essential_expenses_inr"]
    if not inp.reconciliation_clean:
        blocked.append("reconciliation has unexplained differences: resolve them before allocating")

    cash = inp.cash_reserve
    if blocked or ips is None or liq is None:
        line(
            Action.HOLD_CASH,
            ZERO,
            "new money",
            [*blocked, "the unallocated amount stays in the budget (holding cash is a valid outcome)"],
        )
    else:
        plan = _Plan(inp, ips)
        for s in inp.sip_lines:  # SIPs are already moving into their assets this month
            if s.asset_id is not None:
                plan.put(s.asset_id, s.amount_inr)
        risky0 = (
            sum((h.value_inr for h in inp.holdings if h.asset_class in RISKY), ZERO) / plan.v0
            if plan.v0
            else ZERO
        )
        env_lines: list[EnvelopeLine] = []

        def to_reserve(r: Reserve | None, x: Decimal, why: list[str], subject: str | None = None) -> Decimal:
            nonlocal free
            x = min(_floor(x), free)
            if x <= 0:
                return ZERO
            if r is None:
                warnings.append("no reserve is set up: the amount stays unallocated (" + why[0] + ")")
                return ZERO
            e = EnvelopeLine(kind=Kind.RESERVE_TRANSFER, amount_inr=x, reserve_id=r.id)
            line(Action.HOLD_CASH, x, subject or r.name, why, e)
            moves.append(
                {
                    "reserve": r.name,
                    "reserve_id": r.id,
                    "direction": "in",
                    "amount_inr": str(x),
                    "reason": why[0],
                }
            )
            free -= x
            return x

        if liq.emergency is Verdict.FAIL:  # step 1: liquidity first
            to_reserve(
                cash,
                liq.emergency_shortfall_inr,
                ["build liquidity first: emergency fund is below the IPS minimum"],
            )
        if ips.ipo_monthly_sinking_inr > 0:
            to_reserve(
                inp.ipo_reserve,
                ips.ipo_monthly_sinking_inr,
                ["IPO sinking fund contribution from the IPS (mainboard IPOs only)"],
            )

        if plan.v0 <= 0:
            warnings.append("no valued holdings: weights are undefined, so nothing was allocated to assets")
            if inp.reserve_use:
                warnings.append("reserve use refused: no valued holdings, so weights are undefined")
        else:
            held_classes = {h.asset_class for h in inp.holdings}
            excluded: dict[int, list[str]] = {h.asset_id: _exclusions(h, inp, ips, liq) for h in inp.holdings}
            costly: set[int] = set()
            skipped: set[int] = set()
            allocated: dict[int, Decimal] = {}
            notes: dict[int, list[str]] = {}
            scores: dict[int, Any] = {}
            for _ in range(len(inp.holdings) * 3 + 3):
                ranked = []
                for h in inp.holdings:
                    if (
                        excluded[h.asset_id]
                        or h.asset_id in costly
                        or h.asset_id in skipped
                        or h.asset_class not in held_classes
                    ):
                        continue
                    band = ips.asset_class_bands[h.asset_class]
                    w = plan.class_value(h.asset_class) / plan.total()
                    if w >= band.mid:
                        continue
                    tier = 0 if w < band.low else 1
                    fit = min(Decimal(1), (band.mid - w) / band.mid)
                    ctx_score = (
                        Decimal(1)
                        if liq.emergency is Verdict.OK and not liq.income_unstable
                        else Decimal("0.5")
                    )
                    sc = advisory_score(
                        {
                            "market": MARKET_SCORE[inp.risk_level],
                            "fit": fit,
                            "context": ctx_score,
                            "quality": h.components.get("quality"),
                            "valuation": h.components.get("valuation"),
                        },
                        ips.advisory_weights,
                    )
                    scores[h.asset_id] = sc
                    ranked.append(
                        ((tier, -round((sc.total or Decimal("0.5")) * 10), -(band.mid - w), h.name), h, band)
                    )
                if not ranked or free < PAISE:
                    break
                _, h, band = min(ranked, key=lambda r: r[0])
                x, binding = plan.room(h, band, liq, risky0, free)
                if x < PAISE:
                    excluded[h.asset_id] = [f"no room for new money: {binding}"]
                    continue
                check = check_cost(x, inp.costs.get((h.platform, h.asset_class)), ips.cost_threshold_pct)
                if check.verdict is CostVerdict.BELOW_MIN_TICKET:
                    skipped.add(h.asset_id)
                    notes.setdefault(h.asset_id, []).append(check.note)
                    continue
                if check.verdict in (CostVerdict.TOO_COSTLY, CostVerdict.UNKNOWN):
                    costly.add(h.asset_id)
                    to_reserve(
                        cash,
                        x,
                        [f"cost rule: {check.note}; accumulate in the reserve and invest less often"],
                        h.name,
                    )
                    continue
                e = EnvelopeLine(kind=Kind.NEW_ALLOCATION, amount_inr=x, asset_id=h.asset_id)
                env_lines.append(e)
                plan.put(h.asset_id, x)
                free -= x
                allocated[h.asset_id] = x
                notes.setdefault(h.asset_id, []).extend(
                    [f"below its IPS band target; limited by {binding}", check.note]
                )
            for e in env_lines:
                h = by_id[e.asset_id] if e.asset_id is not None else None
                assert h is not None
                sc_ = scores.get(h.asset_id)
                line(
                    Action.ACCUMULATE,
                    e.amount_inr,
                    h.name,
                    notes[h.asset_id],
                    e,
                    h,
                    {
                        "total": None if sc_ is None or sc_.total is None else str(sc_.total),
                        "components": {
                            k: None if v is None else str(v)
                            for k, v in (sc_.components if sc_ else {}).items()
                        },
                        "note": sc_.note if sc_ else "",
                    },
                )
            for h in inp.holdings:
                if h.asset_id in allocated:
                    continue
                if excluded[h.asset_id] and any(
                    r.startswith("insufficient evidence") for r in excluded[h.asset_id]
                ):
                    line(Action.HOLD, ZERO, h.name, excluded[h.asset_id], None, h)
                elif h.risk_veto or h.thesis_failed:
                    line(Action.AVOID, ZERO, h.name, excluded[h.asset_id], None, h)
            for (
                cls,
                band,
            ) in ips.asset_class_bands.items():  # sales only beyond the margin, and never by default
                w = plan.class_value(cls) / plan.total()
                if w > band.high + ips.rebalance_breach_margin:
                    failed = any(h.thesis_failed for h in inp.holdings if h.asset_class == cls)
                    line(
                        Action.REDUCE if failed else Action.REVIEW,
                        ZERO,
                        cls,
                        [
                            f"{cls} is {w:.1%}, above its band high {band.high:.0%} by more than the {ips.rebalance_breach_margin:.0%} margin",
                            "new contributions come first; any sale needs tax, cost, thesis and alternatives shown (tax unavailable: no verified rule)",
                        ],
                    )
            # reserve use (B2): outside the month's budget, explicit and separate. The investor chose the asset, so
            # the class target does not limit it; the bands, concentration limits, data gates and cost rule still do.
            left = cash.available_inr if cash is not None else ZERO
            for asset_id, amount in inp.reserve_use:
                h = by_id.get(asset_id)
                why_not = "no cash reserve" if cash is None else "reserve too small" if amount > left else ""
                if not why_not and h is None:
                    why_not = "unknown asset"
                if not why_not and h is not None:
                    why_not = "; ".join(_exclusions(h, inp, ips, liq))
                x = ZERO
                if not why_not and h is not None:
                    x, binding = plan.room(
                        h, ips.asset_class_bands[h.asset_class], liq, risky0, amount, to_target=False
                    )
                    if x < PAISE:
                        why_not = f"no room: {binding}"
                    else:
                        check = check_cost(
                            x, inp.costs.get((h.platform, h.asset_class)), ips.cost_threshold_pct
                        )
                        if check.verdict is not CostVerdict.OK:
                            why_not = check.note
                if why_not or cash is None or h is None:
                    warnings.append(f"reserve use of {amount} for asset {asset_id} refused: {why_not}")
                    continue
                plan.put(h.asset_id, x)
                left -= x
                e = EnvelopeLine(kind=Kind.RESERVE_USE, amount_inr=x, asset_id=asset_id, reserve_id=cash.id)
                note = "" if x == amount else f" (limited from {amount} by {binding})"
                line(
                    Action.ACCUMULATE, x, h.name,
                    [f"use of the cash reserve, requested by the investor and approved separately (B2){note}"], e, h,
                )  # fmt: skip
                moves.append(
                    {
                        "reserve": cash.name,
                        "reserve_id": cash.id,
                        "direction": "out",
                        "amount_inr": str(x),
                        "reason": "reserve use",
                    }
                )

    all_env = [en for en, _ in drafts if en is not None]
    # RESERVE_USE sits outside the budget identity, so it is not part of build_envelope's totals
    envelope = build_envelope(inp.period, opening, all_env)
    refs, k = {}, 0
    for i, (en, _) in enumerate(drafts):
        if en is not None:
            refs[i] = k
            k += 1
    final = [rl.model_copy(update={"envelope_ref": refs.get(i)}) for i, (_, rl) in enumerate(drafts)]
    kinds = {e.kind for e in all_env}
    if any(ln.action is Action.ACCUMULATE for ln in final):
        overall = Action.ACCUMULATE
    elif Kind.RESERVE_TRANSFER in kinds or any(ln.action is Action.HOLD_CASH for ln in final):
        overall = Action.HOLD_CASH
    elif Kind.SIP in kinds:
        overall = Action.CONTINUE_SIP
    else:
        overall = Action.NO_ACTION
    return Recommendation(
        review_date=inp.today,
        period=inp.period,
        dataset="DEMO" if inp.dataset == "DEMO" else "REAL",
        label=DEMO_LABEL if inp.dataset == "DEMO" else REAL_LABEL,
        personalised=ips is not None,
        risk_level=inp.risk_level.name,
        overall_action=overall,
        budget_inr=opening,
        envelope=envelope,
        lines=tuple(final),
        reserve_movements=tuple(moves),
        warnings=tuple(dict.fromkeys(warnings)),
        missing=tuple(dict.fromkeys(missing)),
        assumptions=tuple(dict.fromkeys(assumptions)),
        scenarios=inp.scenarios,
        sources=inp.sources,
        confidence=conf,
        confidence_reasons=inp.confidence_reasons,
        ips_version=inp.ips_version,
    )
