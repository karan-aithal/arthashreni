"""Computed confidence (E5, ADR-0010): deterministic, never an LLM judgement."""

from decimal import Decimal

import pytest

from arthashreni.dq.confidence import ConfidenceInput, ConfidenceLevel, confidence
from arthashreni.dq.measured import QualityStatus as Q

D = Decimal


def ci(w: str, status: Q = Q.OK, checked: bool = True) -> ConfidenceInput:
    return ConfidenceInput(D(w), status, checked)


def test_all_ok_and_cross_checked_is_high() -> None:
    c = confidence([ci("100"), ci("50")])
    assert c.level is ConfidenceLevel.HIGH
    assert (c.coverage, c.freshness, c.agreement) == (1, 1, 1)
    assert c.reasons == ()


def test_nothing_cross_checked_means_agreement_zero_and_low() -> None:
    c = confidence([ci("100", checked=False)])
    assert c.agreement == 0 and c.level is ConfidenceLevel.LOW
    assert "nothing could be cross-checked, so agreement is 0" in c.reasons


def test_value_weighted_shares() -> None:
    c = confidence([ci("90"), ci("10", Q.STALE)])
    assert c.coverage == D("0.9") and c.freshness == D("0.9") and c.agreement == 1
    assert c.level is ConfidenceLevel.MEDIUM  # >= 0.80 on all three, below 0.95 on two


def test_thresholds_are_inclusive() -> None:
    assert confidence([ci("95"), ci("5", Q.ESTIMATED)]).level is ConfidenceLevel.HIGH  # exactly 0.95
    assert confidence([ci("80"), ci("20", Q.ESTIMATED)]).level is ConfidenceLevel.MEDIUM  # exactly 0.80
    assert confidence([ci("79"), ci("21", Q.ESTIMATED)]).level is ConfidenceLevel.LOW


def test_thresholds_are_configurable() -> None:
    inputs = [ci("90"), ci("10", Q.ESTIMATED)]
    assert confidence(inputs).level is ConfidenceLevel.MEDIUM
    assert confidence(inputs, high_at=D("0.9")).level is ConfidenceLevel.HIGH
    assert confidence(inputs, medium_at=D("0.95")).level is ConfidenceLevel.LOW


def test_any_quarantined_input_forces_low_even_when_tiny() -> None:
    c = confidence([ci("1000"), ci("0.01", Q.QUARANTINED)])
    assert c.level is ConfidenceLevel.LOW
    assert "a quarantined input forces low confidence" in c.reasons


def test_conflicting_counts_as_cross_checked_and_disagreeing() -> None:
    c = confidence([ci("60", Q.CONFLICTING, checked=False), ci("40")])
    assert c.agreement == D("0.4")
    assert c.level is ConfidenceLevel.LOW


def test_missing_is_neither_fresh_nor_ok() -> None:
    c = confidence([ci("50"), ci("50", Q.MISSING)])
    assert (c.coverage, c.freshness) == (D("0.5"), D("0.5"))


def test_estimated_is_fresh_but_not_ok() -> None:
    c = confidence([ci("50"), ci("50", Q.ESTIMATED)])
    assert (c.coverage, c.freshness) == (D("0.5"), 1)


def test_empty_is_low() -> None:
    c = confidence([])
    assert c.level is ConfidenceLevel.LOW and c.reasons == ("no inputs",)


def test_unvalued_inputs_count_equally() -> None:
    c = confidence([ci("0"), ci("0", Q.MISSING)])
    assert c.coverage == D("0.5")


def test_negative_weight_is_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        confidence([ci("-1")])


def test_reasons_name_each_weak_measure() -> None:
    c = confidence([ci("85"), ci("15", Q.STALE)])
    assert any(r.startswith("coverage 85%") for r in c.reasons)
    assert any(r.startswith("freshness 85%") for r in c.reasons)
    assert not any(r.startswith("agreement") for r in c.reasons)
