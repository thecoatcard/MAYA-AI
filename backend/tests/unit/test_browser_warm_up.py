"""Browser warm-up: background start, failure is contained, and rendering still works."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.automation import browser_pdf
from app.automation.browser_pdf import BrowserPdfRenderer, BrowserUnavailableError
from tests.unit.test_browser_pdf import FakeBrowser, _renderer_with


def test_warm_up_starts_the_browser_and_render_reuses_it(tmp_path: Path) -> None:
    browser = FakeBrowser()
    renderer = _renderer_with(browser)
    try:
        assert renderer.warm_up().result(timeout=5) is True
        renderer.render("<html>hi</html>", tmp_path / "out.pdf")
    finally:
        renderer.close()
    assert (tmp_path / "out.pdf").read_bytes().startswith(b"%PDF")
    assert browser.contexts[0].contents == ["<html>hi</html>"]


def test_failed_warm_up_is_contained_and_render_falls_back(tmp_path: Path) -> None:
    renderer = BrowserPdfRenderer()

    async def cannot_start() -> FakeBrowser:
        raise BrowserUnavailableError("no Edge or Chrome")

    renderer._ensure_browser = cannot_start  # type: ignore[method-assign]
    try:
        assert renderer.warm_up().result(timeout=5) is False  # no exception escapes
        with pytest.raises(BrowserUnavailableError):
            renderer.render("<p>x</p>", tmp_path / "out.pdf")
    finally:
        renderer.close()
    assert not (tmp_path / "out.pdf").exists()


def test_start_is_skipped_without_an_installed_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_run() -> None:
        raise AssertionError("no renderer should be created without a browser")

    monkeypatch.setattr(browser_pdf, "browser_available", lambda: False)
    monkeypatch.setattr(browser_pdf, "get_browser_renderer", must_not_run)
    browser_pdf.start_browser_warm_up()  # returns quietly


def test_start_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def warm_up(self) -> None:
            raise RuntimeError("driver missing")

    monkeypatch.setattr(browser_pdf, "browser_available", lambda: True)
    monkeypatch.setattr(browser_pdf, "get_browser_renderer", lambda: Broken())
    browser_pdf.start_browser_warm_up()
