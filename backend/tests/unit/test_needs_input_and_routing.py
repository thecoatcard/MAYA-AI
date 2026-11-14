"""Needs-input pause/resume, default Notion parent, recipient resolution, Notion routing of long
content, voice wording and language rules, and size scaling. Fakes only; no network."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services import notion_routing, recipient_context
from app.tasks import needs_input as ni

SEARCH_TEXT = (
    "Pages the integration can see (most recently edited first):\n"
    "- Inbox | id aaaa1111aaaa1111aaaa1111aaaa1111 | in a workspace | https://notion.so/a\n"
    "- Java Hub | id bbbb2222bbbb2222bbbb2222bbbb2222 | in a page | https://notion.so/b\n"
    "- Untitled | id cccc | in a page | https://notion.so/c\n"
)


# ------------------------------------------------------------------------------ needs input


def test_real_search_results_become_candidates_without_untitled_pages() -> None:
    found = ni.candidates_from_search(SEARCH_TEXT)
    assert [c.label for c in found] == ["Inbox", "Java Hub"]
    assert found[1].value == "bbbb2222bbbb2222bbbb2222bbbb2222"


def test_the_card_marks_the_top_option_suggested_and_offers_a_default() -> None:
    need = ni.NeedsInput(
        ni.KIND_NOTION_PARENT,
        "Which page?",
        header="Notion page",
        options=list(reversed(ni.candidates_from_search(SEARCH_TEXT))),
        offer_default=True,
    )
    questions, mapping = ni.card_questions(need)
    assert questions[0]["options"][0]["label"] == "Java Hub (Suggested)"
    assert questions[0]["allow_text"] is True  # "Other / paste a URL"
    assert questions[1]["question"] == "Use this by default?"
    assert mapping["Java Hub (Suggested)"].startswith("bbbb")


def test_an_option_a_link_or_nothing_resolves_as_the_user_answered() -> None:
    pause = ni.Pause(ni.KIND_NOTION_PARENT, "ask_1", {"Java Hub (Suggested)": "bbbb"}, True)
    chosen = ni.resolve_answer(
        pause.as_dict(),
        [{"selected": ["Java Hub (Suggested)"]}, {"selected": [ni.DEFAULT_YES]}],
    )
    assert chosen is not None
    assert (chosen.value, chosen.label, chosen.make_default) == ("bbbb", "Java Hub", True)
    pasted = ni.resolve_answer(
        pause.as_dict(), [{"selected": [], "text": "https://www.notion.so/Hub-" + "a" * 32}]
    )
    assert pasted is not None and pasted.value.startswith("https://") and not pasted.make_default
    assert ni.resolve_answer(pause.as_dict(), [{"selected": ["made-up"], "text": "hmm"}]) is None


def test_the_default_parent_is_stored_and_parsed_back() -> None:
    stored = ni.format_default("bbbb", "Java Hub")
    assert ni.parse_default(stored) == ("bbbb", "Java Hub")
    assert ni.parse_default(None) is None and ni.parse_default("no-separator") is None


async def test_a_book_step_without_a_parent_pauses_with_real_options_and_shows_the_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tasks import worker as worker_module

    added: list[dict[str, Any]] = []

    class FakeRepo:
        def __init__(self, *args: Any) -> None: ...

        async def add_message(self, session_id: Any, sender: str, text: str, **kw: Any) -> None:
            added.append({"sender": sender, "text": text, **kw})

    async def no_event(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr("app.db.repositories.chat.ChatRepository", FakeRepo)
    monkeypatch.setattr(worker_module, "write_outbox_event", no_event)

    class Invoker:
        async def invoke(self, name: str, args: dict[str, Any], **kw: Any) -> Any:
            assert name == "notion_search"
            return SimpleNamespace(clean_text=SEARCH_TEXT)

    class Session:
        async def connection(self) -> object:
            return object()

        def begin_nested(self) -> Any:
            # the card insert runs in a savepoint (a failed insert must not poison the pause)
            @asynccontextmanager
            async def savepoint() -> Any:
                yield

            return savepoint()

        async def flush(self) -> None: ...

    worker = worker_module.SubtaskWorker(tool_invoker=Invoker())
    subtask = SimpleNamespace(input_data={"pipeline": {"request": "Java notes"}}, status=None)
    task = SimpleNamespace(
        session_id=uuid4(), user_id=uuid4(), status="executing", plan_status=None
    )
    job = SimpleNamespace(
        workspace_id=uuid4(), task_id=uuid4(), subtask_id=uuid4(), user_id=uuid4()
    )
    need = ni.NeedsInput(ni.KIND_NOTION_PARENT, "Which page?", offer_default=True)

    status = await worker._pause_for_input(Session(), job, subtask, task, need)  # type: ignore[arg-type]

    # tasks.status has a CHECK constraint without "awaiting_input" (that value wedged a real task)
    assert status.value == "awaiting_approval" and task.status == "awaiting_approval"
    pause = subtask.input_data[ni.NEEDS_INPUT_KEY]
    assert set(pause["options"].values()) == {
        "aaaa1111aaaa1111aaaa1111aaaa1111",
        "bbbb2222bbbb2222bbbb2222bbbb2222",
    }
    card = added[0]["metadata"]["input_request"]
    assert card["source"] == "task" and card["task_input"]["kind"] == ni.KIND_NOTION_PARENT
    assert card["ask_id"] == pause["ask_id"]


# --------------------------------------------------------------- recipient from the context


def test_same_mail_refers_to_an_earlier_address_and_a_written_address_does_not() -> None:
    assert recipient_context.references_earlier_recipient("good send this to same mail")
    assert recipient_context.references_earlier_recipient("usi ko bhej do")
    assert not recipient_context.references_earlier_recipient("send it to a@b.com")
    assert not recipient_context.references_earlier_recipient("send this to my boss")


def test_candidates_come_only_from_the_conversation_newest_first() -> None:
    history = ["mail me at old@example.com", "ok, coatcard.ai@gmail.com is the right one"]
    assert recipient_context.candidates(history) == ["coatcard.ai@gmail.com", "old@example.com"]
    assert recipient_context.candidates(history, "old@example.com")[0] == "old@example.com"
    assert recipient_context.candidates(["no address here"]) == []
    card = recipient_context.questions(["coatcard.ai@gmail.com"])
    assert card[0]["question"] == "Send to coatcard.ai@gmail.com?"
    assert card[0]["options"][-1]["label"] == recipient_context.OTHER_LABEL


# ------------------------------------------------------- notes never go through a direct write


@pytest.mark.parametrize(
    "spoken",
    [
        "add Java basics notes in MAYA DB",
        "more detailed, content rich",
        "around 100 pages, add it in the notion only",
        "make it a book",
    ],
)
def test_notes_and_long_content_are_refused_as_direct_notion_writes(spoken: str) -> None:
    assert notion_routing.must_use_book_job("notion_create_page", {"title": "Java"}, spoken)
    # Appending a few notes to an existing page is fine; a stated size or detail is a job.
    assert notion_routing.must_use_book_job("notion_append_blocks", {}, spoken) == (
        "notes" not in spoken or "detailed" in spoken
    )


def test_short_writes_and_other_tools_are_untouched() -> None:
    assert not notion_routing.must_use_book_job(
        "notion_create_page", {"title": "Shopping"}, "make a page called Shopping"
    )
    assert not notion_routing.must_use_book_job("notion_add_row", {}, "add detailed notes")
    many = {"blocks": [{"type": "paragraph"}] * 16}
    assert notion_routing.must_use_book_job("notion_append_blocks", many, "add this")
    assert "too large" in notion_routing.BOOK_ROUTE_MESSAGE  # tells the model never to say it


async def test_voice_refuses_a_direct_notes_write_and_never_submits() -> None:
    from app.voice.notion_voice import propose_by_voice

    class Submitter:
        called = False

        async def submit(self, **kwargs: Any) -> Any:
            Submitter.called = True

    result = await propose_by_voice(
        object(),
        Submitter(),
        name="notion_create_page",
        args={"title": "Java basics", "blocks": [{"type": "paragraph"}]},
        workspace_id=uuid4(),
        user_id=uuid4(),
        session_id=None,
        scope=None,
        user_text="add Java basics notes more detailed content rich",
    )
    assert result["status"] == "error" and "submit_task" in result["message"]
    assert Submitter.called is False


# ----------------------------------------------------------- voice wording and language rules


def test_voice_wording_comes_from_the_system_and_notes_are_not_memories() -> None:
    from app.voice import notion_voice, prompts

    assert "approve it on the card" not in notion_voice.NOTION_WRITE_VOICE_INSTRUCTION
    assert "Never save a Notion note" in notion_voice.NOTION_WRITE_VOICE_INSTRUCTION
    base = prompts.build_voice_instruction(
        memory_on=True, repo_on=False, images_on=False, web_on=False, master=False
    )
    assert "ONLY when a system task update says" in base
    assert "never save a document, notes, a Notion page" in base.replace("\n", " ")
    assert "never switches your language" in base  # G4


def test_chat_guidance_keeps_the_language_and_routes_notes_to_the_job() -> None:
    from app.services import required_details

    assert "stray foreign utterance never switches your language" in required_details.GUIDANCE
    assert "learning-book job" in required_details.GUIDANCE


def test_a_large_size_is_scaled_and_estimated_never_refused() -> None:
    from app.agents.learning_book_plan import book_estimate, word_budget

    chapters, words = word_budget("make 100 pages notes on java in notion")
    assert words == 45_000 and chapters > 12
    text = book_estimate(chapters, words)
    assert "model calls" in text and "minutes" in text and "45,000 words" in text


async def test_the_answer_resumes_the_same_step_with_the_chosen_parent_and_saves_a_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager

    from app.db.models import PlanStatus, SubtaskStatus
    from app.services import task_submission as ts

    pause = ni.Pause(ni.KIND_NOTION_PARENT, "ask_1", {"Java Hub (Suggested)": "bbbb"}, True)
    subtask = SimpleNamespace(
        input_data={"notion_book": {"title": "Java"}, ni.NEEDS_INPUT_KEY: pause.as_dict()},
        status=SubtaskStatus.AWAITING_APPROVAL,
        assigned_agent="pm",
    )
    task = SimpleNamespace(status="awaiting_approval", plan_status=PlanStatus.PAUSED)
    rows = iter([subtask, task])

    class Result:
        def __init__(self, row: Any) -> None:
            self.row = row

        def scalars(self) -> Any:
            return SimpleNamespace(first=lambda: self.row)

    class Session:
        async def execute(self, *args: Any, **kwargs: Any) -> Result:
            return Result(next(rows))

        async def commit(self) -> None: ...

    @asynccontextmanager
    async def fake_session(*args: Any, **kwargs: Any) -> Any:
        yield Session()

    saved: list[tuple[str, str]] = []

    class FakeSettings:
        def __init__(self, engine: Any) -> None: ...

        async def set_text(self, ws: Any, user: Any, key: str, value: str) -> None:
            saved.append((key, value))

    enqueued: list[Any] = []

    class Queue:
        async def enqueue(self, job: Any) -> None:
            enqueued.append(job)

    monkeypatch.setattr(ts, "tenant_session", fake_session)
    monkeypatch.setattr(ts, "UserSettingsStore", FakeSettings)
    service = ts.TaskSubmissionService(engine=object(), gateway=None, queue=Queue())  # type: ignore[arg-type]

    text = await service.resume_with_input(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        answers=[{"selected": ["Java Hub (Suggested)"]}, {"selected": [ni.DEFAULT_YES]}],
    )

    assert "Java Hub" in text and subtask.status == SubtaskStatus.PENDING
    assert subtask.input_data["notion_book"]["parent_page_id"] == "bbbb"
    assert ni.NEEDS_INPUT_KEY not in subtask.input_data  # the book Markdown step is untouched
    assert task.status == "executing" and len(enqueued) == 1
    assert saved == [(ni.DEFAULT_PARENT_KEY, "bbbb|Java Hub")]
