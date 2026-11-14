"""Model failover policy: 429 moves keys, model-wide 503 moves models, last-resort model, texts.

Offline: scripted operations, a fake client, an injected clock and sleep. No network.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from google.genai.errors import APIError
from pydantic import BaseModel, SecretStr

from app.core.config import Settings
from app.gateway.exceptions import (
    GatewayConfigurationError,
    GatewayFallbackExhaustedError,
    overloaded_user_message,
)
from app.gateway.key_pool import TOOL_GROUNDING, KeyPool
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import (
    LAST_RESORT_ALIAS,
    ModelRegistry,
    ModelSpec,
    build_default_registry,
)
from app.gateway.resilience import ResiliencePolicy
from app.gateway.startup import verify_gateway_at_startup
from tests.gateway.test_gateway_key_failover import _make_key, make_settings_with_keys
from tests.gateway.test_startup import make_startup_settings
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.models import (
    DISTINCT_LAST_RESORT_MODEL,
    HEAVY,
    LAST_RESORT_OVERRIDES,
    LITE,
    ORCH,
)

# The last-resort (``search``) model only exists when its id differs from the chain, so every test
# here (except the explicit coincidence tests below) uses a registry where it is distinct.
SEARCH = DISTINCT_LAST_RESORT_MODEL


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _keys(n: int) -> list[str]:
    return [_make_key(f"fo{i}") for i in range(n)]


def _policy(pool: KeyPool, clock: _Clock | None = None, **kw: Any) -> ResiliencePolicy:
    kw.setdefault("max_key_attempts", 4)
    return ResiliencePolicy(sleep=AsyncMock(), key_pool=pool, clock=clock or _Clock(), **kw)


def _err(code: int) -> APIError:
    return APIError(code, {"error": {"message": "x"}})


class Script:
    """Raises per model alias; records (alias, key)."""

    def __init__(self, failing: dict[str, int | type[BaseException]]) -> None:
        self.failing = failing
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, spec: ModelSpec, was_fallback: bool, key: SecretStr) -> str:
        self.calls.append((spec.alias, key.get_secret_value()))
        what = self.failing.get(spec.alias)
        if isinstance(what, int):
            raise _err(what)
        if what is not None:
            raise what()
        return f"ok:{spec.alias}"

    def aliases(self) -> list[str]:
        return [a for a, _ in self.calls]


async def _run(policy: ResiliencePolicy, op: Any, start: str = "orchestrator", **kw: Any) -> Any:
    return await policy.execute(build_default_registry(LAST_RESORT_OVERRIDES), start, op, **kw)


# --------------------------------------------------------------------------- key vs model rules


@pytest.mark.asyncio
async def test_429_on_one_key_moves_to_the_next_key_on_the_same_model() -> None:
    keys = _keys(3)
    pool = KeyPool(keys)

    class Op(Script):
        async def __call__(self, spec: ModelSpec, was_fallback: bool, key: SecretStr) -> str:
            self.calls.append((spec.alias, key.get_secret_value()))
            if key.get_secret_value() == keys[0]:
                raise _err(429)
            return f"ok:{spec.alias}"

    op = Op({})
    pool.report_success(keys[0])  # start on key 0 via round robin
    result, spec, was_fallback = await _run(_policy(pool), op)
    assert (spec.alias, was_fallback) == ("orchestrator", False)
    assert len({k for _, k in op.calls}) == 2 and result == "ok:orchestrator"


@pytest.mark.asyncio
async def test_two_503_on_different_keys_go_to_the_next_model_without_burning_keys() -> None:
    pool = KeyPool(_keys(6))
    op = Script({"orchestrator": 503})
    result, spec, was_fallback = await _run(_policy(pool), op)
    assert (result, spec.alias, was_fallback) == ("ok:lite", "lite", True)
    assert op.aliases() == ["orchestrator", "orchestrator", "lite"]
    assert len({k for a, k in op.calls if a == "orchestrator"}) == 2


@pytest.mark.asyncio
async def test_timeouts_on_two_keys_are_model_wide_too() -> None:
    pool = KeyPool(_keys(6))
    op = Script({"orchestrator": TimeoutError})
    _, spec, _ = await _run(_policy(pool), op)
    assert spec.alias == "lite" and op.aliases().count("orchestrator") == 2


@pytest.mark.asyncio
async def test_all_keys_429_move_to_the_next_model_after_every_key_was_tried() -> None:
    pool = KeyPool(_keys(5))
    op = Script({"orchestrator": 429})
    _, spec, was_fallback = await _run(_policy(pool, max_quota_key_attempts=8), op)
    assert (spec.alias, was_fallback) == ("lite", True)
    # 429 is per key: all 5 keys were tried (more than max_key_attempts=4) before moving on.
    assert op.aliases().count("orchestrator") == 5


@pytest.mark.asyncio
async def test_quota_attempt_cap_is_configurable_and_never_below_the_5xx_cap() -> None:
    pool = KeyPool(_keys(20))
    op = Script({"orchestrator": 429})
    await _run(_policy(pool, max_key_attempts=4, max_quota_key_attempts=6), op)
    assert op.aliases().count("orchestrator") == 6
    op2 = Script({"orchestrator": 429})
    await _run(_policy(KeyPool(_keys(20)), max_key_attempts=7, max_quota_key_attempts=2), op2)
    assert op2.aliases().count("orchestrator") == 7


@pytest.mark.asyncio
async def test_cooling_keys_for_a_model_skip_to_the_next_model_without_sleeping() -> None:
    keys = _keys(3)
    pool = KeyPool(keys)
    for k in keys:
        pool.report_failure(k, "429", retry_after=60.0, bucket=(ORCH, "generate"))
    policy = _policy(pool)
    op = Script({})
    _, spec, _ = await _run(policy, op)
    assert spec.alias == "lite" and op.aliases() == ["lite"]
    policy.sleep.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_last_resort_model_is_reached_for_plain_generation_only() -> None:
    pool = KeyPool(_keys(4))
    op = Script({"orchestrator": 503, "lite": 503})
    result, spec, _ = await _run(_policy(pool), op, last_resort=True)
    assert (result, spec.alias) == ("ok:search", LAST_RESORT_ALIAS)
    assert spec.model_id == SEARCH and spec.price_unknown is True

    # Without last_resort (embeddings, thought summaries) the chain ends at lite.
    op2 = Script({"orchestrator": 503, "lite": 503})
    with pytest.raises(GatewayFallbackExhaustedError):
        await _run(_policy(KeyPool(_keys(4))), op2)
    assert "search" not in op2.aliases()


def test_last_resort_is_dropped_when_its_model_is_already_in_the_chain() -> None:
    # Same model id as the orchestrator: a third attempt would repeat a model that just failed.
    reg = build_default_registry({"search": ORCH})
    assert reg.get(LAST_RESORT_ALIAS).model_id == reg.get("orchestrator").model_id
    assert reg.candidate_chain("orchestrator", last_resort=True) == ["orchestrator", "lite"]
    assert reg.candidate_chain("heavy", last_resort=True) == ["heavy", "orchestrator", "lite"]
    # Distinct model id: the last resort is appended.
    distinct = build_default_registry(LAST_RESORT_OVERRIDES)
    assert distinct.candidate_chain("orchestrator", last_resort=True)[-1] == LAST_RESORT_ALIAS


@pytest.mark.asyncio
async def test_no_duplicate_call_when_the_last_resort_model_equals_a_chain_model() -> None:
    reg = build_default_registry({"search": ORCH})
    op = Script({"orchestrator": 503, "lite": 503})
    with pytest.raises(GatewayFallbackExhaustedError) as info:
        await _policy(KeyPool(_keys(4))).execute(reg, "orchestrator", op, last_resort=True)
    assert "search" not in op.aliases() and info.value.models_tried == 2


def test_candidate_chain_is_cycle_safe_and_skips_unavailable_models() -> None:
    reg = build_default_registry(LAST_RESORT_OVERRIDES)
    assert reg.candidate_chain("heavy", last_resort=True) == [
        "heavy",
        "orchestrator",
        "lite",
        "search",
    ]
    assert reg.candidate_chain("search", last_resort=True) == ["search"]
    assert reg.candidate_chain("lite", allow_fallback=False, last_resort=True) == ["lite"]
    assert reg.candidate_chain("embed", last_resort=True) == ["embed", "embed-fallback"]
    reg.mark_unavailable("lite")
    assert reg.candidate_chain("heavy", last_resort=True) == ["heavy", "orchestrator", "search"]
    # A cycle (orchestrator -> lite -> orchestrator) cannot loop.
    assert reg.candidate_chain("orchestrator") == ["orchestrator"]


@pytest.mark.asyncio
async def test_model_cooldown_is_shared_by_concurrent_calls_and_expires() -> None:
    clock = _Clock()
    pool = KeyPool(_keys(6), clock=clock)
    policy = _policy(pool, clock)
    first = Script({"orchestrator": 503})
    await _run(policy, first)
    assert policy.model_is_cooling((ORCH, "generate"))

    # A concurrent call now skips the cooling model first.
    second = Script({"orchestrator": 503})
    await asyncio.gather(_run(policy, second))
    assert second.aliases() == ["lite"]

    clock.now += 16.0  # cooldown over: the model is tried again
    third = Script({})
    _, spec, _ = await _run(policy, third)
    assert spec.alias == "orchestrator" and third.aliases() == ["orchestrator"]
    assert not policy.model_is_cooling((ORCH, "generate"))


@pytest.mark.asyncio
async def test_grounding_bucket_follows_the_same_rules() -> None:
    pool = KeyPool(_keys(4))
    op = Script({"orchestrator": 503})
    policy = _policy(pool)
    _, spec, _ = await _run(policy, op, tool=TOOL_GROUNDING)
    assert spec.alias == "lite" and op.aliases().count("orchestrator") == 2
    assert policy.model_is_cooling((ORCH, TOOL_GROUNDING))
    assert not policy.model_is_cooling((ORCH, "generate"))


@pytest.mark.asyncio
async def test_interactive_deadline_is_still_respected() -> None:
    clock = _Clock()
    pool = KeyPool(_keys(4), clock=clock)

    class Slow(Script):
        async def __call__(self, spec: ModelSpec, was_fallback: bool, key: SecretStr) -> str:
            self.calls.append((spec.alias, key.get_secret_value()))
            clock.now += 40.0
            raise _err(429)

    with pytest.raises(GatewayFallbackExhaustedError) as info:
        await _run(
            _policy(pool, clock),
            Slow({}),
            interactive=True,
            first_chunk_timeout_seconds=20.0,
            total_deadline_seconds=75.0,
        )
    assert info.value.deadline_exceeded is True


@pytest.mark.asyncio
async def test_failover_decisions_are_logged_with_reason_and_no_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    keys = _keys(4)
    pool = KeyPool(keys)
    op = Script({"orchestrator": 503})
    with caplog.at_level(logging.INFO, logger="app.gateway.resilience"):
        await _run(_policy(pool), op)
    lines = [r.getMessage() for r in caplog.records if "gateway_failover" in r.getMessage()]
    assert any("reason=unavailable" in line and "next=model:lite" in line for line in lines)
    assert any("next_key" in line for line in lines)
    assert all(k not in r.getMessage() for r in caplog.records for k in keys)


# --------------------------------------------------------------------------- final error text


@pytest.mark.asyncio
async def test_final_error_counts_models_and_keys_and_hides_labels() -> None:
    pool = KeyPool(_keys(6))
    op = Script({"orchestrator": 503, "lite": 503, "search": 503})
    with pytest.raises(GatewayFallbackExhaustedError) as info:
        await _run(_policy(pool), op, last_resort=True)
    err = info.value
    assert err.models_tried == 3 and err.keys_tried >= 3
    assert err.user_message == (
        f"Gemini is overloaded right now. MAYA tried 3 models and {err.keys_tried} keys; "
        "please try again in a minute."
    )
    assert "key#" not in err.user_message and "503" not in err.user_message
    assert overloaded_user_message(1, 1).startswith(
        "Gemini is overloaded right now. MAYA tried 1 model and 1 key;"
    )


# --------------------------------------------------------------------------- gateway level


def _gateway(client: FakeGeminiClient, keys: list[str]) -> ModelGateway:
    pool = KeyPool(keys)
    return ModelGateway(
        registry=build_default_registry(LAST_RESORT_OVERRIDES),
        settings=make_settings_with_keys(keys=keys),
        client=client,
        resilience_policy=_policy(pool),
        key_pool=pool,
    )


@pytest.mark.asyncio
async def test_generate_text_ends_at_the_last_resort_model_and_is_not_priced() -> None:
    client = FakeGeminiClient()
    client.aio.models.enqueue_errors(ORCH, [_err(503)] * 4)
    client.aio.models.enqueue_errors(LITE, [_err(503)] * 4)
    client.aio.models.default_text = "from the last resort"
    resp = await _gateway(client, _keys(4)).generate_text("orchestrator", ["hi"])
    assert resp.model_id == SEARCH and resp.was_fallback is True
    assert resp.text == "from the last resort"


@pytest.mark.asyncio
async def test_thought_summary_calls_do_not_fall_back_to_the_last_resort_model() -> None:
    client = FakeGeminiClient()
    client.aio.models.enqueue_errors(ORCH, [_err(503)] * 4)
    client.aio.models.enqueue_errors(LITE, [_err(503)] * 4)
    with pytest.raises(GatewayFallbackExhaustedError):
        await _gateway(client, _keys(4)).generate_text(
            "orchestrator", ["hi"], thought_summaries=True
        )


class _Answer(BaseModel):
    value: int


@pytest.mark.asyncio
async def test_structured_output_reaches_the_last_resort_model() -> None:
    client = FakeGeminiClient()
    client.aio.models.enqueue_errors(ORCH, [_err(503)] * 4)
    client.aio.models.enqueue_errors(LITE, [_err(503)] * 4)
    client.aio.models.default_text = '{"value": 7}'
    out = await _gateway(client, _keys(4)).generate_structured("orchestrator", ["q"], _Answer)
    assert out.value == 7


@pytest.mark.asyncio
async def test_grounded_search_alias_has_no_extra_fallback_and_keeps_its_model() -> None:
    client = FakeGeminiClient()
    client.aio.models.default_text = "grounded"
    resp = await _gateway(client, _keys(3)).generate_text(
        "search", ["q"], enable_google_search=True
    )
    assert resp.model_id == SEARCH and resp.was_fallback is False


# --------------------------------------------------------------------------- aliases and startup


def test_every_model_override_sets_the_effective_model_id_of_its_alias() -> None:
    overrides = {
        "orchestrator": "my-orch-1",
        "heavy": "my-heavy-1",
        "lite": "my-lite-1",
        "embed": "my-embed-1",
        "embed-fallback": "my-embed-2",
        "search": "my-search-1",
    }
    reg = build_default_registry(overrides)
    for alias, model in overrides.items():
        assert reg.get(alias).model_id == model
    # The last-resort model follows the search override.
    assert reg.get(LAST_RESORT_ALIAS).model_id == "my-search-1"
    # Pointing an alias at an unpriced id keeps cost accounting safe (price unknown, not zero).
    assert reg.get("lite").price_unknown is True
    # Live voice models are read from the registry chain, not the override list.
    assert reg.get("live").model_id == "gemini-3.8-live"
    assert [reg.get(a).model_id for a in reg.resolve_fallback_chain("live")] == [
        "gemini-3.8-live",
        "gemini-3.8-live-extended-thinking",
    ]


def test_invalid_overrides_fail_at_startup_with_a_plain_message() -> None:
    with pytest.raises(ValueError, match="Invalid model override alias 'nope'"):
        make_startup_settings(gemini_model_overrides={"nope": "gemini-x"})
    with pytest.raises(ValueError, match="Invalid model_id"):
        make_startup_settings(gemini_model_overrides={"lite": "bad model id!"})
    with pytest.raises(GatewayConfigurationError, match="Cannot override"):
        build_default_registry({"live": "gemini-x"})


def test_agent_alias_settings_are_validated_and_applied() -> None:
    s = make_startup_settings(
        agent_model_alias="orchestrator",
        agent_fallback_alias="heavy",
        agent_search_alias="search",
        agent_search_fallback_alias="orchestrator",
    )
    assert (s.agent_model_alias, s.agent_fallback_alias) == ("orchestrator", "heavy")
    from app.agents import model_policy

    try:
        policy = model_policy.configure(s)
        assert policy.alias == "orchestrator" and policy.fallback_alias == "heavy"
        assert policy.search_fallback_alias == "orchestrator"
        kwargs = model_policy.search_call_kwargs()
        assert kwargs["alias"] == "search" and kwargs["fallback_alias"] == "orchestrator"
    finally:
        model_policy.reset()
    with pytest.raises(ValueError, match="live"):
        make_startup_settings(agent_model_alias="live")


def test_quota_attempt_setting_is_bounded_and_wired() -> None:
    s = make_startup_settings()
    assert s.gemini_max_quota_key_attempts == 8
    with pytest.raises(ValueError):
        make_startup_settings(gemini_max_quota_key_attempts=0)
    gw = ModelGateway(
        registry=build_default_registry(LAST_RESORT_OVERRIDES),
        settings=make_settings_with_keys(keys=_keys(3), gemini_max_quota_key_attempts=3),
        client=FakeGeminiClient(),
    )
    assert gw._resilience.max_quota_key_attempts == 3


@pytest.mark.asyncio
async def test_startup_verification_logs_and_marks_missing_models_instead_of_crashing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry: ModelRegistry = build_default_registry(LAST_RESORT_OVERRIDES)
    settings: Settings = make_startup_settings(gemini_verify_models_on_startup=True)
    client = FakeGeminiClient()
    client.aio.models.available_models = [f"models/{HEAVY}", f"models/{LITE}"]
    with caplog.at_level(logging.ERROR, logger="maya"):
        await verify_gateway_at_startup(registry, settings, client=client)
    assert registry.is_unavailable("orchestrator")
    assert not registry.is_unavailable("lite")
    assert any("gateway_startup_model_unavailable" in r.getMessage() for r in caplog.records)
    assert registry.candidate_chain("heavy") == ["heavy", "lite"]


def test_live_unavailable_frame_carries_the_plain_overload_text() -> None:
    from app.voice import protocol

    frame = protocol.live_unavailable("quota", "s1", models_tried=2, keys_tried=8)
    assert frame["message"] == (
        "Gemini is overloaded right now. MAYA tried 2 models and 8 keys; "
        "please try again in a minute."
    )
    assert "message" not in protocol.live_unavailable("no_key")
