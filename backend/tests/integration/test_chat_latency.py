"""Chat latency and honesty (FC): streaming is incremental through the whole stack, the timing
instrumentation records where a turn spends its time, memory recall is capped and skipped for
trivial turns, the memory-off prompt is honest, and file/deliverable requests create tasks.

Fakes only: no real Gemini, no network. Whether a real model *chooses* to call ``submit_task``
for a given phrase is model judgment and cannot be tested here; these tests cover the prompt and
declaration text the model is given and the service behaviour once it does call the tool.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Annotated, Any, cast
from uuid import uuid4

import pytest
from fastapi import Depends

from app.api.v1.chat import get_chat_service
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.core.clock import Clock
from app.core.metrics import CHAT_TURN_PHASE_SECONDS, generate_latest
from app.gateway.schemas import ToolDefinition
from app.services import chat_service
from app.services.chat_service import (
    MEMORY_RECALL_TIMEOUT_SECONDS,
    TASK_TOOL,
    ChatService,
    is_trivial_message,
)
from app.services.context_manager import (
    CHAT_MEMORY_OFF_ADDENDUM,
    CHAT_TOOLS_SYSTEM_INSTRUCTION,
    chat_system_instruction,
)
from app.services.task_submission import TaskSubmission
from tests.integration.test_chat_memory import FakeMemory, Prefs, tool_names
from tests.integration.test_chat_streaming import (
    World,
    chunk,
    converse,
    events,
    make_world,
    names,
    parse_sse,
    send,
    sign_in,
    stream_calls,
)


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async with make_world() as value:
        yield value


@pytest.fixture
async def legacy_world() -> AsyncIterator[World]:
    """The kill switch (MAYA_CHAT_INLINE_FILES=false): the previous model-driven task path."""
    async with make_world(chat_inline_files=False) as value:
        yield value


class FakeSubmitter:
    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []

    async def submit(self, **kwargs: Any) -> TaskSubmission:
        self.submitted.append(kwargs)
        return TaskSubmission(uuid4(), kwargs["title"] or "Task", "queued", True)


def wire(
    world: World,
    *,
    memory: Any = None,
    prefs: Any = None,
    submitter: FakeSubmitter | None = None,
) -> None:
    async def build(principal: Annotated[Principal, Depends(get_principal)]) -> ChatService:
        return ChatService(
            principal=principal,
            database=world.chat.database(),
            gateway=world.gateway,
            registry=world.registry,
            settings=world.settings,
            limiter=world.limiter,
            idempotency=world.store,
            stream_limiter=world.stream_limiter,
            clock=cast(Clock, world.clock),
            monotonic=world.ticker,
            repositories=world.chat.repository,
            task_submitter=submitter,
            preferences=prefs,
            memory=memory,
        )

    world.app.dependency_overrides[get_chat_service] = build


# ------------------------------------------------- item 1: incremental delivery, whole stack


async def test_deltas_reach_the_asgi_client_one_by_one_through_the_full_app_stack(
    world: World,
) -> None:
    """Each provider chunk is released only after the client has RECEIVED the previous delta.

    If anything between the provider and the socket (gateway, chat loop, BaseHTTPMiddleware,
    MaxBodySizeMiddleware, StreamingResponse) buffered, the second chunk would never be released
    and the exchange would time out.
    """
    account = await sign_in(world)
    gates = [asyncio.Event(), asyncio.Event()]
    world.models.script = [chunk("alpha "), gates[0], chunk("beta "), gates[1], chunk("gamma")]
    received: list[tuple[int, int]] = []  # (body message number, gates already released)

    async def release_after_each_delta(count: int, disconnected: asyncio.Event) -> None:
        released = sum(gate.is_set() for gate in gates)
        received.append((count, released))
        if count == 2:  # message.accepted, then the first delta
            gates[0].set()
        elif count == 3:
            gates[1].set()

    exchange = await converse(
        world, account, content="stream me", key=uuid4().hex, after_body=release_after_each_delta
    )

    frames = parse_sse(exchange.body)
    assert names(frames) == ["message.accepted", "delta", "delta", "delta", "done"]
    assert [f.data["text"] for f in frames if f.event == "delta"] == ["alpha ", "beta ", "gamma"]
    # delta 1 was on the wire while both gates were still closed, delta 2 with one open.
    assert received[1] == (2, 0)
    assert received[2] == (3, 1)


async def test_the_gateway_with_tools_yields_each_chunk_before_the_next_is_available(
    world: World,
) -> None:
    gate = asyncio.Event()
    world.models.script = [chunk("one "), gate, chunk("two")]
    tools = [ToolDefinition(name="submit_task", description="d", parameters={"type": "object"})]
    stream = world.gateway.generate_stream("orchestrator", ["hello"], tools=tools)
    first = await asyncio.wait_for(anext(stream), timeout=2)  # gate still closed
    assert first.text == "one " and not gate.is_set()
    gate.set()
    second = await asyncio.wait_for(anext(stream), timeout=2)
    assert second.text == "two"
    await stream.aclose()


# ------------------------------------------------------------------- timing instrumentation


async def test_every_turn_records_phase_timings_in_a_log_line_and_the_histogram(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    memory = FakeMemory()
    wire(world, memory=memory, prefs=Prefs())
    world.ticker.step = 0.01
    account = await sign_in(world)
    secret_text = "ZXQ-timing-content-marker What is my favourite colour?"
    before = generate_latest()
    with caplog.at_level(logging.INFO, logger="maya"):
        frames = events(await send(world, account, secret_text))
    assert names(frames)[-1] == "done"

    lines = [r.getMessage() for r in caplog.records if "chat_turn_timing" in r.getMessage()]
    assert len(lines) == 1
    line = lines[0]
    assert "outcome=completed" in line and "recall=empty" in line
    for field in ("recall_ms=", "first_chunk_ms=", "first_delta_ms=", "total_ms="):
        assert field in line and f"{field}na" not in line
    assert "ZXQ-timing-content-marker" not in caplog.text  # no user content in the line

    rendered = generate_latest()
    assert rendered != before
    for phase in ("recall", "first_chunk", "first_delta", "total"):
        assert 'maya_chat_turn_phase_seconds_count{outcome="' in rendered
        assert f'phase="{phase}"' in rendered
    assert CHAT_TURN_PHASE_SECONDS.name in rendered
    assert "ZXQ-timing-content-marker" not in rendered


async def test_a_failed_turn_is_labelled_failed(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    account = await sign_in(world)
    world.models.script = [chunk("part "), RuntimeError("boom")]
    with caplog.at_level(logging.INFO, logger="maya"):
        frames = events(await send(world, account, "hello there friend"))
    assert names(frames)[-1] == "error"
    assert any("chat_turn_timing outcome=failed" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------------------ recall cap


def test_the_default_recall_cap_is_short() -> None:
    assert MEMORY_RECALL_TIMEOUT_SECONDS <= 2.5


@pytest.mark.parametrize(
    "text",
    ["hi", "Hello!", "hey there", "thanks", "Thank you", "ok", "namaste", "good morning", "Bye."],
)
def test_trivial_messages_are_recognised(text: str) -> None:
    assert is_trivial_message(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "hi?",
        "thanks, what is my dog called",
        "remember my dog is Bruno",
        "what is my favourite colour",
        "hello there how are you today",
        "hi hi hi hi hi",
        "ok do it",
    ],
)
def test_real_messages_are_not_trivial(text: str) -> None:
    assert not is_trivial_message(text)


async def test_a_trivial_turn_skips_recall_and_a_real_one_does_not(world: World) -> None:
    memory = FakeMemory()
    wire(world, memory=memory, prefs=Prefs())
    account = await sign_in(world)
    events(await send(world, account, "hi"))
    assert memory.recall_calls == []
    events(await send(world, account, "What is my dog called?"))
    assert memory.recall_calls == ["What is my dog called?"]


async def test_a_slow_recall_delays_the_first_model_call_by_at_most_the_cap(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_service, "MEMORY_RECALL_TIMEOUT_SECONDS", 0.05)
    memory = FakeMemory()
    memory.recall_delay = 5.0
    wire(world, memory=memory, prefs=Prefs())
    account = await sign_in(world)
    loop = asyncio.get_running_loop()
    started = loop.time()
    frames = events(await send(world, account, "What is my dog called?"))
    assert names(frames)[-1] == "done"
    assert loop.time() - started < 2.0


# -------------------------------------------------------------- item 2: memory-off honesty


async def test_memory_off_prompt_is_honest_has_no_memory_tools_and_is_stable_per_user(
    world: World,
) -> None:
    wire(world, memory=FakeMemory(), prefs=Prefs(memory_on=False), submitter=FakeSubmitter())
    account = await sign_in(world)
    first = events(await send(world, account, "remember my dog is Bruno"))
    session = first[0].data["session_id"]
    events(await send(world, account, "Something else entirely", session_id=session))

    one, two = stream_calls(world)
    for call in (one, two):
        # Both messages are inline answers (router: answer), so no tool at all: no submit_task and,
        # with memory off, no save_memory / forget_memory.
        assert tool_names(call) == []
        text = str(call["config"].system_instruction)
        assert CHAT_MEMORY_OFF_ADDENDUM in text
        assert "turned off" in text and "Never promise" in text
        assert "save_memory" not in text
    assert one["config"].system_instruction == two["config"].system_instruction
    assert one["config"].tools == two["config"].tools


def test_the_memory_variants_are_distinct_and_each_is_stable() -> None:
    on = chat_system_instruction(tasks=True, memory=True)
    off = chat_system_instruction(tasks=True, memory=False)
    assert on != off and on == chat_system_instruction(tasks=True, memory=True)
    assert off == chat_system_instruction(tasks=True, memory=False)
    assert off.endswith(CHAT_MEMORY_OFF_ADDENDUM) and "save_memory" not in off
    assert chat_system_instruction(tasks=False, memory=False).endswith(CHAT_MEMORY_OFF_ADDENDUM)


async def test_with_memory_on_the_save_and_forget_tools_are_declared(world: World) -> None:
    wire(world, memory=FakeMemory(), prefs=Prefs(memory_on=True), submitter=FakeSubmitter())
    account = await sign_in(world)
    events(await send(world, account, "hello there friend"))
    (call,) = stream_calls(world)
    # "hello there friend" is an inline answer (router: answer): no task tool, memory tools only.
    assert tool_names(call) == ["save_memory", "forget_memory"]
    assert CHAT_MEMORY_OFF_ADDENDUM not in str(call["config"].system_instruction)


# ------------------------------------------------------------ item 3: task triggers


TRIGGERS = [
    "pdf",
    "pptx",
    "ppt",
    "slide deck",
    "slides",
    "docx",
    "word document",
    "spreadsheet",
    "excel",
    "xlsx",
    "report",
    "notes document",
    "banao",
    "bana do",
]


@pytest.mark.parametrize("trigger", TRIGGERS)
def test_the_task_declaration_names_every_deliverable_trigger(trigger: str) -> None:
    assert trigger in TASK_TOOL.description.lower()


@pytest.mark.parametrize("trigger", ["pdf", "pptx", "slide deck", "docx", "excel", "report"])
def test_the_system_prompt_names_the_main_deliverable_triggers(trigger: str) -> None:
    assert trigger in CHAT_TOOLS_SYSTEM_INSTRUCTION.lower()


def test_the_prompt_asks_first_for_deliverables_and_keeps_questions_inline() -> None:
    lowered = CHAT_TOOLS_SYSTEM_INSTRUCTION.lower()
    # Task V: the old "ALWAYS call submit_task" made MAYA start a dummy deck for "create a ppt".
    assert "always call submit_task" not in lowered
    assert "immediately" not in lowered
    assert "do not call the tool" in lowered
    assert "banao" in lowered and "bana do" in lowered
    assert len(CHAT_TOOLS_SYSTEM_INSTRUCTION) < 2400
    assert CHAT_TOOLS_SYSTEM_INSTRUCTION.isascii()
    assert "quick question" in TASK_TOOL.description.lower()


@pytest.mark.parametrize(
    "request_text",
    [
        "a short slide deck of 5 tips for sleep with exactly 5 slides",
        "make me a PowerPoint deck (pptx) about sleep",
        "ek presentation banao sleep par",
        "excel sheet bana do monthly budget ka",
    ],
)
async def test_when_the_model_calls_submit_task_a_task_is_created_and_announced(
    legacy_world: World, request_text: str
) -> None:
    # The model-driven path: with MAYA_CHAT_INLINE_FILES=false the model itself calls submit_task.
    world = legacy_world
    submitter = FakeSubmitter()
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        [{"call": {"name": "submit_task", "args": {"instruction": request_text, "title": "Deck"}}}],
        [{"text": "Started. Progress is in the Task Ledger."}],
    ]
    frames = events(await send(world, account, request_text))
    assert "task.created" in names(frames) and names(frames)[-1] == "done"
    assert [s["instruction"] for s in submitter.submitted] == [request_text]
    assert submitter.submitted[0]["source"] == "chat"
    first, second = stream_calls(world)
    assert tool_names(first) == ["submit_task"] and second["config"].tools is None


# ------------------------------------------------------------ item 4: confirmation text


async def test_save_memory_returns_a_ready_made_confirmation_for_the_model_to_echo(
    world: World,
) -> None:
    memory = FakeMemory()
    wire(world, memory=memory, prefs=Prefs())
    account = await sign_in(world)
    call = {"name": "save_memory", "args": {"text": "Dog is called Bruno", "kind": "fact"}}
    world.models.queued_stream_scripts = [[{"call": call}], [{"text": "Saved: Dog is Bruno."}]]
    frames = events(await send(world, account, "Yaad rakhna mera kutta Bruno hai"))
    assert names(frames).count("memory.saved") == 1
    response = stream_calls(world)[1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert response["confirmation"] == "Saved: Dog is called Bruno"
    assert "one short sentence" in response["message"] and "their language" in response["message"]


# ------------------------------------------------- refusal from the task service (no ghost task)


class RefusingSubmitter(FakeSubmitter):
    def __init__(self, refusal: Any) -> None:
        super().__init__()
        self.refusal = refusal

    async def submit(self, **kwargs: Any) -> Any:
        self.submitted.append(kwargs)
        return self.refusal


_REASON = "I cannot access your repositories from here."
_HINT = "Connect GitHub in Integrations first."


@pytest.mark.parametrize(
    "refusal",
    [
        SimpleNamespace(ok=False, reason=_REASON, hint=_HINT),
        {"ok": False, "reason": _REASON, "hint": _HINT},
    ],
    ids=["object", "mapping"],
)
async def test_a_task_refusal_goes_back_to_the_model_and_creates_no_task_event_or_card(
    legacy_world: World, refusal: Any
) -> None:
    # The model-driven path (MAYA_CHAT_INLINE_FILES=false): the model calls submit_task.
    world = legacy_world
    submitter = RefusingSubmitter(refusal)
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        [{"call": {"name": "submit_task", "args": {"instruction": "list my repo"}}}],
        [{"text": "I can't do that yet. Connect GitHub first."}],
    ]
    frames = events(await send(world, account, "list my repo"))

    assert "task.created" not in names(frames) and names(frames)[-1] == "done"
    response = stream_calls(world)[1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert response["ok"] is False and response["status"] == "refused"
    assert (response["reason"], response["hint"]) == (_REASON, _HINT)
    assert "No task was started" in response["message"]
    (assistant,) = world.chat.rows("orchestrator")
    assert "task" not in assistant.metadata and "task_id" not in assistant.metadata


async def test_the_normal_task_result_is_unchanged_and_says_it_has_only_started(
    world: World,
) -> None:
    wire(world, prefs=Prefs(memory_on=False), submitter=FakeSubmitter())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        [
            {
                "call": {
                    "name": "submit_task",
                    "args": {"instruction": "analyse Q3 sales", "title": "PDF"},
                }
            }
        ],
        [{"text": "Started."}],
    ]
    # A topic is required before a task starts (required-details gate): this test used a
    # topic-less "make a pdf"; it now sends a subject-bearing background request.
    frames = events(await send(world, account, "analyse Q3 sales"))
    assert "task.created" in names(frames)
    response = stream_calls(world)[1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert response["status"] == "started" and "task_id" in response
    assert "only started" in response["message"]


def test_the_prompt_tells_the_model_how_to_treat_refusals_and_fresh_tasks() -> None:
    lowered = CHAT_TOOLS_SYSTEM_INSTRUCTION.lower()
    assert "ok false" in lowered and "reason and hint" in lowered
    assert "only started" in lowered and "never call it done" in lowered
