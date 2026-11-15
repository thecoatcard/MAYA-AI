"""PDF engine: legacy API, renderer selection, ReportLab fallback and its honest warnings.

Real-browser behavior is covered in ``test_pdf_real_browser.py``; these tests never launch a
browser, so they are deterministic on every machine.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from app.automation import pdf_engine
from app.automation.assets import make_image_resolver
from app.automation.browser_pdf import BrowserRenderInfo, BrowserUnavailableError
from app.automation.model import Document
from app.automation.pdf_engine import (
    RENDERER_BROWSER,
    RENDERER_FALLBACK,
    create_pdf,
    create_pdf_from_document,
)
from app.automation.themes import THEME_NAMES
from app.automation.verifiers.pdf_verifier import verify_pdf
from tests.support.documents import HINGLISH, TINY_PNG, sample_document


class _OkRenderer:
    def __init__(self) -> None:
        self.html: str | None = None

    def render(self, html: str, output_path: Path | str) -> BrowserRenderInfo:
        self.html = html
        Path(output_path).write_bytes(b"%PDF-1.7 stub")
        return BrowserRenderInfo(channel="msedge", version="9.9")


class _BrokenRenderer:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def render(self, html: str, output_path: Path | str) -> BrowserRenderInfo:
        raise self.error


def test_browser_path_records_renderer_and_browser(tmp_path: Path) -> None:
    fake = _OkRenderer()
    result = create_pdf_from_document(
        sample_document("midnight"),
        tmp_path / "a.pdf",
        renderer=fake,  # type: ignore[arg-type]
    )
    assert result.renderer == RENDERER_BROWSER == "browser"
    assert result.browser == "msedge 9.9"
    assert result.theme == "midnight"
    assert result.warnings == []
    assert fake.html is not None and 'data-theme="midnight"' in fake.html


@pytest.mark.parametrize(
    "error",
    [
        BrowserUnavailableError("No usable Microsoft Edge or Google Chrome was found"),
        RuntimeError("browser crashed"),
        TimeoutError("render timed out"),
    ],
)
def test_browser_failure_falls_back_to_reportlab_with_a_warning(
    tmp_path: Path, error: Exception
) -> None:
    result = create_pdf_from_document(
        sample_document(),
        tmp_path / "b.pdf",
        renderer=_BrokenRenderer(error),  # type: ignore[arg-type]
    )
    assert result.renderer == RENDERER_FALLBACK == "reportlab-fallback"
    assert result.browser is None
    assert any("used the ReportLab fallback" in w for w in result.warnings)
    assert str(error)[:20] in " ".join(result.warnings)
    report = verify_pdf(result.path)
    assert report.status == "verified", report.errors


def test_fallback_warns_that_non_latin_text_may_be_missing(tmp_path: Path) -> None:
    result = create_pdf_from_document(
        sample_document(),
        tmp_path / "c.pdf",
        renderer=_BrokenRenderer(BrowserUnavailableError("none")),  # type: ignore[arg-type]
    )
    assert any("non-Latin" in w for w in result.warnings)
    latin_only = Document.model_validate(
        {"title": "Latin", "blocks": [{"type": "paragraph", "text": "plain text"}]}
    )
    clean = create_pdf_from_document(
        latin_only,
        tmp_path / "d.pdf",
        renderer=_BrokenRenderer(BrowserUnavailableError("none")),  # type: ignore[arg-type]
    )
    assert not any("non-Latin" in w for w in clean.warnings)


@pytest.mark.parametrize("theme", THEME_NAMES)
def test_fallback_renders_every_block_type_in_every_theme(tmp_path: Path, theme: str) -> None:
    document = sample_document(theme)
    document = Document.model_validate(
        {
            **document.model_dump(),
            "blocks": [
                *[b.model_dump() for b in document.blocks],
                {"type": "image", "src": TINY_PNG, "alt": "pixel", "caption": "Figure"},
                {"type": "image", "src": "missing/file.png", "alt": "gone"},
            ],
        }
    )
    result = create_pdf_from_document(
        document,
        tmp_path / f"{theme}.pdf",
        renderer=_BrokenRenderer(BrowserUnavailableError("none")),  # type: ignore[arg-type]
    )
    report = verify_pdf(result.path, min_pages=2)
    assert report.status == "verified", report.errors
    assert any("missing/file.png" in w for w in result.warnings)


def test_default_renderer_is_looked_up_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agent tests rely on monkeypatching ``get_browser_renderer`` to force the fallback."""
    monkeypatch.setattr(
        pdf_engine,
        "get_browser_renderer",
        lambda: _BrokenRenderer(BrowserUnavailableError("patched")),
    )
    result = create_pdf_from_document(sample_document(), tmp_path / "e.pdf")
    assert result.renderer == RENDERER_FALLBACK
    assert "patched" in " ".join(result.warnings)


# ------------------------------------------------------------------------- legacy API
def test_legacy_create_pdf_keeps_its_signature_and_returns_a_verified_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pdf_engine,
        "get_browser_renderer",
        lambda: _BrokenRenderer(BrowserUnavailableError("none")),
    )
    sections: list[dict[str, Any]] = [
        {"type": "heading1", "text": "Overview"},
        {"type": "paragraph", "text": "Verified <b>text</b> layer & more."},
        {"type": "heading2", "text": "Details"},
        {"type": "table", "headers": ["A", "B"], "rows": [["1", "2"], ["3"]]},
        {"type": "page_break"},
        {"type": "heading1", "text": "Next"},
        {"type": "paragraph", "text": "Second page."},
    ]
    positional = create_pdf("Legacy", sections, tmp_path / "p.pdf")
    keyword = create_pdf(
        title="Legacy",
        subtitle="Sub",
        sections=sections,
        output_path=tmp_path / "k.pdf",
    )
    assert isinstance(positional, Path) and positional.is_file()
    assert verify_pdf(positional, min_pages=2).status == "verified"
    assert verify_pdf(keyword, min_pages=2).status == "verified"


def test_legacy_create_pdf_accepts_a_theme_and_rejects_unknown_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _OkRenderer()
    monkeypatch.setattr(pdf_engine, "get_browser_renderer", lambda: fake)
    create_pdf("T", [{"type": "paragraph", "text": "x"}], tmp_path / "t.pdf", theme="minimal")
    assert fake.html is not None and 'data-theme="minimal"' in fake.html
    with pytest.raises(ValueError, match="theme"):
        create_pdf("T", [{"type": "paragraph", "text": "x"}], tmp_path / "u.pdf", theme="neon")


def test_legacy_create_pdf_escapes_markup_in_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _OkRenderer()
    monkeypatch.setattr(pdf_engine, "get_browser_renderer", lambda: fake)
    create_pdf(
        "T <script>1</script>",
        [{"type": "paragraph", "text": "<script>alert(1)</script>"}],
        tmp_path / "x.pdf",
    )
    assert fake.html is not None and "<script" not in fake.html.lower()


# ------------------------------------------------------------------------- assets
def test_image_resolver_stays_inside_the_workspace(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "images").mkdir(parents=True)
    (root / "images" / "a.png").write_bytes(base64.b64decode(TINY_PNG.split(",", 1)[1]))
    (tmp_path / "secret.png").write_bytes(b"not for you")
    resolve = make_image_resolver(root)
    assert resolve is not None
    assert (resolve("images/a.png") or "").startswith("data:image/png;base64,")
    assert resolve("../secret.png") is None
    assert resolve("images/missing.png") is None
    assert resolve("images/a.txt") is None
    assert make_image_resolver(None) is None


def test_workspace_image_is_embedded_in_the_html(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "img").mkdir(parents=True)
    (root / "img" / "p.png").write_bytes(base64.b64decode(TINY_PNG.split(",", 1)[1]))
    document = Document.model_validate(
        {"title": "I", "blocks": [{"type": "image", "src": "img/p.png", "alt": "pixel"}]}
    )
    fake = _OkRenderer()
    result = create_pdf_from_document(
        document,
        tmp_path / "i.pdf",
        assets_root=root,
        renderer=fake,  # type: ignore[arg-type]
    )
    assert result.warnings == []
    assert fake.html is not None and '<img src="data:image/png;base64,' in fake.html


def test_hinglish_constant_is_devanagari() -> None:
    # Guards the test fixtures themselves: the string must contain Devanagari code points.
    assert any(0x0900 <= ord(ch) <= 0x097F for ch in HINGLISH)
