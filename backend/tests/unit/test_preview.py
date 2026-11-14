"""Page previews: LibreOffice is optional, the Edge HTML mirror is the fallback, capped pages."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from app.automation import preview as preview_mod
from app.automation.browser_pdf import (
    BrowserRenderError,
    BrowserRenderInfo,
    ScreenshotResult,
    browser_available,
)
from app.automation.model import Document, ParagraphBlock
from app.automation.preview import (
    APPROXIMATION_NOTE,
    LIBREOFFICE_ENV,
    PREVIEW_MAX_PAGES,
    libreoffice_executable,
    render_preview,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def _document() -> Document:
    return Document(
        title="Preview test",
        blocks=[ParagraphBlock(text="Revenue grew 42 percent in 2026.")],
    )


class FakeRenderer:
    """Stands in for the browser renderer: writes ``total`` PNG slices, records the cap."""

    def __init__(self, total: int = 3, error: Exception | None = None) -> None:
        self.total = total
        self.error = error
        self.max_pages_seen: list[int] = []

    def screenshot_pages(
        self, html: str, out_dir: Path, *, max_pages: int, timeout_seconds: float | None = None
    ) -> ScreenshotResult:
        self.max_pages_seen.append(max_pages)
        if self.error is not None:
            raise self.error
        assert "Revenue grew 42 percent" in html
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for index in range(min(self.total, max_pages)):
            target = out_dir / f"page-{index + 1:03d}.png"
            target.write_bytes(PNG)
            paths.append(target)
        info = BrowserRenderInfo(channel="fake", version="0")
        return ScreenshotResult(paths=paths, total_pages=self.total, browser=info)


@pytest.fixture
def no_libreoffice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preview_mod, "libreoffice_executable", lambda: None)


def test_docx_without_libreoffice_is_an_html_approximation(
    tmp_path: Path, no_libreoffice: None
) -> None:
    renderer = FakeRenderer(total=2)
    result = render_preview(
        tmp_path / "a.docx",
        "docx",
        out_dir=tmp_path / "pages",
        document=_document(),
        renderer=renderer,  # type: ignore[arg-type]
    )
    assert result.method == "edge_html_mirror"
    assert result.approximation is True
    assert len(result.pages) == 2 and result.total_pages == 2
    assert APPROXIMATION_NOTE in result.notes
    assert result.as_dict()["approximation"] is True


def test_missing_libreoffice_is_reported_and_never_required(
    tmp_path: Path, no_libreoffice: None
) -> None:
    result = render_preview(tmp_path / "deck.pptx", "pptx", out_dir=tmp_path / "pages")
    assert result.method == "unavailable" and result.pages == []
    assert any("LibreOffice is not installed" in note for note in result.notes)
    assert any("no html mirror" in note.lower() for note in result.notes)


def test_pdf_without_a_mirror_is_unavailable_without_pymupdf(tmp_path: Path) -> None:
    result = render_preview(tmp_path / "a.pdf", "pdf", out_dir=tmp_path / "pages")
    assert result.method == "unavailable"
    assert any("PyMuPDF" in note for note in result.notes)


def test_page_cap_is_applied(tmp_path: Path, no_libreoffice: None) -> None:
    renderer = FakeRenderer(total=40)
    result = render_preview(
        tmp_path / "a.docx",
        "docx",
        out_dir=tmp_path / "pages",
        document=_document(),
        renderer=renderer,  # type: ignore[arg-type]
        max_pages=500,
    )
    assert renderer.max_pages_seen == [PREVIEW_MAX_PAGES]
    assert len(result.pages) == PREVIEW_MAX_PAGES
    assert result.total_pages == 40


def test_renderer_failure_is_reported_not_raised(tmp_path: Path, no_libreoffice: None) -> None:
    renderer = FakeRenderer(error=BrowserRenderError("browser crashed"))
    result = render_preview(
        tmp_path / "a.docx",
        "docx",
        out_dir=tmp_path / "pages",
        document=_document(),
        renderer=renderer,  # type: ignore[arg-type]
    )
    assert result.method == "unavailable"
    assert any("Edge page preview failed" in note for note in result.notes)


def test_failed_libreoffice_conversion_falls_back_to_the_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview_mod, "libreoffice_executable", lambda: Path("soffice-fake"))

    def boom(*args: Any, **kwargs: Any) -> Path:
        raise RuntimeError("LibreOffice could not convert the file to PDF.")

    monkeypatch.setattr(preview_mod, "_libreoffice_to_pdf", boom)
    result = render_preview(
        tmp_path / "a.docx",
        "docx",
        out_dir=tmp_path / "pages",
        document=_document(),
        renderer=FakeRenderer(total=1),  # type: ignore[arg-type]
    )
    assert result.method == "edge_html_mirror"
    assert any("LibreOffice preview failed" in note for note in result.notes)


def test_libreoffice_without_a_rasteriser_does_not_claim_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preview_mod, "libreoffice_executable", lambda: Path("soffice-fake"))
    pdf = tmp_path / "converted.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(preview_mod, "_libreoffice_to_pdf", lambda *a, **k: pdf)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = render_preview(tmp_path / "deck.pptx", "pptx", out_dir=tmp_path / "pages")
    assert result.method == "unavailable"
    assert any("no page image" in note or "rasteriser" in note for note in result.notes)


def test_configured_libreoffice_path_that_is_missing_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LIBREOFFICE_ENV, str(tmp_path / "not-here" / "soffice.exe"))
    assert libreoffice_executable() is None


@pytest.mark.skipif(not browser_available(), reason="needs Microsoft Edge or Google Chrome")
def test_real_browser_writes_png_slices(tmp_path: Path) -> None:
    from app.automation.browser_pdf import BrowserPdfRenderer
    from app.automation.html_render import render_document_html

    document = Document(
        title="Real preview",
        blocks=[ParagraphBlock(text=f"Paragraph {n} " * 40) for n in range(60)],
    )
    renderer = BrowserPdfRenderer()
    try:
        result = render_preview(
            tmp_path / "a.docx",
            "docx",
            out_dir=tmp_path / "pages",
            document=document,
            renderer=renderer,
            max_pages=2,
        )
    finally:
        renderer.close()
    assert result.method == "edge_html_mirror"
    assert len(result.pages) == 2
    assert all(p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for p in result.pages)
    assert render_document_html(document)  # the mirror itself is a plain HTML string
