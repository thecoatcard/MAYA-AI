"""Notes / report / book form from the user's words, and the user's style applied to output."""

from __future__ import annotations

import pytest

from app.agents.requirements import extract_requirements
from app.automation.form import detect_form, form_for_context, layout_for, render_options
from app.automation.html_render import render_document_html
from app.automation.model import Document
from app.automation.themes import get_theme, themed


@pytest.mark.parametrize(
    ("text", "form"),
    [
        ("make short notes on Java", "notes"),
        ("java ke notes banao", "notes"),
        ("quick revision cheat sheet", "notes"),
        ("write a book on Java", "book"),
        ("guide notes for beginners", "notes"),  # the user said notes
        ("quarterly sales report", "report"),
        ("something about cats", "report"),
    ],
)
def test_form_is_chosen_from_the_users_words(text: str, form: str) -> None:
    assert detect_form(text) == form
    assert form_for_context({"requirements": {"user_request": text}}) == form


def test_notes_have_no_cover_and_flow_while_books_keep_cover_and_breaks() -> None:
    notes = layout_for("notes", 4)
    assert (notes.cover_page, notes.toc, notes.page_breaks) == (False, False, False)
    assert layout_for("notes", 20).toc is True  # compact contents above 8 pages only
    book = layout_for("book", 4)
    assert (book.cover_page, book.toc, book.page_breaks) == (True, True, True)


def test_render_options_apply_the_users_stated_style_over_the_form_default() -> None:
    req = extract_requirements("dense notes on Java in #b91c1c with the minimal look")
    options = render_options({"requirements": req}, 5)
    assert options["cover_page"] is False and options["density"] == "compact"
    assert options["theme"] == "minimal" and options["accent_color"] == "#B91C1C"
    assert render_options({"requirements": extract_requirements("a book")})["cover_page"] is True


def test_render_options_ignore_an_unknown_theme_or_bad_colour() -> None:
    options = render_options(
        {"requirements": {"user_request": "x", "style": {"theme": "neon", "accent_color": "red"}}}
    )
    assert "theme" not in options and "accent_color" not in options


def test_accent_colour_is_applied_to_the_rendered_document() -> None:
    blocks = [{"type": "heading", "level": 1, "text": "A"}, {"type": "paragraph", "text": "x"}]
    plain = render_document_html(Document.model_validate({"title": "T", "blocks": blocks}))
    tinted = render_document_html(
        Document.model_validate({"title": "T", "accent_color": "#B91C1C", "blocks": blocks})
    )
    assert "#B91C1C" in tinted.upper() and "#B91C1C" not in plain.upper()
    assert themed("notes").colors == get_theme("notes").colors
    assert themed("notes", "#112233").colors.accent == "#112233"


def test_accent_colour_must_be_a_hex_value() -> None:
    with pytest.raises(ValueError):
        Document.model_validate(
            {"title": "T", "accent_color": "red", "blocks": [{"type": "paragraph", "text": "x"}]}
        )


def test_document_prompt_carries_the_user_requirements_and_the_form_rules(tmp_path: object) -> None:
    from pathlib import Path

    from app.tasks.dispatch import FleetSupport

    support = FleetSupport(gateway=object(), artifacts_dir=Path(str(tmp_path)))  # type: ignore[arg-type]
    req = extract_requirements("short notes on Java in simple words, blue theme")
    prompt = support._document_prompt("write the notes", {"requirements": req})
    assert "USER REQUIREMENTS" in prompt and "short notes on Java in simple words" in prompt
    assert "FORM: NOTES" in prompt and "accent_color" in prompt
    plain = support._document_prompt("write it", {})
    assert "USER REQUIREMENTS" not in plain
