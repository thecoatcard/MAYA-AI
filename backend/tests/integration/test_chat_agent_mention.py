"""'@python-reviewer mera main.py review karo' through the in-memory chat harness (no network).

A leading @name is ALWAYS the user's custom agent (a file in .maya/agents of the connected folder),
resolved before any other routing. The target file comes from an attachment, a file of this chat,
then the folder; otherwise one question card. A missing agent is a plain answer. GitHub is used
only when the message really names a repository. Nothing here calls a model or GitHub.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agents.custom import CustomAgent, render_definition
from app.files import services as file_services
from tests.integration.test_chat_inline_files import IdempotentSubmitter
from tests.integration.test_chat_phase2_tools import wire as wire_tasks
from tests.integration.test_chat_repo_tools import RecordingRepoTools
from tests.integration.test_chat_repo_tools import wire as wire_repo
from tests.integration.test_chat_streaming import (
    Account,
    World,
    events,
    make_world,
    reply_text,
    send,
    sign_in,
    stream_calls,
)
from tests.support.files import MemorySettings, RecordingServices, make_services

MESSAGE = "@python-reviewer mera main.py review karo"
CODE = "def add(a, b):\n    return a + b\n"
AGENT = CustomAgent(
    name="python-reviewer",
    description="Reviews Python files.",
    instructions="Review the given Python code for bugs and style.",
    capabilities=("read_files",),
)


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts") as value:
        value.submitter = IdempotentSubmitter()  # type: ignore[attr-defined]
        wire_tasks(value, submitter=value.submitter)  # type: ignore[attr-defined]
        yield value


@pytest.fixture
def env(tmp_path: Path) -> Iterator[tuple[RecordingServices, MemorySettings, Path]]:
    services, settings, folder = make_services(tmp_path)
    file_services.configure(services)
    yield services, settings, folder
    file_services.reset()


def connect(env: Any, account: Account, *, agent: bool = True) -> Path:
    _services, settings, folder = env
    settings.paths[(account.workspace_id, account.user_id)] = str(folder)
    if agent:
        target = folder / ".maya" / "agents"
        target.mkdir(parents=True, exist_ok=True)
        (target / "python-reviewer.md").write_text(render_definition(AGENT), encoding="utf-8")
    return Path(folder)


def submitted(world: World) -> list[dict[str, Any]]:
    return world.submitter.submitted  # type: ignore[attr-defined, no-any-return]


def cards(frames: list[Any]) -> list[dict[str, Any]]:
    return [f.data for f in frames if f.event == "input.requested"]


def assert_custom_step(world: World, expect_text: str) -> None:
    (call,) = submitted(world)
    (step,) = call["steps"]
    assert step.input_data["executor"] == "custom"
    assert step.input_data["custom_agent"] == "python-reviewer"
    files = step.input_data["provided_files"]
    assert [f["name"] for f in files] == ["main.py"]
    assert expect_text in files[0]["text"]
    assert "github" not in call["instruction"].lower()


async def test_agent_runs_on_main_py_attached(world: World, env: Any, tmp_path: Path) -> None:
    account = await sign_in(world)
    connect(env, account)
    upload = env[0].uploads.save(account.workspace_id, account.user_id, "main.py", CODE.encode())
    frames = events(await send(world, account, MESSAGE, attachments=[upload.id]))
    assert_custom_step(world, "def add")
    assert "python-reviewer" in reply_text(frames)
    assert stream_calls(world) == []  # no model call routed or rewrote anything


async def test_agent_runs_on_main_py_made_earlier_in_the_chat(world: World, env: Any) -> None:
    account = await sign_in(world)
    connect(env, account)
    world.models.queued_stream_scripts = [
        [{"call": {"name": "create_file_inline", "args": {"name": "main.py", "content": CODE}}}],
        [{"text": "Made main.py."}],
    ]
    first = events(await send(world, account, "main.py banao jisme add function ho"))
    session_id = str(first[0].data["session_id"])
    # make the file differ from the folder copy so the artifact is proven to be the source
    folder_copy = Path(env[2]) / "main.py"
    if folder_copy.exists():
        folder_copy.write_text("def folder_copy():\n    pass\n", encoding="utf-8")
    world.models.queued_stream_scripts = []
    events(await send(world, account, MESSAGE, session_id=session_id))
    assert_custom_step(world, "def add")


async def test_agent_runs_on_main_py_in_the_folder(world: World, env: Any) -> None:
    account = await sign_in(world)
    folder = connect(env, account)
    (folder / "src").mkdir()
    (folder / "src" / "main.py").write_text("print('from folder')\n", encoding="utf-8")
    events(await send(world, account, MESSAGE))
    (call,) = submitted(world)
    (file,) = call["steps"][0].input_data["provided_files"]
    assert file["name"] == "src/main.py" and "from folder" in file["text"]


async def test_no_main_py_anywhere_asks_one_card_never_github(world: World, env: Any) -> None:
    account = await sign_in(world)
    folder = connect(env, account)
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    (folder / "util.py").write_text("y = 2\n", encoding="utf-8")
    frames = events(await send(world, account, MESSAGE))
    (card,) = cards(frames)
    assert "main.py" in card["questions"][0]["question"]
    labels = [o["label"] for o in card["questions"][0]["options"]]
    assert labels == ["app.py", "util.py"]  # real files of the folder, nothing invented
    assert submitted(world) == []
    assert "github" not in str(card).lower()

    answer = {"ask_id": card["ask_id"], "answers": [{"selected": [], "text": "app.py"}]}
    # the chosen file is not a main.py: it asks again with the real files, it never guesses or
    # falls back to GitHub
    again = events(
        await send(world, account, "x", session_id=frames[0].data["session_id"], answer=answer)
    )
    assert len(cards(again)) == 1 and submitted(world) == []


async def test_missing_agent_is_a_plain_answer_and_starts_nothing(world: World, env: Any) -> None:
    account = await sign_in(world)
    connect(env, account, agent=False)
    frames = events(await send(world, account, MESSAGE))
    text = reply_text(frames)
    assert "There is no agent named python-reviewer in your working folder (.maya/agents)" in text
    assert "Mere liye ek agent banao" in text and "Settings > Files" in text
    assert submitted(world) == [] and cards(frames) == []


async def test_missing_agent_lists_the_agents_that_exist(world: World, env: Any) -> None:
    account = await sign_in(world)
    folder = connect(env, account, agent=False)
    other = CustomAgent(
        name="weekly-reporter", description="d", instructions="Write reports.", capabilities=()
    )
    (folder / ".maya" / "agents").mkdir(parents=True)
    (folder / ".maya" / "agents" / "weekly-reporter.md").write_text(
        render_definition(other), encoding="utf-8"
    )
    text = reply_text(events(await send(world, account, MESSAGE)))
    assert "weekly-reporter" in text and submitted(world) == []


async def test_no_folder_connected_says_so_plainly(world: World, env: Any) -> None:
    account = await sign_in(world)
    text = reply_text(events(await send(world, account, MESSAGE)))
    assert "no agent named python-reviewer" in text.lower() and submitted(world) == []


# ---------------------------------------------------------------- GitHub stays where it belongs


@pytest.fixture
async def gh_world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts") as value:
        yield value


def tools_of(call: dict[str, Any]) -> set[str]:
    tools = call["config"].tools
    return set() if tools is None else {d.name for d in tools[0].function_declarations}


async def test_review_a_github_url_still_goes_to_github(gh_world: World) -> None:
    seen: list[httpx.Request] = []
    wire_repo(gh_world, RecordingRepoTools(seen, connected=False))
    account = await sign_in(gh_world)
    gh_world.models.queued_stream_scripts = [[{"text": "ok"}]]
    events(await send(gh_world, account, "review https://github.com/o/r"))
    assert "github_get_repo" in tools_of(stream_calls(gh_world)[0])


async def test_review_my_repo_with_a_connection_still_works(gh_world: World) -> None:
    seen: list[httpx.Request] = []
    wire_repo(gh_world, RecordingRepoTools(seen, connected=True))
    account = await sign_in(gh_world)
    gh_world.models.queued_stream_scripts = [[{"text": "ok"}]]
    events(await send(gh_world, account, "review my repo"))
    assert "github_get_repo" in tools_of(stream_calls(gh_world)[0])


async def test_a_bare_file_name_never_offers_github_even_when_connected(gh_world: World) -> None:
    seen: list[httpx.Request] = []
    wire_repo(gh_world, RecordingRepoTools(seen, connected=True))
    account = await sign_in(gh_world)
    gh_world.models.queued_stream_scripts = [[{"text": "Paste the code."}]]
    events(await send(gh_world, account, "mera main.py review karo"))
    assert not {n for n in tools_of(stream_calls(gh_world)[0]) if n.startswith("github_")}
    assert seen == []  # no GitHub request of any kind


async def test_without_a_connection_a_file_review_never_touches_github(gh_world: World) -> None:
    seen: list[httpx.Request] = []
    wire_repo(gh_world, RecordingRepoTools(seen, connected=False))
    account = await sign_in(gh_world)
    gh_world.models.queued_stream_scripts = [[{"text": "Paste the code."}]]
    events(await send(gh_world, account, "review main.py"))
    assert not {n for n in tools_of(stream_calls(gh_world)[0]) if n.startswith("github_")}
    assert seen == []
