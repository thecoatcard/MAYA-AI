"""Cached-token accounting: override pricing, usage mapping and the token metric."""

from decimal import Decimal
from types import SimpleNamespace

from app.core.metrics import TOKEN_USAGE_TOTAL
from app.gateway.registry import build_default_registry
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.gateway_factory import make_gateway
from tests.support.models import LITE


def test_override_to_a_priced_registry_model_keeps_its_rates() -> None:
    registry = build_default_registry({"orchestrator": LITE})
    spec = registry.get("orchestrator")
    lite = registry.get("lite")
    assert spec.model_id == LITE
    assert spec.price_unknown is False
    assert spec.cost_per_1m_input == lite.cost_per_1m_input
    assert spec.cost_per_1m_output == lite.cost_per_1m_output
    assert spec.cost_per_1m_cached == lite.cost_per_1m_cached


def test_override_to_an_unknown_model_stays_unpriced() -> None:
    spec = build_default_registry({"orchestrator": "gemini-not-in-registry"}).get("orchestrator")
    assert spec.price_unknown is True


def test_cached_tokens_are_mapped_priced_and_counted() -> None:
    gateway = make_gateway(FakeGeminiClient())
    spec = gateway._registry.get("lite")
    metadata = SimpleNamespace(
        prompt_token_count=2000,
        candidates_token_count=100,
        cached_content_token_count=1500,
        thoughts_token_count=0,
        tool_use_prompt_token_count=0,
    )
    before = {
        kind: TOKEN_USAGE_TOTAL.get({"alias": "lite", "type": kind})
        for kind in ("prompt", "candidates", "cached")
    }

    usage = gateway._extract_usage(metadata, spec)

    assert usage.cached_content_token_count == 1500
    # 500 uncached input + 1500 cached + 100 output, at the registry rates.
    expected = (
        Decimal(500) * Decimal("0.30")
        + Decimal(1500) * Decimal("0.03")
        + Decimal(100) * Decimal("2.50")
    ) / Decimal(1_000_000)
    assert usage.estimated_cost_usd == expected
    assert TOKEN_USAGE_TOTAL.get({"alias": "lite", "type": "prompt"}) - before["prompt"] == 2000
    assert (
        TOKEN_USAGE_TOTAL.get({"alias": "lite", "type": "candidates"}) - before["candidates"] == 100
    )
    assert TOKEN_USAGE_TOTAL.get({"alias": "lite", "type": "cached"}) - before["cached"] == 1500
