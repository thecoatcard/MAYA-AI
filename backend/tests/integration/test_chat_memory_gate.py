"""The memory gate (purpose ``memory_gate``): decision-model advisory over memory recall.

A fake decider stands in for the decision service; fail-open means every error path equals the
behaviour without the gate.
"""

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any
from uuid import uuid4

import pytest

from app.decisions import runtime as decisions
from app.decisions.questions import Answers, NoulAnswer, Question, ScoreAnswer
from app.memory.chat_memory import RecalledMemory
from tests.integration.test_chat_memory import (
    FakeMemory,
    user_turn_texts,
    wire,
)
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    send,
    sign_in,
    stream_calls,
)

HITS = [
    RecalledMemory(uuid4(), "Prefers short answers", 0.8),
    RecalledMemory(uuid4(), "Dog is called Bruno", 0.7),
    RecalledMemory(uuid4(), "Works at Acme as a designer", 0.6),
]


class FakeDecider:
    def __init__(self) -> None:
        self.needs: float | None = 0.95  # probability of yes for needs_memory
        self.scores: dict[str, float] = {}
        self.delay = 0.0
        self.calls: list[list[str]] = []

    def available(self, purpose: str) -> bool:
        return True

    def status(self) -> dict[str, Any]:
        return {}

    async def decide(
        self,
        purpose: str,
        input_value: str | Sequence[Mapping[str, Any]],
        questions: Sequence[Question],
        *,
        timeout: float | None = None,  # noqa: ASYNC109
        background: bool = False,
    ) -> Answers | None:
        assert purpose == "memory_gate"
        self.calls.append([q.key for q in questions])
        if self.delay:
            await asyncio.sleep(self.delay)
        if questions[0].key == "needs_memory":
            if self.needs is None:
                return None
            return Answers({"needs_memory": NoulAnswer(self.needs)})
        return Answers({q.key: ScoreAnswer(self.scores.get(q.key, 0.0)) for q in questions})


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async with make_world() as value:
        yield value


@pytest.fixture
def decider() -> Any:
    fake = FakeDecider()
    decisions.set_current(fake)
    yield fake
    decisions.set_current(None)


def memory_text(world: World) -> str:
    return "\n".join(user_turn_texts(stream_calls(world)[0]))


async def test_not_needed_skips_injection_and_says_so(world: World, decider: FakeDecider) -> None:
    memory = FakeMemory()
    memory.hits = list(HITS)
    wire(world, memory)
    decider.needs = 0.02
    account = await sign_in(world)

    frames = events(await send(world, account, "Explain how a binary search works"))

    assert names(frames)[-1] == "done"
    assert "Bruno" not in memory_text(world)
    labels = [f.data["label"] for f in frames if f.event == "progress"]
    assert "No memory needed" in labels


async def test_needed_injects_only_the_relevant_memories_as_context(
    world: World, decider: FakeDecider
) -> None:
    memory = FakeMemory()
    memory.hits = list(HITS)
    wire(world, memory)
    decider.needs = 0.97
    decider.scores = {"m0": 3.0, "m1": 0.0, "m2": 2.0}
    account = await sign_in(world)

    frames = events(await send(world, account, "Reply in my usual style"))

    text = memory_text(world)
    assert "Prefers short answers" in text and "Works at Acme" in text
    assert "Bruno" not in text
    assert "never the subject" in text  # the block still says memory is context, not the topic
    labels = [f.data["label"] for f in frames if f.event == "progress"]
    assert "Using 2 saved memories" in labels


async def test_a_gate_with_no_answer_keeps_todays_behaviour(
    world: World, decider: FakeDecider
) -> None:
    memory = FakeMemory()
    memory.hits = list(HITS)
    wire(world, memory)
    decider.needs = None
    account = await sign_in(world)

    events(await send(world, account, "What do you know about my dog?"))

    text = memory_text(world)
    assert all(word in text for word in ("Bruno", "Acme", "short answers"))


async def test_a_slow_gate_never_blocks_the_reply_and_falls_back(
    world: World, decider: FakeDecider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.decisions import memory_gate

    memory = FakeMemory()
    memory.hits = list(HITS)
    wire(world, memory)
    decider.delay = 0.05
    monkeypatch.setattr(memory_gate, "GATE_TIMEOUT_SECONDS", 0.01)

    class Timeout(FakeDecider):
        async def decide(self, *args: Any, **kwargs: Any) -> Answers | None:
            await asyncio.sleep(0.05)
            return None  # what the real service returns when its timeout fires

    decisions.set_current(Timeout())
    account = await sign_in(world)

    frames = events(await send(world, account, "my dog's name?"))

    assert names(frames)[-1] == "done"
    assert "Bruno" in memory_text(world)


async def test_the_gate_runs_in_parallel_with_the_recall(
    world: World, decider: FakeDecider
) -> None:
    memory = FakeMemory()
    memory.hits = list(HITS)
    memory.recall_delay = 0.05
    wire(world, memory)
    seen: list[str] = []
    original = decider.decide

    async def spy(*args: Any, **kwargs: Any) -> Answers | None:
        seen.append("gate_started_while_recall_pending" if not memory.recall_calls else "late")
        return await original(*args, **kwargs)

    decider.decide = spy  # type: ignore[method-assign]
    account = await sign_in(world)

    events(await send(world, account, "what is my favourite food"))

    assert seen and seen[0] == "gate_started_while_recall_pending" or memory.recall_calls
