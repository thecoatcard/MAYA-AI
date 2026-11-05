"""Real SDK calls through injected async clients; no credential or client factory.

The offline host CLIs remain blocked. Only isolated interactive free-tier
launchers may call these adapters for their approved user-run workload.
"""

import asyncio
import math
import re
import struct
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from contextlib import (
    AbstractAsyncContextManager,
    aclosing,
    asynccontextmanager,
    contextmanager,
)
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol, cast, get_args

from google.genai import errors, types
from google.genai import live as sdk_live
from tests.spikes.spike_s3_gemini_live import SessionPlan, build_plans
from tests.spikes.spike_s4_embeddings import DIMENSIONS, TEXTS, EmbeddingMetrics, analyze_response

MAX_AUDIO_BYTES = 48_000 * 960
CLOSE_SECONDS = 2.0
LIVE_MODEL = "gemini-3.8-live"
SMOKE_AUDIO_BYTES = 48_000 * 10
CONTINUITY_MAX_SECONDS = 960
CONTINUITY_MAX_RESUMPTIONS = 4
# 90 seconds of 24 kHz mono PCM16 output, cumulative across all turns in a session.
CONTINUITY_MAX_AUDIO_BYTES = 48_000 * 90
CONTINUITY_ACTIVITY_INTERVAL_SECONDS = 60.0
CONTINUITY_MAX_SYNTHETIC_TURNS = 15
# Fixed, nonce-free, and does not mention remembered words.
CONTINUITY_ACTIVITY_PROMPT = "Periodic synthetic connection check. Reply with only the word ready."
# RFC 6455 limits a close reason to 123 bytes; anything longer is not trusted.
MAX_CLOSE_REASON_BYTES = 123
CONTINUITY_MAX_RESPONSE_BYTES = 256
CONTINUITY_NONCE_WORDS = (
    "amber",
    "birch",
    "cedar",
    "dawn",
    "elm",
    "fern",
    "glade",
    "harbor",
    "indigo",
    "jacket",
    "lilac",
    "maple",
    "north",
    "opal",
    "paper",
    "raven",
    "silver",
    "stone",
    "violet",
    "willow",
    "yellow",
    "zephyr",
    "apple",
    "candle",
    "garden",
    "river",
)


FailureCategory = Literal[
    "sdk_operation_failed",
    "response_validation_failed",
    "audio_output_cap_exceeded",
    "transcription_bound_exceeded",
    "synthetic_activity_send_failed",
    "cleanup_failed",
]
FAILURE_CATEGORIES: frozenset[str] = frozenset(get_args(FailureCategory))


class AdapterFailure(RuntimeError):
    """Redacted operational failure; never includes SDK errors or response text.

    ``category`` is a fixed allowlisted label, safe to persist as evidence.
    """

    def __init__(self, message: str, category: FailureCategory = "sdk_operation_failed") -> None:
        super().__init__(message)
        self.category: FailureCategory = category


class _ContinuityStop(Exception):
    """Internal fixed-message stop carrying an allowlisted failure category."""

    def __init__(self, message: str, category: FailureCategory) -> None:
        super().__init__(message)
        self.category: FailureCategory = category


class _ContinuityBoundError(_ContinuityStop, ValueError):
    """Response validation or output bound violation."""


class EmbeddingModels(Protocol):
    async def embed_content(
        self, *, model: str, contents: str, config: types.EmbedContentConfig
    ) -> types.EmbedContentResponse: ...


class EmbeddingClient(Protocol):
    @property
    def models(self) -> EmbeddingModels: ...

    async def aclose(self) -> None: ...


class LiveSession(Protocol):
    @property
    def setup_complete(self) -> types.LiveServerSetupComplete | None: ...

    async def send_client_content(
        self, *, turns: types.Content, turn_complete: bool = True
    ) -> None: ...

    async def send_realtime_input(
        self,
        *,
        text: str | None = None,
        audio: types.Blob | None = None,
        audio_stream_end: bool | None = None,
    ) -> None: ...

    def receive(self) -> AsyncIterator[types.LiveServerMessage]: ...


class LiveConnector(Protocol):
    def connect(
        self, *, model: str, config: types.LiveConnectConfig
    ) -> AbstractAsyncContextManager[LiveSession]: ...


class LiveClient(Protocol):
    @property
    def live(self) -> LiveConnector: ...

    async def aclose(self) -> None: ...


async def _finish_cleanup(cleanup: Awaitable[None]) -> None:
    """Do not abandon a bounded close if the outer deadline fires during it."""
    task = asyncio.ensure_future(cleanup)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


@contextmanager
def _websocket_without_proxy() -> Iterator[None]:
    """Pin websockets 17's ambient proxy selection off for free-tier runs."""
    original_connect = cast(Callable[..., object], sdk_live.__dict__["ws_connect"])

    def connect_without_proxy(uri: str, *args: object, **kwargs: object) -> object:
        kwargs["proxy"] = None
        return original_connect(uri, *args, **kwargs)

    sdk_live.__dict__["ws_connect"] = connect_without_proxy
    try:
        yield
    finally:
        sdk_live.__dict__["ws_connect"] = original_connect


@asynccontextmanager
async def _connection(
    connector: LiveConnector,
    handle: str | None,
    *,
    allow_resumption: bool,
    response_modality: types.Modality = types.Modality.AUDIO,
    output_audio_transcription: bool = False,
    disable_ambient_proxy: bool = False,
) -> AsyncIterator[LiveSession]:
    context = connector.connect(
        model=LIVE_MODEL,
        config=live_config(
            handle,
            allow_resumption=allow_resumption,
            response_modality=response_modality,
            output_audio_transcription=output_audio_transcription,
        ),
    )
    if not allow_resumption or disable_ambient_proxy:
        with _websocket_without_proxy():
            session = await context.__aenter__()
    else:
        session = await context.__aenter__()
    try:
        yield session
    finally:
        # A separate cleanup bound still applies after the observation is cancelled.
        async def close_session() -> None:
            try:
                async with asyncio.timeout(CLOSE_SECONDS):
                    await context.__aexit__(None, None, None)
            except Exception:
                raise AdapterFailure("S3 SDK session cleanup failed.", "cleanup_failed") from None

        await _finish_cleanup(close_session())


async def _close(client: EmbeddingClient | LiveClient) -> None:
    async def close_client() -> None:
        async with asyncio.timeout(CLOSE_SECONDS):
            await client.aclose()

    try:
        await _finish_cleanup(close_client())
    except Exception:
        raise AdapterFailure("SDK client cleanup failed.") from None


@dataclass(frozen=True)
class EmbeddingObservation:
    metrics: EmbeddingMetrics
    billable_character_count: int | None
    # SDK usage is not an invoice or a hard spending cap.


async def embed_synthetic(
    client: EmbeddingClient,
    *,
    max_seconds: float = 30,
    request_timeout_seconds: float | None = None,
    close_client: bool = True,
) -> EmbeddingObservation:
    """Own and close the injected async client, including on failure/cancellation."""
    try:
        if not math.isfinite(max_seconds) or not 0 < max_seconds <= 60:
            raise ValueError("Embedding duration must be in (0, 60].")
        if request_timeout_seconds is not None and (
            not math.isfinite(request_timeout_seconds) or not 0 < request_timeout_seconds <= 30
        ):
            raise ValueError("Per-request embedding duration must be in (0, 30].")
        try:
            async with asyncio.timeout(max_seconds):
                vectors: list[dict[str, list[float] | None]] = []
                counts: list[int | None] = []
                for text in TEXTS:
                    # Embedding 2 aggregates list inputs. One request per text is
                    # necessary to obtain three independently comparable vectors.
                    request = client.models.embed_content(
                        model="gemini-embedding-2",
                        contents=text,
                        config=types.EmbedContentConfig(output_dimensionality=DIMENSIONS),
                    )
                    if request_timeout_seconds is None:
                        response = await request
                    else:
                        async with asyncio.timeout(request_timeout_seconds):
                            response = await request
                    if response.embeddings is None or len(response.embeddings) != 1:
                        raise ValueError("Expected exactly one embedding per synthetic text.")
                    vectors.append({"values": response.embeddings[0].values})
                    counts.append(
                        response.metadata.billable_character_count if response.metadata else None
                    )
                # Preserve raw values; the existing analyzer owns all numeric checks.
                metrics = analyze_response({"embeddings": vectors})
                usage = (
                    sum(count for count in counts if count is not None)
                    if all(count is not None for count in counts)
                    else None
                )
                return EmbeddingObservation(metrics, usage)
        except Exception:
            raise AdapterFailure("S4 SDK request or response validation failed.") from None
    finally:
        if close_client:
            await _close(client)


EndReason = Literal[
    "observation_timeout",
    "provider_closed",
    "provider_disconnected",
    "goaway_without_handle",
    "resumption_limit",
    "turn_complete",
    "continuity_mismatch",
]


@dataclass(frozen=True)
class TokenUsage:
    """One raw usage snapshot, not summed (provider counters may be cumulative)."""

    prompt: int | None
    response: int | None
    total: int | None


@dataclass
class LiveObservation:
    method: str
    end_reason: EndReason = "provider_closed"
    first_audio_latency_ms: float | None = None
    audio_bytes: int = 0
    turn_completions: int = 0
    go_away_events: int = 0
    resumptions: int = 0
    observed_seconds: float = 0
    usage: list[TokenUsage] = field(default_factory=list)
    # No transcript, audio payload or resumption handle is exposed by this report.
    context_continuity_verified: bool = False
    live_acceptance_verified: bool = False
    continuity_probe_completed: bool = False
    termination_observed: bool = False
    observation_limit_reached: bool = False
    goaway_observed: bool = False
    resumption_handle_observed: bool = False
    resumed_setup_observed: bool = False
    provider_closed: bool = False
    provider_close_code: int | None = None
    # Allowlisted labels only; the raw close reason is never stored.
    provider_close_code_category: str | None = None
    provider_close_reason_category: str | None = None
    transcription_bytes: int = 0
    synthetic_turns_sent: int = 0
    synthetic_turns_completed: int = 0
    synthetic_turn_limit_reached: bool = False
    seconds_since_last_client_send: float | None = None

    @property
    def audio_duration_ms(self) -> float:
        return self.audio_bytes / 48_000 * 1000


def validate_pcm(data: bytes, *, sample_rate: int) -> None:
    if sample_rate not in (16000, 24000) or not data or len(data) % 2:
        raise ValueError("Audio must be nonempty mono little-endian PCM16 at 16/24 kHz.")


def synthetic_input_audio() -> bytes:
    """100 ms synthetic 440 Hz, signed little-endian PCM16 mono at 16 kHz."""
    return struct.pack(
        "<1600h",
        *(round(8192 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(1600)),
    )


def live_config(
    handle: str | None = None,
    *,
    allow_resumption: bool = True,
    response_modality: types.Modality = types.Modality.AUDIO,
    output_audio_transcription: bool = False,
) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=[response_modality],
        output_audio_transcription=(
            types.AudioTranscriptionConfig() if output_audio_transcription else None
        ),
        session_resumption=(
            types.SessionResumptionConfig(handle=handle) if allow_resumption else None
        ),
        # Deliberately omit context_window_compression: finite uncompressed observation.
    )


def _record_message(
    message: types.LiveServerMessage,
    observation: LiveObservation,
    *,
    sent_at: float,
    clock: Callable[[], float],
    max_audio_bytes: int | None = None,
) -> None:
    if message.usage_metadata:
        usage = message.usage_metadata
        observation.usage.append(
            TokenUsage(
                usage.prompt_token_count, usage.response_token_count, usage.total_token_count
            )
        )
    content = message.server_content
    if content is None:
        return
    if content.model_turn:
        for part in content.model_turn.parts or []:
            blob = part.inline_data
            if blob is None:
                continue
            # Do not silently interpret compressed, stereo, or wrong-rate audio as PCM.
            if blob.mime_type != "audio/pcm;rate=24000" or blob.data is None:
                raise ValueError("Unexpected output audio format.")
            validate_pcm(blob.data, sample_rate=24000)
            observation.audio_bytes += len(blob.data)
            audio_limit = MAX_AUDIO_BYTES if max_audio_bytes is None else max_audio_bytes
            if observation.audio_bytes > audio_limit:
                raise ValueError("Output audio exceeds observation bound.")
            if observation.first_audio_latency_ms is None:
                observation.first_audio_latency_ms = (clock() - sent_at) * 1000
    if content.turn_complete:
        observation.turn_completions += 1


async def observe_session(
    connector: LiveConnector,
    plan: SessionPlan,
    *,
    include_pcm: bool = False,
    clock: Callable[[], float] = time.monotonic,
    allow_resumption: bool = True,
    stop_after_first_audio_turn: bool = False,
    max_audio_bytes: int | None = None,
) -> LiveObservation:
    """Bound connect/send/receive/reconnect together; context owns each socket.

    Resumption does not replay inputs and does not claim context continuity.
    SDK receive() ends at each turn, so it must be called again, not treated as
    the lifetime of the provider session. Empty receives and normal close codes
    are distinct from our own observation timeout.
    """
    plan.text_arguments()
    if plan.prompt != build_plans()[0].prompt or plan.context_compression:
        raise ValueError("Only the built-in synthetic uncompressed probe is supported.")
    if max_audio_bytes is not None and (
        isinstance(max_audio_bytes, bool)
        or not isinstance(max_audio_bytes, int)
        or max_audio_bytes <= 0
    ):
        raise ValueError("Audio byte limit must be a positive integer.")
    observation = LiveObservation(plan.method)
    started = clock()
    sent_at: float | None = None
    handle: str | None = None
    receiving = False
    completed_first_turn = False
    deadline = asyncio.timeout(plan.max_seconds)
    try:
        async with deadline:
            while True:
                goaway = False
                async with _connection(
                    connector, handle, allow_resumption=allow_resumption
                ) as session:
                    if session.setup_complete is None:
                        raise ValueError("SDK connection did not confirm setup.")
                    if sent_at is None:
                        sent_at = clock()
                        if plan.method == "send_client_content":
                            await session.send_client_content(
                                turns=types.Content(
                                    role="user", parts=[types.Part(text=plan.prompt)]
                                ),
                                turn_complete=True,
                            )
                        else:
                            await session.send_realtime_input(text=plan.prompt)
                        if include_pcm:
                            pcm = synthetic_input_audio()
                            validate_pcm(pcm, sample_rate=16000)
                            await session.send_realtime_input(
                                audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
                            )
                            await session.send_realtime_input(audio_stream_end=True)
                    else:
                        observation.resumptions += 1
                    while not goaway:
                        received = False
                        # Explicitly close the receive generator on GoAway and cancellation.
                        messages = session.receive()
                        if not isinstance(messages, AsyncGenerator):
                            raise TypeError("SDK receive must return a closable async generator.")
                        receiving = True
                        async with aclosing(messages):
                            async for message in messages:
                                received = True
                                _record_message(
                                    message,
                                    observation,
                                    sent_at=sent_at,
                                    clock=clock,
                                    max_audio_bytes=max_audio_bytes,
                                )
                                if (
                                    stop_after_first_audio_turn
                                    and observation.first_audio_latency_ms is not None
                                    and observation.turn_completions > 0
                                ):
                                    completed_first_turn = True
                                    break
                                update = message.session_resumption_update
                                if allow_resumption and update is not None:
                                    # An unresumable update invalidates an older handle.
                                    handle = (
                                        update.new_handle
                                        if update.resumable and update.new_handle
                                        else None
                                    )
                                if message.go_away is not None:
                                    observation.go_away_events += 1
                                    goaway = True
                                    break
                        receiving = False
                        if completed_first_turn:
                            observation.end_reason = "turn_complete"
                            return observation
                        if not received:
                            observation.end_reason = "provider_closed"
                            return observation
                if handle is None:
                    observation.end_reason = "goaway_without_handle"
                    return observation
                if observation.resumptions >= 4:
                    observation.end_reason = "resumption_limit"
                    return observation
    except TimeoutError:
        if not deadline.expired():
            raise AdapterFailure("S3 SDK operation failed.") from None
        observation.end_reason = "observation_timeout"
    except errors.APIError as error:
        # SDK _receive converts WebSocket close frames into APIError codes.
        if error.code not in (1000, 1001) or not receiving:
            raise AdapterFailure("S3 SDK operation failed.") from None
        observation.end_reason = "provider_closed"
    except Exception:
        raise AdapterFailure("S3 SDK operation or response validation failed.") from None
    finally:
        observation.observed_seconds = max(0, clock() - started)
    return observation


# RFC 6455 / IANA WebSocket close-code names. Codes are measured; names are not causes.
_CLOSE_CODE_CATEGORIES: dict[int, str] = {
    1000: "normal_closure",
    1001: "going_away",
    1002: "protocol_error",
    1003: "unsupported_data",
    1005: "no_status_received",
    1006: "abnormal_closure",
    1007: "invalid_payload_data",
    1008: "policy_violation",
    1009: "message_too_big",
    1010: "mandatory_extension",
    1011: "internal_error",
    1012: "service_restart",
    1013: "try_again_later",
    1014: "bad_gateway",
}

# Known textual patterns only. Zero matches is "unknown"; several is ambiguous.
_CLOSE_REASON_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "quota_or_rate_limit",
        ("quota", "rate limit", "resource exhausted", "resource_exhausted", "too many requests"),
    ),
    ("deadline_or_timeout", ("deadline", "timeout", "timed out")),
    ("inactivity", ("idle", "inactiv")),
    ("invalid_argument", ("invalid argument", "invalid_argument")),
    (
        "unsupported_or_not_found",
        ("not implemented", "not supported", "unsupported", "not enabled", "not found"),
    ),
    (
        "authentication_or_permission",
        ("api key", "permission", "unauthenticated", "unauthorized", "forbidden"),
    ),
    ("service_unavailable", ("unavailable", "overloaded")),
    ("internal_error", ("internal error",)),
    ("policy_or_safety", ("policy", "safety")),
)
CLOSE_REASON_CATEGORIES: frozenset[str] = frozenset(
    {name for name, _ in _CLOSE_REASON_PATTERNS}
    | {"not_provided", "empty", "unknown", "multiple_patterns"}
)
CLOSE_CODE_CATEGORIES: frozenset[str] = frozenset(
    set(_CLOSE_CODE_CATEGORIES.values()) | {"unknown"}
)


def classify_provider_close(error: errors.APIError) -> tuple[str, str]:
    """Map an SDK close error to allowlisted (code, reason) labels; never return raw text.

    SDK ``_receive`` raises ``APIError.raise_error(code, reason, None)`` for a
    WebSocket close, so ``details`` holds the close reason string. Error JSON
    responses instead carry a ``message`` field.
    """
    code = error.code
    code_category = (
        _CLOSE_CODE_CATEGORIES.get(code, "unknown")
        if isinstance(code, int) and not isinstance(code, bool)
        else "unknown"
    )
    details: object = error.details
    message: object = error.message
    text = details if isinstance(details, str) else message if isinstance(message, str) else None
    if text is None:
        return code_category, "not_provided"
    if len(text.encode("utf-8", errors="replace")) > MAX_CLOSE_REASON_BYTES:
        return code_category, "unknown"
    folded = text.casefold().strip()
    if not folded:
        return code_category, "empty"
    matches = [
        name
        for name, patterns in _CLOSE_REASON_PATTERNS
        if any(pattern in folded for pattern in patterns)
    ]
    if len(matches) == 1:
        return code_category, matches[0]
    return code_category, "unknown" if not matches else "multiple_patterns"


class _ReceivePump:
    """Own at most one pending read of one SDK receive generator.

    ``asyncio.wait`` with a timeout does not cancel the read, so the client can
    send synthetic activity while the same receive stays pending. Cleanup cancels
    only this pump's own read task, awaits it, then closes the generator.
    """

    def __init__(self, messages: AsyncGenerator[types.LiveServerMessage, None]) -> None:
        self._messages = messages
        self._task: asyncio.Task[types.LiveServerMessage | None] | None = None

    async def _read(self) -> types.LiveServerMessage | None:
        try:
            return await anext(self._messages)
        except StopAsyncIteration:
            return None

    async def next(self, wait_seconds: float | None) -> tuple[bool, types.LiveServerMessage | None]:
        """Return (ready, message); a ready ``None`` means this receive ended."""
        if self._task is None:
            self._task = asyncio.create_task(self._read())
        done, _ = await asyncio.wait((self._task,), timeout=wait_seconds)
        if not done:
            return False, None
        task, self._task = self._task, None
        return True, task.result()

    async def aclose(self) -> None:
        async def close() -> None:
            task, self._task = self._task, None
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass  # Cleanup only; the observation outcome is already decided.
            await self._messages.aclose()

        await _finish_cleanup(close())


async def observe_continuity_session(
    connector: LiveConnector,
    plan: SessionPlan,
    *,
    nonce: str,
    clock: Callable[[], float] = time.monotonic,
) -> LiveObservation:
    """Observe one bounded text-input/AUDIO-output session and verify context after resume.

    The nonce is sent before the first connection is interrupted, then requested
    only after resuming; it is never replayed. While no turn is outstanding, a
    fixed nonce-free synthetic turn is sent after each activity interval, at most
    CONTINUITY_MAX_SYNTHETIC_TURNS times, without closing the pending receive.
    Audio and transcription are inspected in memory only.
    """
    plan.text_arguments()
    if plan.prompt != build_plans()[0].prompt or plan.context_compression:
        raise ValueError("Only the built-in synthetic uncompressed probe is supported.")
    if plan.max_seconds > CONTINUITY_MAX_SECONDS:
        raise ValueError("Continuity observation exceeds 960 seconds.")
    nonce_words = nonce.split()
    if len(nonce_words) != 6 or any(word not in CONTINUITY_NONCE_WORDS for word in nonce_words):
        raise ValueError("Continuity probe nonce is malformed.")
    # Read at call time so deterministic checks can shorten the interval only.
    interval = CONTINUITY_ACTIVITY_INTERVAL_SECONDS
    if (
        isinstance(interval, bool)
        or not isinstance(interval, int | float)
        or not math.isfinite(interval)
        or not 0 < interval <= 60
    ):
        raise ValueError("Continuity activity interval must be in (0, 60] seconds.")
    max_synthetic_turns = CONTINUITY_MAX_SYNTHETIC_TURNS
    if (
        isinstance(max_synthetic_turns, bool)
        or not isinstance(max_synthetic_turns, int)
        or not 0 <= max_synthetic_turns <= 15
    ):
        raise ValueError("Continuity synthetic turn limit must be in [0, 15].")

    observation = LiveObservation(plan.method)
    started = clock()
    handle: str | None = None
    receiving = False
    initial_connection = True
    continuation_sent = False
    transcription_parts: list[str] = []
    transcript_bytes = 0
    transcript_finished = False
    turn_complete_seen = False
    last_client_send: float | None = None
    turn_outstanding = False
    synthetic_outstanding = False
    deadline = asyncio.timeout(plan.max_seconds)
    seed_prompt = (
        f"Remember these six synthetic words exactly: {nonce}. "
        "Do not include it in your response yet."
    )
    followup_prompt = (
        "Say only the six synthetic words I asked you to remember, in order. "
        "Do not add punctuation or other text."
    )

    async def send_text(session: LiveSession, text: str) -> None:
        if plan.method == "send_client_content":
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=text)]),
                turn_complete=True,
            )
        else:
            await session.send_realtime_input(text=text)

    def activity_wait_seconds() -> float | None:
        """None means no synthetic turn may be sent now; wait for the next message."""
        if (
            last_client_send is None
            or turn_outstanding
            or observation.synthetic_turns_sent >= max_synthetic_turns
            # Never interleave with the continuity follow-up or its transcript.
            or (continuation_sent and not observation.continuity_probe_completed)
        ):
            return None
        return max(0.0, last_client_send + interval - clock())

    try:
        async with deadline:
            while True:
                goaway = False
                async with _connection(
                    connector,
                    handle,
                    allow_resumption=True,
                    response_modality=types.Modality.AUDIO,
                    output_audio_transcription=True,
                    disable_ambient_proxy=True,
                ) as session:
                    if session.setup_complete is None:
                        raise ValueError("SDK connection did not confirm setup.")
                    # A new connection does not continue an earlier pending turn.
                    turn_outstanding = synthetic_outstanding = False
                    if initial_connection:
                        initial_connection = False
                        await send_text(session, seed_prompt)
                        last_client_send = clock()
                        turn_outstanding = True
                    else:
                        observation.resumptions += 1
                        observation.resumed_setup_observed = True
                        if not continuation_sent:
                            await send_text(session, followup_prompt)
                            last_client_send = clock()
                            turn_outstanding = True
                            continuation_sent = True

                    while not goaway:
                        received = False
                        messages = session.receive()
                        if not isinstance(messages, AsyncGenerator):
                            raise TypeError("SDK receive must return a closable async generator.")
                        pump = _ReceivePump(messages)
                        receiving = True
                        try:
                            while True:
                                ready, message = await pump.next(activity_wait_seconds())
                                if not ready:
                                    # The single receive stays pending while this is sent.
                                    receiving = False
                                    observation.synthetic_turns_sent += 1
                                    observation.synthetic_turn_limit_reached = (
                                        observation.synthetic_turns_sent >= max_synthetic_turns
                                    )
                                    turn_outstanding = synthetic_outstanding = True
                                    try:
                                        await send_text(session, CONTINUITY_ACTIVITY_PROMPT)
                                    except Exception:
                                        raise _ContinuityStop(
                                            "Synthetic activity send failed.",
                                            "synthetic_activity_send_failed",
                                        ) from None
                                    last_client_send = clock()
                                    receiving = True
                                    continue
                                if message is None:
                                    break
                                received = True
                                content = message.server_content
                                if content is not None:
                                    if content.model_turn is not None:
                                        for part in content.model_turn.parts or []:
                                            blob = part.inline_data
                                            if blob is None:
                                                continue
                                            if (
                                                blob.mime_type != "audio/pcm;rate=24000"
                                                or blob.data is None
                                                or not blob.data
                                                or len(blob.data) % 2
                                            ):
                                                raise _ContinuityBoundError(
                                                    "Continuity audio format is invalid.",
                                                    "response_validation_failed",
                                                )
                                            observation.audio_bytes += len(blob.data)
                                            if observation.audio_bytes > CONTINUITY_MAX_AUDIO_BYTES:
                                                raise _ContinuityBoundError(
                                                    "Continuity audio bound exceeded.",
                                                    "audio_output_cap_exceeded",
                                                )
                                    transcription = content.output_transcription
                                    if (
                                        transcription is not None
                                        and transcription.text is not None
                                        and continuation_sent
                                        and not observation.continuity_probe_completed
                                    ):
                                        transcript_bytes += len(transcription.text.encode("utf-8"))
                                        if transcript_bytes > CONTINUITY_MAX_RESPONSE_BYTES:
                                            raise _ContinuityBoundError(
                                                "Continuity transcription bound exceeded.",
                                                "transcription_bound_exceeded",
                                            )
                                        observation.transcription_bytes = transcript_bytes
                                        transcription_parts.append(transcription.text)
                                        transcript_finished = (
                                            transcript_finished or transcription.finished is True
                                        )
                                    if content.turn_complete is True:
                                        if synthetic_outstanding:
                                            observation.synthetic_turns_completed += 1
                                        turn_outstanding = synthetic_outstanding = False
                                        if continuation_sent:
                                            turn_complete_seen = True
                                if (
                                    turn_complete_seen
                                    and transcript_finished
                                    and continuation_sent
                                    and not observation.continuity_probe_completed
                                ):
                                    # Transcription parts are deltas: a word may span parts.
                                    transcript = "".join(transcription_parts).casefold()
                                    normalized = re.sub(r"[^\w\s]", " ", transcript)
                                    observation.continuity_probe_completed = True
                                    observation.context_continuity_verified = (
                                        normalized.split() == nonce_words
                                    )
                                    if not observation.context_continuity_verified:
                                        observation.end_reason = "continuity_mismatch"
                                        return observation

                                update = message.session_resumption_update
                                if update is not None:
                                    handle = (
                                        update.new_handle
                                        if update.resumable and update.new_handle
                                        else None
                                    )
                                    if handle is not None:
                                        observation.resumption_handle_observed = True
                                if message.go_away is not None:
                                    observation.go_away_events += 1
                                    observation.goaway_observed = True
                                    goaway = True
                                    break
                        finally:
                            # Cancel only our own pending read, then close the generator.
                            await pump.aclose()
                        receiving = False
                        if not received:
                            observation.end_reason = "provider_closed"
                            observation.termination_observed = True
                            observation.provider_closed = True
                            return observation
                if handle is None:
                    observation.end_reason = "goaway_without_handle"
                    return observation
                if observation.resumptions >= CONTINUITY_MAX_RESUMPTIONS:
                    observation.end_reason = "resumption_limit"
                    return observation
    except TimeoutError:
        if not deadline.expired():
            raise AdapterFailure("S3 continuity SDK operation failed.") from None
        observation.end_reason = "observation_timeout"
        observation.observation_limit_reached = True
    except errors.APIError as error:
        if not receiving:
            raise AdapterFailure("S3 continuity SDK operation failed.") from None
        code_category, reason_category = classify_provider_close(error)
        code = error.code
        observation.provider_close_code = (
            code if isinstance(code, int) and not isinstance(code, bool) else None
        )
        observation.provider_close_code_category = code_category
        observation.provider_close_reason_category = reason_category
        observation.termination_observed = True
        if observation.provider_close_code in (1000, 1001):
            observation.end_reason = "provider_closed"
            observation.provider_closed = True
        else:
            observation.end_reason = "provider_disconnected"
    except Exception as error:
        category: FailureCategory = "sdk_operation_failed"
        if isinstance(error, _ContinuityStop | AdapterFailure):
            category = error.category
        raise AdapterFailure(
            "S3 continuity SDK operation or response validation failed.", category
        ) from None
    finally:
        now = clock()
        observation.observed_seconds = max(0, now - started)
        if last_client_send is not None:
            observation.seconds_since_last_client_send = max(0.0, now - last_client_send)
    return observation


async def observe_pair(client: LiveClient, *, include_pcm: bool = False) -> list[LiveObservation]:
    """Separate connections/configs and handles for the two synthetic text probes."""
    try:
        return [
            await observe_session(client.live, plan, include_pcm=include_pcm)
            for plan in build_plans()
        ]
    finally:
        await _close(client)


async def observe_smoke_pair(
    client: LiveClient, *, close_client: bool = True
) -> list[LiveObservation]:
    """Run two bounded text-only sessions; do not resume or receive past first audio turn."""
    try:
        observations: list[LiveObservation] = []
        for plan in build_plans():
            observation = await observe_session(
                client.live,
                replace(plan, max_seconds=60),
                allow_resumption=False,
                stop_after_first_audio_turn=True,
                max_audio_bytes=SMOKE_AUDIO_BYTES,
            )
            if (
                observation.end_reason != "turn_complete"
                or observation.first_audio_latency_ms is None
                or observation.audio_bytes == 0
                or observation.turn_completions != 1
            ):
                raise AdapterFailure("S3 free-tier smoke did not complete one audio turn.")
            observations.append(observation)
        return observations
    finally:
        if close_client:
            await _close(client)
