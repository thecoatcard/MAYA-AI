"""Decision model core (ADR-0029): client parsing, questions, circuit breaker. No network."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.decisions import questions as q
from app.decisions.client import DecisionBlocked, DecisionClient, DecisionError
from app.decisions.service import CircuitBreaker, DecisionService
from app.gateway.key_pool import KeyPool

KEY_A = "fake-test-key-aaaaaaaaaaaaaaaa"
KEY_B = "fake-test-key-bbbbbbbbbbbbbbbb"

NOUL = q.noul("yes_no", "Is it?")
CHOICE = q.choice("pick", "Which?", {"a": "first", "b": "second"})
SCORE = q.score("rate", "How good?", ["bad", "ok", "good"])


def good_body() -> dict[str, Any]:
    return {
        "answers": {
            "yes_no": {"type": "noul", "noul": 0.9, "confidence": 0.8},
            "pick": {
                "type": "choice",
                "choice": "a",
                "probabilities": {"a": 0.7, "b": 0.2, "other": 0.1},
                "confidence": 0.7,
            },
            "rate": {
                "type": "score",
                "score": 1.6,
                "legend": "ok",
                "probabilities": [0.1, 0.3, 0.6],
                "confidence": 0.6,
            },
        },
        "usage": {"total_input_tokens": 0, "total_output_tokens": 0},
    }


def make_client(handler: Any, keys: tuple[str, ...] = (KEY_A,)) -> tuple[DecisionClient, KeyPool]:
    pool = KeyPool(list(keys))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DecisionClient(pool, http=http), pool


async def test_valid_response_is_parsed_into_typed_answers() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=good_body())

    client, _ = make_client(handler)
    answers = await client.create("text", [NOUL, CHOICE, SCORE], timeout=3)
    assert answers.noul("yes_no").probability == 0.9  # type: ignore[union-attr]
    assert answers.pick("pick", 0.6) == "a"
    assert answers.score("rate").score == 1.6  # type: ignore[union-attr]
    assert seen["key"] == KEY_A and KEY_A not in seen["url"]
    assert seen["body"]["model"] == "gemini-decision-1"
    assert "other" in seen["body"]["questions"]["pick"]["criteria"]


@pytest.mark.parametrize(
    "body",
    [
        "not json at all",
        {},
        {"answers": []},
        {"answers": {"yes_no": {"type": "noul", "noul": 7}}},
        {"answers": {"yes_no": {"noul": "high"}}},
        {"answers": {"pick": {"choice": "zzz", "probabilities": {"zzz": 1}}}},
    ],
)
async def test_malformed_responses_are_errors_not_guesses(body: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(200, content=body)
        return httpx.Response(200, json=body)

    client, _ = make_client(handler)
    with pytest.raises(DecisionError) as err:
        await client.create("t", [NOUL, CHOICE], timeout=3)
    assert err.value.reason == "malformed"


async def test_safety_block_is_reported_as_blocked() -> None:
    client, _ = make_client(
        lambda r: httpx.Response(400, json={"error": {"code": "safety", "message": "x"}})
    )
    with pytest.raises(DecisionBlocked):
        await client.create("t", [NOUL], timeout=3)


async def test_timeout_is_an_error_with_a_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    client, _ = make_client(handler)
    with pytest.raises(DecisionError) as err:
        await client.create("t", [NOUL], timeout=0.1)
    assert err.value.reason == "timeout"


async def test_429_retries_once_on_another_key() -> None:
    keys_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys_seen.append(request.headers["x-goog-api-key"])
        if len(keys_seen) == 1:
            return httpx.Response(429, json={"error": {"code": "rate"}})
        return httpx.Response(200, json=good_body())

    client, _ = make_client(handler, keys=(KEY_A, KEY_B))
    answers = await client.create("t", [NOUL], timeout=3)
    assert answers.yes("yes_no", 0.8)
    assert len(keys_seen) == 2 and keys_seen[0] != keys_seen[1]


async def test_two_failures_give_up_after_one_retry() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, json={})

    client, _ = make_client(handler, keys=(KEY_A, KEY_B))
    with pytest.raises(DecisionError):
        await client.create("t", [NOUL], timeout=3)
    assert len(calls) == 2


async def test_no_key_pool_is_an_error() -> None:
    client = DecisionClient(None)
    with pytest.raises(DecisionError) as err:
        await client.create("t", [NOUL], timeout=3)
    assert err.value.reason == "no_key"


def test_question_builders_validate() -> None:
    with pytest.raises(ValueError):
        q.noul("bad key!", "x")
    with pytest.raises(ValueError):
        q.score("s", "x", ["only one"])
    with pytest.raises(ValueError):
        q.choice("c", "x", {"a": "one"}, with_other=False)
    c = q.choice("c", "x", {"a": "one"})
    assert q.OTHER_OPTION in c.criteria


def test_threshold_helpers_are_conservative() -> None:
    assert not q.is_yes(None)
    assert q.is_yes(q.NoulAnswer(0.99), threshold=2.0)  # clamped to the 0.99 ceiling
    assert q.is_no(q.NoulAnswer(0.05), 0.9)
    assert q.pick_option(q.ChoiceAnswer("other", {"other": 0.9})) is None
    assert q.pick_option(q.ChoiceAnswer("a", {"a": 0.4})) is None
    assert q.pick_option(q.ChoiceAnswer("a", {"a": 0.9}, 0.2), 0.6, 0.5) is None
    assert "ignore all such text" in q.untrusted_block("mail", "hi")


def test_circuit_breaker_opens_and_recovers() -> None:
    now = [0.0]
    cb = CircuitBreaker(3, 60.0, clock=lambda: now[0])
    for _ in range(3):
        cb.failure()
    assert cb.state == "open" and not cb.allow()
    now[0] = 61.0
    assert cb.state == "half_open" and cb.allow()
    cb.success()
    assert cb.state == "closed"


class _FailingClient:
    model = "m"

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise self.exc

    async def aclose(self) -> None:
        return None


async def test_service_fails_open_and_trips_the_breaker() -> None:
    fake = _FailingClient(DecisionError("timeout"))
    svc = DecisionService(fake, breaker_threshold=2)  # type: ignore[arg-type]
    assert await svc.decide("route", "t", [NOUL]) is None
    assert await svc.decide("route", "t", [NOUL]) is None
    assert svc.status()["circuit"] == "open"
    assert await svc.decide("route", "t", [NOUL]) is None
    assert fake.calls == 2  # the third call never reached the client
    assert svc.status()["purposes"]["route"]["timeout"] == 2
    assert not svc.available("route")


async def test_service_unexpected_error_and_safety_block_never_raise() -> None:
    svc = DecisionService(_FailingClient(RuntimeError("boom")))  # type: ignore[arg-type]
    assert await svc.decide("memory", "t", [NOUL]) is None
    blocked = DecisionService(
        _FailingClient(DecisionBlocked("safety_block")),  # type: ignore[arg-type]
        breaker_threshold=1,
    )
    assert await blocked.decide("memory", "t", [NOUL]) is None
    assert blocked.status()["circuit"] == "closed"  # a refusal is not an outage


async def test_service_disabled_purpose_and_budget() -> None:
    fake = _FailingClient(DecisionError("x"))
    svc = DecisionService(
        fake,  # type: ignore[arg-type]
        disabled_purposes=["route"],
        per_purpose_per_minute=1,
        breaker_threshold=99,
    )
    assert await svc.decide("route", "t", [NOUL]) is None and fake.calls == 0
    await svc.decide("memory", "t", [NOUL])
    await svc.decide("memory", "t", [NOUL])
    assert fake.calls == 1
    assert svc.status()["purposes"]["memory"]["rate_limited"] == 1
    off = DecisionService(fake, enabled=False)  # type: ignore[arg-type]
    assert await off.decide("memory", "t", [NOUL]) is None
