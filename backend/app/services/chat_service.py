"""The chat service: one text message in, a stream of typed events out (ADR-0008).

``ChatService`` is built per request for one authenticated caller. ``begin_message`` does everything
that can still be answered with a plain JSON status and returns a turn whose ``events()`` is the
Server-Sent Events body; the route only wraps it in a response. In this order:

1. validate the ``Idempotency-Key`` header and the body (before any state is touched);
2. refuse with ``model_unavailable`` when no Gemini client exists (nothing is stored);
3. apply the per-user rate limit;
4. check the session belongs to the caller;
5. claim the idempotency key (a repeat is answered from the stored result, never by the model);
6. store the user message, creating the session if none was given, in its own short transaction;
7. stream the reply through ``ModelGateway.generate_stream``.

After ``message.accepted`` is sent the response is already ``200``, so a failure can only end the
stream with an ``error`` event. Whatever happens, one terminal write records the outcome: the
assistant reply (completed or interrupted) in ``messages`` and the final state in the idempotency
record. That write runs as its own task behind ``asyncio.shield``, so a disconnect, a cancellation
or a closed generator cannot lose a reply the provider already generated and billed.

Neither the message text, the reply, the idempotency key nor any provider error text is ever
written to a log; failures are logged as a class name or a status code only.
"""

import asyncio
import itertools
import logging
import re
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass, field, replace
from typing import Any, Final, Protocol
from uuid import UUID, uuid4

from google.genai import errors as genai_errors
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.auth.errors import RateLimited
from app.auth.models import Principal
from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.core.ratelimit import Limit, RateLimiter, RateLimiterUnavailable
from app.db.repositories.chat import (
    MAX_RECENT_LIMIT,
    Channel,
    ChatMessageRecord,
    ChatRepository,
    ChatSessionRecord,
    Modality,
    RecordPage,
    Sender,
    SessionNotFound,
)
from app.gateway.cost_tracker import calculate_cost
from app.gateway.exceptions import (
    ContentBlockedError,
    GatewayConfigurationError,
    GatewayError,
    StreamInterruptedError,
)
from app.gateway.model_gateway import ModelGateway, estimate_tokens
from app.gateway.registry import ModelRegistry, ModelSpec
from app.gateway.resilience import describe_error
from app.gateway.schemas import GatewayStreamChunk, TokenUsage
from app.schemas.chat import (
    ChatMessageRequest,
    DeltaEvent,
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    FinishReason,
    MessageAcceptedEvent,
    MessageOut,
    Page,
    SessionOut,
    UsageOut,
    encode_sse,
    format_cost_usd,
)
from app.services.chat_idempotency import (
    KEY_PATTERN,
    IdempotencyRecord,
    IdempotencyScope,
    IdempotencyStore,
    IdempotencyUnavailable,
    State,
    request_hash,
)
from app.services.context_manager import (
    ContextManager,
    ContextTooLarge,
    ContextWindow,
    HistoryTurn,
)

logger = logging.getLogger("maya")

ORCHESTRATOR_ALIAS: Final = "orchestrator"
# The most stored messages read back as history. The token budget trims further; this only bounds
# the read, so a session with thousands of messages does not load them all for one turn.
HISTORY_MESSAGE_LIMIT: Final = MAX_RECENT_LIMIT
TITLE_CHARS: Final = 60
RATE_LIMIT_WINDOW_SECONDS: Final = 60
# A reply can outlive the fifteen-minute ``in_progress`` claim; it is renewed this often while
# chunks keep arriving.
CLAIM_REFRESH_SECONDS: Final = 60.0

# NUL cannot be stored and a lone surrogate cannot be encoded; the stored text and the streamed
# text must be the same text, so both are cleaned the same way as they arrive.
_UNSAFE_TEXT = re.compile(r"[\x00\ud800-\udfff]")
_RETRYABLE: Final[dict[ErrorCode, bool]] = {
    "content_blocked": False,
    "stream_interrupted": True,
    "model_unavailable": True,
    "internal_error": False,
}
_TASKS: Final[set["asyncio.Task[Any]"]] = set()  # strong references to in-flight terminal writes
_REQUEST_FIELDS: Final = frozenset(ChatMessageRequest.model_fields)
# The final idempotency write is retried so that one Redis hiccup does not leave a delivered reply
# marked as still running (which would let a retry call, and bill, the model again later).
RECORD_ATTEMPTS: Final = 3
RECORD_RETRY_DELAY_SECONDS: Final = 0.2


# --------------------------------------------------------------------------------------- errors


class ChatError(Exception):
    """A refusal that is answered with JSON before any event is sent. Carries no request data."""


class IdempotencyKeyRequired(ChatError):
    """``Idempotency-Key`` is missing, repeated or malformed (400)."""


class ChatValidationError(ChatError):
    """The body is invalid (422). ``fields`` names the offending fields, never their values."""

    def __init__(self, fields: Sequence[str]) -> None:
        super().__init__("Invalid chat request")
        self.fields = tuple(fields)


class RequestInProgress(ChatError):
    """The same key and body is still being processed (409)."""


class IdempotencyKeyReused(ChatError):
    """The key was used earlier with a different ``session_id`` or ``content`` (422)."""


class ModelUnavailable(ChatError):
    """No Gemini client is configured (503). Raised before anything is stored."""


class ChatUnavailable(ChatError):
    """Redis (idempotency, rate limit) or the database is unreachable (503). Chat fails closed."""


# ------------------------------------------------------------------------------ storage ports


class ChatStorage(Protocol):
    """The part of ``ChatRepository`` this service uses (tests may supply an in-memory one)."""

    async def create_session(
        self, title: str | None = None, channel: Channel = "web"
    ) -> ChatSessionRecord: ...

    async def get_session(self, session_id: UUID) -> ChatSessionRecord | None: ...

    async def list_sessions(
        self, limit: int = 20, cursor: str | None = None
    ) -> RecordPage[ChatSessionRecord]: ...

    async def add_message(
        self,
        session_id: UUID,
        sender: Sender,
        content: str,
        modality: Modality = "text",
        metadata: Mapping[str, Any] | None = None,
        message_id: UUID | None = None,
    ) -> ChatMessageRecord: ...

    async def get_message(self, message_id: UUID) -> ChatMessageRecord | None: ...

    async def list_messages(
        self, session_id: UUID, limit: int = 50, cursor: str | None = None
    ) -> RecordPage[ChatMessageRecord]: ...

    async def recent_messages(self, session_id: UUID, limit: int) -> list[ChatMessageRecord]: ...


ChatStorageFactory = Callable[[AsyncConnection, UUID, UUID], ChatStorage]


class TenantTransactions(Protocol):
    """``TenantDatabase`` (``app.auth.dependencies``): transactions bound to the caller's tenant."""

    def transaction(self) -> AbstractAsyncContextManager[AsyncConnection]: ...


class ChatTurn(Protocol):
    """What the route needs from a prepared message: the event stream, and a way to end it."""

    def events(self) -> AsyncIterator[str]:
        """The SSE body: complete frames, one string each. Can be iterated once."""
        ...

    async def close(self) -> None:
        """End the turn. Call it when the response is over, however it ended: it finishes any
        terminal write that is still pending and closes the provider stream. Idempotent."""
        ...


# --------------------------------------------------------------------------- request parsing


def parse_idempotency_key(values: Sequence[str]) -> str:
    """The one ``Idempotency-Key`` header value, or ``IdempotencyKeyRequired``."""
    if len(values) != 1 or KEY_PATTERN.fullmatch(values[0]) is None:
        raise IdempotencyKeyRequired
    return values[0]


def parse_message_request(body: bytes, max_chars: int) -> ChatMessageRequest:
    """The validated body, or ``ChatValidationError`` naming the fields (never their values).

    Only the names of fields this API declares are reported: a client-chosen key (an unknown field)
    is reported as ``body`` instead of being echoed back.
    """
    try:
        command = ChatMessageRequest.model_validate_json(body)
    except ValidationError as error:
        fields: list[str] = []
        for problem in error.errors():
            first = str(problem["loc"][0]) if problem["loc"] else "body"
            name = first if first in _REQUEST_FIELDS else "body"
            if name not in fields:
                fields.append(name)
        raise ChatValidationError(fields) from None
    if len(command.content) > max_chars or not command.content.strip():
        raise ChatValidationError(["content"])
    return command


def make_title(content: str) -> str:
    """The first 60 characters of the message with every run of whitespace collapsed."""
    return " ".join(content.split())[:TITLE_CHARS]


# ------------------------------------------------------------------------------ turn plumbing


@dataclass(frozen=True, slots=True)
class _Wiring:
    """What a turn needs from the service. Passed explicitly so a turn outlives its request."""

    principal: Principal
    database: TenantTransactions
    repositories: ChatStorageFactory
    gateway: ModelGateway
    registry: ModelRegistry
    idempotency: IdempotencyStore
    monotonic: Callable[[], float]


@dataclass(frozen=True, slots=True)
class _Plan:
    scope: IdempotencyScope
    claim: IdempotencyRecord  # the in_progress record; carries the three ids and the hash
    session_id: UUID
    window: ContextWindow

    @property
    def user_message_id(self) -> UUID:
        return self.claim.user_message_id

    @property
    def assistant_message_id(self) -> UUID:
        return self.claim.assistant_message_id


@dataclass(slots=True)
class _Progress:
    parts: list[str] = field(default_factory=list)
    chunks: int = 0
    usage: TokenUsage | None = None  # the latest provider usage seen on a chunk
    partial_usage: TokenUsage | None = None  # the gateway's estimate for an interrupted stream
    provider_finish: str | None = None
    model_id: str | None = None
    alias: str | None = None


@dataclass(frozen=True, slots=True)
class _Completed:
    pass


@dataclass(frozen=True, slots=True)
class _Interrupted:
    """Reply cut short. ``error`` is the code to send, or ``None`` when nobody is listening."""

    error: ErrorCode | None


@dataclass(frozen=True, slots=True)
class _Failed:
    """No reply exists (nothing was received), so no assistant row is stored."""

    code: ErrorCode


_Outcome = _Completed | _Interrupted | _Failed


@dataclass(frozen=True, slots=True)
class _Summary:
    text: str
    finish_reason: FinishReason
    usage: UsageOut


@dataclass(frozen=True, slots=True)
class _Finalized:
    stored: bool


def _clean(text: str) -> str:
    return _UNSAFE_TEXT.sub("\N{REPLACEMENT CHARACTER}", text)


def _usage_out(usage: TokenUsage) -> UsageOut:
    return UsageOut(
        prompt_tokens=usage.prompt_tokens,
        candidates_tokens=usage.candidates_tokens,
        total_tokens=usage.total_token_count
        or usage.prompt_tokens + usage.candidates_tokens + usage.thoughts_token_count,
        cost_usd=format_cost_usd(usage.estimated_cost_usd),
        is_estimate=usage.is_usage_estimate,
    )


def _provider_finish(raw: str | None) -> FinishReason:
    """Only ``MAX_TOKENS`` is told apart from a normal stop; every other reason reads as a stop."""
    return "max_tokens" if raw is not None and raw.upper() == "MAX_TOKENS" else "stop"


class _LiveTurn:
    """A message that goes to the model. ``events()`` is the SSE body."""

    def __init__(self, wiring: _Wiring, plan: _Plan) -> None:
        self._w = wiring
        self._plan = plan
        self._progress = _Progress()
        self._started_at = 0.0
        self._last_refresh = 0.0
        self._started = False
        self._finalizer: asyncio.Task[_Finalized] | None = None
        self._summary: _Summary | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._body = self._run()

    def events(self) -> AsyncIterator[str]:
        return self._body

    async def close(self) -> None:
        if not self._started:
            # The response ended before the first event: the user message is stored and the key is
            # claimed, so nothing else would ever finish this request.
            await asyncio.shield(self._finalize(_Failed("stream_interrupted")))
        try:
            await self._body.aclose()  # a stream suspended mid-reply is finished (as interrupted)
        except RuntimeError:  # still running in another task: that task finishes it
            logger.debug("chat_turn_close_while_running")
        pending = [task for task in self._tasks if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # ---------------------------------------------------------------------------- the stream

    async def _run(self) -> AsyncGenerator[str, None]:
        self._started = True
        plan = self._plan
        sequence = itertools.count(1)
        self._started_at = self._last_refresh = self._w.monotonic()

        failure: ErrorCode | None = None
        progress = self._progress
        # Creating the generator does nothing yet: the call is validated when iteration starts.
        stream = self._w.gateway.generate_stream(
            ORCHESTRATOR_ALIAS,
            plan.window.contents,
            system_instruction=plan.window.system_instruction,
            workspace_id=self._w.principal.workspace_id,
        )
        try:
            yield encode_sse(
                next(sequence),
                "message.accepted",
                MessageAcceptedEvent(
                    session_id=plan.session_id,
                    user_message_id=plan.user_message_id,
                    assistant_message_id=plan.assistant_message_id,
                    replayed=False,
                ),
            )
            async for chunk in stream:
                text = self._observe(chunk)
                if text:
                    yield encode_sse(next(sequence), "delta", DeltaEvent(text=text))
                await self._keep_claim_alive()
        except StreamInterruptedError as error:
            logger.error("chat_stream_interrupted chunks=%d", error.chunks_yielded)
            if isinstance(error.partial_usage, TokenUsage):
                progress.partial_usage = error.partial_usage
            failure = "stream_interrupted"
        except ContentBlockedError:
            logger.warning("chat_content_blocked")
            failure = "content_blocked"
        except (GatewayError, genai_errors.APIError) as error:
            logger.error("chat_model_unavailable error=%s", describe_error(error))
            failure = "model_unavailable"
        except (asyncio.CancelledError, GeneratorExit):
            # The client went away or the task was cancelled. A reply that already has text was
            # generated (and billed), so it is stored as interrupted; with no text at all there is
            # nothing to keep, and an empty assistant message would only confuse the history. The
            # write cannot be cancelled by this.
            logger.info("chat_stream_cancelled chunks=%d", progress.chunks)
            cut: _Outcome = _Interrupted(None) if progress.chunks else _Failed("stream_interrupted")
            await asyncio.shield(self._finalize(cut))
            raise
        except Exception as error:
            logger.error("chat_stream_failed error=%s", type(error).__name__)
            failure = "internal_error"
        finally:
            await self._close_gateway_stream(stream)

        outcome = self._outcome(failure)
        finalized = await asyncio.shield(self._finalize(outcome))
        summary = self._summary
        if isinstance(outcome, _Completed) and finalized.stored and summary is not None:
            yield encode_sse(
                next(sequence),
                "done",
                DoneEvent(
                    assistant_message_id=plan.assistant_message_id,
                    finish_reason=summary.finish_reason,
                    usage=summary.usage,
                ),
            )
            return
        code = self._error_code(outcome)
        yield encode_sse(next(sequence), "error", ErrorEvent(code=code, retryable=_RETRYABLE[code]))

    def _observe(self, chunk: GatewayStreamChunk) -> str:
        progress = self._progress
        progress.chunks += 1
        progress.model_id = chunk.model_id
        progress.alias = chunk.alias_used
        if chunk.usage is not None:
            progress.usage = chunk.usage
        if chunk.finish_reason is not None:
            progress.provider_finish = chunk.finish_reason
        text = _clean(chunk.text)
        if text:
            progress.parts.append(text)
        return text

    async def _keep_claim_alive(self) -> None:
        now = self._w.monotonic()
        if now - self._last_refresh < CLAIM_REFRESH_SECONDS:
            return
        self._last_refresh = now
        try:
            await self._w.idempotency.save(
                self._plan.scope, replace(self._plan.claim, session_id=self._plan.session_id)
            )
        except IdempotencyUnavailable:  # best effort; the stream itself is not at risk
            logger.warning("chat_claim_refresh_failed")

    def _outcome(self, failure: ErrorCode | None) -> _Outcome:
        if failure is None:
            return _Completed()
        if failure == "stream_interrupted":
            return _Interrupted(failure)
        received = self._progress.chunks > 0
        if failure == "internal_error":
            return _Interrupted(failure) if received else _Failed(failure)
        # content_blocked and model_unavailable are only reported before the first chunk. Once
        # text was sent, a late error cannot un-send it: the reply is stored as interrupted.
        return _Interrupted("stream_interrupted") if received else _Failed(failure)

    @staticmethod
    def _error_code(outcome: _Outcome) -> ErrorCode:
        if isinstance(outcome, _Failed):
            return outcome.code
        if isinstance(outcome, _Interrupted) and outcome.error is not None:
            return outcome.error
        return "internal_error"  # a completed reply that could not be stored

    async def _close_gateway_stream(self, stream: AsyncGenerator[GatewayStreamChunk, None]) -> None:
        async def close() -> None:
            try:
                await stream.aclose()
            except Exception:  # best effort: closing must never mask the outcome
                logger.debug("chat_gateway_stream_close_failed")

        await asyncio.shield(self._spawn(close()))

    # --------------------------------------------------------------------- terminal write

    def _spawn[T](self, coroutine: Coroutine[Any, Any, T]) -> "asyncio.Task[T]":
        task = asyncio.get_running_loop().create_task(coroutine)
        for registry in (self._tasks, _TASKS):
            registry.add(task)
            task.add_done_callback(registry.discard)
        return task

    def _finalize(self, outcome: _Outcome) -> "asyncio.Task[_Finalized]":
        """Start the terminal write once. A later call (a disconnect arriving while the reply is
        being stored) gets the task that is already running, so the first outcome wins."""
        if self._finalizer is None:
            if not isinstance(outcome, _Failed):
                self._summary = self._summarize(outcome)
            self._finalizer = self._spawn(self._persist(outcome))
        return self._finalizer

    def _summarize(self, outcome: _Outcome) -> _Summary:
        progress = self._progress
        text = "".join(progress.parts)
        if isinstance(outcome, _Completed):
            usage = progress.usage or self._estimated_usage(text)
            return _Summary(text, _provider_finish(progress.provider_finish), _usage_out(usage))
        if progress.partial_usage is not None:
            usage = progress.partial_usage
        elif progress.usage is not None:
            usage = progress.usage.model_copy(update={"is_usage_estimate": True})
        else:
            usage = self._estimated_usage(text)
        return _Summary(text, "interrupted", _usage_out(usage))

    def _estimated_usage(self, text: str) -> TokenUsage:
        """Usage when the provider reported none: sizes estimated locally and flagged as such."""
        spec = self._spec_used()
        usage = TokenUsage(
            prompt_tokens=estimate_tokens(self._plan.window.contents),
            candidates_tokens=estimate_tokens(text) if text else 0,
            price_unknown=spec.price_unknown,
            is_usage_estimate=True,
        )
        cost, complete = calculate_cost(spec, usage)
        usage.estimated_cost_usd = cost
        usage.is_estimated_cost_complete = complete
        return usage

    def _spec_used(self) -> ModelSpec:
        for alias in (self._progress.alias, ORCHESTRATOR_ALIAS):
            if alias is None:
                continue
            try:
                return self._w.registry.get(alias)
            except GatewayConfigurationError:
                continue
        raise GatewayConfigurationError("The orchestrator alias is not registered")

    async def _persist(self, outcome: _Outcome) -> _Finalized:
        plan = self._plan
        if isinstance(outcome, _Failed):
            await self._record("failed", error_code=outcome.code)
            return _Finalized(stored=False)
        summary = self._summary
        assert summary is not None  # set by _finalize before this task exists
        interrupted = isinstance(outcome, _Interrupted)
        metadata = {
            "model_id": self._progress.model_id,
            "alias": self._progress.alias,
            "finish_reason": summary.finish_reason,
            "usage": summary.usage.model_dump(),
            "latency_ms": int((self._w.monotonic() - self._started_at) * 1000),
            "status": "interrupted" if interrupted else "completed",
        }
        try:
            async with self._w.database.transaction() as connection:
                repository = self._w.repositories(
                    connection, self._w.principal.workspace_id, self._w.principal.user_id
                )
                await repository.add_message(
                    plan.session_id,
                    "orchestrator",
                    summary.text,
                    metadata=metadata,
                    message_id=plan.assistant_message_id,
                )
        except Exception as error:
            logger.error("chat_assistant_persist_failed error=%s", type(error).__name__)
            await self._record("failed", error_code="internal_error")
            return _Finalized(stored=False)
        await self._record("interrupted" if interrupted else "completed", finish=summary)
        logger.info(
            "chat_turn_finished status=%s finish_reason=%s chunks=%d",
            "interrupted" if interrupted else "completed",
            summary.finish_reason,
            self._progress.chunks,
        )
        return _Finalized(stored=True)

    async def _record(
        self,
        state: State,
        *,
        finish: _Summary | None = None,
        error_code: ErrorCode | None = None,
    ) -> None:
        record = replace(
            self._plan.claim,
            state=state,
            session_id=self._plan.session_id,
            finish_reason=None if finish is None else finish.finish_reason,
            error_code=error_code,
        )
        for attempt in range(RECORD_ATTEMPTS):
            try:
                await self._w.idempotency.save(self._plan.scope, record)
                return
            except IdempotencyUnavailable:
                if attempt + 1 < RECORD_ATTEMPTS:
                    await asyncio.sleep(RECORD_RETRY_DELAY_SECONDS * (attempt + 1))
        # The reply is stored; only the promise that a retry replays it is weakened: the key stays
        # in progress until its claim expires, then the same key would be treated as new.
        logger.error("chat_idempotency_update_failed")


class _ReplayTurn:
    """A stored result sent again: no model call, no new rows."""

    def __init__(self, frames: Sequence[str]) -> None:
        self._frames = tuple(frames)

    def events(self) -> AsyncIterator[str]:
        return self._emit()

    async def _emit(self) -> AsyncGenerator[str, None]:
        for frame in self._frames:
            yield frame

    async def close(self) -> None:
        return None


# ----------------------------------------------------------------------------------- service


class ChatService:
    """Chat for one authenticated caller. Cheap to build; holds no state between calls."""

    def __init__(
        self,
        *,
        principal: Principal,
        database: TenantTransactions,
        gateway: ModelGateway,
        registry: ModelRegistry,
        settings: Settings,
        limiter: RateLimiter,
        idempotency: IdempotencyStore,
        clock: Clock | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        repositories: ChatStorageFactory = ChatRepository,
    ) -> None:
        self._principal = principal
        self._database = database
        self._gateway = gateway
        self._registry = registry
        self._settings = settings
        self._limiter = limiter
        self._idempotency = idempotency
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._repositories = repositories
        self._contexts = ContextManager()
        self._hash_secret = settings.jwt_signing_key.get_secret_value().encode()
        self._wiring = _Wiring(
            principal=principal,
            database=database,
            repositories=repositories,
            gateway=gateway,
            registry=registry,
            idempotency=idempotency,
            monotonic=monotonic,
        )

    # ------------------------------------------------------------------------- POST /message

    async def begin_message(self, *, idempotency_keys: Sequence[str], body: bytes) -> ChatTurn:
        """Run every step that precedes the stream and return the turn to send.

        Raises ``ChatError`` subclasses, ``RateLimited`` and ``SessionNotFound``; the API layer
        maps each to its documented JSON answer. Nothing has been stored when it raises, except
        in the one case where the database or Redis fails after the user message was written.
        """
        try:
            return await self._begin(idempotency_keys, body)
        except (IdempotencyUnavailable, RateLimiterUnavailable):
            logger.error("chat_redis_unavailable")
            raise ChatUnavailable from None
        except (SQLAlchemyError, OSError) as error:
            logger.error("chat_database_unavailable error=%s", type(error).__name__)
            raise ChatUnavailable from None

    async def _begin(self, idempotency_keys: Sequence[str], body: bytes) -> ChatTurn:
        key = parse_idempotency_key(idempotency_keys)
        command = parse_message_request(body, self._settings.chat_max_message_chars)
        spec = self._orchestrator_spec()
        self._build_window((), command.content, spec)  # does the message alone fit the budget?
        if self._gateway.client is None:
            raise ModelUnavailable
        await self._enforce_rate_limit()
        if command.session_id is not None:
            await self._require_session(command.session_id)

        scope = IdempotencyScope(self._principal.workspace_id, self._principal.user_id, key)
        claim = IdempotencyRecord(
            state="in_progress",
            request_hash=request_hash(
                self._hash_secret, scope, command.session_id, command.content
            ),
            session_id=command.session_id,
            user_message_id=uuid4(),
            assistant_message_id=uuid4(),
        )
        existing = await self._idempotency.claim(scope, claim)
        if existing is not None:
            return await self._answer_repeat(existing, claim)

        try:
            session_id, window = await self._store_user_message(command, claim, spec)
        except BaseException:
            # Nothing was stored (the transaction rolled back), so the key must not stay blocked.
            with suppress(IdempotencyUnavailable):
                await asyncio.shield(self._idempotency.release(scope, claim))
            raise
        return _LiveTurn(self._wiring, _Plan(scope, claim, session_id, window))

    def _orchestrator_spec(self) -> ModelSpec:
        try:
            return self._registry.get(ORCHESTRATOR_ALIAS)
        except GatewayConfigurationError:
            raise ModelUnavailable from None

    def _build_window(
        self, history: Sequence[HistoryTurn], content: str, spec: ModelSpec
    ) -> ContextWindow:
        # The model's context limit minus the room reserved for its answer.
        ceiling = max(1, spec.context_input_limit - (spec.context_output_limit or 0))
        try:
            return self._contexts.build(
                history,
                content,
                token_budget=self._settings.chat_context_token_budget,
                hard_ceiling=ceiling,
            )
        except ContextTooLarge:
            raise ChatValidationError(["content"]) from None

    async def _enforce_rate_limit(self) -> None:
        limit = Limit(self._settings.chat_rate_limit_per_minute, RATE_LIMIT_WINDOW_SECONDS)
        name = f"chat-message:{self._principal.workspace_id}:{self._principal.user_id}"
        if not await self._limiter.hit(name, limit):
            elapsed = int(self._clock.now().timestamp()) % RATE_LIMIT_WINDOW_SECONDS
            raise RateLimited(retry_after=RATE_LIMIT_WINDOW_SECONDS - elapsed)

    async def _require_session(self, session_id: UUID) -> None:
        async with self._database.transaction() as connection:
            repository = self._storage(connection)
            if await repository.get_session(session_id) is None:
                raise SessionNotFound

    async def _store_user_message(
        self, command: ChatMessageRequest, claim: IdempotencyRecord, spec: ModelSpec
    ) -> tuple[UUID, ContextWindow]:
        """One short transaction: read the history, then write the user message and its session."""
        async with self._database.transaction() as connection:
            repository = self._storage(connection)
            if command.session_id is None:
                session_id = (await repository.create_session(make_title(command.content))).id
                history: list[HistoryTurn] = []
            else:
                session_id = command.session_id
                stored = await repository.recent_messages(session_id, HISTORY_MESSAGE_LIMIT)
                history = [HistoryTurn(message.role, message.content) for message in stored]
            # Built before the insert: a message that cannot be sent leaves nothing behind, and the
            # new message is not in its own history.
            window = self._build_window(history, command.content, spec)
            await repository.add_message(
                session_id, "user", command.content, message_id=claim.user_message_id
            )
        return session_id, window

    def _storage(self, connection: AsyncConnection) -> ChatStorage:
        return self._repositories(connection, self._principal.workspace_id, self._principal.user_id)

    # ---------------------------------------------------------------------- repeated requests

    async def _answer_repeat(
        self, existing: IdempotencyRecord, claim: IdempotencyRecord
    ) -> ChatTurn:
        if existing.request_hash != claim.request_hash:
            raise IdempotencyKeyReused
        if existing.state == "in_progress":
            raise RequestInProgress
        if existing.session_id is None:  # a terminal record always has one; do not guess
            raise ChatUnavailable
        stored: ChatMessageRecord | None = None
        if existing.state in ("completed", "interrupted"):
            async with self._database.transaction() as connection:
                repository = self._storage(connection)
                if await repository.get_session(existing.session_id) is None:
                    raise SessionNotFound
                stored = await repository.get_message(existing.assistant_message_id)
        return _ReplayTurn(self._replay_frames(existing, stored))

    @staticmethod
    def _replay_frames(record: IdempotencyRecord, stored: ChatMessageRecord | None) -> list[str]:
        assert record.session_id is not None
        sequence = itertools.count(1)
        frames = [
            encode_sse(
                next(sequence),
                "message.accepted",
                MessageAcceptedEvent(
                    session_id=record.session_id,
                    user_message_id=record.user_message_id,
                    assistant_message_id=record.assistant_message_id,
                    replayed=True,
                ),
            )
        ]
        code: ErrorCode | None = None
        if record.state == "failed":
            code = _as_error_code(record.error_code)
        elif stored is None or stored.sender != "orchestrator":
            code = "internal_error"
        if code is not None:
            frames.append(
                encode_sse(
                    next(sequence), "error", ErrorEvent(code=code, retryable=_RETRYABLE[code])
                )
            )
            return frames
        assert stored is not None
        if stored.content:
            frames.append(encode_sse(next(sequence), "delta", DeltaEvent(text=stored.content)))
        frames.append(
            encode_sse(
                next(sequence),
                "done",
                DoneEvent(
                    assistant_message_id=record.assistant_message_id,
                    finish_reason=_stored_finish_reason(stored, record),
                    usage=_stored_usage(stored),
                ),
            )
        )
        return frames

    # ------------------------------------------------------------------------------- reads

    async def list_sessions(self, limit: int, cursor: str | None) -> Page[SessionOut]:
        """The caller's sessions, newest first. ``InvalidCursor`` for a bad cursor."""
        try:
            async with self._database.transaction() as connection:
                page = await self._storage(connection).list_sessions(limit, cursor)
        except (SQLAlchemyError, OSError) as error:
            logger.error("chat_database_unavailable error=%s", type(error).__name__)
            raise ChatUnavailable from None
        return Page[SessionOut](
            items=[SessionOut.model_validate(record) for record in page.items],
            next_cursor=page.next_cursor,
        )

    async def list_messages(
        self, session_id: UUID, limit: int, cursor: str | None
    ) -> Page[MessageOut]:
        """One session's messages, oldest first. ``InvalidCursor`` for a bad cursor,
        ``SessionNotFound`` for an unknown or foreign session."""
        try:
            async with self._database.transaction() as connection:
                page = await self._storage(connection).list_messages(session_id, limit, cursor)
        except (SQLAlchemyError, OSError) as error:
            logger.error("chat_database_unavailable error=%s", type(error).__name__)
            raise ChatUnavailable from None
        return Page[MessageOut](
            items=[MessageOut.model_validate(record) for record in page.items],
            next_cursor=page.next_cursor,
        )


def _stored_finish_reason(stored: ChatMessageRecord, record: IdempotencyRecord) -> FinishReason:
    value = stored.finish_reason or record.finish_reason
    if value == "max_tokens":
        return "max_tokens"
    if value == "interrupted" or record.state == "interrupted":
        return "interrupted"
    return "stop"


def _as_error_code(value: str | None) -> ErrorCode:
    for code in _RETRYABLE:
        if code == value:
            return code
    return "internal_error"


def _stored_usage(stored: ChatMessageRecord) -> UsageOut:
    """The usage stored with the reply; an unreadable record reads as an unknown estimate."""
    try:
        return UsageOut.model_validate(stored.metadata.get("usage"))
    except ValidationError:
        return UsageOut(
            prompt_tokens=0, candidates_tokens=0, total_tokens=0, cost_usd=None, is_estimate=True
        )


__all__ = [
    "CLAIM_REFRESH_SECONDS",
    "HISTORY_MESSAGE_LIMIT",
    "ORCHESTRATOR_ALIAS",
    "ChatError",
    "ChatService",
    "ChatStorage",
    "ChatStorageFactory",
    "ChatTurn",
    "ChatUnavailable",
    "ChatValidationError",
    "IdempotencyKeyRequired",
    "IdempotencyKeyReused",
    "ModelUnavailable",
    "RequestInProgress",
    "TenantTransactions",
    "make_title",
    "parse_idempotency_key",
    "parse_message_request",
]
