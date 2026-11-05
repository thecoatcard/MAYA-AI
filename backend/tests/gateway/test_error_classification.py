"""Error classification must use types and status codes, never the text of an error."""

import logging
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from google.genai.errors import APIError, ClientError, ServerError

from app.gateway.exceptions import (
    BudgetExceededError,
    ContentBlockedError,
    GatewayFallbackExhaustedError,
)
from app.gateway.registry import ModelSpec, build_default_registry
from app.gateway.resilience import ResiliencePolicy, classify_gateway_error, describe_error


# Stand-ins for transport exceptions raised by httpx, httpx2 and aiohttp. They are recognised by
# class name so the gateway does not import (or depend on the choice of) an HTTP library.
class ReadTimeout(Exception):  # noqa: N818
    pass


class TransportError(Exception):
    pass


class ConnectError(TransportError):
    pass


class ServerDisconnectedError(Exception):
    pass


def test_message_text_never_decides_the_category() -> None:
    # A task id and a cost can contain "500", "502" or "429" as plain digits. That must not make a
    # budget refusal look like a server error that is retried or failed over (and paid for again).
    task = UUID("11111111-5003-4000-8000-429429429429")
    error = BudgetExceededError(f"Task '{task}' reached its financial budget cap: $2.500000 >= $2")
    assert classify_gateway_error(error) == "non_retryable"
    assert classify_gateway_error(ContentBlockedError("blocked 503 502")) == "non_retryable"
    assert classify_gateway_error(ValueError("upstream said 503 and 429")) == "non_retryable"
    assert classify_gateway_error(RuntimeError("resource_exhausted rate limit")) == "non_retryable"


def test_api_error_status_codes() -> None:
    assert classify_gateway_error(ClientError(429, {"error": {}})) == "retry_with_backoff"
    assert classify_gateway_error(ClientError(408, {"error": {}})) == "retry_with_backoff"
    assert classify_gateway_error(ServerError(500, {"error": {}})) == "failover_immediate"
    assert classify_gateway_error(ServerError(503, {"error": {}})) == "failover_immediate"
    for code in (400, 401, 403, 404):
        assert classify_gateway_error(ClientError(code, {"error": {}})) == "non_retryable"
    assert classify_gateway_error(APIError(302, {"error": {}})) == "non_retryable"


def test_transport_failures_are_recognised_by_class_name() -> None:
    assert classify_gateway_error(TimeoutError()) == "retry_with_backoff"
    assert classify_gateway_error(ReadTimeout()) == "retry_with_backoff"
    assert classify_gateway_error(ConnectError()) == "failover_immediate"
    assert classify_gateway_error(TransportError()) == "failover_immediate"
    assert classify_gateway_error(ServerDisconnectedError()) == "failover_immediate"
    assert classify_gateway_error(ConnectionResetError()) == "failover_immediate"


def test_describe_error_never_includes_error_text() -> None:
    error = ClientError(429, {"error": {"message": "fake-echoed-prompt-text"}})
    description = describe_error(error)
    assert description == "ClientError(429)"
    assert "fake-echoed-prompt-text" not in description
    assert describe_error(ValueError("secret")) == "ValueError"
    assert describe_error(None) == "none"


@pytest.mark.asyncio
async def test_retry_and_exhaustion_do_not_log_or_embed_provider_error_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    leaked_text = "fake-echoed-prompt-text"
    policy = ResiliencePolicy(sleep=AsyncMock(), random_uniform=lambda _low, high: high)
    registry = build_default_registry()

    async def always_rate_limited(spec: ModelSpec, was_fallback: bool) -> str:
        raise ClientError(429, {"error": {"message": leaked_text}})

    with caplog.at_level(logging.DEBUG, logger="app.gateway"):
        with pytest.raises(GatewayFallbackExhaustedError) as exc_info:
            await policy.execute(registry, "orchestrator", always_rate_limited)

    assert leaked_text not in caplog.text
    assert leaked_text not in str(exc_info.value)
    assert "ClientError(429)" in str(exc_info.value)


@pytest.mark.asyncio
async def test_budget_refusal_is_raised_once_without_retry_or_failover() -> None:
    policy = ResiliencePolicy(sleep=AsyncMock(), random_uniform=lambda _low, high: high)
    registry = build_default_registry()
    attempts: list[str] = []

    async def refused(spec: ModelSpec, was_fallback: bool) -> str:
        attempts.append(spec.alias)
        raise BudgetExceededError("Task '11111111-5003-4000-8000-429429429429' over $2.500000")

    with pytest.raises(BudgetExceededError):
        await policy.execute(registry, "orchestrator", refused)

    assert attempts == ["orchestrator"]
