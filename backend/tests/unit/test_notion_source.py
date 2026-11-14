"""Save existing notes to Notion as they are: gate, source choice, extraction, approval binding.

Offline: fake Notion API, fake task submitter, files built in a temp folder. No model is called
anywhere here, which is the point: existing content is saved, never regenerated.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.mcp import notion_book as nb
from app.services import notion_source as ns
from app.services import required_details as rd
from app.services.chat_service import _folded_request, _task_title
from app.voice.notion_voice import SAVE_EXISTING_DEFINITION, save_existing_by_voice
from tests.support.notion_fake import PAGE, FakeNotionApi

LONG = (
    "# Java Streams\n\nA stream is a sequence of elements that supports aggregate operations. "
    "Streams do not store data; they carry values from a source through a pipeline.\n\n"
    "## Intermediate operations\n\n- filter keeps matching elements\n- map transforms them\n"
    "- sorted orders them\n\n## Terminal operations\n\n- collect gathers the result\n"
    "- forEach consumes it\n\n```java\nlist.stream().filter(x -> x > 1).toList();\n```\n"
)
WS, TASK, USER = uuid4(), uuid4(), uuid4()


# ----------------------------------------------------------------------------- the gate


def test_add_this_notes_in_notion_asks_only_the_destination_when_content_exists() -> None:
    data = rd.GateInput(request="add this notes in notion as it is", has_prior_content=True)
    missing = rd.check(data)
    assert missing.slots == (rd.SLOT_DESTINATION,) and missing.kind == "notion_save"


def test_without_prior_content_it_asks_which_notes_never_the_topic() -> None:
    data = rd.GateInput(request="add this notes in notion as it is", has_prior_content=False)
    missing = rd.check(data)
    assert missing.slots == (rd.SLOT_SOURCE, rd.SLOT_DESTINATION)
    questions = rd.build_questions(missing)
    assert [q["header"] for q in questions] == ["Source", "Destination"]
    assert "Which notes" in questions[0]["question"] and questions[0]["options"] == []
    assert "Topic" not in [q["header"] for q in questions]


def test_a_hinglish_reference_is_a_save_and_never_a_depth_or_topic_question() -> None:
    for text in ("isko notion me daal do jaisa hai waisa", "ye notes notion me save kar do"):
        missing = rd.check(rd.GateInput(request=text, has_prior_content=True))
        assert missing.slots == (rd.SLOT_DESTINATION,), text


def test_a_writing_request_is_still_a_writing_request() -> None:
    assert not rd.wants_save_existing("make notes on java streams and save to notion")
    assert not rd.wants_save_existing("explain this and add it to notion")
    missing = rd.check(rd.GateInput(request="Java notes banao aur notion me save karo"))
    assert rd.SLOT_DEPTH in missing.slots


def test_the_answered_destination_leaves_nothing_to_ask() -> None:
    request = "add this notes in notion as it is\nDestination: Study Hub"
    assert not rd.check(rd.GateInput(request=request, has_prior_content=True))


# ----------------------------------------------------- instruction folding on answer turns


def msg(role: str, content: str, **meta: Any) -> Any:
    return SimpleNamespace(role=role, content=content, metadata=meta)


def test_a_second_card_keeps_the_original_request_not_the_first_answer() -> None:
    earlier = [
        msg("user", "add this notes in notion as it is"),
        msg("assistant", "Which Notion page?"),
        msg("user", "Destination: Ai NOTES", answers_to="a1"),
        msg("assistant", "What should the page be about?"),
    ]
    folded = _folded_request(earlier, "Topic: Java Streams")
    assert folded.splitlines() == [
        "add this notes in notion as it is",
        "Destination: Ai NOTES",
        "Topic: Java Streams",
    ]
    assert rd.strip_answers(folded) == "add this notes in notion as it is"


def test_a_task_title_is_never_made_from_card_answers() -> None:
    assert _task_title(None, "Destination: Ai NOTES\nTopic: Java Streams") == "Task"
    title = _task_title(None, "Java streams notes banao\nDestination: Ai NOTES")
    assert title == "Java streams notes banao"


# ------------------------------------------------------------------ source selection


def art(name: str, kind: str, tmp: Path) -> ns.Source:
    return ns.Source("artifact", name, path=tmp / name, file_type=kind)


def test_the_last_substantive_assistant_message_skips_status_lines(tmp_path: Path) -> None:
    texts = [
        LONG,
        "Started: Java notes. I will update you here.",
        "Java_Streams.pdf (built 3 pages)",
    ]
    assert ns.last_assistant_message(texts) == LONG.strip()
    assert ns.last_assistant_message(["ok", "Done"]) is None


def test_this_picks_the_last_answer_and_the_pdf_word_picks_the_file(tmp_path: Path) -> None:
    pdf = art("Java_Streams_Study_Notes.pdf", "pdf", tmp_path)
    msg_text = LONG.strip()
    assert ns.choose("add this notes in notion", msg_text, [pdf]).source.kind == "message"  # type: ignore[union-attr]
    picked = ns.choose("add the pdf in notion", msg_text, [pdf]).source
    assert picked is pdf
    by_name = ns.choose("save java streams study notes to notion", msg_text, [pdf]).source
    assert by_name is pdf


def test_a_short_last_message_falls_back_to_the_newest_artifact(tmp_path: Path) -> None:
    pdf = art("Java_Streams_Study_Notes.pdf", "pdf", tmp_path)
    assert ns.choose("Notion me save kardo", None, [pdf]).source is pdf
    assert ns.choose("Notion me save kardo", None, []).source is None


def test_two_plausible_candidates_ask_one_card_and_the_answer_picks(tmp_path: Path) -> None:
    pdf = art("Java_Streams_Study_Notes.pdf", "pdf", tmp_path)
    choice = ns.choose("Notion me save kardo", LONG.strip(), [pdf])
    assert choice.source is None and len(choice.ask) == 2
    options = ns.source_options(choice.ask)
    assert [label for label, _ in options] == [ns.MESSAGE_LABEL, "Java_Streams_Study_Notes.pdf"]
    asked = rd.build_questions(rd.Missing((rd.SLOT_SOURCE,), "notion_save"), source_options=options)
    assert asked[0]["header"] == "Source" and len(asked[0]["options"]) == 3  # + paste
    answered = "Notion me save kardo\nSource: Java_Streams_Study_Notes.pdf"
    assert ns.choose(answered, LONG.strip(), [pdf]).source is pdf
    last = f"Notion me save kardo\nSource: {ns.MESSAGE_LABEL}"
    assert ns.choose(last, LONG.strip(), [pdf]).source.kind == "message"  # type: ignore[union-attr]
    pasted = "Notion me save kardo\nSource: " + "My pasted notes line. " * 5
    assert ns.choose(pasted, None, []).source.kind == "pasted"  # type: ignore[union-attr]


# ---------------------------------------------------------------------- extraction


def make_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.drawString(72, 780, "Java Streams Study Notes")
    pdf.drawString(72, 760, "A stream carries values through a pipeline.")
    pdf.showPage()
    pdf.drawString(72, 780, "Terminal operations collect the result.")
    pdf.save()


async def test_a_pdf_is_read_with_a_layout_warning_and_no_page_markers(tmp_path: Path) -> None:
    path = tmp_path / "Java_Streams_Study_Notes.pdf"
    make_pdf(path)
    text, note = await ns.load(ns.Source("artifact", path.name, path=path, file_type="pdf"))
    assert "Java Streams Study Notes" in text and "Terminal operations" in text
    assert "== Page" not in text
    assert "approximate" in note


async def test_docx_keeps_headings_and_lists_and_html_converts(tmp_path: Path) -> None:
    from docx import Document

    doc = Document()
    doc.add_heading("Streams", level=1)
    doc.add_paragraph("A pipeline of operations.")
    doc.add_paragraph("filter", style="List Bullet")
    path = tmp_path / "n.docx"
    doc.save(str(path))
    text, _ = await ns.load(ns.Source("artifact", "n.docx", path=path, file_type="docx"))
    assert "# Streams" in text and "- filter" in text
    page = tmp_path / "p.html"
    page.write_text("<h2>Intro</h2><p>Hello</p><ul><li>one</li></ul><script>x()</script>")
    html, _ = await ns.load(ns.Source("artifact", "p.html", path=page, file_type="html"))
    assert "## Intro" in html and "- one" in html and "x()" not in html


async def test_an_unreadable_file_is_a_plain_note_not_a_crash(tmp_path: Path) -> None:
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"not a pdf")
    text, note = await ns.load(ns.Source("artifact", "x.pdf", path=bad, file_type="pdf"))
    assert text == "" and note


def test_artifact_sources_stay_inside_the_storage_root(tmp_path: Path) -> None:
    (tmp_path / "w").mkdir()
    (tmp_path / "w" / "a.pdf").write_bytes(b"x")
    files = [
        {"name": "a.pdf", "storage_key": "w/a.pdf", "file_type": "pdf"},
        {"name": "evil.pdf", "storage_key": "../outside.pdf", "file_type": "pdf"},
        {"name": "p.png", "storage_key": "w/p.png", "file_type": "png"},
    ]
    assert [s.label for s in ns.artifact_sources(files, tmp_path)] == ["a.pdf"]


def test_as_is_is_verbatim_and_otherwise_only_spacing_changes() -> None:
    messy = "# T\n\n\n\n\ntext   \nmore\n"
    assert ns.prepare_content(messy, as_is=True) == messy
    assert ns.prepare_content(messy, as_is=False) == "# T\n\ntext\nmore"
    assert ns.title_for(LONG, ns.Source("message", "x")) == "Java Streams"
    pdf = ns.Source("artifact", "Java_Streams_Study_Notes.pdf")
    assert ns.title_for("no heading here", pdf) == "Java Streams Study Notes"


# -------------------------------------------------- approval binding and the page write


async def prepare(root: Path, markdown: str, **extra: Any) -> dict[str, Any]:
    step = ns.build_step(
        title="Java Streams",
        markdown=markdown,
        parent_id=PAGE,
        parent_label="Study Hub",
        as_is=extra.get("as_is", True),
        source=ns.Source("message", ns.MESSAGE_LABEL),
        note="",
    )
    assert step.tool == ns.NOTION_TOOL and step.tool_args == {}
    return await nb.prepare_book_write(
        artifacts_root=root,
        workspace_id=WS,
        task_id=TASK,
        input_data=dict(step.input_data),
        artifact_rows=[],
    )


async def test_the_approval_shows_the_exact_content_and_binds_its_hash(tmp_path: Path) -> None:
    prepared = await prepare(tmp_path, LONG)
    args = prepared["tool_args"]
    assert args["content_sha256"] == nb.sha256_text(LONG) and args["source"] == "existing"
    diff = prepared["payload_diff"]
    assert "Java Streams" in diff and "Study Hub" in diff and "unchanged" in diff
    assert "filter keeps matching elements" in diff  # the exact content is on the card
    assert "Source: My last answer" in diff
    other = await prepare(tmp_path, LONG + "\nOne more line.\n")
    assert other["tool_args"]["content_sha256"] != args["content_sha256"]
    light = await prepare(tmp_path, LONG, as_is=False)
    assert "light cleanup" in light["payload_diff"].lower() or "Only light" in light["payload_diff"]


async def test_the_saved_page_is_written_from_the_approved_text_and_read_back(
    tmp_path: Path,
) -> None:
    args = (await prepare(tmp_path, LONG))["tool_args"]
    fake = FakeNotionApi()
    result = await nb.execute_book_write(
        args,
        fake.connector(),
        approved=True,
        artifacts_root=tmp_path,
        workspace_id=WS,
        task_id=TASK,
        sleep=fake._sleep,
    )
    assert "VERIFIED by reading it back" in result
    sent = nb.book_blocks(LONG)
    page_id = next(k for k in fake.children if k not in (PAGE, "aaaabbbbccccddddeeeeffff00001111"))
    assert len(fake.children[page_id]) == len(sent)
    # A changed file after approval refuses (nothing written).
    (tmp_path / str(WS) / str(TASK) / nb.SAVED_NOTES_FILE).write_text(LONG + "x", encoding="utf-8")
    with pytest.raises(nb.BookWriteError):
        await nb.execute_book_write(
            args,
            fake.connector(),
            approved=True,
            artifacts_root=tmp_path,
            workspace_id=WS,
            task_id=TASK,
        )


async def test_a_long_page_is_written_in_100_block_batches(tmp_path: Path) -> None:
    big = "\n\n".join(f"Paragraph number {i} of the notes." for i in range(250))
    prepared = await prepare(tmp_path, big)
    assert prepared["tool_args"]["blocks"] == 250
    assert prepared["summary"]["batches"] == 3


# --------------------------------------------------------------------------- the parent


async def test_the_parent_is_a_link_or_an_exact_real_title_never_a_guess() -> None:
    fake = FakeNotionApi()
    fake.pages[PAGE] = {
        "object": "page",
        "id": PAGE,
        "url": "https://notion.so/study-hub",
        "parent": {"type": "workspace"},
        "properties": {"title": {"type": "title", "title": [{"plain_text": "Study Hub"}]}},
    }
    fake.children[PAGE] = []
    toolkit = fake.toolkit(database=None)
    assert (await ns.resolve_parent(toolkit, "Study Hub"))[0] == PAGE
    assert await ns.resolve_parent(toolkit, "Study") == ("", "")  # no near-match guessing
    assert await ns.resolve_parent(toolkit, "") == ("", "")
    link = "https://www.notion.so/Study-Hub-" + "a" * 32
    assert (await ns.resolve_parent(None, link))[0] == "a" * 32
    assert ns.destination_text("add this\nDestination: Study Hub") == "Study Hub"


# ----------------------------------------------------------------------------- voice


class Submitter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def submit(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(task_id=uuid4(), is_new=True)


async def voice(history: list[tuple[str, str]], destination: str, fake: FakeNotionApi) -> Any:
    submitter = Submitter()
    result = await save_existing_by_voice(
        fake.toolkit(database=None),
        submitter,
        history,
        destination=destination,
        as_is=True,
        workspace_id=WS,
        user_id=USER,
        session_id=str(uuid4()),
        scope="s",
    )
    return result, submitter


async def test_voice_asks_the_destination_first_then_only_proposes() -> None:
    fake = FakeNotionApi()
    history = [("user", "explain streams"), ("assistant", LONG)]
    result, submitter = await voice(history, "", fake)
    assert result["status"] == "needs_destination" and submitter.calls == []
    assert "aloud" in result["message"]
    result, submitter = await voice(history, "Study Hub", fake)
    assert result["status"] == "waiting_for_approval" and fake.writes == []
    (call,) = submitter.calls
    (step,) = call["steps"]
    assert step.tool == ns.NOTION_TOOL and call["source"] == "voice"
    assert step.input_data["notion_book"]["inline_markdown"] == LONG.strip()  # copied, not retyped
    assert "Never say it is saved" in result["message"]


async def test_voice_without_prior_content_says_so_and_prepares_nothing() -> None:
    result, submitter = await voice(
        [("user", "hello"), ("assistant", "Hi!")], "Study Hub", FakeNotionApi()
    )
    assert result["status"] == "needs_source" and submitter.calls == []


def test_the_voice_tool_takes_no_text_argument() -> None:
    assert set(SAVE_EXISTING_DEFINITION["properties"]) == {"destination", "as_is"}
