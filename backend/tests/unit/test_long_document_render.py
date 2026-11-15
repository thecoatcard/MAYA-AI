"""Long documents with the REAL browser renderer (skipped when Edge/Chrome is not installed).

These measure what the estimates in ``app.automation.long_document`` only guess: the page count of
a generated 50-page study document, the time to print 60 pages, the contents page numbers, and the
notes-theme features (cover, contents, repeated table headers, wrapped code, callout labels).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from app.automation.browser_pdf import shutdown_browser_renderer
from app.automation.html_render import toc_entries
from app.automation.long_document import estimate_pages
from app.automation.model import Document, document_from_dict
from app.automation.pdf_engine import (
    RENDERER_BROWSER,
    create_pdf_from_document,
    heading_pages,
    render_timeout_seconds,
)
from app.automation.verifiers.pdf_verifier import verify_pdf
from tests.support.documents import requires_browser
from tests.support.longdoc import FakeLongDocGateway, fake_chapter_blocks
from tests.unit.test_long_document_pipeline import Run

pytestmark = [requires_browser, pytest.mark.usefixtures("nine_chapters")]


@pytest.fixture(autouse=True, scope="module")
def _close_shared_browser() -> object:
    yield
    shutdown_browser_renderer()


def _text(path: Path, first: int = 0, last: int | None = None) -> str:
    pages = PdfReader(str(path)).pages[first:last]
    return "\n".join(page.extract_text() or "" for page in pages)


async def _assembled(tmp_path: Path, **gateway: Any) -> tuple[Run, Any]:
    run = Run(tmp_path, FakeLongDocGateway(chapters=9, **gateway))
    deps = await run.stages()
    node = run.nodes[-1]
    ctx = await run.support.build_context(
        run.job, {**node.input_data, "description": node.description, "dependency_outputs": deps}
    )
    return run, ctx


async def test_a_50_page_request_prints_about_50_pages_with_real_contents_numbers(
    tmp_path: Path,
) -> None:
    run = Run(tmp_path, FakeLongDocGateway(chapters=9))
    result = await run.assemble(await run.stages())
    proof = result.verification_proof
    assert proof["renderer"] == RENDERER_BROWSER, proof.get("warnings")
    assert result.status.value == "success", result.summary
    pages = proof["total_pages"]
    assert 45 <= pages <= 63, pages
    assert proof["acceptance"]["met"] is True
    assert proof["browser"] and proof["warnings"] == []  # no missing numbers, no fallback

    path = Path(result.artifact_paths[0])
    reader = PdfReader(str(path))
    assert len(reader.pages) == pages
    first = " ".join(reader.pages[0].extract_text().split())  # the title wraps on the cover
    assert "Java from Basic to Advanced" in first and "Page 1 of" not in first  # a real cover
    contents = _text(path, 1, 3)
    assert "Contents" in contents
    # Every contents number is the page where that heading really is.
    chapter_lines = re.findall(r"Chapter (\d+): ([^\n]+?)\s+(\d+)\s*$", contents, re.MULTILINE)
    # The nine chapters, then the nine "Chapter N" entries under the closing Exercises section.
    assert len(chapter_lines) == 18
    chapter_lines = chapter_lines[:9]
    numbers = [int(n) for _, _, n in chapter_lines]
    assert numbers == sorted(numbers) and numbers[0] == 4 and numbers[-1] <= pages
    for number, _title, printed in chapter_lines:
        heading_page = next(
            i
            for i, page in enumerate(reader.pages, start=1)
            if i > 3 and f"Chapter {number}:" in (page.extract_text() or "")
        )
        assert int(printed) == heading_page, f"chapter {number}"
    assert "Page 5 of" in _text(path, 4, 5) and f"of {pages}" in _text(path, 4, 5)


async def test_the_page_estimate_stays_within_20_percent_of_the_real_print(
    tmp_path: Path,
) -> None:
    run, ctx = await _assembled(tmp_path)
    document = document_from_dict(
        {
            "theme": "notes",
            "title": ctx["title"],
            "cover_page": True,
            "toc": True,
            "blocks": ctx["blocks"],
        }
    )
    real = len(
        PdfReader(str(create_pdf_from_document(document, tmp_path / "estimate.pdf").path)).pages
    )
    estimate = estimate_pages(ctx["assembly"].chapters, exercises=True)
    assert abs(estimate - real) / real <= 0.20, (estimate, real)


async def test_two_pass_numbers_are_exact_and_cost_two_prints(tmp_path: Path) -> None:
    _, ctx = await _assembled(tmp_path)
    document = Document.model_validate(
        {
            "theme": "notes",
            "title": ctx["title"],
            "cover_page": True,
            "toc": True,
            "blocks": ctx["blocks"],
        }
    )
    result = create_pdf_from_document(document, tmp_path / "two_pass.pdf")
    assert result.renderer == RENDERER_BROWSER and result.passes == 2
    assert result.toc_numbered is True
    entries = toc_entries(document)
    pages = heading_pages(result.path, entries)
    assert len(pages) == len(entries)  # every heading's page was measured exactly
    assert not list(tmp_path.glob("*.tmp"))  # noqa: ASYNC240  (the temporary print is gone)


async def test_60_pages_print_within_the_timeout_and_memory_budget(tmp_path: Path) -> None:
    blocks: list[dict[str, Any]] = []
    for chapter in range(12):  # 12 chapters of about 5 pages: roughly 60 pages
        if chapter:
            blocks.append({"type": "page_break"})
        blocks.append({"type": "heading", "level": 1, "text": f"Chapter {chapter + 1}: Topic"})
        blocks.extend(fake_chapter_blocks(f"Topic {chapter}", 1200, seed=chapter))
    document = Document.model_validate(
        {
            "theme": "notes",
            "title": "Sixty pages",
            "cover_page": True,
            "toc": True,
            "blocks": blocks,
        }
    )
    started = time.perf_counter()
    result = create_pdf_from_document(document, tmp_path / "sixty.pdf")
    elapsed = time.perf_counter() - started
    report = verify_pdf(result.path)
    pages = report.metadata["total_pages"]
    print(f"\n60-page render: {pages} pages, {len(blocks)} blocks, {elapsed:.1f}s, 2 prints")
    assert result.renderer == RENDERER_BROWSER and report.status == "verified", report.errors
    # 12 chapters of 1200 page-budget words (the fixture size follows the estimator's block
    # costs): real Edge print on the denser notes theme measured 63 pages, twice (2026-11);
    # the band is that count +-15%.
    assert 54 <= pages <= 72
    assert elapsed < render_timeout_seconds(len(blocks))  # inside the browser budget
    assert result.path.stat().st_size < 20 * 1024 * 1024


def test_notes_theme_features_in_the_printed_pdf(tmp_path: Path) -> None:
    long_line = "x" * 40 + " " + "wrapped_token_" * 30 + "END_OF_LINE"
    rows = [[f"row {i}", "meaning of the row", "e.g. value"] for i in range(70)]
    blocks: list[dict[str, Any]] = [
        {"type": "heading", "level": 1, "text": "Chapter 1: Layout"},
        {"type": "callout", "kind": "info", "text": "A note."},
        {"type": "callout", "kind": "success", "text": "A tip."},
        {"type": "callout", "kind": "warning", "text": "A warning."},
        {"type": "code", "language": "java", "text": f'String s = "{long_line}";'},
        {"type": "table", "headers": ["Concept", "Meaning", "Example"], "rows": rows},
    ]
    document = Document.model_validate(
        {"theme": "notes", "title": "Layout", "cover_page": True, "toc": True, "blocks": blocks}
    )
    result = create_pdf_from_document(document, tmp_path / "layout.pdf")
    assert result.renderer == RENDERER_BROWSER and result.toc_numbered is True
    text = _text(result.path)
    for label in ("note", "tip", "warning"):
        assert label in text.lower()
    assert "END_OF_LINE" in text and "JAVA" in text.upper()  # long code line wrapped, labelled
    # The table is longer than a page, so its header row is printed again on the next page.
    assert text.count("Concept") >= 2
    assert "Page 3 of" in text or "Page 2 of" in text
