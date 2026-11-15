"""The form of a document: ``notes``, ``report`` or ``book`` (chosen from the user's words).

A user who asks for "short notes" must not get a book (cover page, table of contents, every chapter
on a new page, long narrative prose). The form is chosen deterministically from the request
(English and Hinglish keywords) and decides the layout flags and the content-style rules written
into the prompts:

* ``notes``: no cover page, no contents list (a compact one above 8 pages), sections flow without
  page breaks, dense layout, bullet points and short paragraphs.
* ``report``: the default for pdf and docx: a compact title block, contents above 8 pages,
  sections flow.
* ``book``: cover page, contents list and every chapter on a new page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Form = Literal["notes", "report", "book"]
FORMS: tuple[Form, ...] = ("notes", "report", "book")
DEFAULT_FORM: Form = "report"
COMPACT_TOC_PAGES = 8

_NOTES = re.compile(
    r"\bnotes?\b|\bcheat[\s-]?sheets?\b|\bsummary\b|\bsummaries\b|\brevision\b|"
    r"\bquick[\s-]reference\b|\bnotes\s+banao\b|\bnote\s+banao\b|नोट्स",
    re.IGNORECASE,
)
_BOOK = re.compile(
    r"\bbooks?\b|\bguide\b|\bhandbook\b|\bmanual\b|\btextbook\b|\be-?book\b|\bkitab\b|किताब",
    re.IGNORECASE,
)
_REPORT = re.compile(
    r"\breports?\b|\bwhite[\s-]?paper\b|\bproposal\b|\banalysis\b|\bcase\s+study\b",
    re.IGNORECASE,
)


def detect_form(text: str | None, planner_form: str | None = None) -> Form:
    """The form for a request: an explicit planner value wins, then the user's words.

    ``notes`` beats ``book`` beats ``report`` when several words appear ("guide notes" is notes:
    the user said notes); with none of them the default is ``report``.
    """
    if planner_form in FORMS:
        return planner_form
    value = text or ""
    if _NOTES.search(value):
        return "notes"
    if _BOOK.search(value):
        return "book"
    if _REPORT.search(value):
        return "report"
    return DEFAULT_FORM


@dataclass(frozen=True)
class FormLayout:
    cover_page: bool
    toc: bool
    page_breaks: bool  # a page break before each chapter
    density: Literal["normal", "compact"]


def layout_for(form: str, pages: int | None) -> FormLayout:
    """Layout flags for a form and a requested page count (``None`` = not stated)."""
    long_enough = bool(pages and pages > COMPACT_TOC_PAGES)
    if form == "book":
        return FormLayout(cover_page=True, toc=True, page_breaks=True, density="normal")
    return FormLayout(cover_page=False, toc=long_enough, page_breaks=False, density="normal")


NOTES_STYLE_RULES = (
    "\nFORM: NOTES. Write study notes, not a book: bullet points and short paragraphs (at most "
    "three sentences), key terms in **bold**, one-line definitions, tiny code snippets and "
    "examples, comparison tables, and a short 'Remember' callout where a rule is easy to forget. "
    "No long narrative prose, no chapter introductions or conclusions, no filler sentences."
)
BOOK_STYLE_RULES = (
    "\nFORM: BOOK. Write in a book's voice: a short introduction per chapter, explanations with "
    "worked examples and a closing summary."
)
REPORT_STYLE_RULES = (
    "\nFORM: REPORT. Write a professional report: a clear structure, concise paragraphs, tables "
    "for comparisons, no chapter-style introductions."
)
STYLE_RULES: dict[str, str] = {
    "notes": NOTES_STYLE_RULES,
    "book": BOOK_STYLE_RULES,
    "report": REPORT_STYLE_RULES,
}


def style_rules(form: str | None) -> str:
    return STYLE_RULES.get(form or DEFAULT_FORM, REPORT_STYLE_RULES)


def form_for_context(ctx: dict[str, object] | None) -> Form:
    """The form for a task context: the user's verbatim request (``requirements``) decides."""
    req = (ctx or {}).get("requirements")
    text = str(req.get("user_request") or "") if isinstance(req, dict) else ""
    if not text:
        text = str((ctx or {}).get("description") or "")
    return detect_form(text)


def render_options(ctx: dict[str, object] | None, pages: int | None = None) -> dict[str, object]:
    """Renderable choices for a document: cover, contents, density, theme and accent colour.

    The form (notes / report / book) comes from the user's words; a theme, accent colour or
    density the user stated explicitly (``requirements.style``) wins over the form default.
    """
    from app.automation.themes import THEME_NAMES  # local: keeps this module import-light

    form = form_for_context(ctx)
    layout = layout_for(form, pages)
    req = (ctx or {}).get("requirements")
    style = req.get("style") if isinstance(req, dict) else None
    style = style if isinstance(style, dict) else {}
    options: dict[str, object] = {
        "form": form,
        "cover_page": layout.cover_page,
        "toc": layout.toc,
        "page_breaks": layout.page_breaks,
        "density": "compact" if style.get("density") == "compact" else layout.density,
    }
    if style.get("theme") in THEME_NAMES:
        options["theme"] = style["theme"]
    elif form == "notes":
        options["theme"] = "notes"
    accent = style.get("accent_color")
    if isinstance(accent, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", accent):
        options["accent_color"] = accent.upper()
    return options
