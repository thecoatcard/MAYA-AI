"""Unit tests for Hybrid RAG Engine scoring and Defect #16 decay formulas."""

import math

import pytest

from app.memory.rag_engine import (
    calculate_effective_score,
    calculate_hybrid_score,
    qualifies_raw_threshold,
)


def test_qualifies_raw_threshold_defect_16() -> None:
    # Threshold is 0.78
    assert qualifies_raw_threshold(0.78, threshold=0.78) is True
    assert qualifies_raw_threshold(0.92, threshold=0.78) is True
    assert qualifies_raw_threshold(0.7799, threshold=0.78) is False
    assert qualifies_raw_threshold(0.50, threshold=0.78) is False


def test_calculate_hybrid_score() -> None:
    # Formula: 0.7 * S_sim + 0.3 * min(1.0, S_text)
    score_no_text = calculate_hybrid_score(raw_sim=0.80, text_score=0.0)
    assert pytest.approx(score_no_text, rel=1e-4) == 0.56  # 0.7 * 0.80

    score_with_text = calculate_hybrid_score(raw_sim=0.80, text_score=0.5)
    assert pytest.approx(score_with_text, rel=1e-4) == 0.56 + (0.3 * 0.5)  # 0.71

    # Text score capped at 1.0
    score_capped = calculate_hybrid_score(raw_sim=0.80, text_score=2.5)
    assert pytest.approx(score_capped, rel=1e-4) == 0.56 + 0.30  # 0.86


def test_calculate_effective_score_permanent_bypass() -> None:
    hybrid = 0.75
    # Permanent item at 0 days, 60 days, 365 days must NOT decay
    assert calculate_effective_score(hybrid, delta_days=0.0, permanent=True) == hybrid
    assert calculate_effective_score(hybrid, delta_days=60.0, permanent=True) == hybrid
    assert calculate_effective_score(hybrid, delta_days=365.0, permanent=True) == hybrid


def test_calculate_effective_score_time_decay() -> None:
    hybrid = 0.80
    # Day 0: exp(0) = 1.0
    res_0 = calculate_effective_score(hybrid, delta_days=0.0, permanent=False)
    assert pytest.approx(res_0, rel=1e-4) == 0.80

    # Day 60: exp(-0.005 * 60) = exp(-0.3) ~ 0.740818
    expected_60 = 0.80 * math.exp(-0.005 * 60)
    res_60 = calculate_effective_score(hybrid, delta_days=60.0, permanent=False)
    assert pytest.approx(res_60, rel=1e-4) == expected_60

    # Day 180: exp(-0.005 * 180) = exp(-0.9) ~ 0.406569
    expected_180 = 0.80 * math.exp(-0.005 * 180)
    res_180 = calculate_effective_score(hybrid, delta_days=180.0, permanent=False)
    assert pytest.approx(res_180, rel=1e-4) == expected_180

    # Negative delta days (clock jitter) must not boost score
    res_neg = calculate_effective_score(hybrid, delta_days=-0.5, permanent=False)
    assert pytest.approx(res_neg, rel=1e-4) == 0.80


def test_defect_16_decoupling_ranking() -> None:
    # High similarity old item vs fresh item
    sim = 0.85
    text_score = 0.4
    hybrid = calculate_hybrid_score(sim, text_score)

    # Both items qualify raw threshold 0.78
    assert qualifies_raw_threshold(sim, threshold=0.78) is True

    score_fresh = calculate_effective_score(hybrid, delta_days=1.0, permanent=False)
    score_old = calculate_effective_score(hybrid, delta_days=90.0, permanent=False)

    # Fresh ranks higher than old, but old was NOT dropped by the candidate gate
    assert score_fresh > score_old
