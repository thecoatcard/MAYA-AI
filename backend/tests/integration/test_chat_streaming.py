"""The chat HTTP surface, end to end through the real application, with no services.

Fakes stand in for everything outside the process: the Gemini client (``FakeGeminiClient``), the
database (an in-memory repository), Redis (an in-memory idempotency store) and the rate limiter.
Authentication is real: every caller registers, verifies and logs in through the auth API, so a
bearer token is exactly what production issues. The same behaviour against PostgreSQL and Redis is
covered by ``tests/postgres/test_chat_api_postgres.py``.

Streaming, replay and failure tests read whole responses. The two disconnect tests drive the ASGI
application by hand, because a client that leaves mid-reply cannot be expressed through ``httpx``.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated, Any, cast
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from fastapi import Depends, FastAPI
from google.genai import types
from google.genai.errors import ClientError, ServerError
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.types import Message, Receive, Scope, Send

from app.api.v1.chat import (
    get_chat_service,
    get_chat_settings,
    get_model_gateway,
    get_model_registry,
)
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.core.clock import Clock
from app.core.config import Settings
from app.db.repositories.chat import (
    ChatMessageRecord,
    ChatSessionRecord,
    InvalidCursor,
    RecordPage,
    SessionNotFound,
)
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import ModelRegistry, build_default_registry
from app.main import create_app
from app.services.chat_idempotency import (
    IdempotencyRecord,
    IdempotencyScope,
    IdempotencyUnavailable,
    State,
    request_hash,
)
from app.services.chat_service import ChatService, ChatStorage
from tests.support.auth import FakeRateLimiter, Harness, build_harness
from tests.support.clock import ManualClock
from tests.support.fake_gemini_client import FakeAsyncModels, FakeGeminiClient
from tests.support.fake_store import FakeAuthStore
from tests.support.gateway_factory import make_gateway
from tests.support.settings import make_settings

AUTH = "/api/v1/auth"
CHAT = "/api/v1/chat"
REPLY = "Fake model response text"  # what FakeGeminiClient streams by default, word by word
CONTENT_MARKER = "ZXQ-content-marker-731"
REPLY_MARKER = "ZXQ-reply-marker-482"
PROVIDER_MARKER = "ZXQ-provider-marker-905"
KEY_MARKER = "ZXQ-key-marker-1234567"


# ------------------------------------------------------------------------------------- fakes


class MemoryChat:
    """In-memory sessions and messages, scoped by workspace and user like the real repository."""

    def __init__(self) -> None:
        self.sessions: dict[UUID, ChatSessionRecord] = {}
        self.messages: list[ChatMessageRecord] = []
        self.unavailable = False  # the database cannot be reached
        self.fail_assistant_insert = False  # storing the assistant reply raises
        self._tick = 0

    def now(self) -> datetime:
        self._tick += 1
        return datetime(2026, 11, 5, 12, 0, tzinfo=UTC) + timedelta(milliseconds=self._tick)

    def database(self) -> "MemoryDatabase":
        return MemoryDatabase(self)

    def repository(
        self, connection: AsyncConnection, workspace_id: UUID, user_id: UUID
    ) -> ChatStorage:
        return MemoryRepository(self, workspace_id, user_id)

    def rows(self, sender: str) -> list[ChatMessageRecord]:
        return [message for message in self.messages if message.sender == sender]


class MemoryDatabase:
    def __init__(self, chat: MemoryChat) -> None:
        self._chat = chat

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        if self._chat.unavailable:
            raise ConnectionRefusedError(PROVIDER_MARKER)
        yield cast(AsyncConnection, None)


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.startswith("o") or not cursor[1:].isdigit():
        raise InvalidCursor
    return int(cursor[1:])


class MemoryRepository:
    def __init__(self, chat: MemoryChat, workspace_id: UUID, user_id: UUID) -> None:
        self._chat = chat
        self._workspace_id = workspace_id
        self._user_id = user_id

    def _mine(self, session_id: UUID) -> ChatSessionRecord | None:
        record = self._chat.sessions.get(session_id)
        if record is None or (record.workspace_id, record.user_id) != (
            self._workspace_id,
            self._user_id,
        ):
            return None
        return record

    async def create_session(self, title: str | None = None, channel: Any = "web") -> Any:
        record = ChatSessionRecord(
            uuid4(),
            self._workspace_id,
            self._user_id,
            None if title is None else title[:255],
            channel,
            "active",
            self._chat.now(),
        )
        self._chat.sessions[record.id] = record
        return record

    async def get_session(self, session_id: UUID) -> ChatSessionRecord | None:
        return self._mine(session_id)

    async def list_sessions(
        self, limit: int = 20, cursor: str | None = None
    ) -> RecordPage[ChatSessionRecord]:
        mine = sorted(
            (s for s in self._chat.sessions.values() if self._mine(s.id) is not None),
            key=lambda s: s.created_at,
            reverse=True,
        )
        start = _offset(cursor)
        more = len(mine) > start + limit
        return RecordPage(tuple(mine[start : start + limit]), f"o{start + limit}" if more else None)

    async def add_message(
        self,
        session_id: UUID,
        sender: Any,
        content: str,
        modality: Any = "text",
        metadata: Mapping[str, Any] | None = None,
        message_id: UUID | None = None,
    ) -> ChatMessageRecord:
        if sender == "orchestrator" and self._chat.fail_assistant_insert:
            raise RuntimeError(PROVIDER_MARKER)
        if self._mine(session_id) is None:
            raise SessionNotFound
        record = ChatMessageRecord(
            uuid4() if message_id is None else message_id,
            self._workspace_id,
            session_id,
            sender,
            modality,
            content,
            json.loads(json.dumps(dict(metadata or {}))),  # what the JSONB column would keep
            self._chat.now(),
        )
        self._chat.messages.append(record)
        return record

    async def get_message(self, message_id: UUID) -> ChatMessageRecord | None:
        for message in self._chat.messages:
            if message.id == message_id and self._mine(message.session_id) is not None:
                return message
        return None

    def _conversation(self, session_id: UUID) -> list[ChatMessageRecord]:
        if self._mine(session_id) is None:
            raise SessionNotFound
        return [
            m
            for m in self._chat.messages
            if m.session_id == session_id and m.sender in ("user", "orchestrator")
        ]

    async def list_messages(
        self, session_id: UUID, limit: int = 50, cursor: str | None = None
    ) -> RecordPage[ChatMessageRecord]:
        start = _offset(cursor)
        rows = self._conversation(session_id)
        more = len(rows) > start + limit
        return RecordPage(tuple(rows[start : start + limit]), f"o{start + limit}" if more else None)

    async def recent_messages(self, session_id: UUID, limit: int) -> list[ChatMessageRecord]:
        return self._conversation(session_id)[-limit:]


class MemoryIdempotencyStore:
    def __init__(self) -> None:
        self.records: dict[str, IdempotencyRecord] = {}
        self.saves: list[tuple[State, int]] = []  # (state, number of keys) after each save
        self.unavailable = False
        self.terminal = asyncio.Event()  # set when a request reaches a terminal state

    def _check(self) -> None:
        if self.unavailable:
            raise IdempotencyUnavailable

    async def claim(
        self, scope: IdempotencyScope, record: IdempotencyRecord
    ) -> IdempotencyRecord | None:
        self._check()
        existing = self.records.get(scope.redis_key)
        if existing is not None:
            return existing
        self.records[scope.redis_key] = record
        return None

    async def save(self, scope: IdempotencyScope, record: IdempotencyRecord) -> None:
        self._check()
        self.records[scope.redis_key] = record
        self.saves.append((record.state, len(self.records)))
        if record.state != "in_progress":
            self.terminal.set()

    async def release(self, scope: IdempotencyScope, record: IdempotencyRecord) -> None:
        current = self.records.get(scope.redis_key)
        if (
            current is not None
            and current.state == "in_progress"
            and current.assistant_message_id == record.assistant_message_id
        ):
            del self.records[scope.redis_key]

    def only(self) -> IdempotencyRecord:
        assert len(self.records) == 1
        return next(iter(self.records.values()))


class ScriptedModels(FakeAsyncModels):
    """A fake model whose stream is exactly ``script``: chunks, exceptions and gates (events)."""

    def __init__(self) -> None:
        super().__init__()
        self.script: list[Any] = []

    async def _stream_chunks(self, model: str) -> AsyncIterator[types.GenerateContentResponse]:
        if not self.script:  # unscripted: the stock fake streams its default text word by word
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


def chunk(
    text: str,
    *,
    finish: types.FinishReason | None = None,
    tokens: tuple[int, int] | None = None,
) -> types.GenerateContentResponse:
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
                content=types.Content(parts=[types.Part.from_text(text=text)], role="model"),
                finish_reason=finish,
            )
        ],
        usage_metadata=usage,
    )


class Ticker:
    """A monotonic clock that moves by ``step`` each time it is read (0: it never moves)."""

    def __init__(self) -> None:
        self.value = 0.0
        self.step = 0.0

    def __call__(self) -> float:
        self.value += self.step
        return self.value


# ------------------------------------------------------------------------------------- world


@dataclass
class Account:
    token: str
    user_id: UUID
    workspace_id: UUID
    refresh_token: str = ""

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class World:
    client: httpx.AsyncClient
    app: FastAPI
    harness: Harness
    chat: MemoryChat
    store: MemoryIdempotencyStore
    limiter: FakeRateLimiter
    fake: FakeGeminiClient
    models: ScriptedModels
    gateway: ModelGateway
    registry: ModelRegistry
    settings: Settings
    clock: ManualClock
    ticker: Ticker


@asynccontextmanager
async def make_world(*, with_client: bool = True, **settings: Any) -> AsyncIterator[World]:
    harness = build_harness(FakeAuthStore())
    configuration = make_settings(gemini_api_key=None, **settings)
    app = create_app(configuration, auth_service=harness.service)
    app.state.auth_service = harness.service  # no lifespan runs under ASGITransport
    fake = FakeGeminiClient()
    models = ScriptedModels()
    fake.aio.models = models
    registry = build_default_registry()
    gateway = (
        make_gateway(fake, registry=registry)
        if with_client
        else ModelGateway(registry=registry, settings=configuration)
    )
    assert (gateway.client is not None) == with_client  # never a real client, never a live call
    world = World(
        client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"),
        app=app,
        harness=harness,
        chat=MemoryChat(),
        store=MemoryIdempotencyStore(),
        limiter=FakeRateLimiter(harness.clock),
        fake=fake,
        models=models,
        gateway=gateway,
        registry=registry,
        settings=configuration,
        clock=harness.clock,
        ticker=Ticker(),
    )

    async def build(principal: Annotated[Principal, Depends(get_principal)]) -> ChatService:
        return ChatService(
            principal=principal,
            database=world.chat.database(),
            gateway=world.gateway,
            registry=world.registry,
            settings=world.settings,
            limiter=world.limiter,
            idempotency=world.store,
            clock=cast(Clock, world.clock),
            monotonic=world.ticker,
            repositories=world.chat.repository,
        )

    app.dependency_overrides[get_chat_service] = build
    try:
        yield world
    finally:
        await world.client.aclose()


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async with make_world() as value:
        yield value


async def sign_in(world: World) -> Account:
    command = world.harness.command()
    body = {
        "username": command.username,
        "email": command.email,
        "password": command.password,
        "full_name": command.full_name,
    }
    assert (await world.client.post(f"{AUTH}/register", json=body)).status_code == 202
    code = world.harness.email.codes_for(command.email)[-1]
    verified = await world.client.post(
        f"{AUTH}/verify-email",
        json={"email": command.email, "code": code, "password": command.password},
    )
    assert verified.status_code == 200
    login = {"username": command.username, "password": command.password}
    tokens = (await world.client.post(f"{AUTH}/token", json=login)).json()
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await world.client.get(f"{AUTH}/me", headers=bearer)).json()
    return Account(
        tokens["access_token"],
        UUID(me["user_id"]),
        UUID(me["workspace_id"]),
        tokens["refresh_token"],
    )


def new_key() -> str:
    return uuid4().hex


async def send(
    world: World,
    account: Account,
    content: str = "Hello MAYA",
    *,
    key: str | None = None,
    with_key: bool = True,
    session_id: UUID | str | None = None,
    raw: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    **extra: Any,
) -> httpx.Response:
    body: dict[str, Any] = {"content": content, **extra}
    if session_id is not None:
        body["session_id"] = str(session_id)
    sent = {**account.headers, "Content-Type": "application/json"}
    if with_key:
        sent["Idempotency-Key"] = new_key() if key is None else key
    if headers:
        sent.update(headers)
    if raw is not None:
        return await world.client.post(f"{CHAT}/message", content=raw, headers=sent)
    return await world.client.post(f"{CHAT}/message", json=body, headers=sent)


@dataclass(frozen=True)
class Frame:
    id: int
    event: str
    data: dict[str, Any]


def parse_sse(text: str) -> list[Frame]:
    """Strict: every frame is exactly ``id``, ``event`` and one ``data`` line, then a blank line."""
    assert text == "" or text.endswith("\n\n")
    frames = []
    for block in text.split("\n\n")[:-1]:
        lines = block.split("\n")
        assert len(lines) == 3, lines
        assert lines[0].startswith("id: ") and lines[1].startswith("event: ")
        assert lines[2].startswith("data: ")
        frames.append(Frame(int(lines[0][4:]), lines[1][7:], json.loads(lines[2][6:])))
    assert [frame.id for frame in frames] == list(range(1, len(frames) + 1))
    return frames


def events(response: httpx.Response) -> list[Frame]:
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


def names(frames: list[Frame]) -> list[str]:
    return [frame.event for frame in frames]


def reply_text(frames: list[Frame]) -> str:
    return "".join(f.data["text"] for f in frames if f.event == "delta")


def stream_calls(world: World) -> list[dict[str, Any]]:
    return [c for c in world.fake.aio.models.calls if c["method"] == "generate_content_stream"]


def nothing_happened(world: World) -> None:
    assert world.chat.messages == [] and world.chat.sessions == {}
    assert world.store.records == {}
    assert stream_calls(world) == []


# ------------------------------------------------------------------------ authentication


async def test_every_chat_route_requires_a_valid_access_token(world: World) -> None:
    session = uuid4()
    for method, path in (
        ("POST", f"{CHAT}/message"),
        ("GET", f"{CHAT}/sessions"),
        ("GET", f"{CHAT}/sessions/{session}/messages"),
    ):
        missing = await world.client.request(method, path, json={"content": "hi"})
        assert (missing.status_code, missing.json()) == (401, {"error": "not_authenticated"})
        bad = await world.client.request(
            method, path, json={"content": "hi"}, headers={"Authorization": "Bearer nonsense"}
        )
        assert (bad.status_code, bad.json()) == (401, {"error": "invalid_token"})
    nothing_happened(world)


async def test_authentication_is_checked_before_the_key_and_the_body(world: World) -> None:
    response = await world.client.post(f"{CHAT}/message", content=b"{not json")  # no key either
    assert response.status_code == 401


async def test_a_logged_out_session_can_no_longer_chat(world: World) -> None:
    account = await sign_in(world)
    assert (await send(world, account)).status_code == 200
    calls = len(stream_calls(world))
    logout = await world.client.post(
        f"{AUTH}/logout", json={"refresh_token": account.refresh_token}
    )
    assert logout.status_code == 204

    response = await send(world, account)

    assert (response.status_code, response.json()) == (401, {"error": "invalid_token"})
    assert len(stream_calls(world)) == calls


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "keys",
    [
        None,
        "",
        "short",  # 5 characters
        "a" * 7,
        "a" * 129,
        "has space in it",
        "semi;colon-key1",
        "colon:colon:colon",
    ],
)
async def test_a_missing_or_malformed_idempotency_key_is_a_400(
    world: World, keys: str | None
) -> None:
    account = await sign_in(world)
    headers = {} if keys is None else {"Idempotency-Key": keys}
    response = await world.client.post(
        f"{CHAT}/message", json={"content": "hi"}, headers={**account.headers, **headers}
    )
    assert (response.status_code, response.json()) == (400, {"error": "idempotency_key_required"})
    nothing_happened(world)


@pytest.mark.parametrize("key", ["a" * 8, "a" * 128, "A-b_C-d_9" * 3])
async def test_keys_of_8_to_128_url_safe_characters_are_accepted(world: World, key: str) -> None:
    account = await sign_in(world)
    assert (await send(world, account, key=key)).status_code == 200


async def test_a_repeated_idempotency_key_header_is_a_400(world: World) -> None:
    account = await sign_in(world)
    response = await world.client.post(
        f"{CHAT}/message",
        content=b'{"content":"hi"}',
        headers=[
            *account.headers.items(),
            ("Idempotency-Key", "aaaaaaaa"),
            ("Idempotency-Key", "bbbbbbbb"),
        ],
    )
    assert response.status_code == 400
    nothing_happened(world)


async def test_the_key_is_checked_before_the_body(world: World) -> None:
    account = await sign_in(world)
    response = await world.client.post(
        f"{CHAT}/message", content=b"{not json", headers=account.headers
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        (b"{not json", ["body"]),
        (b"[]", ["body"]),
        (b'"text"', ["body"]),
        (b"{}", ["content"]),
        (b'{"content": ""}', ["content"]),
        (b'{"content": "   \\n\\t "}', ["content"]),
        (b'{"content": 12}', ["content"]),
        (b'{"content": null}', ["content"]),
        (b'{"content": "hi", "extra": 1}', ["body"]),  # an unknown field's name is not echoed
        (b'{"content": "hi", "modality": "audio"}', ["modality"]),
        (b'{"content": "hi", "session_id": "not-a-uuid"}', ["session_id"]),
        (b'{"content": "hi", "session_id": 7}', ["session_id"]),
        (b'{"content": "bell\\u0007"}', ["content"]),
        (b'{"content": "nul\\u0000"}', ["content"]),
        (b'{"content": "c1\\u0085"}', ["content"]),
        (b'{"content": "\\ud800"}', ["body"]),
    ],
)
async def test_an_invalid_body_is_a_422_that_names_fields_and_never_stores_anything(
    world: World, body: bytes, fields: list[str]
) -> None:
    account = await sign_in(world)
    response = await send(world, account, raw=body)
    assert response.status_code == 422
    assert response.json() == {"error": "validation_error", "fields": fields}
    nothing_happened(world)


async def test_a_validation_error_never_repeats_what_was_sent(world: World) -> None:
    account = await sign_in(world)
    response = await send(
        world, account, raw=json.dumps({"content": CONTENT_MARKER, "x": CONTENT_MARKER}).encode()
    )
    assert response.status_code == 422
    assert CONTENT_MARKER not in response.text


async def test_content_longer_than_the_configured_maximum_is_refused() -> None:
    async with make_world(chat_max_message_chars=20) as world:
        account = await sign_in(world)
        ok = await send(world, account, "x" * 20)
        assert ok.status_code == 200
        too_long = await send(world, account, "x" * 21)
        assert (too_long.status_code, too_long.json()) == (
            422,
            {"error": "validation_error", "fields": ["content"]},
        )
        assert len(world.chat.rows("user")) == 1  # only the first message was stored


async def test_a_message_too_big_for_the_context_budget_is_refused_before_storing() -> None:
    async with make_world(chat_max_message_chars=16_000, chat_context_token_budget=1_000) as world:
        account = await sign_in(world)
        response = await send(world, account, "word " * 3_000)  # about 5,000 estimated tokens
        assert response.status_code == 422
        assert response.json() == {"error": "validation_error", "fields": ["content"]}
        nothing_happened(world)


# ------------------------------------------------------------ preconditions and refusals


async def test_without_a_gemini_client_the_answer_is_503_and_nothing_is_stored() -> None:
    async with make_world(with_client=False) as world:
        account = await sign_in(world)
        response = await send(world, account)
        assert (response.status_code, response.json()) == (503, {"error": "model_unavailable"})
        nothing_happened(world)
        assert world.limiter.count("chat-message") == 0  # refused before the rate limit was spent


async def test_the_per_user_rate_limit_answers_429_with_retry_after() -> None:
    async with make_world(chat_rate_limit_per_minute=2) as world:
        account = await sign_in(world)
        for _ in range(2):
            assert (await send(world, account)).status_code == 200
        before = len(world.chat.messages)
        calls = len(stream_calls(world))
        world.clock.advance(seconds=20)
        refused = await send(world, account)
        assert (refused.status_code, refused.json()) == (429, {"error": "rate_limited"})
        assert refused.headers["Retry-After"] == "40"  # the rest of the 60-second window
        assert len(world.chat.messages) == before and len(stream_calls(world)) == calls
        world.clock.advance(seconds=40)  # the next window
        assert (await send(world, account)).status_code == 200


async def test_the_rate_limit_is_per_user_not_shared() -> None:
    async with make_world(chat_rate_limit_per_minute=1) as world:
        first, second = await sign_in(world), await sign_in(world)
        assert (await send(world, first)).status_code == 200
        assert (await send(world, first)).status_code == 429
        assert (await send(world, second)).status_code == 200


async def test_a_rate_limiter_outage_fails_closed_with_503(world: World) -> None:
    account = await sign_in(world)
    world.limiter.unavailable = True
    response = await send(world, account)
    assert (response.status_code, response.json()) == (503, {"error": "chat_unavailable"})
    nothing_happened(world)


async def test_an_idempotency_store_outage_fails_closed_with_503(world: World) -> None:
    account = await sign_in(world)
    world.store.unavailable = True
    response = await send(world, account)
    assert (response.status_code, response.json()) == (503, {"error": "chat_unavailable"})
    assert world.chat.messages == [] and stream_calls(world) == []


async def test_a_database_outage_answers_503_and_frees_the_key() -> None:
    async with make_world() as world:
        account = await sign_in(world)
        key = new_key()
        world.chat.unavailable = True
        failed = await send(world, account, key=key)
        assert (failed.status_code, failed.json()) == (503, {"error": "chat_unavailable"})
        assert PROVIDER_MARKER not in failed.text
        assert world.store.records == {} and stream_calls(world) == []
        world.chat.unavailable = False
        assert (await send(world, account, key=key)).status_code == 200  # the same key works


async def test_an_unknown_session_is_a_404_before_the_key_is_claimed(world: World) -> None:
    account = await sign_in(world)
    response = await send(world, account, session_id=uuid4())
    assert (response.status_code, response.json()) == (404, {"error": "session_not_found"})
    nothing_happened(world)


async def test_another_users_session_looks_exactly_like_a_missing_one(world: World) -> None:
    owner, intruder = await sign_in(world), await sign_in(world)
    session_id = UUID(events(await send(world, owner, "secret plans"))[0].data["session_id"])
    calls = len(stream_calls(world))
    rows = len(world.chat.messages)
    attempt = await send(world, intruder, "hello?", session_id=session_id)
    missing = await send(world, intruder, "hello?", session_id=uuid4())
    assert (attempt.status_code, attempt.json()) == (missing.status_code, missing.json())
    assert attempt.json() == {"error": "session_not_found"}
    assert len(stream_calls(world)) == calls and len(world.chat.messages) == rows


# ---------------------------------------------------------------------------- happy path


async def test_a_message_without_a_session_creates_one_and_streams_the_reply(world: World) -> None:
    account = await sign_in(world)
    response = await send(world, account, "  Summarize\n the   open   risks.  ")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    frames = events(response)
    assert names(frames) == ["message.accepted", *["delta"] * 4, "done"]
    accepted = frames[0].data
    assert set(accepted) == {"session_id", "user_message_id", "assistant_message_id", "replayed"}
    assert accepted["replayed"] is False
    assert reply_text(frames) == REPLY
    done = frames[-1].data
    assert set(done) == {"assistant_message_id", "finish_reason", "usage"}
    assert done["assistant_message_id"] == accepted["assistant_message_id"]
    assert done["finish_reason"] == "stop"
    usage = done["usage"]
    assert set(usage) == {
        "prompt_tokens",
        "candidates_tokens",
        "total_tokens",
        "cost_usd",
        "is_estimate",
    }
    assert (usage["prompt_tokens"], usage["candidates_tokens"], usage["total_tokens"]) == (
        100,
        50,
        150,
    )
    assert usage["is_estimate"] is False
    assert isinstance(usage["cost_usd"], str) and usage["cost_usd"] not in ("0", "0.0")
    assert "e" not in usage["cost_usd"].lower() and float(usage["cost_usd"]) > 0

    session = world.chat.sessions[UUID(accepted["session_id"])]
    assert session.title == "Summarize the open risks."
    assert (session.workspace_id, session.user_id) == (account.workspace_id, account.user_id)
    user, assistant = world.chat.rows("user")[0], world.chat.rows("orchestrator")[0]
    assert (str(user.id), user.content) == (
        accepted["user_message_id"],
        "  Summarize\n the   open   risks.  ",
    )
    assert str(assistant.id) == accepted["assistant_message_id"]
    assert assistant.content == REPLY
    assert set(assistant.metadata) == {
        "model_id",
        "alias",
        "finish_reason",
        "usage",
        "latency_ms",
        "status",
    }
    assert assistant.metadata["model_id"] == "gemini-3.8-flash"
    assert assistant.metadata["alias"] == "orchestrator"
    assert assistant.metadata["status"] == "completed"
    assert assistant.metadata["usage"] == usage
    record = world.store.only()
    assert (record.state, record.finish_reason, record.error_code) == ("completed", "stop", None)
    assert record.session_id == session.id
    assert world.fake.aio.models.open_streams == 0


async def test_the_session_title_is_the_first_60_characters_with_whitespace_collapsed(
    world: World,
) -> None:
    account = await sign_in(world)
    text = "alpha\n\n\tbeta " + "g" * 100
    frames = events(await send(world, account, text))
    session = world.chat.sessions[UUID(frames[0].data["session_id"])]
    assert session.title == ("alpha beta " + "g" * 100)[:60]
    assert len(session.title or "") == 60


async def test_the_gateway_is_called_with_the_orchestrator_alias_the_workspace_and_no_task(
    world: World,
) -> None:
    account = await sign_in(world)
    with patch.object(world.gateway, "generate_stream", wraps=world.gateway.generate_stream) as spy:
        events(await send(world, account))
    assert spy.call_count == 1
    assert spy.call_args.args[0] == "orchestrator"
    assert spy.call_args.kwargs["workspace_id"] == account.workspace_id
    assert "task_id" not in spy.call_args.kwargs
    assert spy.call_args.kwargs["system_instruction"]


async def test_the_history_reaches_the_model_on_the_second_message(world: World) -> None:
    account = await sign_in(world)
    first = events(await send(world, account, "My name is Priya."))
    session_id = first[0].data["session_id"]
    second = events(await send(world, account, "What is my name?", session_id=session_id))
    assert second[0].data["session_id"] == session_id
    assert len(world.chat.sessions) == 1  # no second session
    contents = stream_calls(world)[1]["contents"]
    assert [item["role"] for item in contents] == ["user", "model", "user"]
    assert [item["parts"][0]["text"] for item in contents] == [
        "My name is Priya.",
        REPLY,
        "What is my name?",
    ]
    assert [m.role for m in world.chat.messages if m.session_id == UUID(session_id)] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


async def test_the_new_message_is_not_duplicated_in_its_own_history(world: World) -> None:
    account = await sign_in(world)
    frames = events(await send(world, account, "only once"))
    contents = stream_calls(world)[0]["contents"]
    assert [item["parts"][0]["text"] for item in contents] == ["only once"]
    assert frames[0].data["replayed"] is False


async def test_a_context_budget_trims_the_oldest_turns_but_deletes_nothing() -> None:
    async with make_world(chat_context_token_budget=1_000, chat_max_message_chars=2_000) as world:
        account = await sign_in(world)
        session_id = events(await send(world, account, "first " * 150))[0].data["session_id"]
        for index in range(3):
            events(await send(world, account, f"turn {index} " * 150, session_id=session_id))
        sent = [item["parts"][0]["text"] for item in stream_calls(world)[-1]["contents"]]
        assert sent[-1].startswith("turn 2")  # the newest message is always sent
        assert not any(text.startswith("first") for text in sent)  # the oldest turn was dropped
        assert len(world.chat.rows("user")) == 4  # but still stored


# -------------------------------------------------------------------- SSE well-formedness


async def test_frames_are_well_formed_even_when_the_reply_contains_line_breaks(
    world: World,
) -> None:
    account = await sign_in(world)
    world.models.script = [
        chunk("line one\nline two\r\n"),
        chunk("sep\u2028para\u2029next\u0085x "),
        chunk('"quote" \\ back', finish=types.FinishReason.STOP, tokens=(5, 6)),
    ]
    response = await send(world, account)
    assert response.status_code == 200
    body = response.text
    for raw in ("\r", "\u2028", "\u2029", "\u0085"):
        assert raw not in body
    frames = parse_sse(body)
    assert len(body.splitlines()) == 4 * len(frames)  # id, event, data and a blank line each
    assert (
        reply_text(frames) == 'line one\nline two\r\nsep\u2028para\u2029next\u0085x "quote" \\ back'
    )
    assert all(
        line.split(": ", 1)[0] in ("id", "event", "data") for line in body.split("\n") if line
    )


async def test_empty_chunks_are_not_sent_as_deltas(world: World) -> None:
    account = await sign_in(world)
    world.models.script = [chunk(""), chunk("a"), chunk(""), chunk("b", tokens=(1, 2))]
    frames = events(await send(world, account))
    assert names(frames) == ["message.accepted", "delta", "delta", "done"]
    assert reply_text(frames) == "ab"


async def test_the_provider_finish_reason_is_mapped_to_stop_or_max_tokens(world: World) -> None:
    account = await sign_in(world)
    cases = [
        (types.FinishReason.STOP, "stop"),
        (types.FinishReason.MAX_TOKENS, "max_tokens"),
        (types.FinishReason.OTHER, "stop"),
        (None, "stop"),
    ]
    for provider, expected in cases:
        world.models.script = [chunk("a"), chunk("b", finish=provider, tokens=(1, 1))]
        done = events(await send(world, account))[-1]
        assert done.event == "done" and done.data["finish_reason"] == expected
        assert world.chat.rows("orchestrator")[-1].metadata["finish_reason"] == expected


async def test_an_unknown_cost_is_null_and_never_zero() -> None:
    async with make_world() as world:
        registry = build_default_registry({"orchestrator": "gemini-custom-test"})
        world.gateway = make_gateway(world.fake, registry=registry)
        world.registry = registry
        account = await sign_in(world)
        done = events(await send(world, account))[-1]
        assert done.data["usage"]["cost_usd"] is None
        assert done.data["usage"]["is_estimate"] is False
        assert '"cost_usd":null' in (await send(world, account)).text


async def test_missing_provider_usage_is_estimated_and_flagged(world: World) -> None:
    account = await sign_in(world)
    world.models.script = [chunk("some words here", finish=types.FinishReason.STOP)]  # no usage
    done = events(await send(world, account))[-1]
    usage = done.data["usage"]
    assert usage["is_estimate"] is True
    assert usage["prompt_tokens"] > 0 and usage["candidates_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["candidates_tokens"]
    assert usage["cost_usd"] not in (None, "0")


async def test_a_long_reply_renews_the_in_progress_claim(world: World) -> None:
    account = await sign_in(world)
    world.ticker.step = 40.0  # every read of the monotonic clock is 40 seconds later
    events(await send(world, account))
    states = [state for state, _ in world.store.saves]
    assert states.count("in_progress") >= 1 and states[-1] == "completed"
    assert world.chat.rows("orchestrator")[0].metadata["latency_ms"] > 0


# --------------------------------------------------------------- idempotency and replay


async def test_the_same_key_and_body_replays_the_stored_reply_without_calling_the_model(
    world: World,
) -> None:
    account = await sign_in(world)
    key = new_key()
    first = events(await send(world, account, "Tell me a story", key=key))
    calls, rows = len(stream_calls(world)), len(world.chat.messages)

    again = events(await send(world, account, "Tell me a story", key=key))

    assert names(again) == ["message.accepted", "delta", "done"]
    assert again[0].data == {**first[0].data, "replayed": True}
    assert again[1].data == {"text": REPLY}  # the whole reply in one delta
    assert again[2].data == first[-1].data  # same usage, same finish reason
    assert len(stream_calls(world)) == calls  # no second model call
    assert len(world.chat.messages) == rows  # no new rows
    assert len(world.chat.sessions) == 1


async def test_a_retry_after_a_new_session_was_created_replays_into_that_session(
    world: World,
) -> None:
    account = await sign_in(world)
    key = new_key()
    first = events(await send(world, account, "hello there", key=key))
    again = events(await send(world, account, "hello there", key=key))  # still no session_id
    assert again[0].data["session_id"] == first[0].data["session_id"]
    assert len(world.chat.sessions) == 1 and len(stream_calls(world)) == 1


async def test_the_same_key_with_a_different_body_is_a_422_and_does_nothing(world: World) -> None:
    account = await sign_in(world)
    key = new_key()
    first = events(await send(world, account, "original", key=key))
    session_id = first[0].data["session_id"]
    calls, rows = len(stream_calls(world)), len(world.chat.messages)

    different_text = await send(world, account, "changed", key=key)
    different_session = await send(world, account, "original", key=key, session_id=session_id)

    for response in (different_text, different_session):
        assert (response.status_code, response.json()) == (422, {"error": "idempotency_key_reused"})
    assert len(stream_calls(world)) == calls and len(world.chat.messages) == rows


async def test_a_key_is_private_to_its_user(world: World) -> None:
    first, second = await sign_in(world), await sign_in(world)
    key = new_key()
    events(await send(world, first, "same words", key=key))
    other = events(await send(world, second, "same words", key=key))
    assert other[0].data["replayed"] is False  # not a replay: another user's key space
    assert len(stream_calls(world)) == 2


async def test_a_request_that_is_still_running_is_refused_with_409(world: World) -> None:
    account = await sign_in(world)
    key = new_key()
    scope = IdempotencyScope(account.workspace_id, account.user_id, key)
    secret = world.settings.jwt_signing_key.get_secret_value().encode()
    claim = IdempotencyRecord(
        "in_progress", request_hash(secret, scope, None, "busy"), None, uuid4(), uuid4()
    )
    assert await world.store.claim(scope, claim) is None

    response = await send(world, account, "busy", key=key)

    assert (response.status_code, response.json()) == (409, {"error": "request_in_progress"})
    assert stream_calls(world) == [] and world.chat.messages == []


async def test_a_second_request_while_the_first_is_streaming_gets_409() -> None:
    async with make_world() as world:
        account = await sign_in(world)
        gate = asyncio.Event()
        world.models.script = [chunk("first "), gate, chunk("last", tokens=(3, 4))]
        key = new_key()
        first = asyncio.create_task(send(world, account, "slow one", key=key))
        await wait_until(lambda: world.models.open_streams > 0)  # the first one waits on the model
        busy = await send(world, account, "slow one", key=key)
        assert (busy.status_code, busy.json()) == (409, {"error": "request_in_progress"})
        gate.set()
        frames = events(await first)
        assert names(frames)[-1] == "done"
        assert len(stream_calls(world)) == 1


# -------------------------------------------------------------------------------- failures


async def test_a_stream_cut_after_the_first_chunk_stores_the_partial_reply_as_interrupted(
    world: World,
) -> None:
    account = await sign_in(world)
    key = new_key()
    world.models.script = [chunk("Fake "), chunk("model "), ConnectionResetError(PROVIDER_MARKER)]

    response = await send(world, account, "go on", key=key)

    frames = events(response)
    assert names(frames) == ["message.accepted", "delta", "delta", "error"]
    assert frames[-1].data == {"code": "stream_interrupted", "retryable": True}
    assert PROVIDER_MARKER not in response.text
    assistant = world.chat.rows("orchestrator")[0]
    assert assistant.content == "Fake model "
    assert assistant.metadata["finish_reason"] == "interrupted"
    assert assistant.metadata["status"] == "interrupted"
    assert assistant.metadata["usage"]["is_estimate"] is True
    assert str(assistant.id) == frames[0].data["assistant_message_id"]
    record = world.store.only()
    assert (record.state, record.finish_reason) == ("interrupted", "interrupted")
    assert world.fake.aio.models.open_streams == 0

    # Retrying with the same key replays the partial reply; nothing is billed again.
    calls = len(stream_calls(world))
    again = events(await send(world, account, "go on", key=key))
    assert names(again) == ["message.accepted", "delta", "done"]
    assert again[0].data["replayed"] is True
    assert again[1].data == {"text": "Fake model "}
    assert again[2].data["finish_reason"] == "interrupted"
    assert again[2].data["usage"]["is_estimate"] is True
    assert len(stream_calls(world)) == calls and len(world.chat.rows("orchestrator")) == 1


async def test_a_blocked_prompt_ends_with_content_blocked_and_stores_no_reply(world: World) -> None:
    account = await sign_in(world)
    blocked = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        )
    )
    world.models.script = [blocked]
    key = new_key()

    response = await send(world, account, "something", key=key)

    frames = events(response)
    assert names(frames) == ["message.accepted", "error"]
    assert frames[-1].data == {"code": "content_blocked", "retryable": False}
    assert world.chat.rows("orchestrator") == [] and len(world.chat.rows("user")) == 1
    record = world.store.only()
    assert (record.state, record.error_code) == ("failed", "content_blocked")
    assert world.fake.aio.models.open_streams == 0

    # The failure is what a retry with the same key gets back: no new model call.
    calls = len(stream_calls(world))
    again = events(await send(world, account, "something", key=key))
    assert names(again) == ["message.accepted", "error"]
    assert again[0].data["replayed"] is True
    assert again[1].data == {"code": "content_blocked", "retryable": False}
    assert len(stream_calls(world)) == calls


async def test_every_model_failing_before_a_chunk_ends_with_model_unavailable(world: World) -> None:
    account = await sign_in(world)
    world.fake.aio.models.enqueue_errors("gemini-3.8-flash", [ServerError(503, {})])
    world.fake.aio.models.enqueue_errors(
        "gemini-3.5-flash-lite", [ConnectionResetError(PROVIDER_MARKER)]
    )

    response = await send(world, account, "anything")

    frames = events(response)
    assert names(frames) == ["message.accepted", "error"]
    assert frames[-1].data == {"code": "model_unavailable", "retryable": True}
    assert PROVIDER_MARKER not in response.text
    assert world.chat.rows("orchestrator") == []
    record = world.store.only()
    assert (record.state, record.error_code) == ("failed", "model_unavailable")


async def test_a_provider_rejection_is_reported_as_model_unavailable_without_its_text(
    world: World,
) -> None:
    account = await sign_in(world)
    world.fake.aio.models.enqueue_errors(
        "gemini-3.8-flash", [ClientError(401, {"error": PROVIDER_MARKER})]
    )
    response = await send(world, account)
    frames = events(response)
    assert frames[-1].data == {"code": "model_unavailable", "retryable": True}
    assert PROVIDER_MARKER not in response.text


async def test_an_unexpected_error_is_reported_as_internal_error_without_detail(
    world: World,
) -> None:
    account = await sign_in(world)
    world.fake.aio.models.enqueue_errors("gemini-3.8-flash", [RuntimeError(PROVIDER_MARKER)])
    response = await send(world, account)
    frames = events(response)
    assert names(frames) == ["message.accepted", "error"]
    assert frames[-1].data == {"code": "internal_error", "retryable": False}
    assert PROVIDER_MARKER not in response.text
    assert world.store.only().error_code == "internal_error"


async def test_a_reply_that_cannot_be_stored_ends_with_internal_error_and_never_calls_again(
    world: World,
) -> None:
    account = await sign_in(world)
    world.chat.fail_assistant_insert = True
    key = new_key()
    response = await send(world, account, "store me", key=key)
    frames = events(response)
    assert names(frames)[-1] == "error" and "done" not in names(frames)
    assert frames[-1].data == {"code": "internal_error", "retryable": False}
    assert PROVIDER_MARKER not in response.text
    record = world.store.only()
    assert (record.state, record.error_code) == ("failed", "internal_error")

    world.chat.fail_assistant_insert = False
    calls = len(stream_calls(world))
    again = events(await send(world, account, "store me", key=key))
    assert names(again) == ["message.accepted", "error"] and len(stream_calls(world)) == calls


async def test_when_redis_fails_after_the_reply_the_reply_is_still_delivered(world: World) -> None:
    account = await sign_in(world)
    original = world.store.save

    async def failing_save(scope: IdempotencyScope, record: IdempotencyRecord) -> None:
        if record.state != "in_progress":
            raise IdempotencyUnavailable
        await original(scope, record)

    with (
        patch.object(world.store, "save", failing_save),
        patch("app.services.chat_service.RECORD_RETRY_DELAY_SECONDS", 0),  # keep the test fast
    ):
        frames = events(await send(world, account))
    assert names(frames)[-1] == "done"
    assert len(world.chat.rows("orchestrator")) == 1


# ----------------------------------------------------------------- turn lifecycle (direct)


async def wait_until(condition: Callable[[], bool], *, attempts: int = 10_000) -> None:
    for _ in range(attempts):
        if condition():
            return
        await asyncio.sleep(0)
    pytest.fail("the condition was never reached")


async def begin(world: World, account: Account, content: str = "hello") -> Any:
    service = ChatService(
        principal=Principal(account.user_id, account.workspace_id, "admin", uuid4()),
        database=world.chat.database(),
        gateway=world.gateway,
        registry=world.registry,
        settings=world.settings,
        limiter=world.limiter,
        idempotency=world.store,
        clock=cast(Clock, world.clock),
        monotonic=world.ticker,
        repositories=world.chat.repository,
    )
    return await service.begin_message(
        idempotency_keys=[new_key()], body=json.dumps({"content": content}).encode()
    )


async def test_closing_a_turn_between_two_events_stores_the_partial_reply(world: World) -> None:
    account = await sign_in(world)
    world.models.script = [chunk("one "), chunk("two "), chunk("three", tokens=(2, 3))]
    turn = await begin(world, account)
    stream = turn.events()
    seen = [await stream.__anext__() for _ in range(3)]  # accepted, "one ", "two "
    assert len(seen) == 3

    await turn.close()  # the generator is suspended after "two "

    assistant = world.chat.rows("orchestrator")[0]
    assert (assistant.content, assistant.metadata["finish_reason"]) == ("one two ", "interrupted")
    assert world.store.only().state == "interrupted"
    assert world.fake.aio.models.open_streams == 0
    await turn.close()  # idempotent
    assert len(world.chat.rows("orchestrator")) == 1


async def test_cancelling_the_consumer_while_the_model_is_silent_stores_what_arrived(
    world: World,
) -> None:
    account = await sign_in(world)
    gate = asyncio.Event()  # never set: the model goes quiet after two words
    world.models.script = [chunk("alpha "), chunk("beta "), gate]
    turn = await begin(world, account)
    received: list[str] = []

    async def consume() -> None:
        async for frame in turn.events():
            received.append(frame)

    task = asyncio.create_task(consume())
    await wait_until(lambda: len(received) >= 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await turn.close()

    assistant = world.chat.rows("orchestrator")[0]
    assert (assistant.content, assistant.metadata["finish_reason"]) == (
        "alpha beta ",
        "interrupted",
    )
    assert world.store.only().state == "interrupted"
    assert world.fake.aio.models.open_streams == 0


async def test_a_turn_that_was_never_iterated_ends_its_request_as_failed(world: World) -> None:
    account = await sign_in(world)
    turn = await begin(world, account)
    assert len(world.chat.rows("user")) == 1 and stream_calls(world) == []

    await turn.close()

    record = world.store.only()
    assert (record.state, record.error_code) == ("failed", "stream_interrupted")
    assert stream_calls(world) == [] and world.chat.rows("orchestrator") == []
    assert [frame async for frame in turn.events()] == []  # nothing left to send


# ---------------------------------------------------------- real disconnects (hand-driven ASGI)


@dataclass
class Exchange:
    status: int
    body: str


async def converse(
    world: World,
    account: Account,
    *,
    content: str,
    key: str,
    after_body: Callable[[int, asyncio.Event], Awaitable[None]],
) -> Exchange:
    """POST one message straight into the ASGI app; ``after_body`` runs after each body chunk the
    app sends and may end the conversation by setting the event (a client that went away)."""
    payload = json.dumps({"content": content}).encode()
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},  # as uvicorn's HTTP servers report
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

    async def send_message(message: Message) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            await after_body(len(chunks), disconnected)

    await asyncio.wait_for(
        world.app(scope, cast(Receive, receive), cast(Send, send_message)), timeout=10
    )
    return Exchange(status, b"".join(chunks).decode())


async def test_a_client_that_disconnects_while_the_model_is_silent_keeps_the_partial_reply(
    world: World,
) -> None:
    account = await sign_in(world)
    gate = asyncio.Event()  # never set
    world.models.script = [chunk("alpha "), chunk("beta "), gate]
    key = new_key()

    async def leave_after_three_events(count: int, disconnected: asyncio.Event) -> None:
        if count == 3:  # message.accepted and two deltas have been delivered
            disconnected.set()

    exchange = await converse(
        world, account, content="stream then leave", key=key, after_body=leave_after_three_events
    )

    assert exchange.status == 200
    assert names(parse_sse(exchange.body)) == ["message.accepted", "delta", "delta"]
    assistant = world.chat.rows("orchestrator")[0]
    assert (assistant.content, assistant.metadata["finish_reason"]) == (
        "alpha beta ",
        "interrupted",
    )
    assert assistant.metadata["usage"]["is_estimate"] is True
    record = world.store.only()
    assert (record.state, record.finish_reason) == ("interrupted", "interrupted")
    assert world.fake.aio.models.open_streams == 0  # the provider stream was closed at once

    calls = len(stream_calls(world))
    replay = events(await send(world, account, "stream then leave", key=key))
    assert (
        replay[1].data == {"text": "alpha beta "}
        and replay[-1].data["finish_reason"] == "interrupted"
    )
    assert len(stream_calls(world)) == calls


async def test_a_client_that_disconnects_between_two_events_keeps_the_partial_reply(
    world: World,
) -> None:
    account = await sign_in(world)
    world.models.script = [chunk(f"w{index} ") for index in range(40)]
    key = new_key()

    async def stall_then_leave(count: int, disconnected: asyncio.Event) -> None:
        if count == 3:
            # A slow client: it stops reading while the server has the next event ready, then
            # the connection drops. The generator is suspended between two events.
            disconnected.set()
            await asyncio.wait_for(world.store.terminal.wait(), timeout=5)

    exchange = await converse(
        world, account, content="slow reader", key=key, after_body=stall_then_leave
    )

    assert exchange.status == 200
    assistant = world.chat.rows("orchestrator")[0]
    assert assistant.metadata["finish_reason"] == "interrupted"
    assert assistant.content.startswith("w0 w1 ") and len(assistant.content.split()) < 40
    assert world.store.only().state == "interrupted"
    assert world.fake.aio.models.open_streams == 0


CRLF = b"\r\n"


class _ReadyServer(uvicorn.Server):
    """A real uvicorn server on an ephemeral port that says when it is listening."""

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.ready = asyncio.Event()

    async def startup(self, sockets: list[Any] | None = None) -> None:
        await super().startup(sockets)
        self.ready.set()

    @property
    def port(self) -> int:
        return int(self.servers[0].sockets[0].getsockname()[1])


async def test_a_real_connection_closed_mid_reply_keeps_the_partial_reply() -> None:
    """The same promise as the hand-driven tests, through uvicorn and a real TCP socket."""
    async with make_world() as world:
        account = await sign_in(world)
        gate = asyncio.Event()  # never set: the model goes quiet after two words
        world.models.script = [chunk("alpha "), chunk("beta "), gate]
        server = _ReadyServer(
            uvicorn.Config(
                world.app,
                host="127.0.0.1",
                port=0,
                http="h11",
                lifespan="off",
                log_level="warning",
            )
        )
        serving = asyncio.create_task(server.serve())
        try:
            await asyncio.wait_for(server.ready.wait(), timeout=10)
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            payload = json.dumps({"content": "over a socket"}).encode()
            request = b"POST /api/v1/chat/message HTTP/1.1" + CRLF
            request += b"Host: test" + CRLF
            request += f"Authorization: Bearer {account.token}".encode() + CRLF
            request += f"Idempotency-Key: {new_key()}".encode() + CRLF
            request += b"Content-Type: application/json" + CRLF
            request += f"Content-Length: {len(payload)}".encode() + CRLF + CRLF + payload
            writer.write(request)
            await writer.drain()
            received = b""
            while b"beta" not in received:
                data = await asyncio.wait_for(reader.read(4096), timeout=10)
                assert data, "the server closed the connection early"
                received += data
            assert b"200 OK" in received.split(CRLF, 1)[0]
            writer.close()  # the client leaves while the model is silent
            await asyncio.wait_for(world.store.terminal.wait(), timeout=10)
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=10)

        assistant = world.chat.rows("orchestrator")[0]
        assert (assistant.content, assistant.metadata["finish_reason"]) == (
            "alpha beta ",
            "interrupted",
        )
        assert world.store.only().state == "interrupted"
        assert world.fake.aio.models.open_streams == 0  # the provider stream was closed


# --------------------------------------------------------------------------------- reads


async def test_sessions_are_listed_newest_first_with_a_cursor(world: World) -> None:
    account = await sign_in(world)
    other = await sign_in(world)
    titles = ["first", "second", "third"]
    for title in titles:
        events(await send(world, account, title))
    events(await send(world, other, "not yours"))

    page = await world.client.get(f"{CHAT}/sessions", params={"limit": 2}, headers=account.headers)
    assert page.status_code == 200
    body = page.json()
    assert [item["title"] for item in body["items"]] == ["third", "second"]
    assert set(body["items"][0]) == {"id", "title", "channel", "status", "created_at"}
    assert body["items"][0]["channel"] == "web" and body["items"][0]["status"] == "active"
    assert body["items"][0]["created_at"].endswith("Z")
    assert body["next_cursor"] is not None
    rest = await world.client.get(
        f"{CHAT}/sessions",
        params={"limit": 2, "cursor": body["next_cursor"]},
        headers=account.headers,
    )
    assert [item["title"] for item in rest.json()["items"]] == ["first"]
    assert rest.json()["next_cursor"] is None
    assert "next_cursor" in rest.json()  # present (null) on the last page


async def test_messages_are_listed_oldest_first_with_the_documented_fields(world: World) -> None:
    account = await sign_in(world)
    frames = events(await send(world, account, "question"))
    session_id = frames[0].data["session_id"]

    page = await world.client.get(f"{CHAT}/sessions/{session_id}/messages", headers=account.headers)

    assert page.status_code == 200
    items = page.json()["items"]
    assert [item["role"] for item in items] == ["user", "assistant"]
    assert set(items[0]) == {"id", "role", "content", "created_at", "finish_reason", "model_id"}
    assert items[0]["finish_reason"] is None and items[0]["model_id"] is None
    assert items[1]["content"] == REPLY
    assert (items[1]["finish_reason"], items[1]["model_id"]) == ("stop", "gemini-3.8-flash")
    assert page.json()["next_cursor"] is None

    first = await world.client.get(
        f"{CHAT}/sessions/{session_id}/messages", params={"limit": 1}, headers=account.headers
    )
    assert len(first.json()["items"]) == 1 and first.json()["next_cursor"] is not None


async def test_messages_of_an_unknown_or_foreign_session_are_a_404(world: World) -> None:
    owner, other = await sign_in(world), await sign_in(world)
    session_id = events(await send(world, owner, "private"))[0].data["session_id"]
    for caller, target in ((other, session_id), (owner, str(uuid4()))):
        response = await world.client.get(
            f"{CHAT}/sessions/{target}/messages", headers=caller.headers
        )
        assert (response.status_code, response.json()) == (404, {"error": "session_not_found"})


@pytest.mark.parametrize(
    ("path", "params", "fields"),
    [
        ("/sessions", {"limit": 0}, ["limit"]),
        ("/sessions", {"limit": 51}, ["limit"]),
        ("/sessions", {"limit": "abc"}, ["limit"]),
        ("/sessions", {"cursor": ""}, ["cursor"]),
        ("/sessions", {"cursor": "garbage"}, ["cursor"]),
        ("/sessions/{sid}/messages", {"limit": 101}, ["limit"]),
        ("/sessions/{sid}/messages", {"cursor": "garbage"}, ["cursor"]),
        ("/sessions/not-a-uuid/messages", {}, ["session_id"]),
    ],
)
async def test_bad_query_values_are_a_422_naming_the_field(
    world: World, path: str, params: dict[str, Any], fields: list[str]
) -> None:
    account = await sign_in(world)
    session_id = events(await send(world, account, "x"))[0].data["session_id"]
    response = await world.client.get(
        f"{CHAT}{path.format(sid=session_id)}", params=params, headers=account.headers
    )
    assert response.status_code == 422
    assert response.json() == {"error": "validation_error", "fields": fields}


# ---------------------------------------------------------------------------------- logging


async def test_nothing_the_user_or_the_model_wrote_ever_reaches_a_log(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = Capture(level=logging.DEBUG)
    maya = logging.getLogger("maya")
    maya.addHandler(capture)
    previous_level = maya.level
    maya.setLevel(logging.DEBUG)
    try:
        with caplog.at_level(logging.DEBUG):
            account = await sign_in(world)
            key = KEY_MARKER
            world.models.script = [chunk(REPLY_MARKER + " "), chunk("tail", tokens=(1, 1))]
            events(await send(world, account, CONTENT_MARKER, key=key))  # completed
            events(await send(world, account, CONTENT_MARKER, key=key))  # replay
            world.models.script = [chunk(REPLY_MARKER), ConnectionResetError(PROVIDER_MARKER)]
            events(await send(world, account, CONTENT_MARKER + "2"))  # interrupted
            world.models.script = []
            world.fake.aio.models.enqueue_errors(
                "gemini-3.8-flash", [RuntimeError(PROVIDER_MARKER)]
            )
            events(await send(world, account, CONTENT_MARKER + "3"))  # internal error
            world.chat.fail_assistant_insert = True
            events(await send(world, account, CONTENT_MARKER + "4"))  # not stored
    finally:
        maya.removeHandler(capture)
        maya.setLevel(previous_level)

    rendered = [record.getMessage() for record in records] + [
        r.getMessage() for r in caplog.records
    ]
    assert any("chat_" in line for line in rendered)  # the chat service did log something
    everything = "\n".join(rendered)
    for secret in (CONTENT_MARKER, REPLY_MARKER, PROVIDER_MARKER, KEY_MARKER):
        assert secret not in everything
    for record in records + list(caplog.records):
        if record.exc_info is not None:
            assert PROVIDER_MARKER not in str(record.exc_info[1])


# --------------------------------------------------------------------------- wiring points


async def test_the_default_dependencies_read_the_application_resources() -> None:
    gateway, registry, settings = object(), object(), make_settings()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resources=SimpleNamespace(model_gateway=gateway, registry=registry),
                settings=settings,
            )
        )
    )
    assert await get_model_gateway(cast(Any, request)) is gateway
    assert await get_model_registry(cast(Any, request)) is registry
    assert await get_chat_settings(cast(Any, request)) is settings


async def test_the_public_surface_includes_exactly_the_three_documented_chat_routes(
    world: World,
) -> None:
    schema = world.app.openapi()
    chat = {
        (path, method.upper())
        for path, operations in schema["paths"].items()
        if path.startswith(CHAT)
        for method in operations
    }
    assert chat == {
        (f"{CHAT}/message", "POST"),
        (f"{CHAT}/sessions", "GET"),
        (f"{CHAT}/sessions/{{session_id}}/messages", "GET"),
    }
    post = schema["paths"][f"{CHAT}/message"]["post"]
    assert post["parameters"][0]["name"] == "Idempotency-Key"
    assert "text/event-stream" in post["responses"]["200"]["content"]


# ------------------------------------------------------------------- idempotency records


SOME_HASH = "0123456789abcdef" * 4  # any valid digest; these tests do not compute one


def _record(**changes: Any) -> IdempotencyRecord:
    base: dict[str, Any] = {
        "state": "completed",
        "request_hash": SOME_HASH,
        "session_id": uuid4(),
        "user_message_id": uuid4(),
        "assistant_message_id": uuid4(),
        "finish_reason": "stop",
        "error_code": None,
    }
    base.update(changes)
    return IdempotencyRecord(**base)


@pytest.mark.parametrize(
    "record",
    [
        _record(),
        _record(state="in_progress", session_id=None, finish_reason=None),
        _record(state="interrupted", finish_reason="interrupted"),
        _record(state="failed", finish_reason=None, error_code="content_blocked"),
    ],
)
def test_an_idempotency_record_survives_a_round_trip(record: IdempotencyRecord) -> None:
    raw = record.to_json()
    assert IdempotencyRecord.from_json(raw) == record
    assert IdempotencyRecord.from_json(raw.encode()) == record
    assert set(json.loads(raw)) == {
        "v",
        "state",
        "hash",
        "session_id",
        "user_message_id",
        "assistant_message_id",
        "finish_reason",
        "error_code",
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(v=2),
        lambda d: d.update(state="done"),
        lambda d: d.update(hash="not-a-sha256"),
        lambda d: d.update(hash=SOME_HASH.upper()),
        lambda d: d.update(session_id="not-a-uuid"),
        lambda d: d.update(user_message_id=None),
        lambda d: d.update(error_code="a provider message"),
        lambda d: d.update(finish_reason=7),
        lambda d: d.pop("state"),
    ],
)
def test_a_record_that_this_module_did_not_write_is_rejected(
    mutate: Callable[[dict[str, Any]], object],
) -> None:
    body = json.loads(_record().to_json())
    mutate(body)
    with pytest.raises(ValueError):
        IdempotencyRecord.from_json(json.dumps(body))
    for garbage in ("", "null", "[]", "42", "{not json", '"text"'):
        with pytest.raises(ValueError):
            IdempotencyRecord.from_json(garbage)


def test_the_request_hash_covers_the_session_and_the_exact_text() -> None:
    secret = b"a fake secret that is long enough for the hash"
    scope = IdempotencyScope(uuid4(), uuid4(), "abcdefgh12345678")
    session = uuid4()
    digests = {
        request_hash(secret, scope, None, "hello"),
        request_hash(secret, scope, session, "hello"),
        request_hash(secret, scope, None, "hello "),
        request_hash(secret, scope, None, "Hello"),
        request_hash(secret, scope, uuid4(), "hello"),
    }
    assert len(digests) == 5
    assert request_hash(secret, scope, session, "hello") == request_hash(
        secret, scope, session, "hello"
    )
    assert len(request_hash(secret, scope, None, "x")) == 64
    assert "hello" not in request_hash(secret, scope, None, "hello")


def test_the_request_hash_is_keyed_and_bound_to_the_whole_scope() -> None:
    # A reader of the Redis record cannot test guesses of a short message without the secret, and
    # the same text from another user, workspace or key gives a different value.
    secret = b"a fake secret that is long enough for the hash"
    scope = IdempotencyScope(uuid4(), uuid4(), "abcdefgh12345678")
    base = request_hash(secret, scope, None, "hello")
    assert request_hash(b"another fake secret, also long enough", scope, None, "hello") != base
    assert request_hash(secret, replace(scope, user_id=uuid4()), None, "hello") != base
    assert request_hash(secret, replace(scope, workspace_id=uuid4()), None, "hello") != base
    assert request_hash(secret, replace(scope, key="zyxwvuts87654321"), None, "hello") != base
    # Length-prefixing: moving a character between two parts cannot give the same digest.
    assert request_hash(secret, replace(scope, key="abcdefgh1"), None, "2345678hello") != (
        request_hash(secret, replace(scope, key="abcdefgh12"), None, "345678hello")
    )


def test_a_key_is_scoped_by_workspace_and_user_in_redis() -> None:
    workspace, user = uuid4(), uuid4()
    scope = IdempotencyScope(workspace, user, "abcdefgh12345678")
    assert scope.redis_key == f"maya:chat:idem:{workspace}:{user}:abcdefgh12345678"
    assert scope.redis_key != IdempotencyScope(workspace, uuid4(), scope.key).redis_key
    assert scope.redis_key != IdempotencyScope(uuid4(), user, scope.key).redis_key


# ------------------------------------------------------------------ review follow-ups (Phase 04)


async def test_create_app_stores_the_settings_the_chat_routes_read() -> None:
    # Without this the routes fell back to Settings() built from the process environment alone,
    # which ignores an env file and fails outright when the process has no MAYA_* variables.
    configuration = make_settings(gemini_api_key=None, chat_rate_limit_per_minute=3)
    app = create_app(configuration)
    assert app.state.settings is configuration
    assert await get_chat_settings(cast(Any, SimpleNamespace(app=app))) is configuration


async def test_a_validation_error_never_echoes_a_field_name_the_client_chose(world: World) -> None:
    account = await sign_in(world)
    body = json.dumps({"content": "hi", "<img src=x onerror=alert(1)>": 1}).encode()
    response = await send(world, account, raw=body)
    assert (response.status_code, response.json()) == (
        422,
        {"error": "validation_error", "fields": ["body"]},
    )
    assert "onerror" not in response.text
    nothing_happened(world)


async def test_the_listing_routes_are_not_cacheable(world: World) -> None:
    account = await sign_in(world)
    session_id = events(await send(world, account))[0].data["session_id"]
    for path in (f"{CHAT}/sessions", f"{CHAT}/sessions/{session_id}/messages"):
        response = await world.client.get(path, headers=account.headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


async def test_last_event_id_is_accepted_and_ignored(world: World) -> None:
    account = await sign_in(world)
    key = new_key()
    events(await send(world, account, "resume me", key=key))
    calls = len(stream_calls(world))

    again = events(await send(world, account, "resume me", key=key, headers={"Last-Event-ID": "2"}))

    assert names(again) == ["message.accepted", "delta", "done"]  # the whole reply, from event 1
    assert [frame.id for frame in again] == [1, 2, 3]
    assert again[0].data["replayed"] is True
    assert len(stream_calls(world)) == calls


async def test_a_disconnect_after_chunks_that_carried_usage_marks_the_usage_as_an_estimate(
    world: World,
) -> None:
    account = await sign_in(world)
    gate = asyncio.Event()  # never set: the model goes quiet after two chunks that reported usage
    world.models.script = [chunk("a ", tokens=(10, 2)), chunk("b ", tokens=(10, 4)), gate]

    async def leave_after_three_events(count: int, disconnected: asyncio.Event) -> None:
        if count == 3:
            disconnected.set()

    exchange = await converse(
        world,
        account,
        content="usage then leave",
        key=new_key(),
        after_body=leave_after_three_events,
    )

    assert exchange.status == 200
    usage = world.chat.rows("orchestrator")[0].metadata["usage"]
    assert usage["is_estimate"] is True  # the reply was cut, so the provider's totals are partial
    assert (usage["prompt_tokens"], usage["candidates_tokens"]) == (10, 4)


async def test_a_client_that_leaves_before_any_text_arrives_stores_no_reply(world: World) -> None:
    account = await sign_in(world)
    gate = asyncio.Event()  # never set: the model has said nothing yet
    world.models.script = [gate]
    key = new_key()

    async def leave_after_accepted(count: int, disconnected: asyncio.Event) -> None:
        if count == 1:  # only message.accepted was delivered
            disconnected.set()

    exchange = await converse(
        world, account, content="leave at once", key=key, after_body=leave_after_accepted
    )

    assert exchange.status == 200
    assert world.chat.rows("orchestrator") == []  # no empty assistant message in the history
    record = world.store.only()
    assert (record.state, record.error_code) == ("failed", "stream_interrupted")
    assert world.fake.aio.models.open_streams == 0

    calls = len(stream_calls(world))
    again = events(await send(world, account, "leave at once", key=key))
    assert names(again) == ["message.accepted", "error"]
    assert again[-1].data == {"code": "stream_interrupted", "retryable": True}
    assert len(stream_calls(world)) == calls  # the same key never calls the model again


async def test_the_final_idempotency_write_is_retried_after_a_redis_hiccup(world: World) -> None:
    account = await sign_in(world)
    original = world.store.save
    failures = [IdempotencyUnavailable, IdempotencyUnavailable]  # the first two final writes fail

    async def flaky_save(scope: IdempotencyScope, record: IdempotencyRecord) -> None:
        if record.state != "in_progress" and failures:
            raise failures.pop()
        await original(scope, record)

    with (
        patch.object(world.store, "save", flaky_save),
        patch("app.services.chat_service.RECORD_RETRY_DELAY_SECONDS", 0),
    ):
        frames = events(await send(world, account))

    assert names(frames)[-1] == "done"
    assert world.store.only().state == "completed"  # not left "in progress" for a later re-run
