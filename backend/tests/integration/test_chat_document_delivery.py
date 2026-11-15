"""A deck made by ``create_document`` in the chat tool loop is delivered (MAYA_CHAT_TOOL_LOOP).

The model is scripted: it calls ``create_document`` for a 6-slide dark deck, then ends the turn.
Covered: the tool ran and its result names the theme and where the file was saved; the file card
rides the reply and its bytes download from the chat; the file is copied into a connected folder
when one is connected; an unknown theme is refused with the allowed names; and a reply that claims
a file was made, when no file was saved this turn, gets a correction (a claim backed by a saved file
gets none).

Fakes only: no real Gemini, no network, no database. Files go to a temporary folder.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from app.files import services as file_services
from app.files.services import FileServices
from app.services.chat_service import FALSE_FILE_CLAIM_CORRECTION, claims_file_made
from app.services.document_tools import FORCED_FILE_FAILED_TEXT
from tests.integration.test_chat_inline_files import IdempotentSubmitter
from tests.integration.test_chat_latency import wire
from tests.integration.test_chat_memory import Prefs
from tests.integration.test_chat_streaming import (
    CHAT,
    Account,
    World,
    events,
    make_world,
    names,
    send,
    sign_in,
    stream_calls,
)
from tests.support.files import MemorySettings, make_services

DECK_TITLE = "SQL Joins"
SAVED_NAME = "SQL_Joins.pptx"
SLIDES = [
    {"layout": "title", "title": DECK_TITLE, "subtitle": "INNER, LEFT, RIGHT and FULL joins"},
    {"layout": "bullets", "title": "What a join does", "bullets": ["Combines rows", "On a key"]},
    {"layout": "bullets", "title": "INNER JOIN", "bullets": ["Only matching rows"]},
    {"layout": "bullets", "title": "LEFT JOIN", "bullets": ["All left rows, NULLs on the right"]},
    {"layout": "bullets", "title": "RIGHT JOIN", "bullets": ["All right rows, NULLs on the left"]},
    {"layout": "bullets", "title": "FULL JOIN", "bullets": ["All rows from both sides"]},
]
DECK_SPEC = {"theme": "midnight", "slides": SLIDES}
PROMPT = "Make a 6-slide PPT on SQL joins, dark theme"


def call(name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"call": {"name": name, "args": args}}]


def frames_of(frames: list[Any], event: str) -> list[dict[str, Any]]:
    return [f.data for f in frames if f.event == event]


def reply_text(frames: list[Any]) -> str:
    return "".join(f.data["text"] for f in frames if f.event == "delta")


def tool_results(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The function responses sent back to the model in one request."""
    return [
        part["function_response"]["response"]
        for content in record["contents"]
        for part in content.get("parts", [])
        if "function_response" in part
    ]


@pytest.fixture
async def loop_world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as value:
        yield value


@pytest.fixture
def services(tmp_path: Path) -> Iterator[tuple[FileServices, MemorySettings, Path]]:
    built, settings, folder = make_services(tmp_path)
    file_services.configure(built)
    yield built, settings, folder
    file_services.reset()


async def run_deck(world: World, account: Account, text: str = "Deck saved.") -> list[Any]:
    world.models.queued_stream_scripts = [
        call("create_document", {"format": "pptx", "title": DECK_TITLE, "spec": DECK_SPEC}),
        [{"text": text}],
    ]
    return events(await send(world, account, PROMPT))


async def test_a_six_slide_deck_is_a_card_and_its_bytes_download_from_the_chat(
    loop_world: World, tmp_path: Path
) -> None:
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    frames = await run_deck(loop_world, account, "I created a 6-slide dark SQL Joins deck.")

    assert [f.data["status"] for f in frames if f.event == "tool.finished"] == ["ok"]
    (card,) = frames_of(frames, "file.created")
    assert card["name"] == SAVED_NAME and card["file_type"] == "pptx"
    assert names(frames)[-1] == "done"

    (saved,) = [p for p in (tmp_path / "artifacts").rglob("*.pptx")]
    assert saved.read_bytes()[:2] == b"PK"  # a pptx is a zip container
    (assistant,) = loop_world.chat.rows("orchestrator")
    assert assistant.metadata["files_created"][0]["file_id"] == card["file_id"]
    assert assistant.metadata["chat_files"][0]["storage_key"].endswith(SAVED_NAME)
    assert reply_text(frames) == "I created a 6-slide dark SQL Joins deck."  # no correction

    session_id = frames[0].data["session_id"]
    message_id = frames[0].data["assistant_message_id"]
    url = f"{CHAT}/sessions/{session_id}/messages/{message_id}/files/{card['file_id']}"
    download = await loop_world.client.get(url, headers=account.headers)
    assert download.status_code == 200 and download.content == saved.read_bytes()


async def test_the_tool_result_names_the_theme_and_where_the_file_was_saved(
    loop_world: World,
) -> None:
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    await run_deck(loop_world, account)

    (result,) = tool_results(stream_calls(loop_world)[1])
    assert result["status"] == "created" and result["name"] == SAVED_NAME
    assert result["theme"] == "midnight"
    assert result["saved_to"] == "chat"
    assert result["warnings"] == []


async def test_the_deck_is_copied_into_the_connected_folder(
    loop_world: World, services: tuple[FileServices, MemorySettings, Path], tmp_path: Path
) -> None:
    _built, settings, folder = services
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    settings.paths[(account.workspace_id, account.user_id)] = str(folder)
    frames = await run_deck(loop_world, account)

    assert [f.data["status"] for f in frames if f.event == "tool.finished"] == ["ok"]
    (copy,) = list(folder.rglob("*.pptx"))
    assert copy.name == SAVED_NAME and copy.read_bytes()[:2] == b"PK"
    (result,) = tool_results(stream_calls(loop_world)[1])
    assert result["saved_to"] == "connected_folder"
    # The chat card still downloads from MAYA's own copy.
    (saved,) = [p for p in (tmp_path / "artifacts").rglob("*.pptx")]
    assert saved.read_bytes() == copy.read_bytes()


async def test_an_unknown_theme_is_refused_with_the_allowed_names(loop_world: World) -> None:
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call(
            "create_document",
            {"format": "pptx", "title": DECK_TITLE, "spec": {**DECK_SPEC, "theme": "dark"}},
        ),
        [{"text": "The theme name was not valid, so no deck was saved."}],
    ]
    frames = events(await send(loop_world, account, PROMPT))

    assert [f.data["status"] for f in frames if f.event == "tool.finished"] == ["error"]
    assert "file.created" not in names(frames)
    (result,) = tool_results(stream_calls(loop_world)[1])
    assert result["status"] == "error" and "unknown theme 'dark'" in result["message"]
    assert "midnight" in result["message"]  # the allowed names are listed for the retry
    assert reply_text(frames) == "The theme name was not valid, so no deck was saved."


async def test_a_claimed_file_with_no_tool_call_is_corrected(loop_world: World) -> None:
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [{"text": "I have created a 6-slide dark-themed PowerPoint (SQL_Joins.pptx)."}],
    ]
    frames = events(await send(loop_world, account, PROMPT))

    assert "file.created" not in names(frames) and "tool.started" not in names(frames)
    # A file request whose first round makes no tool call: the claimed text is held back and
    # dropped, the model is retried once, and the honest message is the whole reply.
    assert reply_text(frames) == FORCED_FILE_FAILED_TEXT
    (assistant,) = loop_world.chat.rows("orchestrator")
    assert assistant.content == FORCED_FILE_FAILED_TEXT
    assert "files_created" not in assistant.metadata


async def test_a_claim_after_a_failed_tool_is_corrected(loop_world: World) -> None:
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call(
            "create_document",
            {"format": "pptx", "title": DECK_TITLE, "spec": {**DECK_SPEC, "theme": "dark"}},
        ),
        [{"text": "I have created the SQL Joins deck for you."}],
    ]
    frames = events(await send(loop_world, account, PROMPT))

    assert [f.data["status"] for f in frames if f.event == "tool.finished"] == ["error"]
    assert reply_text(frames).endswith(FALSE_FILE_CLAIM_CORRECTION)


async def test_a_claim_backed_by_a_saved_file_gets_no_correction(loop_world: World) -> None:
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    frames = await run_deck(loop_world, account, "I have created SQL_Joins.pptx for you.")

    assert FALSE_FILE_CLAIM_CORRECTION not in reply_text(frames)


@pytest.mark.parametrize(
    ("text", "claimed"),
    [
        ("I have created a 6-slide PowerPoint (SQL_Joins.pptx).", True),
        ("I created the PDF and it is ready to download.", True),
        ("Maine ek presentation bana di hai.", True),  # Hinglish: "I made a presentation"
        ("I can create a PowerPoint on SQL joins for you.", False),
        ("I could not create the deck because the theme was wrong.", False),
        ("Shall I make the PowerPoint now?", False),
        ("SQL joins combine rows from two tables on a key.", False),
        ("I have created a plan for your study.", False),
    ],
)
def test_only_a_past_tense_file_claim_is_flagged(text: str, claimed: bool) -> None:
    assert claims_file_made(text) is claimed


async def test_an_empty_last_turn_after_a_file_gets_a_one_line_closing_sentence(
    loop_world: World,
) -> None:
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "pptx", "title": DECK_TITLE, "spec": DECK_SPEC}),
        [],  # the model's last turn returns nothing
    ]
    frames = events(await send(loop_world, account, PROMPT))

    assert names(frames)[-1] == "done"
    text = reply_text(frames)
    assert text.strip() == f"{SAVED_NAME} is ready (6 pages)."
    (assistant,) = loop_world.chat.rows("orchestrator")
    assert assistant.content.strip() == text.strip()
    # The card's download works once the reply is stored.
    (card,) = frames_of(frames, "file.created")
    session_id = frames[0].data["session_id"]
    message_id = frames[0].data["assistant_message_id"]
    url = f"{CHAT}/sessions/{session_id}/messages/{message_id}/files/{card['file_id']}"
    download = await loop_world.client.get(url, headers=account.headers)
    assert download.status_code == 200 and download.content[:2] == b"PK"


async def test_a_model_closing_text_is_kept_and_no_sentence_is_added(loop_world: World) -> None:
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    frames = await run_deck(loop_world, account, "Your deck is done.")

    assert reply_text(frames).strip() == "Your deck is done."


async def test_a_reply_cut_after_a_file_with_no_text_is_kept_so_the_card_downloads(
    loop_world: World,
) -> None:
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "pptx", "title": DECK_TITLE, "spec": DECK_SPEC}),
        cast(Any, [ConnectionResetError("provider reset")]),  # fails before any text
    ]
    frames = events(await send(loop_world, account, PROMPT))

    (card,) = frames_of(frames, "file.created")
    assert names(frames)[-1] == "error"
    (assistant,) = loop_world.chat.rows("orchestrator")  # stored, not dropped
    assert assistant.metadata["finish_reason"] == "interrupted"
    session_id = frames[0].data["session_id"]
    message_id = frames[0].data["assistant_message_id"]
    url = f"{CHAT}/sessions/{session_id}/messages/{message_id}/files/{card['file_id']}"
    download = await loop_world.client.get(url, headers=account.headers)
    assert download.status_code == 200
