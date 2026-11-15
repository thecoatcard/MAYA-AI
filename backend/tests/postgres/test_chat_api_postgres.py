"""Chat against the real services: PostgreSQL with row-level security, Redis, and the real wiring.

Nothing here is replaced except the Gemini client (``FakeGeminiClient``; no network, no key). The
application reads its collaborators from ``app.state.resources`` exactly as in production: the
restricted-role engine, the Redis client, the registry and the gateway. Callers register, verify and
log in through the auth API; a colleague in the same workspace gets a token minted with the same
signing key and a live session row. The test database has a random name and is dropped afterwards;
the Redis keys the tests create are deleted at the end.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from dotenv import dotenv_values
from fastapi import FastAPI
from google.genai import types
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import Message, Receive, Scope, Send

from app.api.v1.chat import get_chat_rate_limiter
from app.auth.postgres import PostgresAuthStore
from app.core.ratelimit import RateLimiter, RedisRateLimiter
from app.core.redis import create_redis_client
from app.gateway.registry import build_default_registry
from app.main import create_app
from app.services.chat_idempotency import (
    IN_PROGRESS_TTL_SECONDS,
    TERMINAL_TTL_SECONDS,
    IdempotencyRecord,
    IdempotencyScope,
    IdempotencyUnavailable,
    RedisIdempotencyStore,
)
from tests.support.auth import Harness, build_harness
from tests.support.clock import START, ManualClock
from tests.support.fake_gemini_client import FakeAsyncModels, FakeGeminiClient
from tests.support.gateway_factory import make_gateway
from tests.support.models import ORCH
from tests.support.settings import make_settings

pytestmark = [pytest.mark.postgres, pytest.mark.redis]

AUTH = "/api/v1/auth"
CHAT = "/api/v1/chat"
REPLY = "Fake model response text"


def redis_url() -> str:
    url = os.environ.get("MAYA_TEST_REDIS_URL")
    env_file = os.environ.get("MAYA_TEST_ENV_FILE")
    if url is None and env_file:
        url = dotenv_values(env_file).get("MAYA_REDIS_URL")
    if not url:
        if os.environ.get("MAYA_REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Local Redis test target is not configured")
        pytest.skip("Local Redis test target is not configured")
    return url


class ScriptedModels(FakeAsyncModels):
    """The stock fake, unless ``script`` is set: then exactly those chunks, errors and gates."""

    def __init__(self) -> None:
        super().__init__()
        self.script: list[Any] = []

    async def _stream_chunks(self, model: str) -> AsyncIterator[types.GenerateContentResponse]:
        if not self.script:
            async for item in super()._stream_chunks(model):
                yield item
            return
        self.open_streams += 1
        try:
            for step in list(self.script):
                if isinstance(step, BaseException):
                    raise step
                if isinstance(step, asyncio.Event):
                    await step.wait()
                    continue
                yield step
        finally:
            self.open_streams -= 1


def chunk(text_: str, *, tokens: tuple[int, int] | None = None) -> types.GenerateContentResponse:
    usage = None
    if tokens is not None:
        usage = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=tokens[0],
            candidates_token_count=tokens[1],
            total_token_count=tokens[0] + tokens[1],
        )
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(parts=[types.Part.from_text(text=text_)], role="model"),
                finish_reason=types.FinishReason.STOP if tokens is not None else None,
            )
        ],
        usage_metadata=usage,
    )


@dataclass
class Account:
    token: str
    user_id: UUID
    workspace_id: UUID

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class Stack:
    client: httpx.AsyncClient
    app: FastAPI
    harness: Harness
    admin: AsyncEngine
    redis: Redis
    fake: FakeGeminiClient
    models: ScriptedModels
    workspaces: list[UUID] = field(default_factory=list)


@pytest.fixture
async def stack(app_engine: AsyncEngine, admin_engine: AsyncEngine) -> AsyncIterator[Stack]:
    harness = build_harness(PostgresAuthStore(app_engine))
    settings = make_settings(redis_url=redis_url())
    redis = create_redis_client(settings)
    fake = FakeGeminiClient()
    models = ScriptedModels()
    fake.aio.models = models
    registry = build_default_registry()
    app = create_app(settings, auth_service=harness.service)
    app.state.auth_service = harness.service  # no lifespan runs under ASGITransport
    app.state.settings = settings
    app.state.resources = SimpleNamespace(
        engine=app_engine,
        redis=redis,
        registry=registry,
        model_gateway=make_gateway(fake, registry=registry),
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    value = Stack(client, app, harness, admin_engine, redis, fake, models)
    try:
        yield value
    finally:
        await client.aclose()
        for workspace in value.workspaces:  # the keys this test created
            for pattern in (
                f"maya:chat:idem:{workspace}:*",
                f"maya:chat:streams:{workspace}:*",
                f"maya:rl:chat-message:{workspace}:*",
            ):
                async for name in redis.scan_iter(match=pattern):
                    await redis.delete(name)
        await redis.aclose()


async def onboard(stack: Stack) -> Account:
    """A new account in its own new workspace, through the public auth API."""
    command = stack.harness.command()
    body = {
        "username": command.username,
        "email": command.email,
        "password": command.password,
        "full_name": command.full_name,
    }
    assert (await stack.client.post(f"{AUTH}/register", json=body)).status_code == 202
    code = stack.harness.email.codes_for(command.email)[-1]
    verified = await stack.client.post(
        f"{AUTH}/verify-email",
        json={"email": command.email, "code": code, "password": command.password},
    )
    assert verified.status_code == 200
    login = {"username": command.username, "password": command.password}
    tokens = (await stack.client.post(f"{AUTH}/token", json=login)).json()
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await stack.client.get(f"{AUTH}/me", headers=bearer)).json()
    account = Account(tokens["access_token"], UUID(me["user_id"]), UUID(me["workspace_id"]))
    stack.workspaces.append(account.workspace_id)
    return account


async def colleague(stack: Stack, of: Account) -> Account:
    """A second user in the same workspace, with a live session and a validly signed token."""
    user_id, session_id = uuid4(), uuid4()
    suffix = user_id.hex[:10]
    async with stack.admin.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, workspace_id, username, email, full_name, hashed_password) "
                "VALUES (:id, :ws, :username, :email, 'Colleague', 'not-a-real-hash')"
            ),
            {
                "id": user_id,
                "ws": of.workspace_id,
                "username": f"colleague_{suffix}",
                "email": f"colleague.{suffix}@example.com",
            },
        )
        await connection.execute(
            text(
                "INSERT INTO auth_sessions (id, workspace_id, user_id, refresh_jti, "
                "refresh_expires_at, absolute_expires_at) "
                "VALUES (:id, :ws, :user, gen_random_uuid(), :expires, :expires)"
            ),
            {
                "id": session_id,
                "ws": of.workspace_id,
                "user": user_id,
                "expires": datetime(2031, 1, 1, tzinfo=UTC),
            },
        )
    token, _ = stack.harness.tokens.issue_access(
        user_id=user_id,
        workspace_id=of.workspace_id,
        role="member",
        session_id=session_id,
        token_id=uuid4(),
    )
    return Account(token, user_id, of.workspace_id)


@dataclass(frozen=True)
class Frame:
    id: int
    event: str
    data: dict[str, Any]


def parse_sse(body: str) -> list[Frame]:
    assert body.endswith("\n\n")
    frames = []
    for block in body.split("\n\n")[:-1]:
        lines = block.split("\n")
        assert len(lines) == 3 and lines[1].startswith("event: ") and lines[2].startswith("data: ")
        frames.append(Frame(int(lines[0][4:]), lines[1][7:], json.loads(lines[2][6:])))
    assert [frame.id for frame in frames] == list(range(1, len(frames) + 1))
    return frames


def events(response: httpx.Response) -> list[Frame]:
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


def names(frames: list[Frame], *, with_progress: bool = False) -> list[str]:
    """Event names. The additive live step list (``progress``) is left out unless asked for."""
    return [frame.event for frame in frames if with_progress or frame.event != "progress"]


def new_key() -> str:
    return uuid4().hex


async def post(
    stack: Stack,
    account: Account,
    content: str = "Hello MAYA",
    *,
    key: str | None = None,
    session_id: UUID | str | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"content": content}
    if session_id is not None:
        body["session_id"] = str(session_id)
    headers = {**account.headers, "Idempotency-Key": key or new_key()}
    return await stack.client.post(f"{CHAT}/message", json=body, headers=headers)


def model_calls(stack: Stack) -> int:
    return len([c for c in stack.fake.aio.models.calls if c["method"] == "generate_content_stream"])


async def rows(stack: Stack, workspace_id: UUID, sender: str | None = None) -> list[Any]:
    """Messages of a workspace in insertion order, read as the administrator (bypassing RLS)."""
    everyone = text(
        "SELECT id, session_id, sender, content, metadata, created_at FROM messages "
        "WHERE workspace_id = :ws ORDER BY created_at, id"
    )
    one_sender = text(
        "SELECT id, session_id, sender, content, metadata, created_at FROM messages "
        "WHERE workspace_id = :ws AND sender = :sender ORDER BY created_at, id"
    )
    async with stack.admin.connect() as connection:
        if sender is None:
            return list(await connection.execute(everyone, {"ws": workspace_id}))
        return list(await connection.execute(one_sender, {"ws": workspace_id, "sender": sender}))


async def session_rows(stack: Stack, workspace_id: UUID) -> list[Any]:
    async with stack.admin.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT id, user_id, title, channel, status FROM sessions WHERE workspace_id = :w"
            ),
            {"w": workspace_id},
        )
        return list(result)


async def wait_until(condition: Callable[[], bool]) -> None:
    for _ in range(2_000):
        if condition():
            return
        await asyncio.sleep(0.005)
    pytest.fail("the condition was never reached")


# ---------------------------------------------------------------------- a conversation


async def test_a_conversation_over_two_messages_is_stored_and_the_history_reaches_the_model(
    stack: Stack,
) -> None:
    alice = await onboard(stack)

    first = events(await post(stack, alice, "My name is Priya.  Remember it."))
    assert names(first) == ["message.accepted", *["delta"] * 4, "done"]
    session_id = first[0].data["session_id"]
    second = events(await post(stack, alice, "What is my name?", session_id=session_id))
    assert second[0].data["session_id"] == session_id

    calls = [c for c in stack.fake.aio.models.calls if c["method"] == "generate_content_stream"]
    assert [item["role"] for item in calls[1]["contents"]] == ["user", "model", "user"]
    assert calls[1]["contents"][0]["parts"][0]["text"] == "My name is Priya.  Remember it."
    assert calls[1]["contents"][1]["parts"][0]["text"] == REPLY
    assert calls[1]["contents"][2]["parts"][0]["text"] == "What is my name?"

    stored = await rows(stack, alice.workspace_id)
    assert [row.sender for row in stored] == ["user", "orchestrator", "user", "orchestrator"]
    assert {row.session_id for row in stored} == {UUID(session_id)}
    assistant = stored[1]
    assert str(assistant.id) == first[0].data["assistant_message_id"]
    assert assistant.content == REPLY
    assert set(assistant.metadata) == {
        "model_id",
        "alias",
        "finish_reason",
        "usage",
        "latency_ms",
        "status",
    }
    assert assistant.metadata["usage"] == first[-1].data["usage"]
    (stored_session,) = await session_rows(stack, alice.workspace_id)
    assert stored_session.title == "My name is Priya. Remember it."
    assert stored_session.user_id == alice.user_id

    listed = (await stack.client.get(f"{CHAT}/sessions", headers=alice.headers)).json()
    assert [item["id"] for item in listed["items"]] == [session_id]
    thread = (
        await stack.client.get(f"{CHAT}/sessions/{session_id}/messages", headers=alice.headers)
    ).json()
    assert [item["role"] for item in thread["items"]] == ["user", "assistant", "user", "assistant"]
    assert thread["items"][1]["model_id"] == ORCH
    assert thread["items"][1]["finish_reason"] == "stop"


async def test_the_user_message_is_stored_before_the_model_is_called(stack: Stack) -> None:
    alice = await onboard(stack)
    gate = asyncio.Event()
    stack.models.script = [chunk("held "), gate, chunk("released", tokens=(2, 2))]

    request = asyncio.create_task(post(stack, alice, "stored first"))
    await wait_until(lambda: stack.models.open_streams > 0)  # the model has been called

    assert [row.content for row in await rows(stack, alice.workspace_id)] == ["stored first"]
    gate.set()
    frames = events(await request)
    assert names(frames)[-1] == "done"
    assert len(await rows(stack, alice.workspace_id)) == 2


# --------------------------------------------------------------------------- isolation


async def test_other_users_and_other_workspaces_cannot_reach_a_session_or_its_key(
    stack: Stack,
) -> None:
    alice = await onboard(stack)
    bob = await colleague(stack, alice)  # same workspace, another user
    carol = await onboard(stack)  # another workspace
    key = new_key()
    first = events(await post(stack, alice, "alice's private question", key=key))
    session_id = first[0].data["session_id"]
    calls = model_calls(stack)
    alice_rows = await rows(stack, alice.workspace_id)

    for intruder in (bob, carol):
        write = await post(stack, intruder, "let me in", session_id=session_id)
        assert (write.status_code, write.json()) == (404, {"error": "session_not_found"})
        read = await stack.client.get(
            f"{CHAT}/sessions/{session_id}/messages", headers=intruder.headers
        )
        assert (read.status_code, read.json()) == (404, {"error": "session_not_found"})
        listed = (await stack.client.get(f"{CHAT}/sessions", headers=intruder.headers)).json()
        assert session_id not in [item["id"] for item in listed["items"]]
    assert model_calls(stack) == calls
    assert await rows(stack, alice.workspace_id) == alice_rows

    # Alice's idempotency key is not a way into her result: a stranger using the same key and
    # text is simply a new request of their own, answered by the model, never a replay.
    for intruder in (bob, carol):
        again = events(await post(stack, intruder, "alice's private question", key=key))
        assert again[0].data["replayed"] is False
        assert again[0].data["session_id"] != session_id
    assert model_calls(stack) == calls + 2
    own_sessions = {row.id for row in await session_rows(stack, alice.workspace_id)}
    assert UUID(session_id) in own_sessions and len(own_sessions) == 2  # alice's and bob's
    for intruder in (bob, carol):
        mine = (await stack.client.get(f"{CHAT}/sessions", headers=intruder.headers)).json()
        assert len(mine["items"]) == 1 and mine["items"][0]["id"] != session_id


# ------------------------------------------------------------------------- idempotency


async def test_an_idempotent_retry_makes_one_user_row_one_assistant_row_and_one_model_call(
    stack: Stack,
) -> None:
    alice = await onboard(stack)
    key = new_key()
    first = events(await post(stack, alice, "pay the invoice once", key=key))

    replies = [events(await post(stack, alice, "pay the invoice once", key=key)) for _ in range(3)]

    assert model_calls(stack) == 1
    assert len(await rows(stack, alice.workspace_id, "user")) == 1
    assert len(await rows(stack, alice.workspace_id, "orchestrator")) == 1
    assert len(await session_rows(stack, alice.workspace_id)) == 1
    for replay in replies:
        assert names(replay) == ["message.accepted", "delta", "done"]
        assert replay[0].data == {**first[0].data, "replayed": True}
        assert replay[1].data == {"text": REPLY}
        assert replay[2].data == first[-1].data


async def test_simultaneous_identical_requests_call_the_model_exactly_once(stack: Stack) -> None:
    alice = await onboard(stack)
    key = new_key()

    responses = await asyncio.gather(*(post(stack, alice, "race me", key=key) for _ in range(6)))

    # Each one is the original request, a replay of it, or told it is still running.
    assert {response.status_code for response in responses} <= {200, 409}
    streams = [parse_sse(r.text) for r in responses if r.status_code == 200]
    assert sum(1 for frames in streams if frames[0].data["replayed"] is False) == 1
    for response in responses:
        if response.status_code == 409:
            assert response.json() == {"error": "request_in_progress"}
    assert model_calls(stack) == 1
    assert len(await rows(stack, alice.workspace_id, "user")) == 1
    assert len(await rows(stack, alice.workspace_id, "orchestrator")) == 1


async def test_the_same_key_with_another_body_is_refused_and_changes_nothing(stack: Stack) -> None:
    alice = await onboard(stack)
    key = new_key()
    first = events(await post(stack, alice, "the original", key=key))
    before = await rows(stack, alice.workspace_id)

    changed = await post(stack, alice, "something else", key=key)
    own_session = first[0].data["session_id"]
    other_session = await post(stack, alice, "the original", key=key, session_id=own_session)

    assert (changed.status_code, changed.json()) == (422, {"error": "idempotency_key_reused"})
    assert other_session.status_code == 422  # a different session_id is a different request
    assert other_session.json() == {"error": "idempotency_key_reused"}
    assert model_calls(stack) == 1 and await rows(stack, alice.workspace_id) == before
    assert first[0].data["replayed"] is False


async def test_the_redis_record_holds_identifiers_never_content_and_expires(stack: Stack) -> None:
    alice = await onboard(stack)
    key = new_key()
    gate = asyncio.Event()
    stack.models.script = [chunk("working "), gate, chunk("done", tokens=(3, 3))]
    redis_key = f"maya:chat:idem:{alice.workspace_id}:{alice.user_id}:{key}"
    private_text = "a-private-sentence-that-must-not-reach-redis"

    request = asyncio.create_task(post(stack, alice, private_text, key=key))
    await wait_until(lambda: stack.models.open_streams > 0)

    running = json.loads(cast(str, await stack.redis.get(redis_key)))
    assert running["state"] == "in_progress"
    # a crashed request cannot block its key for a day
    assert 0 < await stack.redis.ttl(redis_key) <= IN_PROGRESS_TTL_SECONDS < 3600
    gate.set()
    frames = events(await request)

    raw = cast(str, await stack.redis.get(redis_key))
    record = json.loads(raw)
    assert set(record) == {
        "v",
        "state",
        "hash",
        "session_id",
        "user_message_id",
        "assistant_message_id",
        "finish_reason",
        "error_code",
    }
    assert record["state"] == "completed" and record["finish_reason"] == "stop"
    assert record["session_id"] == frames[0].data["session_id"]
    assert record["user_message_id"] == frames[0].data["user_message_id"]
    assert record["assistant_message_id"] == frames[0].data["assistant_message_id"]
    assert private_text not in raw and "working" not in raw
    assert 86_000 < await stack.redis.ttl(redis_key) <= 86_400


async def test_a_stream_cut_midway_is_stored_interrupted_and_replayed(stack: Stack) -> None:
    alice = await onboard(stack)
    key = new_key()
    stack.models.script = [chunk("Fake "), chunk("model "), ConnectionResetError("dropped")]

    frames = events(await post(stack, alice, "cut me", key=key))

    assert names(frames) == ["message.accepted", "delta", "delta", "error"]
    assert frames[-1].data == {"code": "stream_interrupted", "retryable": True}
    (assistant,) = await rows(stack, alice.workspace_id, "orchestrator")
    assert assistant.content == "Fake model "
    assert assistant.metadata["finish_reason"] == "interrupted"
    assert assistant.metadata["usage"]["is_estimate"] is True
    calls = model_calls(stack)

    # The stored steps ("Reply stopped") are replayed too; the other events are checked as before.
    again = [
        f for f in events(await post(stack, alice, "cut me", key=key)) if f.event != "progress"
    ]
    assert names(again) == ["message.accepted", "delta", "done"]
    assert (
        again[1].data == {"text": "Fake model "} and again[2].data["finish_reason"] == "interrupted"
    )
    assert model_calls(stack) == calls
    assert len(await rows(stack, alice.workspace_id, "orchestrator")) == 1


# ------------------------------------------------------------------- a client that leaves


@dataclass
class Exchange:
    status: int
    body: str


async def converse(
    stack: Stack,
    account: Account,
    *,
    content: str,
    key: str,
    after_body: Callable[[int, asyncio.Event], Awaitable[None]],
) -> Exchange:
    """POST straight into the ASGI app; ``after_body`` may set the event (a client that left)."""
    payload = json.dumps({"content": content}).encode()
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"{CHAT}/message",
        "raw_path": f"{CHAT}/message".encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", b"test"),
            (b"authorization", f"Bearer {account.token}".encode()),
            (b"idempotency-key", key.encode()),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    disconnected = asyncio.Event()
    sent_request = False
    status = 0
    chunks: list[bytes] = []

    async def receive() -> Message:
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            await after_body(len(chunks), disconnected)

    await asyncio.wait_for(stack.app(scope, cast(Receive, receive), cast(Send, send)), timeout=15)
    return Exchange(status, b"".join(chunks).decode())


async def test_a_client_that_leaves_mid_reply_leaves_the_partial_reply_in_the_database(
    stack: Stack,
) -> None:
    alice = await onboard(stack)
    key = new_key()
    gate = asyncio.Event()  # never set: the model goes quiet
    stack.models.script = [chunk("alpha "), chunk("beta "), gate]

    async def leave(count: int, disconnected: asyncio.Event) -> None:
        if count == 3:
            disconnected.set()

    exchange = await converse(stack, alice, content="stream then leave", key=key, after_body=leave)

    assert exchange.status == 200 and names(parse_sse(exchange.body)) == [
        "message.accepted",
        "delta",
        "delta",
    ]
    (assistant,) = await rows(stack, alice.workspace_id, "orchestrator")
    assert assistant.content == "alpha beta "
    assert assistant.metadata["finish_reason"] == "interrupted"
    assert assistant.metadata["status"] == "interrupted"
    assert stack.models.open_streams == 0  # the provider stream was closed
    record = json.loads(
        cast(
            str,
            await stack.redis.get(f"maya:chat:idem:{alice.workspace_id}:{alice.user_id}:{key}"),
        )
    )
    assert (record["state"], record["finish_reason"]) == ("interrupted", "interrupted")

    calls = model_calls(stack)
    replay = events(await post(stack, alice, "stream then leave", key=key))
    assert replay[1].data == {"text": "alpha beta "} and replay[-1].event == "done"
    assert model_calls(stack) == calls


# ------------------------------------------------------------------------ rate limiting


async def test_the_rate_limit_is_counted_in_redis_per_user(stack: Stack) -> None:
    stack.app.state.settings = make_settings(redis_url=redis_url(), chat_rate_limit_per_minute=2)
    clock = ManualClock(START + timedelta(seconds=10))  # ten seconds into a window; it cannot roll

    async def limiter_on_a_fixed_clock() -> RateLimiter:
        return RedisRateLimiter(stack.redis, clock)

    stack.app.dependency_overrides[get_chat_rate_limiter] = limiter_on_a_fixed_clock
    alice = await onboard(stack)
    bob = await colleague(stack, alice)

    assert (await post(stack, alice)).status_code == 200
    assert (await post(stack, alice)).status_code == 200
    refused = await post(stack, alice)

    assert (refused.status_code, refused.json()) == (429, {"error": "rate_limited"})
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    assert (await post(stack, bob)).status_code == 200  # another user in the same workspace
    assert model_calls(stack) == 3


# ----------------------------------------------------------------------- dependency outages


async def test_an_unreachable_redis_fails_closed_before_anything_is_stored(stack: Stack) -> None:
    alice = await onboard(stack)
    dead = Redis.from_url(
        "redis://127.0.0.1:1/0", socket_connect_timeout=0.2, decode_responses=True
    )
    stack.app.state.resources.redis = dead
    try:
        response = await post(stack, alice)
    finally:
        await dead.aclose()

    assert (response.status_code, response.json()) == (503, {"error": "chat_unavailable"})
    assert model_calls(stack) == 0
    assert await rows(stack, alice.workspace_id) == []
    assert await session_rows(stack, alice.workspace_id) == []


# ----------------------------------------------------------------------------- pagination


async def test_sessions_and_messages_paginate_with_opaque_cursors(stack: Stack) -> None:
    alice = await onboard(stack)
    titles = ["first topic", "second topic", "third topic"]
    sessions: list[str] = []
    for title in titles:
        sessions.append(events(await post(stack, alice, title))[0].data["session_id"])
    for turn in range(2):  # the third session gets four more messages
        events(await post(stack, alice, f"follow-up {turn}", session_id=sessions[2]))

    page_one = (
        await stack.client.get(f"{CHAT}/sessions", params={"limit": 2}, headers=alice.headers)
    ).json()
    assert [item["title"] for item in page_one["items"]] == ["third topic", "second topic"]
    assert page_one["next_cursor"] is not None
    page_two = (
        await stack.client.get(
            f"{CHAT}/sessions",
            params={"limit": 2, "cursor": page_one["next_cursor"]},
            headers=alice.headers,
        )
    ).json()
    assert [item["title"] for item in page_two["items"]] == ["first topic"]
    assert page_two["next_cursor"] is None

    collected: list[dict[str, Any]] = []
    cursor: str | None = None
    messages_cursor = ""
    for _ in range(10):
        params: dict[str, Any] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        page = (
            await stack.client.get(
                f"{CHAT}/sessions/{sessions[2]}/messages", params=params, headers=alice.headers
            )
        ).json()
        collected += page["items"]
        cursor = page["next_cursor"]
        messages_cursor = messages_cursor or cursor or ""
        if cursor is None:
            break
    assert [item["role"] for item in collected] == ["user", "assistant"] * 3
    assert [item["content"] for item in collected][::2] == [
        "third topic",
        "follow-up 0",
        "follow-up 1",
    ]
    assert len({item["id"] for item in collected}) == 6

    # A cursor belongs to the list it came from: another session's messages, or the session list.
    assert messages_cursor
    for path in (f"{CHAT}/sessions/{sessions[0]}/messages", f"{CHAT}/sessions"):
        wrong_list = await stack.client.get(
            path, params={"cursor": messages_cursor}, headers=alice.headers
        )
        assert wrong_list.status_code == 422
        assert wrong_list.json() == {"error": "validation_error", "fields": ["cursor"]}
    garbage = await stack.client.get(
        f"{CHAT}/sessions", params={"cursor": "garbage"}, headers=alice.headers
    )
    assert garbage.json() == {"error": "validation_error", "fields": ["cursor"]}


# ------------------------------------------------------------------------- the Redis store


def _claim(session_id: UUID | None = None) -> IdempotencyRecord:
    return IdempotencyRecord(
        state="in_progress",
        request_hash="fedcba9876543210" * 4,
        session_id=session_id,
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
    )


async def test_the_redis_store_claims_a_key_exactly_once_and_expires_it(stack: Stack) -> None:
    store = RedisIdempotencyStore(stack.redis)
    scope = IdempotencyScope(uuid4(), uuid4(), new_key())
    stack.workspaces.append(scope.workspace_id)

    attempts = [_claim() for _ in range(20)]
    outcomes = await asyncio.gather(*(store.claim(scope, record) for record in attempts))

    winners = [record for record, seen in zip(attempts, outcomes, strict=True) if seen is None]
    assert len(winners) == 1  # SET NX: one claimant, whatever the interleaving
    assert all(seen == winners[0] for seen in outcomes if seen is not None)
    assert 0 < await stack.redis.ttl(scope.redis_key) <= IN_PROGRESS_TTL_SECONDS

    done = replace(winners[0], state="completed", finish_reason="stop")
    await store.save(scope, done)
    assert await store.claim(scope, _claim()) == done
    assert IN_PROGRESS_TTL_SECONDS < await stack.redis.ttl(scope.redis_key) <= TERMINAL_TTL_SECONDS


async def test_a_late_write_does_not_overwrite_the_request_that_took_the_key_over(
    stack: Stack,
) -> None:
    store = RedisIdempotencyStore(stack.redis)
    scope = IdempotencyScope(uuid4(), uuid4(), new_key())
    stack.workspaces.append(scope.workspace_id)
    first, second = _claim(), _claim()

    assert await store.claim(scope, first) is None
    await stack.redis.delete(scope.redis_key)  # the first claim expired ...
    assert await store.claim(scope, second) is None  # ... and another request took the key

    await store.save(scope, replace(first, state="completed", finish_reason="stop"))  # a late write
    assert await store.claim(scope, _claim()) == second  # the second attempt's claim stands

    # nobody owns the key: an expired lease cannot resurrect
    await stack.redis.delete(scope.redis_key)
    assert not await store.save(scope, replace(first, state="completed", finish_reason="stop"))


async def test_releasing_deletes_only_the_callers_own_unfinished_claim(stack: Stack) -> None:
    store = RedisIdempotencyStore(stack.redis)
    scope = IdempotencyScope(uuid4(), uuid4(), new_key())
    stack.workspaces.append(scope.workspace_id)
    mine, someone_elses = _claim(), _claim()

    assert await store.claim(scope, mine) is None
    await store.release(scope, someone_elses)  # a different attempt's claim: left alone
    assert await store.claim(scope, someone_elses) == mine
    await store.save(scope, replace(mine, state="completed", finish_reason="stop"))
    await store.release(scope, mine)  # finished: a result must outlive its request
    assert (await store.claim(scope, someone_elses)) is not None

    free = IdempotencyScope(scope.workspace_id, scope.user_id, new_key())
    assert await store.claim(free, mine) is None
    await store.release(free, mine)
    assert await store.claim(free, someone_elses) is None  # released, so it can be claimed again


async def test_an_unreadable_record_or_an_unreachable_redis_fails_closed(stack: Stack) -> None:
    store = RedisIdempotencyStore(stack.redis)
    scope = IdempotencyScope(uuid4(), uuid4(), new_key())
    stack.workspaces.append(scope.workspace_id)
    await stack.redis.set(scope.redis_key, "this is not a record", ex=60)
    with pytest.raises(IdempotencyUnavailable):
        await store.claim(scope, _claim())

    dead = RedisIdempotencyStore(
        Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2, decode_responses=True)
    )
    with pytest.raises(IdempotencyUnavailable):
        await dead.claim(scope, _claim())
    with pytest.raises(IdempotencyUnavailable):
        await dead.save(scope, _claim())
    await dead.release(scope, _claim())  # best effort: never raises
