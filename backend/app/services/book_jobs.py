"""Chat side of the learning-book pipeline (ADR-0031): the job instruction and the replies.

The chat model never writes a book itself. A notes request (PDF, DOCX or Notion) becomes ONE
background job through the normal task submission path; the planner
(``app.agents.learning_book_plan``) turns that instruction into the book pipeline. These helpers
keep the wording in one place and are pure, so they are unit-tested without a chat turn.
"""

from __future__ import annotations

from typing import Any, Final

from app.agents.learning_book_plan import (
    book_estimate,
    destination_for,
    detect_code_language,
    is_learning_book_request,
    is_programming_topic,
    word_budget,
)

__all__ = [
    "book_estimate",
    "book_started_message",
    "language_question_result",
    "needs_code_language",
    "notion_book_instruction",
    "should_route_notion_book",
    "word_budget",
]

MAX_PARENT_CHARS: Final = 600


def needs_code_language(request: str) -> bool:
    """True when the book is about programming and the request names no code language."""
    return is_programming_topic(request) and not detect_code_language(request)


def should_route_notion_book(request: str, args: dict[str, Any]) -> bool:
    """A ``notion_create_page`` call for a notes request, with a parent page and no database row:
    the chat model's own short page is replaced by the book pipeline."""
    return (
        is_learning_book_request(request)
        and bool(str(args.get("parent_page_id") or "").strip())
        and not args.get("database_id")
        and destination_for(request) == "notion"
    )


def notion_book_instruction(request: str, args: dict[str, Any]) -> str:
    """The job instruction for a Notion book: the user's request plus the parent page and title
    the chat model resolved (the planner reads the two marked lines)."""
    parent = " ".join(str(args.get("parent_page_id") or "").split())[:MAX_PARENT_CHARS]
    title = " ".join(str(args.get("title") or "").split())[:200]
    lines = [" ".join((request or "").split()) + " (destination: Notion page)"]
    lines.append(f"Notion parent page: {parent}")
    if title:
        lines.append(f"Notion page title: {title}")
    return "\n".join(lines)


def language_question_result() -> dict[str, Any]:
    """What the model gets back when a programming book names no code language: ask once."""
    return {
        "status": "needs_language",
        "message": (
            "Nothing was started. The notes are about programming but name no programming "
            "language. If the saved preferences in your context already name the user's "
            "programming language, call start_job NOW with the full request and that language "
            "written in it (for example 'in Python'). Otherwise call ask_user once to ask which "
            "programming language the code examples should use (offer the common ones), and when "
            "the answer arrives call start_job with the full request and the chosen language in "
            "it. Never pick a language yourself and never assume Java."
        ),
    }


def book_started_message(destination: str, chapters: int, estimate: str = "") -> str:
    """The instruction to the model after the book job started."""
    where = {
        "notion": (
            "When the book is written, a Notion page waits for the user's approval on a card "
            "(outline, counts and the full text can be opened there). Say that, and that nothing "
            "is written to Notion before they approve."
        ),
        "docx": "The Word document will be a file on the Tasks page when it is finished.",
        "pdf": "The PDF will be a file on the Tasks page when it is finished.",
    }.get(destination, "")
    return (
        f"A learning book of about {chapters} chapters is being written in the background"
        + (f" ({estimate})" if estimate else "")
        + ", "
        "chapter by chapter, and its progress is on the Tasks page. Nothing exists yet. Your whole "
        "final answer is two short sentences: say that, and give the job id. " + where + " Do not "
        "say it is finished and do not write a shorter version meanwhile."
    )
