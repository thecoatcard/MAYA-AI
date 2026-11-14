"""Chat tool loop: a planned page count is no requirement, and a repair is no failure.

"ek report PDF banao solar power par" states no count. The model plans `pages: 3`, the file has
one page: the first attempt is accepted (no retry, no "partial"). A spec problem the model repairs
is shown as a muted "skipped" step with one short sentence; only the last attempt is "failed", and
the model-facing instruction text never reaches the user-visible step. Fakes only.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_chat_document_delivery import call, tool_results
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
from tests.unit.test_page_fit import StepPdf, use_renderer

BLOCKS = [
    {"type": "heading", "level": 1, "text": "Solar power"},
    {"type": "paragraph", "text": "Solar panels turn sunlight into electricity. " * 30},
]
GOOD = {"format": "pdf", "title": "Solar", "pages": 3, "spec": {"theme": "notes", "blocks": BLOCKS}}
BAD = {"format": "pdf", "title": "Solar", "spec": {"theme": "no-such-theme", "blocks": BLOCKS}}


@pytest.fixture
async def loop_world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as value:
        yield value


def steps(frames: list[Any]) -> list[dict[str, Any]]:
    return [f.data for f in frames if f.event == "progress"]


async def test_an_unsized_report_is_accepted_on_the_first_attempt(
    loop_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_renderer(monkeypatch, StepPdf(lambda step: 1))
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", GOOD),
        [{"text": "I made Solar.pdf."}],
    ]
    frames = events(await send(loop_world, account, "ek report PDF banao solar power par"))

    (result,) = tool_results(stream_calls(loop_world)[1])
    assert result["status"] == "created" and result["delivery"] == "complete"
    assert result["pages_planned"] == 3 and result["pages_requested"] is None
    assert result["pages"] == 1 and result["length"] is None
    renders = [s for s in steps(frames) if s["label"] == "Rendering PDF"]
    assert [s["state"] for s in renders if s["state"] != "running"] == ["done"]
    assert len(stream_calls(loop_world)) == 2  # one create_document call, no retry round


async def test_a_user_stated_count_that_is_missed_is_delivered_with_the_true_count(
    loop_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Count policy (deliberate update): a missed user-stated count used to come back to the model
    # as an ERROR ("were asked for ... Nothing was created"), discarding the built file. The file
    # is now delivered and the tool result carries the true numbers for the final answer.
    use_renderer(monkeypatch, StepPdf(lambda step: 1))
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", GOOD),
        [{"text": "I made Solar.pdf with 1 page; you asked for 3."}],
    ]
    frames = events(await send(loop_world, account, "3 page ka report PDF banao solar power par"))

    (result,) = tool_results(stream_calls(loop_world)[1])
    assert result["status"] == "created" and result["pages"] == 1
    assert result["pages_requested"] == 3
    assert "You asked for 3 pages; the file has 1" in result["length_note"]
    assert "never a failure" in result["message"]
    renders = [s for s in steps(frames) if s["label"] == "Rendering PDF"]
    assert [s["state"] for s in renders if s["state"] != "running"] == ["done"]
    assert len(stream_calls(loop_world)) == 2  # no forced retry round


async def test_a_repairable_spec_problem_is_an_adjustment_and_only_the_last_attempt_fails(
    loop_world: World,
) -> None:
    wire(loop_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        *[call("create_document", BAD) for _ in range(5)],
        [{"text": "I could not build the file."}],
    ]
    frames = events(await send(loop_world, account, "make a pdf about solar power"))

    finished = [
        s for s in steps(frames) if s["label"] == "Rendering PDF" and s["state"] != "running"
    ]
    assert [s["state"] for s in finished] == ["skipped", "skipped", "skipped", "failed"]
    for step in finished:
        assert "call create_document" not in step["detail"] and len(step["detail"]) <= 80
    # The model-facing text keeps the full instruction.
    results = [r for record in stream_calls(loop_world)[1:] for r in tool_results(record)]
    assert "no-such-theme" in results[0]["message"]
