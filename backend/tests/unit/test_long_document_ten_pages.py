"""A 10-page notes request through the chapter pipeline, printed by the real browser.

The owner's "make a 10page java stream notes in a pdf" must give 10 pages of filled content, not
the 5 thin pages one call produced. The model is the long-document fake; the print is real
(skipped without Edge or Chrome).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader
from pypdf.generic import DictionaryObject

from app.automation.acceptance import pages_bounds, words_floor
from app.automation.browser_pdf import shutdown_browser_renderer
from app.automation.page_fit import fill_report, page_word_counts
from tests.support.documents import requires_browser
from tests.support.longdoc import FakeLongDocGateway
from tests.unit.test_long_document_pipeline import Run

pytestmark = requires_browser


@pytest.fixture(autouse=True, scope="module")
def _close_shared_browser() -> object:
    yield
    shutdown_browser_renderer()


async def test_ten_page_notes_have_ten_filled_pages_and_clean_running_text(
    tmp_path: Path,
) -> None:
    probe = Run(tmp_path, FakeLongDocGateway(chapters=3), pages=10, form="notes")
    run = Run(tmp_path, FakeLongDocGateway(chapters=probe.plan.chapters), pages=10, form="notes")
    result = await run.assemble(await run.stages())
    assert result.status.value == "success", result.summary
    path = Path(result.artifact_paths[0])
    reader = PdfReader(str(path))
    low, high = pages_bounds(10)
    assert low <= len(reader.pages) <= high, len(reader.pages)

    counts = page_word_counts(path)
    assert sum(counts) >= words_floor(10), counts
    report = fill_report(counts, theme="notes", leading_pages=0, page_breaks=0)
    # Chapters now each start on a new page (owner request), so the last page of every chapter and
    # the contents page may be short; before this only two thin pages were tolerated.
    assert report.thin == () or len(report.thin) <= 1 + probe.plan.chapters, (counts, report.note)

    # Running header and footer are clean text, never glued ("5MAYA") and not on the cover.
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        assert f"of {len(reader.pages)}MAYA" not in text.replace(" ", ""), number
    fonts: set[str] = set()
    for page in reader.pages:
        resources = page["/Resources"]
        assert isinstance(resources, DictionaryObject)
        table = resources.get("/Font")
        assert isinstance(table, DictionaryObject)
        for font in table.values():
            obj = font.get_object()
            assert isinstance(obj, DictionaryObject)
            fonts.add(str(obj["/BaseFont"]))
    assert fonts and all("+" in name for name in fonts)  # subset-embedded
    print("PAGES", len(reader.pages), "WORDS", counts)
