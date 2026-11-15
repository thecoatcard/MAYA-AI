"""A long pdf request in the chat tool loop is built by the chapter pipeline, honestly.

"make a 10page java stream notes in a pdf" used to run one create_document call that returned a
5-page, 900-word file. Now create_document hands a request above 8 pages to the background job
(outline -> chapters -> assemble) and says so; a short request still builds in one call, and a
reply that runs out of attempts says plainly that nothing was created. Fakes only.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.integration.test_chat_document_delivery import call, reply_text, tool_results
from tests.integration.test_chat_inline_files import IdempotentSubmitter
from tests.integration.test_chat_latency import wire
from tests.integration.test_chat_memory import Prefs
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    send,
    sign_in,
    stream_calls,
)

PROMPT = "make a 10page java stream notes in a pdf"
BLOCKS = [{"type": "paragraph", "text": "Streams process collections declaratively."}]


@pytest.fixture
async def loop_world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as value:
        yield value


async def test_a_ten_page_request_starts_the_chapter_job_and_creates_no_file(
    loop_world: World, tmp_path: Path
) -> None:
    submitter = IdempotentSubmitter()
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call(
            "create_document",
            {
                "format": "pdf",
                "title": "Java Stream Notes",
                "pages": 10,
                "spec": {"blocks": BLOCKS},
            },
        ),
        [{"text": "Building it in the background in about 4 chapters."}],
    ]
    frames = events(await send(loop_world, account, PROMPT))

    (job,) = submitter.submitted
    assert "10" in job["instruction"] and "pdf" in job["instruction"].lower()
    assert not list((tmp_path / "artifacts").rglob("*.pdf"))  # nothing was built inline
    assert "file.created" not in [f.event for f in frames]
    (result,) = tool_results(stream_calls(loop_world)[1])
    assert result["status"] == "started" and result["job_id"]
    assert result["pages_requested"] == 10 and result["chapters"] >= 3
    assert "background" in result["message"] and "finished" in result["message"]
    assert "background" in reply_text(frames)


async def test_running_out_of_attempts_says_what_went_wrong(loop_world: World) -> None:
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    bad = {"format": "pdf", "title": "T", "spec": {"theme": "no-such-theme", "blocks": BLOCKS}}
    loop_world.models.queued_stream_scripts = [
        call("create_document", bad),
        call("create_document", bad),
        call("create_document", bad),
        call("create_document", bad),
        call("create_document", bad),
        [{"text": "I could not build the file."}],
    ]
    await send(loop_world, account, "make a pdf about java streams")
    results = [r for record in stream_calls(loop_world)[1:] for r in tool_results(record)]
    last = results[-1]
    assert last["status"] == "skipped"
    assert "no-such-theme" in last["message"] and "offer to try again" in last["message"]
