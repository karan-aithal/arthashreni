"""Data-quality thresholds. Hypotheses, not statistics (ADR-0023): tune with real data in Phase 4."""

from decimal import Decimal

JUMP_K = Decimal(6)  # flag a move beyond k x rolling volatility
JUMP_WINDOW = 60  # returns used for the rolling volatility
JUMP_MIN_RETURNS = 10  # fewer returns than this: the check cannot judge and says so
# A move below the floor is never a jump, so a flat liquid-fund NAV (volatility ~0) does not flag noise.
JUMP_FLOOR = {"price": Decimal("0.05"), "nav": Decimal("0.01"), "fx": Decimal("0.01")}
SPLIT_WINDOW_DAYS = 2  # a corporate action within this many days explains a jump
DISAGREE_PRICE_PCT = Decimal("0.02")  # fetched price vs statement price on the same date
# SBI TT buying sits below the interbank reference by its own spread (about 1.3% on 2026-11-03), so the
# FBIL vs SBI TT comparison uses a wider threshold than a price comparison.
DISAGREE_FX_PCT = Decimal("0.03")
