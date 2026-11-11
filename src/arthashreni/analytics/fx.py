"""FX attribution (F1): INR return = local return + FX effect + interaction, shown separately.

With cost C (local), value V (local), purchase rate f0 = cost_inr / C and valuation rate f1:
    (1 + r_inr) = (1 + r_local)(1 + r_fx)   so   r_inr = r_local + r_fx + r_local * r_fx
In INR:   local effect = (V - C) * f0     FX effect = C * (f1 - f0)     interaction = (V - C)(f1 - f0)
These three add up to V*f1 - C*f0 = the INR gain exactly (algebra, not approximation); `residual` proves it.

The purchase rate is the blended rate the ledger recorded (broker's rate for INDmoney trades), so the
"FX effect" includes the broker's conversion markup relative to the valuation series. The valuation
rate's type is named by the caller (ADR-0016).
"""

from dataclasses import dataclass
from decimal import Decimal, localcontext

from arthashreni.analytics import PRECISION
from arthashreni.dq.measured import Measured, m_add, m_div, m_mul, m_sub

RESIDUAL_TOLERANCE = Decimal("1e-12")


@dataclass(frozen=True)
class FxAttribution:
    valuation_rate_type: str
    purchase_rate: Measured[Decimal]  # blended INR per unit of foreign currency at purchase
    local_return: Measured[Decimal]
    fx_return: Measured[Decimal]
    interaction: Measured[Decimal]
    inr_return: Measured[Decimal]
    local_effect_inr: Measured[Decimal]
    fx_effect_inr: Measured[Decimal]
    interaction_inr: Measured[Decimal]
    gain_inr: Measured[Decimal]
    residual: Decimal | None  # |gain - (local + fx + interaction)| in INR; None if anything is missing

    @property
    def reconciles(self) -> bool:
        return self.residual is not None and self.residual <= RESIDUAL_TOLERANCE


def attribute_fx(
    cost_local: Measured[Decimal],
    cost_inr: Measured[Decimal],
    value_local: Measured[Decimal],
    valuation_rate: Measured[Decimal],
    valuation_rate_type: str,
) -> FxAttribution:
    with localcontext() as ctx:
        ctx.prec = PRECISION
        f0 = m_div(cost_inr, cost_local)
        d_local = m_sub(value_local, cost_local)
        d_fx = m_sub(valuation_rate, f0)
        local_effect = m_mul(d_local, f0)
        fx_effect = m_mul(cost_local, d_fx)
        interaction_inr = m_mul(d_local, d_fx)
        gain = m_sub(m_mul(value_local, valuation_rate), cost_inr)
        r_local = m_div(d_local, cost_local)
        r_fx = m_div(d_fx, f0)
        r_inr = m_div(gain, cost_inr)
        r_cross = m_mul(r_local, r_fx)
        total = m_add(m_add(local_effect, fx_effect), interaction_inr)
        residual = None if total.value is None or gain.value is None else abs(gain.value - total.value)
    return FxAttribution(
        valuation_rate_type,
        f0,
        r_local,
        r_fx,
        r_cross,
        r_inr,
        local_effect,
        fx_effect,
        interaction_inr,
        gain,
        residual,
    )
