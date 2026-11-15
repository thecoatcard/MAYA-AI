"""Real-browser PDF tests: run only when Microsoft Edge or Google Chrome is installed.

They are SKIPPED (not passed) on a machine without a browser; see ``requires_browser``.
"""

from __future__ import annotations

import asyncio
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from pypdf import PdfReader

from app.automation.browser_pdf import (
    BrowserPdfRenderer,
    get_browser_renderer,
    shutdown_browser_renderer,
)
from app.automation.html_render import render_document_html
from app.automation.model import Document
from app.automation.pdf_engine import RENDERER_BROWSER, create_pdf, create_pdf_from_document
from app.automation.themes import THEME_NAMES
from app.automation.verifiers.pdf_verifier import verify_pdf
from tests.support.documents import HINGLISH, requires_browser, sample_document

pytestmark = requires_browser


@pytest.fixture(autouse=True, scope="module")
def _close_shared_browser() -> object:
    yield
    shutdown_browser_renderer()


@pytest.mark.parametrize("theme", THEME_NAMES)
def test_themed_pdf_renders_in_the_real_browser_and_verifies(tmp_path: Path, theme: str) -> None:
    result = create_pdf_from_document(sample_document(theme), tmp_path / f"{theme}.pdf")
    assert result.renderer == RENDERER_BROWSER, result.warnings
    assert result.warnings == []
    assert result.browser and result.browser.split()[0] in {"msedge", "chrome"}

    report = verify_pdf(result.path, min_pages=2)  # the sample has an explicit page break
    assert report.status == "verified", report.errors
    assert report.metadata["total_pages"] >= 2
    assert report.metadata["text_extractable"] is True
    assert report.metadata["fonts"], "fonts must be listed in the proof"
    assert report.metadata["unembedded_fonts"] == []


def test_devanagari_hinglish_text_renders_and_is_extractable(tmp_path: Path) -> None:
    document = Document.model_validate(
        {
            "theme": "executive",
            "title": HINGLISH,
            "blocks": [
                {"type": "heading", "level": 1, "text": HINGLISH},
                {"type": "paragraph", "text": f"Hinglish line: {HINGLISH}. यह एक परीक्षण है."},
            ],
        }
    )
    result = create_pdf_from_document(document, tmp_path / "hinglish.pdf")
    assert result.renderer == RENDERER_BROWSER, result.warnings

    report = verify_pdf(result.path)
    assert report.status == "verified", report.errors
    text = "".join(page.extract_text() for page in PdfReader(str(result.path)).pages)
    assert "MAYA" in text
    # Shaped conjuncts can extract in a different order, so assert on stable base letters.
    assert "नम" in text and "परीक्षण" in text
    # A Unicode-capable font was embedded (Nirmala UI on Windows, Noto elsewhere).
    fonts = " ".join(report.metadata["fonts"]).lower()
    assert any(name in fonts for name in ("nirmala", "noto", "mangal", "devanagari", "arial"))


def test_page_numbers_and_running_header_are_printed(tmp_path: Path) -> None:
    blocks = []
    for n in range(1, 9):
        blocks += [
            {"type": "heading", "level": 1, "text": f"Section {n}"},
            {"type": "paragraph", "text": "word " * 400},
        ]
    document = Document.model_validate({"title": "Running Header Test", "blocks": blocks})
    result = create_pdf_from_document(document, tmp_path / "pages.pdf")
    pages = PdfReader(str(result.path)).pages
    total = len(pages)
    assert total >= 3
    assert f"Page 1 of {total}" in pages[0].extract_text()
    assert f"Page {total} of {total}" in pages[-1].extract_text()
    assert "Running Header Test" in pages[1].extract_text()  # header from page 2 on


def test_long_table_repeats_its_header_row_on_each_page(tmp_path: Path) -> None:
    rows = [[f"row-{i}", "value"] for i in range(120)]
    document = Document.model_validate(
        {
            "title": "Table",
            "blocks": [{"type": "table", "headers": ["ColumnAlpha", "ColumnBeta"], "rows": rows}],
        }
    )
    result = create_pdf_from_document(document, tmp_path / "table.pdf")
    pages = PdfReader(str(result.path)).pages
    assert len(pages) >= 2
    assert all("ColumnAlpha" in page.extract_text() for page in pages)


class _Recorder(BaseHTTPRequestHandler):
    hits: ClassVar[list[str]] = []

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        type(self).hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"x")

    def log_message(self, *args: object) -> None:
        pass


def test_script_does_not_run_and_no_request_leaves_the_browser(tmp_path: Path) -> None:
    """Browser-level lock-down, tested without the CSP meta so only the context protects."""
    _Recorder.hits = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        html = render_document_html(sample_document())
        html = re.sub(r"<meta http-equiv=\"Content-Security-Policy\"[^>]*>", "", html)
        assert "Content-Security-Policy" not in html
        base = f"http://127.0.0.1:{port}"
        hostile = html.replace(
            "</body>",
            '<script>document.title="PWNED";'
            f'new Image().src="{base}/script-beacon"</script>'
            f'<img src="{base}/img"><link rel="stylesheet" href="{base}/css">'
            f'<iframe src="{base}/frame"></iframe></body>',
        )
        renderer = BrowserPdfRenderer(timeout_seconds=30)
        try:
            info = renderer.render(hostile, tmp_path / "hostile.pdf")
        finally:
            renderer.close()
    finally:
        server.shutdown()
        server.server_close()
    assert info.channel in {"msedge", "chrome"}
    assert _Recorder.hits == [], f"the browser made requests: {_Recorder.hits}"
    reader = PdfReader(str(tmp_path / "hostile.pdf"))
    assert reader.metadata is None or reader.metadata.title != "PWNED"
    assert verify_pdf(tmp_path / "hostile.pdf").status == "verified"


def test_shared_renderer_is_reused_and_works_inside_a_running_loop(tmp_path: Path) -> None:
    async def run() -> tuple[object, object]:
        renderer = get_browser_renderer()
        html = render_document_html(sample_document())
        first, second = await asyncio.gather(
            renderer.arender(html, tmp_path / "one.pdf"),
            renderer.arender(html, tmp_path / "two.pdf"),
        )
        return first, second

    first, second = asyncio.run(run())
    assert first == second  # one browser, one version
    assert get_browser_renderer() is get_browser_renderer()
    assert (tmp_path / "one.pdf").stat().st_size > 1000


def test_legacy_create_pdf_uses_the_browser_when_available(tmp_path: Path) -> None:
    out = create_pdf(
        "Legacy via browser",
        [{"type": "heading1", "text": "H"}, {"type": "paragraph", "text": "body"}],
        tmp_path / "legacy.pdf",
    )
    report = verify_pdf(out)
    assert report.status == "verified", report.errors
    assert any("fonts_detected" in check for check in report.checks_passed)
