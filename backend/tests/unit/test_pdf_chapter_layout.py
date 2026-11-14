"""Chapter layout of long documents, measured on the real browser's PDF (pypdf geometry).

The document is a cover, a contents list and three chapters (intro, three subsections, a Java code
block, tables, a callout, key takeaways, exercises). The tests assert where text really sits on
each page: chapters start on a fresh page, the contents page holds no body text, no interior page
is mostly empty, no heading is stranded at the bottom, the contents numbers equal the real pages,
the footer counts every page (the cover is page 1 and shows no number), and a split table repeats
its header row. They are SKIPPED without Edge/Chrome. Geometry only: no pixels are rendered.

Numbering rule (documented in ``html_render``): the cover, when present, counts as page 1 but prints
no running header, brand or number; the contents list is the next page; "Page X of N" counts every
page of the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.automation.browser_pdf import shutdown_browser_renderer
from app.automation.html_render import render_document_html, toc_entries
from app.automation.model import Document
from app.automation.pdf_engine import RENDERER_BROWSER, create_pdf_from_document
from app.automation.themes import themed
from tests.support.documents import requires_browser

PAGE_H = 842.0  # A4 in points (the PDF is A4 portrait)
MAX_EMPTY_BOTTOM = 0.35


@pytest.fixture(autouse=True, scope="module")
def _close_shared_browser() -> object:
    yield
    shutdown_browser_renderer()


# --------------------------------------------------------------------------- the document
def _chapter(n: int, title: str, *, long_table: bool = False) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = [
        {"type": "heading", "level": 1, "text": f"Chapter {n}: {title}"},
        {"type": "paragraph", "text": f"BODY-{n}-intro " + "Streams process data lazily. " * 30},
    ]
    for sub in range(1, 4):
        blocks += [
            {"type": "heading", "level": 2, "text": f"Topic {n}.{sub}"},
            {"type": "paragraph", "text": f"BODY-{n}-{sub} " + "A pipeline has a source. " * 45},
            {"type": "bullets", "items": [f"point {i} " + "detail " * 12 for i in range(4)]},
        ]
        if sub == 1:
            blocks.append(
                {
                    "type": "code",
                    "language": "java",
                    "text": "\n".join(
                        f"    list.stream().filter(x -> x > {i}).map(String::valueOf);"
                        for i in range(22)
                    ),
                }
            )
        if sub == 2:
            rows = [
                [f"ROW-{n}-{i}", "terminal", "lazy " * 6] for i in range(40 if long_table else 5)
            ]
            blocks.append(
                {
                    "type": "table",
                    "headers": [f"Operator{n}", "Kind", "Notes"],
                    "rows": rows,
                }
            )
        if sub == 3:
            blocks.append(
                {"type": "callout", "kind": "info", "text": "Remember: streams are single use."}
            )
    blocks += [
        {"type": "heading", "level": 2, "text": f"Key takeaways {n}"},
        {"type": "bullets", "items": ["Lazy evaluation", "Single use", "Prefer pure functions"]},
        {"type": "heading", "level": 2, "text": f"Exercises {n}"},
        {"type": "numbered", "items": ["Filter the list.", "Sum the values.", "Group by key."]},
    ]
    return blocks


def _long_document() -> Document:
    blocks: list[dict[str, object]] = []
    blocks += _chapter(1, "Stream Fundamentals")
    blocks += _chapter(2, "Intermediate Operations", long_table=True)
    blocks += _chapter(3, "Collectors")
    return Document.model_validate(
        {
            "theme": "notes",
            "title": "Java Streams Reference Notes",
            "author": "Test Author",
            "cover_page": True,
            "toc": True,
            "blocks": blocks,
        }
    )


# --------------------------------------------------------------------------- geometry
@dataclass
class Line:
    page: int  # 1-based
    y: float
    text: str


def _origin(cm: list[float], tm: list[float]) -> tuple[float, float]:
    """Page position (x, y from the bottom) of a text run: the text matrix inside the CTM."""
    x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
    y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
    return x, y


def _lines(path: Path) -> list[Line]:
    """Text lines of every page with their baseline y (points from the page bottom)."""
    out: list[Line] = []
    for number, page in enumerate(PdfReader(str(path)).pages, start=1):
        pieces: list[tuple[float, float, str]] = []

        def visit(
            text: str,
            cm: list[float],
            tm: list[float],
            _fd: object,
            _fs: float,
            pieces: list[tuple[float, float, str]] = pieces,
        ) -> None:
            if text.strip():
                x, y = _origin(cm, tm)
                pieces.append((round(y / 2) * 2, x, text))  # y bucket, x, text

        page.extract_text(visitor_text=visit)
        for y in sorted({p[0] for p in pieces}, reverse=True):
            row = sorted((p for p in pieces if p[0] == y), key=lambda p: p[1])
            out.append(Line(number, float(y), "".join(p[2] for p in row).strip()))
    return out


def _body_lines(lines: list[Line], margin_mm: float) -> list[Line]:
    """Lines inside the text area, so the running header and the footer are left out."""
    mm = 72 / 25.4
    top = PAGE_H - margin_mm * mm
    bottom = (margin_mm + 2) * mm
    return [ln for ln in lines if bottom - 2 <= ln.y <= top + 2]


def _find(lines: list[Line], pattern: str, after_page: int = 0) -> Line:
    rx = re.compile(pattern)
    for ln in lines:
        if ln.page > after_page and rx.fullmatch(ln.text):
            return ln
    raise AssertionError(f"no line matches {pattern!r} after page {after_page}")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    document = _long_document()
    path = tmp_path_factory.mktemp("chapters") / "long.pdf"
    result = create_pdf_from_document(document, path)
    assert result.renderer == RENDERER_BROWSER, result.warnings
    margin = themed(document.theme, None, None).sizes.page_margin_mm
    lines = _lines(result.path)
    return {
        "document": document,
        "result": result,
        "lines": lines,
        "body": _body_lines(lines, margin),
        "pages": len(PdfReader(str(result.path)).pages),
    }


@requires_browser
def test_every_chapter_heading_starts_at_the_top_of_a_page(built: dict[str, object]) -> None:
    body: list[Line] = built["body"]  # type: ignore[assignment]
    mm = 72 / 25.4
    margin = themed("notes", None, None).sizes.page_margin_mm
    top_zone = PAGE_H - margin * mm - 45  # within ~45 pt below the top margin
    for n in (1, 2, 3):
        heading = _find(body, rf"Chapter {n}: .*", after_page=2)  # not the contents entry
        first_on_page = max((ln.y for ln in body if ln.page == heading.page), default=0)
        assert heading.y >= top_zone, f"chapter {n} starts mid-page (y={heading.y})"
        assert heading.y == first_on_page, f"chapter {n} is not the first text of its page"


@requires_browser
def test_the_contents_page_has_no_chapter_body_text(built: dict[str, object]) -> None:
    lines: list[Line] = built["lines"]  # type: ignore[assignment]
    contents = _find(lines, "Contents")
    on_page = " ".join(ln.text for ln in lines if ln.page == contents.page)
    assert "BODY-" not in on_page
    assert contents.page == 2, "cover is page 1, contents is page 2"
    first_chapter = _find(lines, r"Chapter 1: .*", after_page=contents.page)
    assert first_chapter.page > contents.page


@requires_browser
def test_no_interior_page_is_mostly_empty_except_a_chapter_end(built: dict[str, object]) -> None:
    body: list[Line] = built["body"]  # type: ignore[assignment]
    total: int = built["pages"]  # type: ignore[assignment]
    mm = 72 / 25.4
    margin = themed("notes", None, None).sizes.page_margin_mm
    bottom = (margin + 2) * mm
    height = PAGE_H - (2 * margin + 2) * mm
    chapter_starts = {_find(body, rf"Chapter {n}: .*", after_page=2).page for n in (1, 2, 3)}
    for page in range(3, total):  # interior: after the contents, before the last page
        lowest = min(ln.y for ln in body if ln.page == page)
        empty = (lowest - bottom) / height
        if page + 1 in chapter_starts:
            continue  # the last page of a chapter may be short
        assert empty <= MAX_EMPTY_BOTTOM, f"page {page} is {empty:.0%} empty at the bottom"


@requires_browser
def test_no_heading_is_the_last_text_on_a_page(built: dict[str, object]) -> None:
    body: list[Line] = built["body"]  # type: ignore[assignment]
    total: int = built["pages"]  # type: ignore[assignment]
    heading = re.compile(r"(Chapter \d: .*|Topic \d\.\d|Key takeaways \d|Exercises \d)")
    for page in range(1, total + 1):
        page_lines = [ln for ln in body if ln.page == page]
        if page_lines:
            last = min(page_lines, key=lambda ln: ln.y)
            assert not heading.fullmatch(last.text), f"heading stranded on page {page}: {last.text}"


@requires_browser
def test_contents_numbers_equal_the_real_pages(built: dict[str, object]) -> None:
    document: Document = built["document"]  # type: ignore[assignment]
    result = built["result"]
    lines: list[Line] = built["lines"]  # type: ignore[assignment]
    assert result.toc_numbered is True, result.warnings  # type: ignore[attr-defined]
    contents_page = _find(lines, "Contents").page
    shown: dict[str, int] = {}
    for ln in lines:
        if ln.page == contents_page:
            match = re.fullmatch(r"(.*?)\s*(\d+)", ln.text)
            if match:
                shown[match.group(1).strip()] = int(match.group(2))
    # Real page of each heading, found independently in the text (not in the destinations):
    # walk the document order and take the next body line equal to the heading text.
    body: list[Line] = built["body"]  # type: ignore[assignment]
    cursor = (contents_page, float("inf"))
    for _index, _level, text in toc_entries(document):
        for ln in body:
            if (ln.page, -ln.y) > (cursor[0], -cursor[1]) and ln.text == text:
                cursor = (ln.page, ln.y)
                assert shown.get(text) == ln.page, (
                    f"{text!r}: contents {shown.get(text)} real {ln.page}"
                )
                break
        else:
            raise AssertionError(f"heading {text!r} not found in the body")


@requires_browser
def test_page_x_of_n_counts_every_page_and_the_cover_is_page_one(built: dict[str, object]) -> None:
    lines: list[Line] = built["lines"]  # type: ignore[assignment]
    total: int = built["pages"]  # type: ignore[assignment]
    for page in range(1, total + 1):
        texts = " ".join(ln.text for ln in lines if ln.page == page)
        match = re.search(r"Page (\d+) of (\d+)", texts)
        if page == 1:
            assert match is None, "the cover carries no page number"
            assert "Java Streams Reference Notes" in texts  # but it is page 1 of the count
        else:
            assert match, f"page {page} has no 'Page X of N'"
            assert (int(match.group(1)), int(match.group(2))) == (page, total)


@requires_browser
def test_footer_brand_is_the_author_and_never_overlaps_the_page_number(
    built: dict[str, object],
) -> None:
    result = built["result"]
    reader = PdfReader(str(result.path))  # type: ignore[attr-defined]
    page = reader.pages[3]
    boxes: dict[str, float] = {}

    def visit(text: str, cm: list[float], tm: list[float], _fd: object, _fs: float) -> None:
        x, y = _origin(cm, tm)
        if text.strip() and y < 40:
            boxes[text.strip()] = x

    page.extract_text(visitor_text=visit)
    brand_x = [x for t, x in boxes.items() if "Test Author" in t]
    number_x = [x for t, x in boxes.items() if t.startswith("Page")]
    assert brand_x and number_x and max(brand_x) < min(number_x)


@requires_browser
def test_a_split_table_repeats_its_header_row(built: dict[str, object]) -> None:
    lines: list[Line] = built["lines"]  # type: ignore[assignment]
    row_pages = sorted({ln.page for ln in lines if ln.text.startswith("ROW-2-")})
    assert len(row_pages) >= 2, "the 40-row table was expected to split across pages"
    for page in row_pages:
        assert any(ln.page == page and ln.text.startswith("Operator2") for ln in lines), page


@requires_browser
def test_a_short_report_does_not_get_a_page_break_before_every_h1(tmp_path: Path) -> None:
    blocks: list[dict[str, object]] = []
    for n in (1, 2, 3):
        blocks += [
            {"type": "heading", "level": 1, "text": f"Section {n}"},
            {"type": "paragraph", "text": "Short text. " * 20},
        ]
    document = Document.model_validate({"theme": "notes", "title": "Brief", "blocks": blocks})
    result = create_pdf_from_document(document, tmp_path / "short.pdf")
    assert result.renderer == RENDERER_BROWSER, result.warnings
    assert len(PdfReader(str(result.path)).pages) == 1


def test_chapter_rule_is_only_in_the_css_when_asked() -> None:
    document = _long_document()
    assert "break-before: page" not in render_document_html(document)
    assert "h1.h { break-before: page" in render_document_html(document, chapter_breaks=True)
