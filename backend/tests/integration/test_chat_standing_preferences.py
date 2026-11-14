"""Chat applies saved standing preferences on every turn (the "two sum code" -> Java bug).

Scripted model, fake memory: nothing calls a real model."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from app.memory.chat_memory import RecalledMemory, format_standing_block
from tests.integration.test_chat_memory import FakeMemory, Prefs, wire
from tests.integration.test_chat_progress import progress
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    send,
    sign_in,
    stream_calls,
)

JAVA = "User's programming language is Java."
LABEL = "Using your saved preferences"


class StandingMemory(FakeMemory):
    def __init__(self, items: list[str]) -> None:
        super().__init__()
        self.items = [RecalledMemory(uuid4(), text, 1.0) for text in items]
        self.standing_calls = 0

    async def standing(self) -> list[RecalledMemory]:
        self.standing_calls += 1
        return self.items


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async with make_world() as value:
        yield value


async def test_saved_language_reaches_the_model_when_recall_misses(world: World) -> None:
    wire(world, StandingMemory([JAVA]))  # recall hits stay empty: the exact reported case
    account = await sign_in(world)
    frames = events(await send(world, account, "two sum code"))
    assert names(frames)[-1] == "done"
    system = str(stream_calls(world)[-1]["config"].system_instruction)
    assert "<user_profile>" in system and "Java" in system
    assert "do not silently default to Python" in system
    steps = progress(frames)
    assert any(s["label"] == LABEL for s in steps)
    assert "Java" not in str(steps)  # the step never carries the memory text


async def test_explicit_language_gets_the_message_wins_rule(world: World) -> None:
    wire(world, StandingMemory([JAVA]))
    account = await sign_in(world)
    await send(world, account, "two sum code in Python")
    system = str(stream_calls(world)[-1]["config"].system_instruction)
    assert "explicit language in the user's current message always wins" in system
    assert "offer, in one sentence, to update the saved preference" in system


async def test_no_memory_means_no_block_and_no_step(world: World) -> None:
    wire(world, StandingMemory([]))
    account = await sign_in(world)
    frames = events(await send(world, account, "two sum code"))
    assert "<user_profile>" not in str(stream_calls(world)[-1]["config"].system_instruction)
    assert not [s for s in progress(frames) if s["label"] == LABEL]


async def test_memory_off_means_no_block(world: World) -> None:
    memory = StandingMemory([JAVA])
    wire(world, memory, Prefs(memory_on=False))
    account = await sign_in(world)
    await send(world, account, "two sum code")
    assert memory.standing_calls == 0
    assert "<user_profile>" not in str(stream_calls(world)[-1]["config"].system_instruction)


async def test_a_recalled_memory_already_in_the_block_is_not_repeated(world: World) -> None:
    memory = StandingMemory([JAVA])
    memory.hits = [memory.items[0], RecalledMemory(uuid4(), "Project Atlas ships in March", 0.9)]
    wire(world, memory)
    account = await sign_in(world)
    await send(world, account, "two sum code for project Atlas")
    call = stream_calls(world)[-1]
    user_text = " ".join(p["text"] for p in call["contents"][-1]["parts"])
    assert "Atlas" in user_text and "Java" not in user_text
    assert format_standing_block(memory.items) in str(call["config"].system_instruction)


async def test_a_failing_standing_lookup_never_fails_the_turn(world: World) -> None:
    class Broken(StandingMemory):
        async def standing(self) -> list[RecalledMemory]:
            raise RuntimeError("db down")

    wire(world, Broken([JAVA]))
    account = await sign_in(world)
    frames = events(await send(world, account, "two sum code"))
    assert names(frames)[-1] == "done"
