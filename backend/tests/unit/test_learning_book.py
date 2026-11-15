"""The learning-book prompts and routing (ADR-0031), offline.

* the prompt builders resolve every placeholder, keep the heading depth rule, take the code
  language from the request or the saved preference and never hard-code Java;
* a notes request is recognised (and a meeting note is not), the destination is read from it, and
  the planner builds the book pipeline in code: 8 to 12 chapters, a Notion step only for Notion.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.agents.learning_book_plan import (
    NOTION_TOOL,
    build_learning_book_plan,
    destination_for,
    detect_code_language,
    is_learning_book_request,
    notes_language,
    notion_hints,
    word_budget,
)
from app.agents.planner import DAGPlanner
from app.agents.prompts import learning_book as lb
from app.services.book_jobs import (
    needs_code_language,
    notion_book_instruction,
    should_route_notion_book,
)
from app.services.document_tools import long_route

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def sample_outline(**extra: Any) -> dict[str, Any]:
    chapters = [
        {
            "index": i,
            "title": f"Title {i}",
            "kind": "code" if i == 5 else "concept",
            "goal": "Brute force to optimized problem" if i == 5 else f"goal {i}",
            "subtopics": ["a", "b"],
            "target_words": 800,
            "needs_code": i == 5,
            "needs_table": False,
            "story_beat": f"beat {i}",
        }
        for i in range(1, 11)
    ]
    return {
        "title": "Book",
        "audience": "a beginner",
        "level": "BEGINNER",
        "story_premise": "Asha fixes a slow app.",
        "chapters": chapters,
        "request": "make study notes on arrays",
        **extra,
    }


# --------------------------------------------------------------------------- prompts
def test_plan_prompt_resolves_every_placeholder_and_states_the_budget() -> None:
    text = lb.build_plan_prompt(
        today="2026-11-13",
        request="study notes on arrays",
        chapters=10,
        total_words=8000,
        pages_per_chapter=1.8,
        body_pages=18,
        code_language="Python",
        material="Some supplied text about arrays.",
    )
    assert PLACEHOLDER.search(text) is None
    assert "EXACTLY 10" in text and "8000 words" in text and "BEGINNER" in text
    assert "Code language for examples: Python." in text
    assert "Some supplied text about arrays." in text and "untrusted" in text
    assert "crawledChapters" not in text and "primaryData" not in text  # OPENCODE leftovers


def test_plan_system_prompt_asks_for_the_story_thread_and_8_to_12_chapters() -> None:
    system = lb.PLAN_SYSTEM_PROMPT
    assert "8 to 12 chapters" in system and "story_beat" in system and "story_premise" in system
    assert "needs_code" in system and "needs_table" in system
    assert "never invent statistics" in system.lower()


def test_chapter_prompts_resolve_placeholders_and_keep_the_heading_depth_rule() -> None:
    out = sample_outline(code_language="Python", notes_language="English")
    system = lb.chapter_system_prompt(out)
    user = lb.build_chapter_prompt(out, out["chapters"][2], 10, today="2026-11-13")
    assert PLACEHOLDER.search(system) is None and PLACEHOLDER.search(user) is None
    assert 'ONLY "#", "##" and "###"' in system and "heading_4" in system
    assert "$...$" in system and "numbered list of steps" in system  # maths and dry-run rules
    assert "never invent statistics" in system.lower()
    assert "Write chapter 3 of 10" in user and "Understanding the Concept" in user
    assert "Chapter Takeaway" in user and "Common Misconceptions" in user


def test_the_story_so_far_carries_only_the_earlier_beats() -> None:
    out = sample_outline()
    user = lb.build_chapter_prompt(out, out["chapters"][3], 10, today="2026-11-13")
    assert "Asha fixes a slow app." in user
    for earlier in (1, 2, 3):
        assert f"Chapter {earlier}: beat {earlier}" in user
    assert "Chapter 5: beat 5" not in user  # later chapters are not known yet
    assert "In THIS chapter: beat 4" in user
    assert "(this chapter opens the story)" in lb.story_so_far(out, 1)


def test_each_chapter_gets_the_template_of_its_role() -> None:
    out = sample_outline()
    roles = [lb.chapter_role(c["index"], 10, c) for c in out["chapters"]]
    assert roles[0] == lb.ROLE_OPENING and roles[-1] == lb.ROLE_CLOSING
    assert roles[4] == lb.ROLE_PROBLEM and roles[1] == lb.ROLE_CONCEPT
    opening = lb.build_chapter_prompt(out, out["chapters"][0], 10, today="d")
    problem = lb.build_chapter_prompt(out, out["chapters"][4], 10, today="d")
    closing = lb.build_chapter_prompt(out, out["chapters"][9], 10, today="d")
    assert "OPENING chapter" in opening and "Start immediately with the story" in opening
    assert "Brute Force Approach" in problem and "Brute vs Better vs Optimized" in problem
    assert "Final Revision Material" in closing and "Knowledge Check" in closing
    assert "Interview / Exam Questions" in closing and "Frequently Asked Questions" in closing


def test_the_code_language_comes_from_the_request_or_the_preference_never_java() -> None:
    python_book = lb.chapter_system_prompt(sample_outline(code_language="Python"))
    assert "use Python for every code example" in python_book and "```python" in python_book
    assert "java" not in python_book.lower()  # not hard-coded anywhere in the book prompts
    unnamed = lb.chapter_system_prompt(sample_outline(code_language=""))
    assert "no programming language was named" in unnamed
    assert "Never assume Java" in unnamed and "```java" not in unnamed
    cpp = lb.code_rule("C++")
    assert "```cpp" in cpp
    assert detect_code_language("dsa notes in Rust") == "Rust"
    assert detect_code_language("dsa notes", "Prefers Python for coding.") == "Python"
    assert detect_code_language("dsa notes", "programming language: Go") == "Go"
    assert detect_code_language("dsa notes", "") == ""  # nothing guessed


def test_notes_are_simple_english_unless_the_user_asked_for_another_language() -> None:
    assert notes_language("java ke notes banao, simple samjhao") == "English"
    assert notes_language("make notes in Hinglish on java") == "Hinglish"
    assert "simple, clear English" in lb.language_rule("English")
    assert "Keep every technical term" in lb.language_rule("Hindi")


def test_sources_are_listed_only_when_they_were_really_fetched() -> None:
    out = sample_outline(sources=[{"title": "Docs", "url": "https://example.org/docs"}])
    opening = lb.build_chapter_prompt(out, out["chapters"][0], 10, today="d")
    assert "Explored Sources" in opening and "https://example.org/docs" in opening
    bare = lb.build_chapter_prompt(sample_outline(), sample_outline()["chapters"][0], 10, today="d")
    assert "Explored Sources" not in bare
    assert "no outside source was read" in lb.chapter_system_prompt(sample_outline())


# --------------------------------------------------------------------------- routing
@pytest.mark.parametrize(
    "text",
    [
        "make study notes on operating systems",
        "create master notes for DBMS in Notion",
        "write a learning book on data structures",
        "java ke notes banao",
        "I need revision notes on thermodynamics as a PDF",
        "notes on photosynthesis in a docx",
    ],
)
def test_a_notes_request_is_recognised(text: str) -> None:
    assert is_learning_book_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "add a note to my page",
        "write the meeting notes",
        "show me my notes",
        "where are my notes",
        "release notes for v2",
        "take notes during the call",
        "summarize my notes",
        "what is the weather",
    ],
)
def test_other_kinds_of_notes_are_not_a_book(text: str) -> None:
    assert not is_learning_book_request(text)


def test_a_stated_length_for_a_file_keeps_the_long_document_pipeline() -> None:
    assert not is_learning_book_request("make notes on java, 50 pages pdf")
    assert not is_learning_book_request("make notes on java, 15 pages pdf")  # enforced on the file
    assert is_learning_book_request("make notes on java, 15 pages in notion")  # only sizes the book
    # A large Notion size is scaled (about 450 words a page), not refused, up to the cap.
    assert is_learning_book_request("make notes on java, 60 pages in notion")
    assert not is_learning_book_request("make notes on java, 900 pages in notion")
    assert is_learning_book_request("make notes on java as a pdf")


def test_the_destination_is_read_from_the_request() -> None:
    assert destination_for("study notes on dbms in Notion") == "notion"
    assert destination_for("study notes on dbms as a word document") == "docx"
    assert destination_for("study notes on dbms") == "pdf"
    assert destination_for("notes as pdf") == "pdf"


def test_the_budget_is_8_to_12_chapters_and_6000_to_10000_words() -> None:
    chapters, words = word_budget("study notes on arrays")
    assert 8 <= chapters <= 12 and 6000 <= words <= 10000
    # A size above the default range is scaled (450 words a page, chapters of ~1,350 words).
    assert word_budget("study notes, 40 pages")[1] == 18000
    chapters, words = word_budget("study notes, 100 pages")
    assert words == 45000 and 30 <= chapters <= 36
    assert word_budget("study notes, 250 pages")[1] == 200 * 450  # the documented cap
    assert word_budget("study notes, 5000 words")[1] == 6000


def test_long_route_sends_a_notes_request_to_the_book_pipeline_for_pdf_and_docx() -> None:
    route = long_route("pdf", None, "make study notes on arrays")
    assert route is not None and route.mode == "learning_book"
    assert 8 <= route.chapters <= 12 and "(format: pdf)" in route.instruction
    docx = long_route("docx", None, "make study notes on arrays")
    assert docx is not None and "(format: docx)" in docx.instruction
    assert long_route("pdf", None, "make study notes on arrays", edits_file_id="f1") is None
    assert long_route("xlsx", None, "make study notes on arrays") is None
    assert long_route("pdf", None, "a one page flyer for my shop") is None


def test_chat_helpers_route_a_notion_page_and_ask_for_a_missing_code_language() -> None:
    args = {"parent_page_id": "0123456789abcdef0123456789abcdef", "title": "DSA"}
    request = "make study notes on dsa in Notion"
    assert should_route_notion_book(request, args)
    assert not should_route_notion_book(request, {"title": "DSA"})  # no parent: normal path
    assert not should_route_notion_book(request, {**args, "database_id": "d"})
    assert not should_route_notion_book("add the note buy milk to Notion", args)
    text = notion_book_instruction(request, args)
    assert notion_hints(text) == {"parent_page_id": args["parent_page_id"], "title": "DSA"}
    assert needs_code_language("make study notes on dsa")  # programming, no language named
    assert not needs_code_language("make study notes on dsa in python")
    assert not needs_code_language("make study notes on photosynthesis")


# --------------------------------------------------------------------------- the plan
class _NoModel:
    client = None

    async def generate_structured(self, **_: Any) -> Any:
        raise AssertionError("a learning book is planned in code, without a model call")


async def test_the_planner_builds_the_book_pipeline_for_notion() -> None:
    parent = "0123456789abcdef0123456789abcdef"
    request = (
        "make master study notes on dsa in notion\n"
        f"Notion parent page: {parent}\nNotion page title: DSA Book"
    )
    intent, nodes = await DAGPlanner(gateway=_NoModel()).plan(request)  # type: ignore[arg-type]
    ids = [n.id for n in nodes]
    chapters = [n for n in nodes if n.id.startswith("chapter_")]
    assert intent == "Write a learning book"
    assert ids[0] == "outline" and ids[-2:] == ["assemble", "notion_page"]
    assert 8 <= len(chapters) <= 12
    assert all(
        c.dependencies == ["outline"] and c.input_data["mode"] == "learning_book" for c in chapters
    )
    assert all(c.input_data["critical"] is False for c in chapters)
    assert nodes[0].input_data["pipeline"]["mode"] == "learning_book"
    assert nodes[0].input_data["pipeline"]["destination"] == "notion"
    assert nodes[0].input_data["file_type"] == "md"
    notion = nodes[-1]
    assert notion.tool == NOTION_TOOL == "notion_create_page_from_job"
    assert notion.dependencies == ["assemble"]
    assert notion.input_data["notion_book"] == {"parent_page_id": parent, "title": "DSA Book"}


@pytest.mark.parametrize(("where", "file_type"), [("as a pdf", "pdf"), ("as a docx", "docx")])
async def test_the_planner_builds_the_book_pipeline_for_a_file(where: str, file_type: str) -> None:
    _, nodes = await DAGPlanner(gateway=_NoModel()).plan(f"make study notes on dbms {where}")  # type: ignore[arg-type]
    assert [n.id for n in nodes][-1] == "assemble"  # no Notion step for a file
    assert all(n.tool is None for n in nodes)
    assert nodes[-1].input_data["file_type"] == file_type


def test_the_plan_reports_the_chapters_the_words_and_the_language_it_found() -> None:
    _, nodes, facts = build_learning_book_plan(
        "study notes on dsa in python", preferences="Prefers Go"
    )
    assert facts["code_language"] == "Python" and 8 <= facts["chapters"] <= 12
    assert nodes[0].input_data["pipeline"]["code_language"] == "Python"
    assert nodes[0].input_data["pipeline"]["notes_language"] == "English"


def test_notion_hints_carry_the_destination_title_the_user_already_chose() -> None:
    from app.agents.learning_book_plan import build_learning_book_plan, notion_hints

    text = "Java streams notes in Notion\nNotion parent title: Ai NOTES"
    assert notion_hints(text)["parent_title"] == "Ai NOTES"
    assert "parent_title" not in notion_hints("no hints")
    _intent, nodes, _facts = build_learning_book_plan(text)
    book = nodes[-1].input_data["notion_book"]
    assert book["parent_title"] == "Ai NOTES" and book["parent_page_id"] == ""
