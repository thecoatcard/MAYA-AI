"""Unit tests for Model Registry, alias definitions, and terminating fallback traversal."""

from decimal import Decimal

import pytest

from app.gateway.exceptions import (
    GatewayConfigurationError,
    UnsupportedAliasTransportError,
)
from app.gateway.registry import (
    ModelRegistry,
    ModelSpec,
    build_default_registry,
    normalize_model_id,
)


def test_normalize_model_id() -> None:
    assert normalize_model_id("models/gemini-3.8-flash") == "gemini-3.8-flash"
    assert normalize_model_id("gemini-3.8-flash") == "gemini-3.8-flash"
    assert normalize_model_id("models/custom-model") == "custom-model"


def test_default_registry_contains_expected_aliases() -> None:
    registry = build_default_registry()
    specs = registry.specs

    expected = {
        "orchestrator",
        "heavy",
        "lite",
        "embed",
        "embed-fallback",
        "live",
        "live-thinking",
        "research",
        "tts",
    }
    assert set(specs.keys()) == expected


def test_mvp_aliases_pricing_and_context() -> None:
    registry = build_default_registry()

    orch = registry.get("orchestrator")
    assert orch.model_id == "gemini-3.8-flash"
    assert orch.fallback_alias == "lite"
    assert orch.cost_per_1m_input == Decimal("0.75")
    assert orch.cost_per_1m_output == Decimal("3.75")
    assert orch.cost_per_1m_cached == Decimal("0.075")
    assert orch.context_input_limit == 1_048_576
    assert orch.transport == "rest"
    assert orch.verify_at_startup is True
    assert orch.enabled is True

    heavy = registry.get("heavy")
    assert heavy.model_id == "gemini-4-argon"
    assert heavy.fallback_alias == "orchestrator"
    assert heavy.cost_per_1m_input == Decimal("2.00")
    assert heavy.cost_per_1m_output == Decimal("10.00")
    assert heavy.cost_per_1m_cached == Decimal("0.10")
    assert heavy.verify_at_startup is True

    lite = registry.get("lite")
    assert lite.model_id == "gemini-3.5-flash-lite"
    assert lite.fallback_alias == "orchestrator"
    assert lite.cost_per_1m_input == Decimal("0.30")
    assert lite.cost_per_1m_output == Decimal("2.50")
    assert lite.cost_per_1m_cached == Decimal("0.03")
    assert lite.verify_at_startup is True

    embed = registry.get("embed")
    assert embed.model_id == "gemini-embedding-2"
    assert embed.fallback_alias == "embed-fallback"
    assert embed.cost_per_1m_input == Decimal("0.20")
    assert embed.cost_per_1m_output is None
    assert embed.cost_per_1m_cached is None
    assert embed.verify_at_startup is True

    embed_fallback = registry.get("embed-fallback")
    assert embed_fallback.model_id == "gemini-embedding-001"
    assert embed_fallback.fallback_alias is None
    assert embed_fallback.cost_per_1m_input is None
    assert embed_fallback.cost_per_1m_output is None
    assert embed_fallback.cost_per_1m_cached is None
    assert embed_fallback.price_unknown is True
    assert embed_fallback.context_unverified is True
    assert embed_fallback.verify_at_startup is False

    live = registry.get("live")
    assert live.cost_per_1m_input == Decimal("1.50")
    assert live.cost_per_1m_output == Decimal("9.00")
    assert live.cost_per_1m_cached is None  # Unknown in 7.1
    assert live.verify_at_startup is False

    research = registry.get("research")
    assert research.cost_per_1m_input is None
    assert research.cost_per_1m_output is None
    assert research.price_unknown is True
    assert research.verify_at_startup is False

    tts = registry.get("tts")
    assert tts.cost_per_1m_input is None
    assert tts.cost_per_1m_output is None
    assert tts.price_unknown is True
    assert tts.is_post_mvp is True
    assert tts.enabled is False
    assert tts.verify_at_startup is False


def test_unsupported_transports_raise_error() -> None:
    registry = build_default_registry()

    for alias in ("live", "live-thinking", "research", "tts"):
        spec = registry.get(alias)
        with pytest.raises(UnsupportedAliasTransportError):
            spec.check_supported_transport()

    for alias in ("orchestrator", "heavy", "lite", "embed", "embed-fallback"):
        spec = registry.get(alias)
        # Does not raise
        spec.check_supported_transport()


def test_fallback_traversal_always_terminates() -> None:
    registry = build_default_registry()

    # Circular pair in plan: orchestrator -> lite -> orchestrator
    orch_chain = registry.resolve_fallback_chain("orchestrator")
    assert orch_chain == ["orchestrator", "lite"]

    lite_chain = registry.resolve_fallback_chain("lite")
    assert lite_chain == ["lite", "orchestrator"]

    heavy_chain = registry.resolve_fallback_chain("heavy")
    assert heavy_chain == ["heavy", "orchestrator", "lite"]

    # Circular pair in plan: live -> live-thinking -> live
    live_chain = registry.resolve_fallback_chain("live")
    assert live_chain == ["live", "live-thinking"]

    # research has no automatic fallback
    research_chain = registry.resolve_fallback_chain("research")
    assert research_chain == ["research"]

    # embed -> embed-fallback
    embed_chain = registry.resolve_fallback_chain("embed")
    assert embed_chain == ["embed", "embed-fallback"]


def test_model_overrides_application() -> None:
    overrides = {"orchestrator": "gemini-3.8-flash-custom"}
    registry = build_default_registry(overrides=overrides)
    assert registry.get("orchestrator").model_id == "gemini-3.8-flash-custom"
    assert registry.get("orchestrator").price_unknown is True
    # Unchanged
    assert registry.get("heavy").model_id == "gemini-4-argon"
    assert registry.get("heavy").price_unknown is False

    # Direct with_overrides call
    base = build_default_registry()
    updated = base.with_overrides({"lite": "gemini-3.5-lite-custom"})
    assert updated.get("lite").model_id == "gemini-3.5-lite-custom"
    assert base.get("lite").model_id == "gemini-3.5-flash-lite"

    # Reject unknown alias
    with pytest.raises(GatewayConfigurationError, match="unknown alias"):
        base.with_overrides({"nonexistent": "model-x"})

    # Reject non-REST alias
    for non_rest in ("live", "live-thinking", "research", "tts"):
        with pytest.raises(GatewayConfigurationError, match="non-REST"):
            base.with_overrides({non_rest: "model-x"})

    # Reject invalid model_id format
    with pytest.raises(GatewayConfigurationError, match="Invalid model_id"):
        base.with_overrides({"orchestrator": "invalid model spaces"})


def test_unknown_alias_raises_configuration_error() -> None:
    registry = build_default_registry()
    with pytest.raises(GatewayConfigurationError, match="Unknown model alias"):
        registry.get("non-existent-alias")


def test_invalid_fallback_raises_configuration_error() -> None:
    specs = {
        "valid": ModelSpec(
            alias="valid",
            model_id="m1",
            fallback_alias="broken",
            context_input_limit=1000,
            context_output_limit=1000,
            cost_per_1m_input=Decimal("1.0"),
            cost_per_1m_output=Decimal("1.0"),
            cost_per_1m_cached=Decimal("0.1"),
        )
    }
    registry = ModelRegistry(specs)
    with pytest.raises(GatewayConfigurationError, match="unknown fallback alias"):
        registry.validate_configuration()


def test_disabled_fallback_raises_configuration_error() -> None:
    specs = {
        "primary": ModelSpec(
            alias="primary",
            model_id="m1",
            fallback_alias="disabled_sec",
            context_input_limit=1000,
            context_output_limit=1000,
            cost_per_1m_input=Decimal("1.0"),
            cost_per_1m_output=Decimal("1.0"),
            cost_per_1m_cached=Decimal("0.1"),
            enabled=True,
        ),
        "disabled_sec": ModelSpec(
            alias="disabled_sec",
            model_id="m2",
            fallback_alias=None,
            context_input_limit=1000,
            context_output_limit=1000,
            cost_per_1m_input=Decimal("1.0"),
            cost_per_1m_output=Decimal("1.0"),
            cost_per_1m_cached=Decimal("0.1"),
            enabled=False,
        ),
    }
    registry = ModelRegistry(specs)
    with pytest.raises(GatewayConfigurationError, match="disabled fallback alias"):
        registry.validate_configuration()
