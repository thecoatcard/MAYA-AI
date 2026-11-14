"""Study-notes layout: the notes theme, cover page, contents list, callout labels, wrapped code.

HTML and the two-pass contents numbers are tested without a browser (a fake renderer writes PDFs
with named destinations); the real print is covered in ``test_long_document_render.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from app.automation.browser_pdf import BrowserRenderInfo
from app.automation.html_render import render_document_html, toc_entries
from app.automation.model import Document
from app.automation.pdf_engine import (
    create_pdf_from_document,
    heading_pages,
    render_timeout_seconds,
)
from app.automation.themes import THEME_NAMES, get_theme


def _document(**fields: Any) -> Document:
    blocks: list[dict[str, Any]] = [
        {"type": "heading", "level": 1, "text": "Chapter 1: Basics"},
        {"type": "paragraph", "text": "Intro."},
        {"type": "heading", "level": 2, "text": "Variables"},
        {"type": "paragraph", "text": "Text."},
        {"type": "page_break"},
        {"type": "heading", "level": 1, "text": "Chapter 2: Classes <b>"},
        {"type": "heading", "level": 3, "text": "Not in the contents"},
        {"type": "paragraph", "text": "More."},
    ]
    data: dict[str, Any] = {"theme": "notes", "title": "Java notes", "blocks": blocks, **fields}
    return Document.model_validate(data)


def test_notes_theme_exists_and_is_light_and_print_friendly() -> None:
    assert "notes" in THEME_NAMES
    theme = get_theme("notes")
    assert not theme.dark and theme.colors.page_bg == "#FFFFFF"
    assert (
        theme.sizes.line_height >= 1.4
        and theme.colors.accent != get_theme("executive").colors.accent
    )


def test_a_report_keeps_the_compact_title_block_and_has_no_anchors_or_contents() -> None:
    html = render_document_html(_document(theme="executive"))
    assert '<header class="cover">' in html and 'class="cover page"' not in html
    assert 'class="toc"' not in html and ' id="h0"' not in html


def test_cover_page_and_contents_list_chapters_and_sections_only() -> None:
    document = _document(cover_page=True, toc=True)
    html = render_document_html(document)
    assert '<header class="cover page">' in html
    assert '<nav class="toc">' in html and "Contents" in html
    assert toc_entries(document) == [
        (0, 1, "Chapter 1: Basics"),
        (2, 2, "Variables"),
        (5, 1, "Chapter 2: Classes <b>"),
    ]
    assert 'href="#h0"' in html and 'id="h5"' in html
    assert "<b>" not in html.split("<nav")[1].split("</nav>")[0]  # escaped, never markup
    # The page-number column is reserved but empty until the numbers are known.
    assert '<span class="n"></span>' in html


def test_contents_numbers_are_printed_only_when_given() -> None:
    html = render_document_html(_document(toc=True), toc_pages={0: 3, 2: 3, 5: 7})
    assert '<span class="n">3</span>' in html and '<span class="n">7</span>' in html
    partial = render_document_html(_document(toc=True), toc_pages={0: 3})
    assert partial.count('<span class="n">3</span>') == 1
    assert partial.count('<span class="n"></span>') == 2  # no number rather than a guess


def test_callouts_are_labelled_note_tip_warning_and_code_is_labelled_and_wrapped() -> None:
    document = Document.model_validate(
        {
            "theme": "notes",
            "title": "T",
            "blocks": [
                {"type": "callout", "kind": "info", "text": "a"},
                {"type": "callout", "kind": "success", "text": "b"},
                {"type": "callout", "kind": "warning", "text": "c"},
                {"type": "code", "language": "java", "text": "int x = 1;"},
                {"type": "code", "language": "java", "text": "\n".join(["line;"] * 40)},
            ],
        }
    )
    html = render_document_html(document)
    labels = re.findall(r'<span class="label">([^<]+)</span>', html)
    assert labels == ["Note", "Tip", "Warning"]
    assert '<div class="code-lang">java</div>' in html
    assert "white-space: pre-wrap" in html and "overflow-wrap: anywhere" in html
    # Layout rule replaced on purpose: every code block may split across pages (orphans/widows
    # keep a short one whole), so there is no separate "long" variant any more.
    assert html.count("<pre>") == 2 and '<pre class="long">' not in html
    assert "thead {" in html and "table-header-group" in html  # repeated table headers
    assert "@bottom-right" in html and "counter(pages)" in html  # page X of Y


def test_cover_page_hides_the_running_header_and_footer_on_the_first_page() -> None:
    html = render_document_html(_document(cover_page=True))
    assert "@page :first { @top-left { content: none; } @bottom-left { content: none; }" in html


# --------------------------------------------------------------------------- two-pass numbers
class _DestRenderer:
    """Writes a PDF whose headings are named destinations, like the real browser does.

    ``maps`` gives, per print, the 1-based page of heading ``hN``.
    """

    timeout_seconds = 45.0

    def __init__(self, *maps: dict[int, int] | None) -> None:
        self.maps = list(maps)
        self.prints: list[str] = []

    def render(self, html: str, output_path: Path | str, **_: Any) -> BrowserRenderInfo:
        self.prints.append(html)
        mapping = self.maps.pop(0) if self.maps else None
        writer = PdfWriter()
        for _page in range(8):
            writer.add_blank_page(width=595, height=842)
        for index, page in (mapping or {}).items():
            writer.add_named_destination(f"h{index}", page - 1)
        with Path(output_path).open("wb") as handle:
            writer.write(handle)
        return BrowserRenderInfo(channel="msedge", version="1")


def test_two_prints_put_the_measured_page_numbers_in_the_contents(tmp_path: Path) -> None:
    pages = {0: 3, 2: 3, 5: 6}
    renderer = _DestRenderer(pages, pages)
    result = create_pdf_from_document(
        _document(cover_page=True, toc=True),
        tmp_path / "a.pdf",
        renderer=renderer,  # type: ignore[arg-type]
    )
    assert result.passes == 2 and result.toc_numbered is True and result.warnings == []
    assert '<span class="n"></span>' in renderer.prints[0]  # first print: nothing guessed
    assert '<span class="n">3</span>' in renderer.prints[1]
    assert '<span class="n">6</span>' in renderer.prints[1]
    assert heading_pages(result.path, toc_entries(_document(toc=True))) == pages
    assert not list(tmp_path.glob("*.tmp"))


def test_unmeasurable_pages_ship_the_contents_without_numbers_and_say_so(tmp_path: Path) -> None:
    renderer = _DestRenderer(None)  # the PDF has no named destinations
    result = create_pdf_from_document(
        _document(toc=True),
        tmp_path / "b.pdf",
        renderer=renderer,  # type: ignore[arg-type]
    )
    assert result.passes == 1 and result.toc_numbered is False
    assert any("no page numbers" in w for w in result.warnings)
    assert '<span class="n"></span>' in renderer.prints[0]  # never a fake number


def test_numbers_that_moved_between_prints_are_dropped_not_guessed(tmp_path: Path) -> None:
    renderer = _DestRenderer({0: 3, 2: 3, 5: 6}, {0: 3, 2: 4, 5: 7})
    result = create_pdf_from_document(
        _document(toc=True),
        tmp_path / "c.pdf",
        renderer=renderer,  # type: ignore[arg-type]
    )
    assert result.passes == 2 and result.toc_numbered is False
    assert any("moved between two prints" in w for w in result.warnings)
    # The kept file is the first print, whose contents have no numbers.
    assert heading_pages(result.path, toc_entries(_document(toc=True))) == {0: 3, 2: 3, 5: 6}


def test_a_report_without_contents_is_printed_once(tmp_path: Path) -> None:
    renderer = _DestRenderer(None)
    result = create_pdf_from_document(
        _document(),
        tmp_path / "d.pdf",
        renderer=renderer,  # type: ignore[arg-type]
    )
    assert result.passes == 1 and result.toc_numbered is None and len(renderer.prints) == 1


def test_render_budget_grows_with_block_count_and_is_capped() -> None:
    assert render_timeout_seconds(10) == 45.0
    assert 45.0 < render_timeout_seconds(600) < 240.0
    assert render_timeout_seconds(100_000) == 240.0


@pytest.mark.parametrize("theme", THEME_NAMES)
def test_every_theme_renders_a_cover_and_contents(theme: str) -> None:
    html = render_document_html(_document(theme=theme, cover_page=True, toc=True))
    assert f'data-theme="{theme}"' in html and 'class="toc"' in html
