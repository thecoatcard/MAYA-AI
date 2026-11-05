"""All provider messages are real SDK types; all I/O clients are fakes."""

import asyncio
import inspect
import io
import json
import math
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from sys import executable
from typing import cast, get_type_hints
from unittest.mock import patch

import pytest
from google.genai import errors, types
from google.genai import live as sdk_live
from google.genai.client import AsyncClient
from google.genai.live import AsyncLive, AsyncSession
from google.genai.models import AsyncModels
from tests.spikes.spike_s3_gemini_live import build_plans, pcm_fixture
from tests.spikes.spike_s4_embeddings import TEXTS

from gemini_spike import adapters as sdk_adapters
from gemini_spike import free_tier_continuity, free_tier_smoke
from gemini_spike.adapters import (
    AdapterFailure,
    EmbeddingClient,
    LiveClient,
    LiveSession,
    embed_synthetic,
    live_config,
    observe_continuity_session,
    observe_pair,
    observe_session,
    observe_smoke_pair,
    synthetic_input_audio,
    validate_pcm,
)


def sdk_structural_contract(client: AsyncClient) -> tuple[EmbeddingClient, LiveClient]:
    """Strict mypy checks SDK substitutability; never called or constructed."""
    return client, client


class FakeModels:
    def __init__(self) -> None:
        self.responses = [
            types.EmbedContentResponse(
                embeddings=[types.ContentEmbedding(values=vector)],
                metadata=types.EmbedContentMetadata(billable_character_count=14),
            )
            for vector in (
                [2.0] + [0.0] * 767,
                [4.0, 3.0] + [0.0] * 766,
                [0.0, 0.0, 1.0] + [0.0] * 765,
            )
        ]
        self.calls: list[tuple[str, str, types.EmbedContentConfig]] = []
        self.error: Exception | None = None
        self.block = False
        self.entered = asyncio.Event()

    async def embed_content(
        self, *, model: str, contents: str, config: types.EmbedContentConfig
    ) -> types.EmbedContentResponse:
        self.calls.append((model, contents, config))
        self.entered.set()
        if self.error:
            raise self.error
        if self.block:
            await asyncio.Event().wait()
        return self.responses[len(self.calls) - 1]


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.models = FakeModels()
        self.closed = 0
        self.close_error = False
        self.close_block = False

    async def aclose(self) -> None:
        self.closed += 1
        if self.close_error:
            raise RuntimeError("private key/endpoint")
        if self.close_block:
            await asyncio.Event().wait()


def audio_message(
    data: bytes | None = None, mime: str = "audio/pcm;rate=24000"
) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(
                role="model",
                parts=[
                    types.Part(
                        inline_data=types.Blob(
                            data=pcm_fixture() if data is None else data, mime_type=mime
                        )
                    )
                ],
            )
        )
    )


def resume_message(handle: str | None, resumable: bool = True) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        session_resumption_update=types.LiveServerSessionResumptionUpdate(
            new_handle=handle, resumable=resumable
        )
    )


TURN_COMPLETE = types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))
GO_AWAY = types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="10s"))


class FakeSession:
    def __init__(self, batches: list[list[types.LiveServerMessage]] | None = None) -> None:
        self.setup_complete: types.LiveServerSetupComplete | None = types.LiveServerSetupComplete()
        self.batches = batches if batches is not None else [[audio_message(), TURN_COMPLETE]]
        self.sends: list[tuple[str, str | types.Content | types.Blob | bool | None]] = []
        self.error_stage: str | None = None
        self.closed_generators = 0
        self.entered = asyncio.Event()
        self.block = False
        self.receive_error: Exception | None = None
        self.block_send = False
        self.send_error: Exception | None = None

    async def send_client_content(
        self, *, turns: types.Content, turn_complete: bool = True
    ) -> None:
        self.sends.extend([("client", turns), ("turn_complete", turn_complete)])
        if self.send_error:
            raise self.send_error
        if self.block_send:
            self.entered.set()
            await asyncio.Event().wait()
        if self.error_stage == "send":
            raise RuntimeError("private prompt/token")

    async def send_realtime_input(
        self,
        *,
        text: str | None = None,
        audio: types.Blob | None = None,
        audio_stream_end: bool | None = None,
    ) -> None:
        self.sends.append(("realtime", text if text is not None else audio or audio_stream_end))
        if self.error_stage == "send":
            raise RuntimeError("private prompt/token")

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        try:
            self.entered.set()
            if self.error_stage == "receive":
                raise RuntimeError("private response/token")
            if self.receive_error:
                raise self.receive_error
            if self.block:
                await asyncio.Event().wait()
            for message in self.batches.pop(0) if self.batches else []:
                yield message
        finally:
            self.closed_generators += 1


class FakeLive:
    def __init__(self, sessions: list[FakeSession] | None = None) -> None:
        self.sessions = sessions if sessions is not None else [FakeSession(), FakeSession()]
        self.configs: list[types.LiveConnectConfig] = []
        self.models: list[str] = []
        self.closed = 0
        self.error = False
        self.close_error = False
        self.block_connect = False
        self.entered = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.block_close = False

    @asynccontextmanager
    async def connect(
        self, *, model: str, config: types.LiveConnectConfig
    ) -> AsyncIterator[LiveSession]:
        self.configs.append(config)
        self.models.append(model)
        self.entered.set()
        if self.error:
            raise RuntimeError("private URL/key")
        if self.block_connect:
            await asyncio.Event().wait()
        session = self.sessions.pop(0)
        try:
            yield session
        finally:
            self.close_entered.set()
            if self.block_close:
                await self.close_release.wait()
            self.closed += 1
            if self.close_error:
                raise RuntimeError("private close details")


class FakeLiveClient:
    def __init__(self, live: FakeLive | None = None) -> None:
        self.live = live if live is not None else FakeLive()
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


class FakeSdkClient:
    def __init__(self, live: FakeLive | None = None) -> None:
        self.models = FakeModels()
        self.live = live if live is not None else FakeLive()
        self.aio = self
        self.async_closed = 0
        self.sync_closed = 0
        self.async_close_error = False

    async def aclose(self) -> None:
        self.async_closed += 1
        if self.async_close_error:
            raise RuntimeError("private async close detail")

    def close(self) -> None:
        self.sync_closed += 1


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_installed_sdk_signatures_and_types() -> None:
    assert version("google-genai") == "2.34.0"
    assert version("websockets") == "17.4"
    assert get_type_hints(AsyncModels.embed_content)["return"] is types.EmbedContentResponse
    assert inspect.isasyncgenfunction(AsyncSession.receive)
    assert "model" in inspect.signature(AsyncLive.connect).parameters
    assert "turns" in inspect.signature(AsyncSession.send_client_content).parameters
    for argument in ("text", "audio", "audio_stream_end"):
        assert argument in inspect.signature(AsyncSession.send_realtime_input).parameters
    config = live_config()
    assert config.response_modalities == [types.Modality.AUDIO]
    assert config.context_window_compression is None
    assert config.session_resumption is not None
    assert config.session_resumption.handle is None


async def test_real_embedding_request_and_analyzer() -> None:
    client = FakeEmbeddingClient()
    result = await embed_synthetic(client)
    assert len(client.models.calls) == 3
    assert [contents for _, contents, _ in client.models.calls] == list(TEXTS)
    for model, _, config in client.models.calls:
        assert model == "gemini-embedding-2"
        assert config.output_dimensionality == 768
        assert config.task_type is None
    assert result.metrics.raw_norms == [2.0, 5.0, 1.0]
    assert result.metrics.related_cosine == pytest.approx(0.8)
    assert result.billable_character_count == 42
    assert client.closed == 1


@pytest.mark.parametrize(
    "values",
    [None, [], [0.0] * 768, [math.nan] * 768, [math.inf] * 768, [1.0] * 767, [1e100] * 768],
)
async def test_embedding_malformed_vectors_are_redacted(values: list[float] | None) -> None:
    client = FakeEmbeddingClient()
    client.models.responses = [
        types.EmbedContentResponse(embeddings=[types.ContentEmbedding(values=values)])
    ] * 3
    with pytest.raises(AdapterFailure, match="S4 SDK") as error:
        await embed_synthetic(client)
    assert error.value.__suppress_context__
    assert client.closed == 1


@pytest.mark.parametrize("embeddings", [None, []])
async def test_embedding_missing_vectors(
    embeddings: list[types.ContentEmbedding] | None,
) -> None:
    client = FakeEmbeddingClient()
    client.models.responses = [types.EmbedContentResponse(embeddings=embeddings)] * 3
    with pytest.raises(AdapterFailure):
        await embed_synthetic(client)
    assert client.closed == 1


async def test_embedding_request_failure_and_optional_usage() -> None:
    client = FakeEmbeddingClient()
    client.models.responses[1].metadata = None
    assert (await embed_synthetic(client)).billable_character_count is None
    client = FakeEmbeddingClient()
    client.models.error = RuntimeError("private URL/key/text")
    with pytest.raises(AdapterFailure) as error:
        await embed_synthetic(client)
    assert "private" not in str(error.value)
    assert client.closed == 1


async def test_embedding_extra_vectors_are_not_silently_discarded() -> None:
    client = FakeEmbeddingClient()
    client.models.responses[0].embeddings = [types.ContentEmbedding(values=[1.0] * 768)] * 2
    with pytest.raises(AdapterFailure):
        await embed_synthetic(client)
    assert len(client.models.calls) == 1 and client.closed == 1


async def test_embedding_late_malformed_response_does_not_return_partial_metrics() -> None:
    client = FakeEmbeddingClient()
    client.models.responses[2].embeddings = []
    with pytest.raises(AdapterFailure):
        await embed_synthetic(client)
    assert len(client.models.calls) == 3 and client.closed == 1


async def test_two_text_methods_use_separate_sessions_and_optional_pcm() -> None:
    first, second = FakeSession(), FakeSession()
    connector = FakeLive([first, second])
    client = FakeLiveClient(connector)
    results = await observe_pair(client, include_pcm=True)
    assert [r.method for r in results] == ["send_client_content", "send_realtime_input"]
    assert all(r.end_reason == "provider_closed" for r in results)
    assert all(r.audio_duration_ms == 100 for r in results)
    assert all(not r.live_acceptance_verified for r in results)
    assert first.sends[0][0] == "client"
    turns = first.sends[0][1]
    assert isinstance(turns, types.Content)
    assert turns.role == "user" and turns.parts == [types.Part(text=build_plans()[0].prompt)]
    assert first.sends[1] == ("turn_complete", True)
    assert second.sends[0] == ("realtime", build_plans()[1].prompt)
    blob = second.sends[1][1]
    assert isinstance(blob, types.Blob)
    assert blob.mime_type == "audio/pcm;rate=16000"
    assert blob.data == synthetic_input_audio()
    assert len(blob.data) == 3200
    assert second.sends[2] == ("realtime", True)
    assert connector.configs[0] is not connector.configs[1]
    assert all(
        c.session_resumption and c.session_resumption.handle is None for c in connector.configs
    )
    assert connector.closed == 2 and client.closed == 1


async def test_monotonic_latency_usage_and_receive_across_turns() -> None:
    session = FakeSession(
        [
            [audio_message(), TURN_COMPLETE],
            [
                types.LiveServerMessage(
                    usage_metadata=types.UsageMetadata(
                        prompt_token_count=3, response_token_count=5, total_token_count=8
                    )
                )
            ],
        ]
    )
    ticks = iter([100.0, 100.1, 100.225, 101.0])
    result = await observe_session(FakeLive([session]), build_plans()[0], clock=lambda: next(ticks))
    assert result.first_audio_latency_ms == pytest.approx(125)
    assert result.observed_seconds == 1
    assert result.turn_completions == 1
    assert result.usage[0].total == 8
    assert session.closed_generators == 3  # turn, usage, EOF; not one-turn lifetime.


async def test_latest_handle_is_used_without_replaying_prompt() -> None:
    first = FakeSession(
        [
            [
                resume_message("older-private-handle"),
                resume_message("latest-private-handle"),
                audio_message(),
                TURN_COMPLETE,
                GO_AWAY,
            ]
        ]
    )
    second = FakeSession([[resume_message("newest-private-handle"), GO_AWAY]])
    third = FakeSession()
    connector = FakeLive([first, second, third])
    result = await observe_session(connector, build_plans()[0])
    assert [
        c.session_resumption.handle if c.session_resumption else None for c in connector.configs
    ] == [None, "latest-private-handle", "newest-private-handle"]
    assert result.go_away_events == 2 and result.resumptions == 2
    assert not result.context_continuity_verified
    assert "private-handle" not in repr(result)
    assert not second.sends and not third.sends
    assert connector.closed == 3 and first.closed_generators == 1


@pytest.mark.parametrize("update", [resume_message(None), resume_message("stale", False)])
async def test_unresumable_update_invalidates_older_handle(
    update: types.LiveServerMessage,
) -> None:
    connector = FakeLive([FakeSession([[resume_message("old"), update, GO_AWAY]])])
    result = await observe_session(connector, build_plans()[0])
    assert result.end_reason == "goaway_without_handle"
    assert result.resumptions == 0 and connector.closed == 1


async def test_reconnects_are_bounded() -> None:
    connector = FakeLive([FakeSession([[resume_message("private"), GO_AWAY]]) for _ in range(5)])
    result = await observe_session(connector, build_plans()[0])
    assert result.end_reason == "resumption_limit"
    assert result.resumptions == 4 and len(connector.configs) == 5


@pytest.mark.parametrize("method_index", [0, 1])
@pytest.mark.parametrize("stage", ["connect", "send", "receive", "close"])
async def test_live_failures_are_redacted_and_closed(stage: str, method_index: int) -> None:
    session = FakeSession()
    session.error_stage = stage
    connector = FakeLive([session])
    connector.error = stage == "connect"
    connector.close_error = stage == "close"
    with pytest.raises(AdapterFailure) as error:
        await observe_session(connector, build_plans()[method_index])
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__
    assert connector.closed == (0 if stage == "connect" else 1)


async def test_pair_closes_client_after_failure() -> None:
    client = FakeLiveClient()
    client.live.error = True
    with pytest.raises(AdapterFailure):
        await observe_pair(client)
    assert client.closed == 1


@pytest.mark.parametrize(
    "mime,data",
    [
        ("audio/wav", b"\0\0"),
        ("audio/pcm;rate=16000", b"\0\0"),
        ("audio/pcm;rate=24000", b"x"),
        ("audio/pcm;rate=24000", b""),
    ],
)
async def test_invalid_provider_audio_fails(mime: str, data: bytes) -> None:
    connector = FakeLive([FakeSession([[audio_message(data, mime)]])])
    with pytest.raises(AdapterFailure, match="validation"):
        await observe_session(connector, build_plans()[0])
    assert connector.closed == 1


@pytest.mark.parametrize("data,rate", [(b"", 16000), (b"x", 16000), (b"xx", 44100)])
def test_invalid_pcm_rejected(data: bytes, rate: int) -> None:
    with pytest.raises(ValueError):
        validate_pcm(data, sample_rate=rate)


@pytest.mark.parametrize("stage", ["connect", "send", "receive", "embedding"])
async def test_cancellation_closes_owned_resources(stage: str) -> None:
    if stage == "embedding":
        embedding_client = FakeEmbeddingClient()
        embedding_client.models.block = True
        task = asyncio.create_task(embed_synthetic(embedding_client))
        await embedding_client.models.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert embedding_client.closed == 1
        return
    session = FakeSession()
    session.block = True
    connector = FakeLive([session])
    connector.block_connect = stage == "connect"
    client = FakeLiveClient(connector)
    if stage == "send":
        session.block_send = True
    live_task = asyncio.create_task(observe_pair(client))
    await (connector.entered if stage == "connect" else session.entered).wait()
    live_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await live_task
    assert client.closed == 1
    assert connector.closed == (0 if stage == "connect" else 1)


def expire_on_next_tick(seconds: float | None) -> asyncio.Timeout:
    # Event-loop scheduling, not elapsed wall-clock sleeps. Do not expire cleanup.
    deadline = asyncio.Timeout(None)
    if seconds != 2.0:
        loop = asyncio.get_running_loop()
        loop.call_soon(deadline.reschedule, loop.time())
    return deadline


@pytest.mark.parametrize("stage", ["connect", "send", "receive"])
async def test_observation_timeout_is_not_provider_termination(stage: str) -> None:
    session = FakeSession()
    session.block = True
    connector = FakeLive([session])
    connector.block_connect = stage == "connect"
    session.block_send = stage == "send"
    with patch("gemini_spike.adapters.asyncio.timeout", expire_on_next_tick):
        result = await observe_session(connector, build_plans()[0])
    assert result.end_reason == "observation_timeout"
    assert connector.closed == (0 if stage == "connect" else 1)
    assert session.closed_generators == (1 if stage == "receive" else 0)
    assert result.first_audio_latency_ms is None


async def test_embedding_timeout_closes_client() -> None:
    client = FakeEmbeddingClient()
    client.models.block = True
    with patch("gemini_spike.adapters.asyncio.timeout", expire_on_next_tick):
        with pytest.raises(AdapterFailure):
            await embed_synthetic(client)
    assert client.closed == 1


async def test_embedding_per_request_timeout_stops_before_next_request() -> None:
    client = FakeEmbeddingClient()
    client.models.block = True

    def expire_one_request(seconds: float | None) -> asyncio.Timeout:
        deadline = asyncio.Timeout(None)
        if seconds == 0.01:
            loop = asyncio.get_running_loop()
            loop.call_soon(deadline.reschedule, loop.time())
        return deadline

    with patch("gemini_spike.adapters.asyncio.timeout", expire_one_request):
        with pytest.raises(AdapterFailure):
            await embed_synthetic(
                client,
                max_seconds=10,
                request_timeout_seconds=0.01,
            )
    assert len(client.models.calls) == 1
    assert client.closed == 1


@pytest.mark.parametrize("code", [1000, 1001, 1006, 403, 429])
async def test_sdk_websocket_error_codes_do_not_fake_success(code: int) -> None:
    session = FakeSession()
    session.receive_error = errors.APIError(code, {"message": "private reason/key"})
    connector = FakeLive([session])
    if code in (1000, 1001):
        result = await observe_session(connector, build_plans()[0])
        assert result.end_reason == "provider_closed"
        assert result.first_audio_latency_ms is None
        assert not result.live_acceptance_verified
    else:
        with pytest.raises(AdapterFailure):
            await observe_session(connector, build_plans()[0])
    assert connector.closed == 1


async def test_normal_close_code_during_send_is_still_a_failed_send() -> None:
    session = FakeSession()
    session.send_error = errors.APIError(1000, {"message": "private reason/key"})
    connector = FakeLive([session])
    with pytest.raises(AdapterFailure):
        await observe_session(connector, build_plans()[0])
    assert connector.closed == 1


async def test_missing_setup_confirmation_never_sends_probe() -> None:
    session = FakeSession()
    session.setup_complete = None
    connector = FakeLive([session])
    with pytest.raises(AdapterFailure):
        await observe_session(connector, build_plans()[0])
    assert not session.sends and connector.closed == 1


async def test_provider_timeout_error_is_not_observation_expiry() -> None:
    session = FakeSession()
    session.receive_error = TimeoutError("private URL")
    with pytest.raises(AdapterFailure):
        await observe_session(FakeLive([session]), build_plans()[0])


async def test_no_arbitrary_text_or_compression() -> None:
    connector = FakeLive()
    for plan in (
        replace(build_plans()[0], prompt="not the fixture"),
        replace(build_plans()[0], context_compression=True),
    ):
        with pytest.raises(ValueError):
            await observe_session(connector, plan)
    assert not connector.configs


@pytest.mark.parametrize("value", [0.0, -1.0, math.nan, math.inf, 61.0])
async def test_invalid_embedding_timeout_never_calls_models(value: float) -> None:
    client = FakeEmbeddingClient()
    with pytest.raises(ValueError):
        await embed_synthetic(client, max_seconds=value)
    assert not client.models.calls and client.closed == 1


async def test_client_cleanup_failure_is_redacted() -> None:
    client = FakeEmbeddingClient()
    client.close_error = True
    with pytest.raises(AdapterFailure, match="cleanup") as error:
        await embed_synthetic(client)
    assert "private" not in str(error.value)


async def test_client_cleanup_is_bounded() -> None:
    client = FakeEmbeddingClient()
    client.close_block = True
    with patch(
        "gemini_spike.adapters.asyncio.timeout",
        side_effect=lambda seconds: asyncio.Timeout(asyncio.get_running_loop().time()),
    ):
        with pytest.raises(AdapterFailure, match="cleanup"):
            await embed_synthetic(client)
    assert client.closed == 1


async def test_cancellation_during_socket_cleanup_does_not_abandon_close() -> None:
    connector = FakeLive([FakeSession([])])
    connector.block_close = True
    client = FakeLiveClient(connector)
    task = asyncio.create_task(observe_pair(client))
    await connector.close_entered.wait()
    task.cancel()
    connector.close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert connector.closed == 1 and client.closed == 1


async def test_socket_cleanup_is_bounded() -> None:
    connector = FakeLive([FakeSession([])])
    connector.block_close = True
    with patch(
        "gemini_spike.adapters.asyncio.timeout",
        side_effect=lambda seconds: asyncio.Timeout(asyncio.get_running_loop().time()),
    ):
        with pytest.raises(AdapterFailure):
            await observe_session(connector, build_plans()[0])
    assert connector.close_entered.is_set()


async def test_output_audio_volume_is_bounded() -> None:
    connector = FakeLive([FakeSession([[audio_message()]])])
    with patch("gemini_spike.adapters.MAX_AUDIO_BYTES", 2):
        with pytest.raises(AdapterFailure):
            await observe_session(connector, build_plans()[0])
    assert connector.closed == 1


async def test_invalid_synthetic_pcm_fails_before_audio_send() -> None:
    session = FakeSession()
    connector = FakeLive([session])
    with patch("gemini_spike.adapters.synthetic_input_audio", return_value=b"x"):
        with pytest.raises(AdapterFailure):
            await observe_session(connector, build_plans()[0], include_pcm=True)
    assert all(not isinstance(value, types.Blob) for _, value in session.sends)
    assert connector.closed == 1


async def test_free_tier_smoke_uses_bounded_requests_and_redacts_evidence(tmp_path: Path) -> None:
    first_session = FakeSession([[audio_message(), TURN_COMPLETE, GO_AWAY]])
    second_session = FakeSession([[audio_message(), TURN_COMPLETE, GO_AWAY]])
    embedding_client = FakeSdkClient()
    live_connector = FakeLive([first_session, second_session])
    live_client = FakeSdkClient(live_connector)
    clients = [embedding_client, live_client]
    supplied_keys: list[str] = []
    test_key = "synthetic-test-key-not-a-credential"

    def factory(key: str) -> FakeSdkClient:
        supplied_keys.append(key)
        return clients.pop(0)

    evidence_path = tmp_path / "smoke.json"
    completed, evidence = await free_tier_smoke.run_smoke(
        test_key,
        evidence_path,
        client_factory=factory,
    )

    assert completed
    assert evidence["status"] == "completed"
    assert supplied_keys == [test_key, test_key]
    assert len(embedding_client.models.calls) == 3
    assert all(call[1] in TEXTS for call in embedding_client.models.calls)
    assert all(call[2].output_dimensionality == 768 for call in embedding_client.models.calls)
    assert len(live_connector.configs) == 2
    assert all(config.session_resumption is None for config in live_connector.configs)
    assert first_session.closed_generators == second_session.closed_generators == 1
    assert live_connector.closed == 2
    assert embedding_client.async_closed == embedding_client.sync_closed == 1
    assert live_client.async_closed == live_client.sync_closed == 1
    serialized = evidence_path.read_text(encoding="utf-8")
    assert test_key not in serialized
    assert test_key not in repr(evidence)
    assert "synthetic audio test" not in serialized
    assert "private-handle" not in serialized
    report = json.loads(serialized)
    assert report["account_scope"]["programmatically_verified"] is False
    assert report["account_scope"]["provider_cost_cap_enforced"] is False
    assert report["live_acceptance_verified"] is False
    assert report["paid_calls_authorized"] is False
    assert report["observations"]["s4_embeddings"]["metrics"]["dimensions"] == [768] * 3
    assert [item["end_reason"] for item in report["observations"]["s3_live_sessions"]] == [
        "turn_complete",
        "turn_complete",
    ]


async def test_free_tier_smoke_fails_on_provider_quota_without_retry_or_fallback(
    tmp_path: Path,
) -> None:
    client = FakeSdkClient()
    client.models.error = errors.APIError(429, {"message": "private key and quota detail"})
    factory_calls = 0

    def factory(key: str) -> FakeSdkClient:
        nonlocal factory_calls
        factory_calls += 1
        return client

    evidence_path = tmp_path / "failed.json"
    completed, evidence = await free_tier_smoke.run_smoke(
        "synthetic-test-key",
        evidence_path,
        client_factory=factory,
    )
    serialized = evidence_path.read_text(encoding="utf-8")
    assert not completed and evidence["status"] == "failed"
    assert evidence["failure"] == {
        "stage": "s4_embeddings",
        "code": "operation_failed",
    }
    assert len(client.models.calls) == 1
    assert factory_calls == 1
    assert client.async_closed == client.sync_closed == 1
    assert "private key" not in serialized and "quota detail" not in serialized
    assert "synthetic-test-key" not in serialized


async def test_live_failure_writes_failed_partial_evidence_and_stops(tmp_path: Path) -> None:
    embedding_client = FakeSdkClient()
    connector = FakeLive([FakeSession(), FakeSession()])
    connector.error = True
    live_client = FakeSdkClient(connector)
    clients = [embedding_client, live_client]
    evidence_path = tmp_path / "partial.json"

    completed, evidence = await free_tier_smoke.run_smoke(
        "synthetic-test-key",
        evidence_path,
        client_factory=lambda _key: clients.pop(0),
    )

    assert not completed and evidence["status"] == "failed"
    assert evidence["failure"] == {
        "stage": "s3_live_sessions",
        "code": "operation_failed",
    }
    assert len(connector.configs) == 1
    assert embedding_client.async_closed == embedding_client.sync_closed == 1
    assert live_client.async_closed == live_client.sync_closed == 1
    serialized = evidence_path.read_text(encoding="utf-8")
    report = json.loads(serialized)
    assert "private URL/key" not in serialized
    assert "synthetic-test-key" not in serialized
    assert report["observations"]["s4_embeddings"]["metrics"]["dimensions"] == [768] * 3


async def test_async_cleanup_failure_still_closes_sync_client(tmp_path: Path) -> None:
    client = FakeSdkClient()
    client.async_close_error = True
    completed, evidence = await free_tier_smoke.run_smoke(
        "synthetic-test-key",
        tmp_path / "cleanup-failure.json",
        client_factory=lambda _key: client,
    )
    assert not completed and evidence["status"] == "failed"
    assert evidence["failure"] == {
        "stage": "s4_embeddings",
        "code": "operation_failed",
    }
    assert client.async_closed == client.sync_closed == 1
    assert "private async close detail" not in repr(evidence)


async def test_free_tier_smoke_total_timeout_is_failed_and_closes_both_clients(
    tmp_path: Path,
) -> None:
    client = FakeSdkClient()
    client.models.block = True
    with patch.object(free_tier_smoke, "OPERATION_DEADLINE_SECONDS", 0.001):
        completed, evidence = await free_tier_smoke.run_smoke(
            "synthetic-test-key",
            tmp_path / "timeout.json",
            client_factory=lambda _key: client,
        )
    assert not completed
    assert evidence["status"] == "failed"
    assert evidence["failure"] == {
        "stage": "s4_embeddings",
        "code": "total_deadline_exceeded",
    }
    assert len(client.models.calls) == 1
    assert client.async_closed == client.sync_closed == 1


async def test_smoke_session_stops_at_first_turn_without_resumption_or_reconnect() -> None:
    first = FakeSession([[audio_message(), TURN_COMPLETE, GO_AWAY]])
    second = FakeSession([[audio_message(), TURN_COMPLETE, GO_AWAY]])
    connector = FakeLive([first, second])
    observations = await observe_smoke_pair(FakeLiveClient(connector), close_client=False)
    assert len(observations) == 2
    assert len(connector.configs) == 2
    assert all(config.session_resumption is None for config in connector.configs)
    assert first.closed_generators == second.closed_generators == 1
    assert connector.closed == 2


async def test_smoke_session_timeout_does_not_reconnect() -> None:
    session = FakeSession()
    session.block = True
    connector = FakeLive([session, FakeSession()])
    with patch("gemini_spike.adapters.asyncio.timeout", expire_on_next_tick):
        result = await observe_session(
            connector,
            replace(build_plans()[0], max_seconds=60),
            allow_resumption=False,
            stop_after_first_audio_turn=True,
        )
    assert result.end_reason == "observation_timeout"
    assert len(connector.configs) == 1
    assert connector.configs[0].session_resumption is None
    assert connector.closed == 1


def transcription_message(
    text: str, *, finished: bool = True, turn_complete: bool = True
) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(
            output_transcription=types.Transcription(text=text, finished=finished),
            turn_complete=turn_complete,
        )
    )


def continuity_audio_message(
    data: bytes = b"\x00\x00",
    *,
    mime: str = "audio/pcm;rate=24000",
) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(
                role="model",
                parts=[types.Part(inline_data=types.Blob(data=data, mime_type=mime))],
            )
        )
    )


def continuity_audio_turn_message(
    data: bytes = b"\x00\x00",
) -> types.LiveServerMessage:
    message = continuity_audio_message(data)
    assert message.server_content is not None
    message.server_content.turn_complete = True
    return message


def continuity_sessions(nonce: str) -> list[FakeSession]:
    # The initial turn completes before GoAway; the adapter must keep receiving
    # on that connection rather than treating one completed turn as lifetime.
    first = FakeSession(
        [
            [
                transcription_message("ready.", finished=True),
                continuity_audio_turn_message(),
            ],
            [resume_message("handle-one"), GO_AWAY],
        ]
    )
    resumed = FakeSession(
        [
            [
                transcription_message(
                    " ".join(nonce.upper().split()[:3]),
                    finished=False,
                    turn_complete=False,
                ),
                continuity_audio_message(),
                continuity_audio_turn_message(),
                transcription_message(
                    " ".join(nonce.split()[3:]) + ".",
                    finished=True,
                    turn_complete=False,
                ),
            ],
            [resume_message("handle-two"), GO_AWAY],
        ]
    )
    return [first, resumed, FakeSession([])]


@pytest.mark.parametrize("method_index", [0, 1])
async def test_continuity_nonce_is_recalled_after_resumed_setup_and_observes_end(
    method_index: int,
) -> None:
    plan = build_plans()[method_index]
    nonce = "amber cedar violet harbor maple silver"
    first, resumed, final = continuity_sessions(nonce)
    connector = FakeLive([first, resumed, final])

    result = await observe_continuity_session(connector, plan, nonce=nonce)

    assert result.method == plan.method
    assert result.end_reason == "provider_closed"
    assert result.resumptions == 2 and result.go_away_events == 2
    assert result.goaway_observed and result.resumption_handle_observed
    assert result.resumed_setup_observed
    assert result.continuity_probe_completed and result.context_continuity_verified
    assert result.provider_closed and result.termination_observed
    assert not result.observation_limit_reached
    assert first.closed_generators == 2 and resumed.closed_generators == 2
    assert final.closed_generators == 1
    assert all(config.response_modalities == [types.Modality.AUDIO] for config in connector.configs)
    assert all(
        isinstance(config.output_audio_transcription, types.AudioTranscriptionConfig)
        for config in connector.configs
    )
    assert all(config.tools is None for config in connector.configs)
    handles = []
    for config in connector.configs:
        assert config.session_resumption is not None
        handles.append(config.session_resumption.handle)
    assert handles == [None, "handle-one", "handle-two"]
    assert nonce in repr(first.sends)
    assert nonce not in repr(resumed.sends)
    assert resumed.sends
    assert "handle-" not in repr(result) and nonce not in repr(result)


async def test_continuity_stops_on_nonce_mismatch_without_claiming_success() -> None:
    nonce = "amber cedar violet harbor maple silver"
    first = FakeSession([[resume_message("private-handle"), GO_AWAY]])
    resumed = FakeSession([[transcription_message("wrong response")]])
    connector = FakeLive([first, resumed, FakeSession([])])

    result = await observe_continuity_session(connector, build_plans()[0], nonce=nonce)

    assert result.end_reason == "continuity_mismatch"
    assert result.continuity_probe_completed and not result.context_continuity_verified
    assert not result.termination_observed and not result.provider_closed
    assert len(connector.configs) == 2
    assert "private-handle" not in repr(result) and nonce not in repr(result)


@pytest.mark.parametrize(
    "transcript",
    [
        "amber cedar violet harbor maple",  # partial nonce is not enough
        "amber cedar violet harbor maple silver extra",  # no substring acceptance
        "amber cedar violet harbor maple s1lver",  # near match is not exact
    ],
)
async def test_continuity_requires_full_nonce_not_a_transcript_substring(
    transcript: str,
) -> None:
    nonce = "amber cedar violet harbor maple silver"
    connector = FakeLive(
        [
            FakeSession([[resume_message("handle-one"), GO_AWAY]]),
            FakeSession([[transcription_message(transcript)]]),
        ]
    )
    result = await observe_continuity_session(connector, build_plans()[0], nonce=nonce)
    assert result.continuity_probe_completed
    assert not result.context_continuity_verified
    assert result.end_reason == "continuity_mismatch"


async def test_continuity_waits_for_finished_transcription() -> None:
    nonce = "amber cedar violet harbor maple silver"
    connector = FakeLive(
        [
            FakeSession([[resume_message("handle-one"), GO_AWAY]]),
            FakeSession(
                [
                    [
                        transcription_message(nonce, finished=False, turn_complete=True),
                        resume_message("handle-two"),
                        GO_AWAY,
                    ]
                ]
            ),
            FakeSession([]),
        ]
    )
    result = await observe_continuity_session(connector, build_plans()[0], nonce=nonce)
    assert result.end_reason == "provider_closed"
    assert not result.continuity_probe_completed
    assert not result.context_continuity_verified


async def test_continuity_stops_on_goaway_without_resumable_handle() -> None:
    connector = FakeLive([FakeSession([[resume_message(None), GO_AWAY]]), FakeSession([])])
    result = await observe_continuity_session(
        connector,
        build_plans()[0],
        nonce="amber cedar violet harbor maple silver",
    )
    assert result.end_reason == "goaway_without_handle"
    assert result.goaway_observed and not result.resumption_handle_observed
    assert result.resumptions == 0 and not result.resumed_setup_observed
    assert len(connector.configs) == 1


@pytest.mark.parametrize(
    "nonce",
    ["", "private arbitrary text", "amber cedar violet harbor maple unknown"],
)
async def test_malformed_continuity_nonce_fails_before_connect(nonce: str) -> None:
    connector = FakeLive()
    with pytest.raises(ValueError, match="nonce is malformed"):
        await observe_continuity_session(connector, build_plans()[0], nonce=nonce)
    assert not connector.configs


async def test_continuity_resumptions_stop_at_four_without_claiming_termination() -> None:
    nonce = "amber cedar violet harbor maple silver"
    sessions = [
        FakeSession([[resume_message("handle-zero"), GO_AWAY]]),
        FakeSession(
            [
                [
                    continuity_audio_message(),
                    transcription_message(nonce),
                    resume_message("handle-one"),
                    GO_AWAY,
                ]
            ]
        ),
        FakeSession([[resume_message("handle-two"), GO_AWAY]]),
        FakeSession([[resume_message("handle-three"), GO_AWAY]]),
        FakeSession([[resume_message("handle-four"), GO_AWAY]]),
    ]
    connector = FakeLive(sessions)

    result = await observe_continuity_session(connector, build_plans()[0], nonce=nonce)

    assert result.end_reason == "resumption_limit"
    assert result.resumptions == 4 and len(connector.configs) == 5
    assert result.context_continuity_verified
    assert not result.termination_observed and not result.provider_closed
    assert not result.live_acceptance_verified
    assert "handle-" not in repr(result) and nonce not in repr(result)


async def test_continuity_timeout_is_distinct_from_provider_closure() -> None:
    session = FakeSession()
    session.block = True
    connector = FakeLive([session])
    with patch("gemini_spike.adapters.asyncio.timeout", expire_on_next_tick):
        result = await observe_continuity_session(
            connector,
            build_plans()[0],
            nonce="amber cedar violet harbor maple silver",
        )
    assert result.end_reason == "observation_timeout"
    assert result.observation_limit_reached
    assert not result.provider_closed and not result.termination_observed
    assert not result.context_continuity_verified
    assert len(connector.configs) == 1 and connector.closed == 1


async def test_continuity_rejects_audio_exceeding_bound() -> None:
    from gemini_spike.adapters import CONTINUITY_MAX_AUDIO_BYTES

    session = FakeSession([[continuity_audio_message(b"\0" * (CONTINUITY_MAX_AUDIO_BYTES + 2))]])
    connector = FakeLive([session])
    with pytest.raises(AdapterFailure) as error:
        await observe_continuity_session(
            connector,
            build_plans()[0],
            nonce="amber cedar violet harbor maple silver",
        )
    assert isinstance(error.value.__context__, ValueError)
    assert "audio bound" in str(error.value.__context__)
    assert connector.closed == 1


@pytest.mark.parametrize(
    "message",
    [
        continuity_audio_message(b""),
        continuity_audio_message(b"x"),
        continuity_audio_message(b"\0\0", mime="audio/wav"),
        continuity_audio_message(b"\0\0", mime="audio/pcm;rate=16000"),
    ],
)
async def test_continuity_rejects_malformed_audio_output(
    message: types.LiveServerMessage,
) -> None:
    connector = FakeLive([FakeSession([[message]])])
    with pytest.raises(AdapterFailure, match="response validation"):
        await observe_continuity_session(
            connector,
            build_plans()[0],
            nonce="amber cedar violet harbor maple silver",
        )
    assert connector.closed == 1


async def test_continuity_rejects_transcription_exceeding_bound() -> None:
    from gemini_spike.adapters import CONTINUITY_MAX_RESPONSE_BYTES

    nonce = "amber cedar violet harbor maple silver"
    first = FakeSession([[resume_message("handle-one"), GO_AWAY]])
    resumed = FakeSession([[transcription_message("x" * (CONTINUITY_MAX_RESPONSE_BYTES + 1))]])
    connector = FakeLive([first, resumed])
    with pytest.raises(AdapterFailure) as error:
        await observe_continuity_session(connector, build_plans()[0], nonce=nonce)
    assert isinstance(error.value.__context__, ValueError)
    assert "transcription bound" in str(error.value.__context__)
    assert len(connector.configs) == 2


async def test_continuity_records_abnormal_provider_disconnect_code_redacted() -> None:
    session = FakeSession()
    session.receive_error = errors.APIError(1011, {"message": "private disconnect detail"})
    connector = FakeLive([session])
    result = await observe_continuity_session(
        connector,
        build_plans()[0],
        nonce="amber cedar violet harbor maple silver",
    )
    assert result.end_reason == "provider_disconnected"
    assert result.provider_close_code == 1011
    assert result.termination_observed and not result.provider_closed
    assert "private disconnect detail" not in repr(result)


async def test_continuity_launcher_writes_redacted_evidence_for_two_sessions(
    tmp_path: Path,
) -> None:
    nonces = [
        "amber cedar violet harbor maple silver",
        "birch dawn elm fern glade harbor",
    ]
    created_clients = [
        FakeSdkClient(FakeLive(continuity_sessions(nonces[0]))),
        FakeSdkClient(FakeLive(continuity_sessions(nonces[1]))),
    ]
    clients = list(created_clients)
    remaining_nonces = iter(nonces)
    supplied_keys: list[str] = []

    def factory(key: str) -> FakeSdkClient:
        supplied_keys.append(key)
        return clients.pop(0)

    output = tmp_path / "continuity.json"
    completed, evidence = await free_tier_continuity.run_continuity_observation(
        "synthetic-test-key",
        output,
        client_factory=factory,
        nonce_factory=lambda: next(remaining_nonces),
    )
    serialized = output.read_text(encoding="utf-8")
    assert completed and evidence["status"] == "completed"
    assert evidence["live_acceptance_verified"] is False
    assert evidence["paid_calls_authorized"] is False
    observations = evidence["observations"]
    assert isinstance(observations, list)
    assert len(observations) == 2
    assert supplied_keys == ["synthetic-test-key", "synthetic-test-key"]
    assert "synthetic-test-key" not in serialized
    assert all(nonce not in serialized for nonce in nonces)
    assert "synthetic acknowledgement" not in serialized
    assert "amber cedar" not in serialized
    assert "transcription_bytes" in serialized and "audio_bytes" in serialized
    for item in observations:
        assert isinstance(item, dict)
        assert item["audio_bytes"] == 6
        assert isinstance(item["transcription_bytes"], int)
        assert item["transcription_bytes"] > 0
    assert "handle-" not in serialized
    assert all(client.async_closed == 1 and client.sync_closed == 1 for client in created_clients)


async def test_continuity_launcher_stops_when_first_session_lacks_continuity(
    tmp_path: Path,
) -> None:
    nonce = "amber cedar violet harbor maple silver"
    first_client = FakeSdkClient(
        FakeLive(
            [
                FakeSession([[resume_message("private-handle"), GO_AWAY]]),
                FakeSession([[transcription_message("wrong response")]]),
                FakeSession([]),
            ]
        )
    )
    requested_keys: list[str] = []

    def factory(key: str) -> FakeSdkClient:
        requested_keys.append(key)
        return first_client

    output = tmp_path / "continuity-failure.json"
    completed, evidence = await free_tier_continuity.run_continuity_observation(
        "synthetic-test-key",
        output,
        client_factory=factory,
        nonce_factory=lambda: "amber cedar violet harbor maple silver",
    )
    serialized = output.read_text(encoding="utf-8")
    assert not completed and evidence["status"] == "failed"
    assert evidence["failure"] == {
        "stage": "session_1",
        "code": "continuity_or_termination_not_observed",
    }
    assert requested_keys == ["synthetic-test-key"]
    assert first_client.async_closed == first_client.sync_closed == 1
    assert nonce not in serialized
    assert "private-handle" not in serialized
    assert "synthetic-test-key" not in serialized


async def test_continuity_launcher_records_only_safe_abnormal_close_evidence(
    tmp_path: Path,
) -> None:
    session = FakeSession()
    session.receive_error = errors.APIError(1011, {"message": "private disconnect detail"})
    client = FakeSdkClient(FakeLive([session]))
    output = tmp_path / "disconnect.json"
    completed, evidence = await free_tier_continuity.run_continuity_observation(
        "synthetic-test-key",
        output,
        client_factory=lambda _key: client,
        nonce_factory=lambda: "amber cedar violet harbor maple silver",
    )
    serialized = output.read_text(encoding="utf-8")
    observations = evidence["observations"]
    assert not completed and evidence["status"] == "failed"
    assert evidence["failure"] == {"stage": "session_1", "code": "provider_disconnect"}
    assert isinstance(observations, list) and isinstance(observations[0], dict)
    assert observations[0]["end_reason"] == "provider_disconnected"
    assert observations[0]["provider_close_code"] == 1011
    assert observations[0]["termination_observed"] is True
    assert "private disconnect detail" not in serialized
    assert "synthetic-test-key" not in serialized
    assert "0123456789abcdef" not in serialized
    assert client.async_closed == client.sync_closed == 1


async def test_continuity_cancellation_persistence_failure_warns_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session = FakeSession()
    session.block = True
    client = FakeSdkClient(FakeLive([session]))
    task = asyncio.create_task(
        free_tier_continuity.run_continuity_observation(
            "synthetic-test-key",
            tmp_path / "cancelled.json",
            client_factory=lambda _key: client,
        )
    )
    await session.entered.wait()
    with patch.object(
        free_tier_continuity, "_write_evidence", side_effect=OSError("private path detail")
    ):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    warning = capsys.readouterr().err
    assert "cancellation evidence could not be saved" in warning
    assert "private path detail" not in warning
    assert client.async_closed == client.sync_closed == 1


def test_continuity_hidden_key_prompt_waits_for_both_confirmations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in free_tier_smoke.FORBIDDEN_ENVIRONMENT_OVERRIDES:
        monkeypatch.delenv(variable, raising=False)
    with (
        patch("sys.stdin", TtyStringIO()),
        patch("sys.stdout", TtyStringIO()),
        patch("builtins.input", side_effect=["YES", "NO"]),
        patch(
            "getpass.getpass",
            side_effect=AssertionError("hidden prompt must wait for both confirmations"),
        ),
    ):
        assert free_tier_continuity._confirm_and_get_key() is None


def test_continuity_cli_help_and_noninteractive_refusal_do_not_request_credentials() -> None:
    backend = Path(__file__).resolve().parents[4]
    module = "tests.spikes.gemini.gemini_spike.free_tier_continuity"
    help_result = subprocess.run(
        [executable, "-m", module, "--help"],
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert help_result.returncode == 0
    assert "--output-dir" in help_result.stdout
    assert "--api-key" not in help_result.stdout
    noninteractive = subprocess.run(
        [executable, "-m", module],
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert noninteractive.returncode == 2
    assert "interactive terminal is required" in noninteractive.stderr
    assert "hidden input" not in noninteractive.stdout + noninteractive.stderr


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
async def test_invalid_smoke_audio_limit_fails_before_connect(limit: int | bool | float) -> None:
    connector = FakeLive()
    with pytest.raises(ValueError, match="positive integer"):
        await observe_session(
            connector,
            replace(build_plans()[0], max_seconds=60),
            allow_resumption=False,
            stop_after_first_audio_turn=True,
            max_audio_bytes=cast(int, limit),
        )
    assert not connector.configs


def test_smoke_websocket_connect_disables_ambient_proxy() -> None:
    captured: dict[str, object] = {}

    def fake_connect(uri: str, **kwargs: object) -> object:
        captured["uri"] = uri
        captured.update(kwargs)
        return object()

    with patch("google.genai.live.ws_connect", fake_connect):
        with sdk_adapters._websocket_without_proxy():
            sdk_live.__dict__["ws_connect"]("wss://generativelanguage.googleapis.com")
    assert captured["proxy"] is None
    assert captured["uri"] == "wss://generativelanguage.googleapis.com"


async def test_sdk_client_configuration_pins_endpoint_and_zero_retry_transports() -> None:
    sdk_client = FakeSdkClient()
    with patch("gemini_spike.free_tier_smoke.genai.Client", return_value=sdk_client) as constructor:
        free_tier_smoke.create_sdk_client("synthetic-test-key")
    kwargs = constructor.call_args.kwargs
    options = kwargs["http_options"]
    assert constructor.call_count == 1
    assert kwargs["api_key"] == "synthetic-test-key"
    assert kwargs["enterprise"] is False and kwargs["vertexai"] is False
    assert "credentials" not in kwargs and "project" not in kwargs
    assert options.base_url == "https://generativelanguage.googleapis.com"
    assert options.api_version == "v1beta"
    assert options.retry_options.attempts == 1
    assert kwargs["debug_config"].client_mode is None
    assert kwargs["debug_config"].replays_directory is None
    assert kwargs["debug_config"].replay_id is None
    sync_transport = options.client_args["transport"]
    async_transport = options.async_client_args["transport"]
    assert sync_transport._pool._retries == 0
    assert async_transport._pool._retries == 0
    assert options.client_args["trust_env"] is False
    assert options.async_client_args["trust_env"] is False
    sync_transport.close()
    await async_transport.aclose()


def test_hidden_key_is_requested_only_after_both_interactive_confirmations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in free_tier_smoke.FORBIDDEN_ENVIRONMENT_OVERRIDES:
        monkeypatch.delenv(variable, raising=False)
    terminal = TtyStringIO()
    calls: list[str] = []

    def confirmation(prompt: str) -> str:
        calls.append(prompt)
        return "YES"

    def hidden_prompt(prompt: str) -> str:
        calls.append(prompt)
        return "synthetic-test-key"

    with (
        patch("sys.stdin", TtyStringIO()),
        patch("sys.stdout", terminal),
        patch("builtins.input", side_effect=confirmation),
        patch("getpass.getpass", side_effect=hidden_prompt),
    ):
        key = free_tier_smoke._confirm_and_get_key()
    assert key == "synthetic-test-key"
    assert len(calls) == 3
    assert "Free-tier-only smoke" in terminal.getvalue()
    assert calls[-1] == "Gemini Developer API key (hidden input): "


def test_declined_confirmation_never_requests_hidden_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in free_tier_smoke.FORBIDDEN_ENVIRONMENT_OVERRIDES:
        monkeypatch.delenv(variable, raising=False)
    terminal = TtyStringIO()
    with (
        patch("sys.stdin", TtyStringIO()),
        patch("sys.stdout", terminal),
        patch("builtins.input", return_value="NO"),
        patch("getpass.getpass", side_effect=AssertionError("hidden prompt was reached")),
    ):
        assert free_tier_smoke._confirm_and_get_key() is None
    assert "Free-tier-only smoke" in terminal.getvalue()


@pytest.mark.parametrize("variable", ["GOOGLE_GEMINI_BASE_URL", "WSS_PROXY"])
def test_environment_override_rejected_without_printing_its_value(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    marker = "https://ambient-routing-value.invalid"
    monkeypatch.setenv(variable, marker)
    with (
        patch("sys.stdin", TtyStringIO()),
        patch("sys.stdout", TtyStringIO()) as terminal,
        patch("getpass.getpass", side_effect=AssertionError("hidden prompt was reached")),
    ):
        assert free_tier_smoke._confirm_and_get_key() is None
    assert marker not in terminal.getvalue()


def test_smoke_cli_help_and_noninteractive_refusal_do_not_request_credentials() -> None:
    backend = Path(__file__).resolve().parents[4]
    module = "tests.spikes.gemini.gemini_spike.free_tier_smoke"
    help_result = subprocess.run(
        [executable, "-m", module, "--help"],
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert help_result.returncode == 0
    assert "--output-dir" in help_result.stdout
    assert "--api-key" not in help_result.stdout
    api_key_flag = subprocess.run(
        [executable, "-m", module, "--api-key"],
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert api_key_flag.returncode == 2
    assert "unrecognized arguments" in api_key_flag.stderr
    assert "hidden input" not in api_key_flag.stdout + api_key_flag.stderr
    noninteractive = subprocess.run(
        [executable, "-m", module],
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert noninteractive.returncode == 2
    assert "interactive terminal is required" in noninteractive.stderr
    assert "hidden input" not in noninteractive.stdout + noninteractive.stderr


def test_main_refuses_noninteractive_run_before_key_prompt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("getpass.getpass", side_effect=AssertionError("key prompt was reached")):
        assert free_tier_smoke.main([]) == 2
    assert "interactive terminal is required" in capsys.readouterr().err


def test_fail_closed_cli_before_sdk_import_or_credential_acquisition() -> None:
    # Fresh process: no SDK already cached in sys.modules can mask an import leak.
    code = """
import builtins
import sys
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.startswith(('google', 'gemini_spike', 'getpass', 'dotenv')):
        raise AssertionError('Forbidden SDK/credential import')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from tests.spikes.spike_s3_gemini_live import main as s3
from tests.spikes.spike_s4_embeddings import main as s4
for cli in (s3, s4):
    try:
        cli(['--run'])
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError('Live CLI did not fail closed')
"""
    backend = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [executable, "-c", code],
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("hard spending cap") == 2
