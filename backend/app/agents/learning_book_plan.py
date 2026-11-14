"""Routing and plan of a notes request: the learning-book pipeline (ADR-0031).

A request for study notes, master notes, learning notes or a book (PDF, DOCX or Notion) is not
one model call: it is planned here, in code and without a model call, as

``outline`` (the plan: 8 to 12 chapters with goals, budgets and a story beat each)
  -> ``chapter_1`` ... ``chapter_N`` (Markdown, three at a time, the story thread passed forward)
  -> ``assemble`` (deterministic: a PDF or DOCX through the document agent, or for Notion the
     assembled Markdown kept as an ``.md`` artifact)
  -> ``notion_page`` (Notion only: ``notion_create_page_from_job``, APPROVAL_REQUIRED, bound to the
     exact Markdown).

It reuses the step ids and the ``pipeline`` markers of the long-document plan
(``app.agents.long_document_plan``); ``pipeline["mode"] == "learning_book"`` switches the prompts.
"""

from __future__ import annotations

import os
import re
from typing import Any, Final

from app.agents.plan_repair import PERSISTED_EXECUTOR_ROLE
from app.agents.prompts.learning_book import (
    DEFAULT_CHAPTERS,
    DEFAULT_LEVEL,
    DEFAULT_TOTAL_WORDS,
    MAX_CHAPTERS,
    MAX_TOTAL_WORDS,
    MIN_CHAPTERS,
    MIN_TOTAL_WORDS,
)
from app.agents.state import SubTask
from app.automation.acceptance import NOTES_WORDS_PER_PAGE, extract_acceptance
from app.tasks.pipeline import STAGE_ASSEMBLE, STAGE_CHAPTER, STAGE_OUTLINE

MODE: Final = "learning_book"
OUTLINE_ID: Final = "outline"
ASSEMBLE_ID: Final = "assemble"
NOTION_ID: Final = "notion_page"
NOTION_TOOL: Final = "notion_create_page_from_job"

# "notes" in the sense of study material. Everything that is a note of another kind is excluded
# first (a meeting note, release notes, "add a note", "where are my notes", a notes app).
_NOT_STUDY: Final = re.compile(
    r"\b(meeting|release|patch|sticky|speaker|call|lab|field|stage)\s+notes?\b"
    r"|\bnotes?\s+(app|taking|section)\b|\b(take|add|save|jot|leave)\s+(down\s+)?(a\s+)?notes?\b"
    r"|\bnotes?\s+(for|of|from|on)\s+(the\s+|my\s+|our\s+)?"
    r"(meeting|call|standup|sync|today|tomorrow)\b"
    r"|\b(my|existing|old|saved)\s+notes?\b|\b(find|search|show|open|read|list|delete|edit)\b"
    r".{0,24}\bnotes?\b|\bnote\s+that\b|\bsummar[iy][sz]e\b",
    re.IGNORECASE,
)
_STUDY: Final = re.compile(
    r"\b(study|master|learning|revision|lecture|exam|class|course|complete|detailed|full|"
    r"comprehensive|beginner|interview|preparation|prep)\s+(notes?|book)\b"
    r"|\bnotes?\s+(on|about|for|of|regarding)\s+\S"
    r"|\b(make|create|write|prepare|generate|build|compose|draft)\s+(me\s+|us\s+)?(a\s+|the\s+)?"
    r"(\w+\s+){0,3}(notes|book)\b"
    r"|\b(\w+\s+){1,3}(ke|ki|ka)\s+(\w+\s+)?notes\b|\bnotes\s+(banao|bana\s+do|likho|chahiye)\b"
    r"|\b(learning|master)\s+book\b|\bbook\s+(on|about)\s+\S",
    re.IGNORECASE,
)
_NOTION: Final = re.compile(r"\bnotion\b", re.IGNORECASE)
_DOCX: Final = re.compile(r"\b(docx|word document|ms word|\.docx)\b", re.IGNORECASE)
_PDF: Final = re.compile(r"\bpdf\b", re.IGNORECASE)
_NOTES_LANGUAGE: Final = re.compile(
    r"\b(?:in|into)\s+(hinglish|hindi)\b|\b(hinglish|hindi)\s+(?:mein|me|notes|language)\b",
    re.IGNORECASE,
)
_PARENT_LINE: Final = re.compile(r"Notion parent page:\s*(\S+)", re.IGNORECASE)
_TITLE_LINE: Final = re.compile(r"Notion page title:\s*(.+)", re.IGNORECASE)
# The destination the user ALREADY chose on the gate card, as a page title (no id known yet): the
# Notion step resolves it by an exact title match and only asks when that is not possible.
_PARENT_TITLE_LINE: Final = re.compile(r"Notion parent title:\s*(.+)", re.IGNORECASE)
_CODE_LANGUAGES: Final = (
    "python|java|javascript|typescript|c\\+\\+|cpp|c#|csharp|golang|go|rust|kotlin|swift|ruby|php|"
    "scala|sql|r|dart|c"
)
_LANG_ALIASES: Final = {"cpp": "C++", "csharp": "C#", "golang": "Go", "javascript": "JavaScript"}
_PROGRAMMING_TOPIC: Final = re.compile(
    r"\b(dsa|data structures?|algorithms?|leetcode|coding|programming|code|python|java|javascript|"
    r"typescript|c\+\+|golang|rust|kotlin|swift|sql|react|node|django|spring|backend|api|"
    r"recursion|array|linked list|tree|graph|sorting|dynamic programming)\b",
    re.IGNORECASE,
)


def is_notes_request(text: str) -> bool:
    """True for a request for study notes, master notes, learning notes or a book."""
    body = " ".join((text or "").split())
    if not body or _NOT_STUDY.search(body):
        return False
    return _STUDY.search(body) is not None


def stated_words(text: str) -> int:
    """The length (in words) the user stated, from pages or words; 0 when none."""
    acceptance = extract_acceptance(text)
    return acceptance.get("words") or acceptance.get("pages", 0) * NOTES_WORDS_PER_PAGE


def is_learning_book_request(text: str) -> bool:
    """True for a notes request the learning-book pipeline writes: study notes, master notes,
    learning notes or a book.

    A PDF or DOCX with a STATED length ("10 pages") keeps the long-document pipeline, which
    enforces that length on the file; a Notion page has no enforced length, so a stated length
    only sizes the book (6,000 to 10,000 words)."""
    if not is_notes_request(text):
        return False
    stated = stated_words(text)
    if not stated:
        return True
    return destination_for(text) == "notion" and stated <= max_book_pages() * WORDS_PER_PAGE * 1.15


def destination_for(text: str) -> str:
    """``notion``, ``docx`` or ``pdf`` (the default) for where the notes go."""
    if _NOTION.search(text or ""):
        return "notion"
    if _DOCX.search(text or "") and not _PDF.search(text or ""):
        return "docx"
    return "pdf"


def notes_language(text: str) -> str:
    """Simple English, unless the user asked for Hindi or Hinglish notes explicitly."""
    found = _NOTES_LANGUAGE.search(text or "")
    if found is None:
        return "English"
    return (found.group(1) or found.group(2)).capitalize()


def is_programming_topic(text: str) -> bool:
    return _PROGRAMMING_TOPIC.search(text or "") is not None


def detect_code_language(request: str, preferences: str = "") -> str:
    """The programming language the notes' code uses: named in the request, else the user's saved
    preference, else ``""`` (never a guess, never a default such as Java)."""
    named = re.search(
        rf"\b(?:in|using|with|use)\s+({_CODE_LANGUAGES})\b(?!\s*(?:notes|book))",
        request or "",
        re.IGNORECASE,
    )
    if named is None:
        named = re.search(
            rf"\b({_CODE_LANGUAGES})\s+(?:code|solutions?|examples?)\b",
            request or "",
            re.IGNORECASE,
        )
    if named is not None:
        return _pretty(named.group(1))
    pref = re.search(
        rf"(?:programming|coding|code)[ -]?language[^.\n]{{0,40}}?\b({_CODE_LANGUAGES})\b"
        rf"|\b(?:prefers?|favou?rite|default|likes?|uses?)\s+(?:the\s+)?({_CODE_LANGUAGES})\b"
        rf"(?:\s+(?:for|as)\s+(?:coding|programming|code))?",
        preferences or "",
        re.IGNORECASE,
    )
    if pref is not None:
        return _pretty(pref.group(1) or pref.group(2))
    return ""


def _pretty(raw: str) -> str:
    low = raw.strip().lower()
    return _LANG_ALIASES.get(low, low.upper() if low in ("sql", "php", "r") else low.capitalize())


def notion_hints(text: str) -> dict[str, str]:
    """The Notion parent page and page title the chat put on the instruction (empty when absent)."""
    parent = _PARENT_LINE.search(text or "")
    title = _TITLE_LINE.search(text or "")
    parent_title = _PARENT_TITLE_LINE.search(text or "")
    found = {
        "parent_page_id": parent.group(1).strip() if parent else "",
        "title": title.group(1).strip()[:200] if title else "",
    }
    if parent_title is not None and parent_title.group(1).strip():
        found["parent_title"] = parent_title.group(1).strip()[:120]  # only when chosen
    return found


WORDS_PER_PAGE: Final = 450  # a stated size is scaled at about 400 to 500 words a page
CHAPTER_WORDS: Final = 1350  # and chapters of about 1,200 to 1,500 words
DEFAULT_MAX_PAGES: Final = 200
MAX_BOOK_CHAPTERS: Final = 80
LARGE_BOOK_WORDS: Final = 12_000  # above this the size is scaled instead of clamped


def max_book_pages() -> int:
    """The largest book MAYA builds (``MAYA_LEARNING_BOOK_MAX_PAGES``, default 200 pages)."""
    try:
        value = int(os.environ.get("MAYA_LEARNING_BOOK_MAX_PAGES", DEFAULT_MAX_PAGES))
    except ValueError:
        return DEFAULT_MAX_PAGES
    return max(10, min(value, 1000))


def book_estimate(chapters: int, total_words: int) -> str:
    """An honest size, call and time estimate to show before the work starts."""
    calls = chapters + 5  # outline, chapters, assemble and verification
    low = max(3, round((chapters / 3) * 1.5 + 2))
    return (
        f"about {chapters} chapters, ~{total_words:,} words, ~{calls} model calls, roughly "
        f"{low}-{round(low * 1.6)} minutes"
    )


def word_budget(text: str) -> tuple[int, int]:
    """``(chapters, total words)`` for a request: 8 to 12 chapters and 6000 to 10000 words,
    scaled to a length the user stated. A size above that (``100 pages``, ``40 chapters``) is
    scaled (about 450 words a page, chapters of about 1,350 words) up to
    ``max_book_pages()`` pages and 80 chapters; it is never refused as too large."""
    acceptance = extract_acceptance(text)
    wanted = acceptance.get("words") or (acceptance.get("pages", 0) * NOTES_WORDS_PER_PAGE or 0)
    pages = acceptance.get("pages", 0) or (wanted // WORDS_PER_PAGE)
    if pages and pages * WORDS_PER_PAGE > LARGE_BOOK_WORDS:
        total_big = min(pages, max_book_pages()) * WORDS_PER_PAGE
        return min(
            MAX_BOOK_CHAPTERS, max(MAX_CHAPTERS, round(total_big / CHAPTER_WORDS))
        ), total_big
    total = DEFAULT_TOTAL_WORDS
    if wanted:
        total = max(MIN_TOTAL_WORDS, min(MAX_TOTAL_WORDS, wanted))
    chapters = DEFAULT_CHAPTERS
    if total <= 6500:
        chapters = MIN_CHAPTERS
    elif total >= 9500:
        chapters = MAX_CHAPTERS
    return chapters, total


def build_learning_book_plan(
    instruction: str, *, preferences: str = ""
) -> tuple[str, list[SubTask], dict[str, Any]]:
    """``(intent, subtasks, facts)`` for a notes request; ``facts`` is what the chat tells the
    user (destination, chapters, words)."""
    destination = destination_for(instruction)
    chapters, total_words = word_budget(instruction)
    body_pages = round(total_words / NOTES_WORDS_PER_PAGE, 1)
    file_type = "md" if destination == "notion" else destination
    code_language = detect_code_language(instruction, preferences)
    shared: dict[str, Any] = {"file_type": file_type, "mode": MODE, "destination": destination}
    acceptance = extract_acceptance(instruction)
    if acceptance and destination != "notion":
        shared["acceptance"] = dict(acceptance)  # a stated length is checked on the file
    nodes: list[SubTask] = [
        SubTask(
            id=OUTLINE_ID,
            assigned_agent=PERSISTED_EXECUTOR_ROLE,
            description=f"Plan the learning book: {chapters} chapters, about {total_words} words",
            dependencies=[],
            input_data={
                "executor": "outline",
                "critical": True,
                "step_label": "Plan the book",
                "pipeline": {
                    "stage": STAGE_OUTLINE,
                    "mode": MODE,
                    "slots": chapters,
                    "target_pages": round(body_pages),
                    "body_pages": body_pages,
                    "total_words": total_words,
                    "pages_per_chapter": round(body_pages / chapters, 1),
                    "request": instruction[:2000],
                    "level": DEFAULT_LEVEL,
                    "code_language": code_language,
                    "notes_language": notes_language(instruction),
                    "destination": destination,
                },
                **shared,
            },
        )
    ]
    for index in range(1, chapters + 1):
        nodes.append(
            SubTask(
                id=f"chapter_{index}",
                assigned_agent=PERSISTED_EXECUTOR_ROLE,
                description=f"Chapter {index} of {chapters} (title set from the plan)",
                dependencies=[OUTLINE_ID],
                input_data={
                    "executor": "chapter",
                    "critical": False,
                    "step_label": f"Chapter {index} of {chapters}",
                    "pipeline": {
                        "stage": STAGE_CHAPTER,
                        "mode": MODE,
                        "index": index,
                        "of": chapters,
                    },
                    **shared,
                },
            )
        )
    deps = [OUTLINE_ID, *(f"chapter_{i}" for i in range(1, chapters + 1))]
    kind = {"notion": "Markdown book", "pdf": "PDF", "docx": "DOCX"}[destination]
    nodes.append(
        SubTask(
            id=ASSEMBLE_ID,
            assigned_agent="document",
            description=f"Assemble the {kind}: contents and {chapters} chapters",
            dependencies=deps,
            input_data={
                "critical": True,
                "step_label": "Assemble",
                "pipeline": {
                    "stage": STAGE_ASSEMBLE,
                    "mode": MODE,
                    "slots": chapters,
                    "target_pages": round(body_pages),
                    "destination": destination,
                },
                **shared,
            },
        )
    )
    if destination == "notion":
        hints = notion_hints(instruction)
        nodes.append(
            SubTask(
                id=NOTION_ID,
                assigned_agent="pm",
                description="Create the Notion page from the finished book (needs your approval)",
                dependencies=[ASSEMBLE_ID],
                input_data={
                    "critical": True,
                    "step_label": "Create the Notion page",
                    "notion_book": {
                        "parent_page_id": hints["parent_page_id"],
                        "title": hints["title"],
                        **(
                            {"parent_title": hints["parent_title"]}
                            if hints.get("parent_title")
                            else {}
                        ),
                    },
                },
                tool=NOTION_TOOL,
                tool_args={},
            )
        )
    facts = {
        "destination": destination,
        "chapters": chapters,
        "words": total_words,
        "code_language": code_language,
    }
    return "Write a learning book", nodes, facts


__all__ = [
    "ASSEMBLE_ID",
    "MODE",
    "NOTION_ID",
    "NOTION_TOOL",
    "OUTLINE_ID",
    "book_estimate",
    "build_learning_book_plan",
    "destination_for",
    "is_notes_request",
    "max_book_pages",
    "detect_code_language",
    "is_learning_book_request",
    "is_programming_topic",
    "notes_language",
    "notion_hints",
    "word_budget",
]
