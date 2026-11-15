"""PDF redesign mode: the text of an uploaded PDF is extracted and rebuilt as a NEW PDF. The
original is never edited, and preserve-layout editing stays refused in plain words."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.automation import pdf_engine
from app.automation.browser_pdf import browser_available
from app.automation.file_edit import apply_edit
from app.automation.model import HeadingBlock, ParagraphBlock
from app.automation.pdf_redesign import (
    MAX_REDESIGN_CHARS,
    document_from_pdf_text,
    document_plain_text,
)
from app.files.edit_step import run_file_edit_step
from app.files.extract import extract
from app.tasks.worker import NonRetryableSubtaskError
from tests.support.files import make_job


def _pdf(path: Path, lines: list[str]) -> Path:
    from reportlab.pdfgen import canvas

    page = canvas.Canvas(str(path))
    y = 760
    for line in lines:
        page.drawString(72, y, line)
        y -= 18
    page.save()
    return path


def test_text_is_extracted_from_the_uploaded_pdf(tmp_path: Path) -> None:
    source = _pdf(
        tmp_path / "report.pdf",
        ["Quarterly results", "Revenue grew 42 percent in 2026.", "Hiring 17 engineers."],
    )
    view = extract(source, "pdf")
    assert view.readable
    assert "42 percent" in view.text and "17 engineers" in view.text
    document = document_from_pdf_text(view.text, title="Report")
    assert "2026" in document_plain_text(document)
    assert document.title == "Report"


def test_paragraphs_headings_and_page_markers() -> None:
    text = (
        "== Page 1\nQuarterly results\n\nRevenue grew 42 percent in 2026.\n\n"
        "== Page 2\nHiring 17 engineers."
    )
    blocks = document_from_pdf_text(text, title="Report").blocks
    assert isinstance(blocks[0], HeadingBlock) and blocks[0].text == "Quarterly results"
    assert isinstance(blocks[1], ParagraphBlock) and blocks[1].text.endswith("in 2026.")
    assert isinstance(blocks[2], ParagraphBlock) and blocks[2].text == "Hiring 17 engineers."
    assert "== Page" not in document_plain_text(document_from_pdf_text(text, title="R"))


def test_empty_text_is_refused() -> None:
    with pytest.raises(ValueError, match="no text"):
        document_from_pdf_text("== Page 1\n\n   \n", title="Empty")


def test_text_over_the_limit_is_refused() -> None:
    with pytest.raises(ValueError, match="too large"):
        document_from_pdf_text("a" * (MAX_REDESIGN_CHARS + 1), title="Big")


def test_a_very_long_paragraph_is_split_without_losing_words() -> None:
    words = " ".join(f"word{n}" for n in range(3000))
    blocks = document_from_pdf_text(words, title="Long").blocks
    assert len(blocks) > 1
    assert all(isinstance(b, ParagraphBlock) and len(b.text) <= 4800 for b in blocks)
    assert document_plain_text(document_from_pdf_text(words, title="Long")).split() == words.split()


async def test_redesign_writes_a_new_pdf_and_keeps_the_original(
    file_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _services, folder = file_env
    original = _pdf(folder / "report.pdf", ["Revenue grew 42 percent in 2026.", "Hired 17 people."])
    before = original.read_bytes()

    def fake_build(document: Any, output_path: Any, **kwargs: Any) -> SimpleNamespace:
        from reportlab.pdfgen import canvas

        page = canvas.Canvas(str(output_path))
        y = 760
        for block in document.blocks:
            page.drawString(72, y, block.text[:200])
            y -= 18
        page.save()
        return SimpleNamespace(warnings=["fake builder"])

    monkeypatch.setattr(pdf_engine, "create_pdf_from_document", fake_build)
    output = await run_file_edit_step(
        object(), make_job(), {"path": "report.pdf", "description": "Redesign this PDF"}
    )
    assert original.read_bytes() == before
    new_version = folder / "report (copy).pdf"
    assert new_version.is_file()
    assert "42 percent" in extract(new_version, "pdf").text
    assert "layout of the original is not kept" in output["result"]["summary"]
    assert output["proof"]["status"] == "verified"
    assert "numbers_kept" in output["proof"]["report"]["checks_passed"]
    assert output["result"]["warnings"] == ["fake builder"]


async def test_editing_a_pdf_in_place_is_still_refused_plainly(file_env: Any) -> None:
    _services, folder = file_env
    _pdf(folder / "report.pdf", ["Revenue grew 42 percent."])
    with pytest.raises(NonRetryableSubtaskError) as raised:
        await run_file_edit_step(
            object(), make_job(), {"path": "report.pdf", "description": "change 42 to 43"}
        )
    message = str(raised.value)
    assert "not supported" in message and "layout" in message
    assert "redesign" in message.lower()


def test_apply_edit_pdf_needs_a_document_plan(tmp_path: Path) -> None:
    from app.automation.file_edit import NotEditableError

    source = _pdf(tmp_path / "a.pdf", ["x"])
    with pytest.raises(NotEditableError):
        apply_edit("pdf", source, {"new_content": "nope"}, tmp_path / "b.pdf")


@pytest.mark.skipif(not browser_available(), reason="needs Microsoft Edge or Google Chrome")
async def test_redesign_with_the_real_pdf_builder(file_env: Any) -> None:
    _services, folder = file_env
    _pdf(folder / "brief.pdf", ["Board brief", "Revenue grew 42 percent in 2026."])
    output = await run_file_edit_step(
        object(), make_job(), {"path": "brief.pdf", "description": "rebuild this pdf"}
    )
    created = folder / "brief (copy).pdf"
    assert created.is_file() and output["proof"]["status"] == "verified"
    assert "42 percent" in extract(created, "pdf").text
