"""Owner transcript: MAYA showed a Python script, then "give in a file" and "give main.py file" both
returned main.txt. Code that is already in the conversation is saved as a file named from the
conversation (never .txt for code), verbatim, and the reply names the real file.

Fakes only: no real model, no network; files go to a temporary artifact folder.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_chat_inline_files import (
    IdempotentSubmitter,
    card_frames,
    stored_files,
)
from tests.integration.test_chat_latency import wire
from tests.integration.test_chat_memory import Prefs
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    send,
    sign_in,
    stream_calls,
)

CODE = (
    "import sys\n\n\ndef average(values):\n    return sum(values) / len(values)\n\n\n"
    'if __name__ == "__main__":\n    print(average([1, 2, 3]))\n'
)
ANSWER = f"Here is the script:\n\n```python\n{CODE}```\n\nIt has four bugs."
FIRST = "make a main.py having a code with 4 bugs that are hard to find"


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts") as value:
        yield value


async def start(world: World, answer: str = ANSWER, first: str = FIRST) -> tuple[Any, str]:
    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": answer}]]
    frames = events(await send(world, account, first))
    (accepted,) = [f.data for f in frames if f.event == "message.accepted"]
    return account, str(accepted["session_id"])


async def ask(world: World, account: Any, session: str, text: str) -> list[Any]:
    return events(await send(world, account, text, session_id=session))


def reply_text(frames: list[Any]) -> str:
    return "".join(f.data["text"] for f in frames if f.event == "delta")


def saved(world: World, tmp_path: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in stored_files(world, tmp_path)}


async def test_the_owner_transcript_gives_main_py_with_the_exact_code(
    world: World, tmp_path: Path
) -> None:
    account, session = await start(world)
    calls_before = len(stream_calls(world))

    first = await ask(world, account, session, "give in a file")
    (card,) = card_frames(first)
    assert card["name"] == "main.py" and card["file_type"] == "py"
    assert "main.py (9 lines)" in reply_text(first)

    second = await ask(world, account, session, "give main.py file")
    (card2,) = card_frames(second)
    assert card2["name"].endswith(".py") and "txt" not in card2["name"]
    assert "main" in reply_text(second) and ".py" in reply_text(second)

    files = saved(world, tmp_path)
    assert files and all(name.endswith(".py") for name in files)
    assert all(text == CODE for text in files.values())  # verbatim: no fences, no rewrite
    assert len(stream_calls(world)) == calls_before  # no model call for either file
    assert names(first)[-1] == "done" and "task.created" not in names(first)


@pytest.mark.parametrize(
    ("answer_lang", "code", "ask_text", "expected"),
    [
        ("python", "print('hi')\n", "file me de do", "main.py"),
        ("python", "print('hi')\n", "isko main.py me save kar do", "main.py"),
        ("python", "print('hi')\n", "isko script.py me save kar do", "script.py"),
        ("java", "public class Hello {\n}\n", "java me file de do", "Hello.java"),
        ("javascript", "console.log(1)\n", "give it as a .js file", "main.js"),
        ("sql", "SELECT 1;\n", "download this as a file", "query.sql"),
        ("", "def f():\n    return 1\n", "give in a file", "main.py"),
    ],
)
async def test_variants_name_the_file_from_the_conversation(
    world: World, tmp_path: Path, answer_lang: str, code: str, ask_text: str, expected: str
) -> None:
    account, session = await start(
        world, f"Sure:\n\n```{answer_lang}\n{code}```\n", "show me a small example"
    )
    frames = await ask(world, account, session, ask_text)
    (card,) = card_frames(frames)
    assert card["name"] == expected
    assert saved(world, tmp_path)[expected] == code
    assert expected in reply_text(frames)


async def test_a_different_name_never_returns_an_older_file(world: World, tmp_path: Path) -> None:
    account, session = await start(world)
    one = card_frames(await ask(world, account, session, "give in a file"))[0]
    two = card_frames(await ask(world, account, session, "isko app.py me save kar do"))[0]
    assert one["name"] == "main.py" and two["name"] == "app.py"
    assert one["file_id"] != two["file_id"]
    assert set(saved(world, tmp_path)) == {"main.py", "app.py"}


async def test_an_executable_type_is_not_created_by_this_path(world: World, tmp_path: Path) -> None:
    account, session = await start(world)
    world.models.queued_stream_scripts = [[{"text": "I cannot make that here."}]]
    frames = await ask(world, account, session, "give run.sh file")
    assert card_frames(frames) == [] and saved(world, tmp_path) == {}


async def test_a_new_request_with_its_own_topic_is_not_taken_over(world: World) -> None:
    account, session = await start(world)
    world.models.queued_stream_scripts = [[{"text": "Here is a list of fruit."}]]
    frames = await ask(world, account, session, "give me a file about fruit with 10 names")
    assert card_frames(frames) == []
