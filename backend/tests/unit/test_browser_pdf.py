"""Browser PDF renderer: lock-down configuration, with a fake browser (no real browser)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.automation.browser_pdf import (
    BrowserPdfRenderer,
    BrowserRenderInfo,
    BrowserUnavailableError,
    context_options,
    detect_browser_paths,
    pdf_options,
)


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = type("Request", (), {"url": url})()
        self.fulfilled: dict[str, Any] | None = None
        self.aborted = False

    async def fulfill(self, **kwargs: Any) -> None:
        self.fulfilled = kwargs

    async def abort(self) -> None:
        self.aborted = True


class FakePage:
    def __init__(self, context: FakeContext) -> None:
        self.context = context

    async def set_content(self, html: str, **kwargs: Any) -> None:
        self.context.contents.append(html)

    async def pdf(self, path: str, **kwargs: Any) -> None:
        self.context.pdf_options = kwargs
        await asyncio.to_thread(Path(path).write_bytes, b"%PDF-1.7 fake")


class FakeContext:
    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        self.handler: Any = None
        self.pattern: str | None = None
        self.contents: list[str] = []
        self.pdf_options: dict[str, Any] = {}
        self.closed = False

    def set_default_timeout(self, _ms: int) -> None:
        pass

    async def route(self, pattern: str, handler: Any) -> None:
        self.pattern, self.handler = pattern, handler

    async def new_page(self) -> FakePage:
        return FakePage(self)

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []

    def is_connected(self) -> bool:
        return True

    async def new_context(self, **options: Any) -> FakeContext:
        context = FakeContext(options)
        self.contexts.append(context)
        return context


def _renderer_with(browser: FakeBrowser) -> BrowserPdfRenderer:
    renderer = BrowserPdfRenderer()

    async def fake_ensure() -> FakeBrowser:
        renderer._info = BrowserRenderInfo(channel="fakechannel", version="1.2.3")
        return browser

    renderer._ensure_browser = fake_ensure  # type: ignore[method-assign]
    return renderer


def test_context_options_disable_javascript_downloads_and_service_workers() -> None:
    options = context_options()
    assert options["java_script_enabled"] is False
    assert options["accept_downloads"] is False
    assert options["service_workers"] == "block"
    assert options["bypass_csp"] is False


def test_pdf_options_use_css_page_size_and_backgrounds() -> None:
    assert pdf_options() == {"prefer_css_page_size": True, "print_background": True}


def test_render_creates_a_locked_down_context_and_writes_the_pdf(tmp_path: Path) -> None:
    browser = FakeBrowser()
    renderer = _renderer_with(browser)
    try:
        info = renderer.render("<html>hi</html>", tmp_path / "out.pdf")
    finally:
        renderer.close()
    assert info == BrowserRenderInfo(channel="fakechannel", version="1.2.3")
    assert (tmp_path / "out.pdf").read_bytes().startswith(b"%PDF")
    (context,) = browser.contexts
    assert context.options["java_script_enabled"] is False
    assert context.pattern == "**/*"  # every request goes through the handler
    assert context.contents == ["<html>hi</html>"]  # injected, never fetched
    assert context.pdf_options == pdf_options()
    assert context.closed is True  # the context is always closed


def test_route_handler_aborts_every_request(tmp_path: Path) -> None:
    browser = FakeBrowser()
    renderer = _renderer_with(browser)
    try:
        renderer.render("<html>BODY</html>", tmp_path / "out.pdf")
    finally:
        renderer.close()
    handler = browser.contexts[0].handler

    for url in (
        "https://evil.example/steal?x=1",
        "http://localhost:8000/api/v1/auth/session",
        "http://127.0.0.1:5432/",
        "http://169.254.169.254/latest/meta-data",
        "file:///C:/Users/someone/secret.txt",
        "ftp://example.com/x",
        "data:text/html,<b>x</b>",
        "about:blank",
    ):
        route = FakeRoute(url)
        asyncio.run(handler(route))
        assert route.aborted and route.fulfilled is None, url


def test_render_failure_is_wrapped_and_still_closes_the_context(tmp_path: Path) -> None:
    browser = FakeBrowser()
    renderer = _renderer_with(browser)

    async def boom(self: FakePage, html: str, **kwargs: Any) -> None:
        raise RuntimeError("navigation exploded")

    FakePage.set_content, original = boom, FakePage.set_content  # type: ignore[method-assign]
    try:
        with pytest.raises(Exception, match="Browser PDF rendering failed: navigation exploded"):
            renderer.render("<html></html>", tmp_path / "out.pdf")
    finally:
        FakePage.set_content = original  # type: ignore[method-assign]
        renderer.close()
    assert browser.contexts[0].closed is True


def test_async_render_works_inside_a_running_loop(tmp_path: Path) -> None:
    browser = FakeBrowser()
    renderer = _renderer_with(browser)

    async def run() -> BrowserRenderInfo:
        return await renderer.arender("<html></html>", tmp_path / "async.pdf")

    try:
        info = asyncio.run(run())
    finally:
        renderer.close()
    assert info.channel == "fakechannel"
    assert (tmp_path / "async.pdf").is_file()


def test_missing_browser_raises_a_clear_error_without_downloading_anything(
    tmp_path: Path,
) -> None:
    renderer = BrowserPdfRenderer(channels=())
    try:
        with pytest.raises(BrowserUnavailableError, match="Install Edge or Chrome"):
            renderer.render("<html></html>", tmp_path / "x.pdf")
        # The failure is remembered: the second call fails fast with the same message.
        with pytest.raises(BrowserUnavailableError, match="no channels tried"):
            renderer.render("<html></html>", tmp_path / "x.pdf")
    finally:
        renderer.close()
    assert not (tmp_path / "x.pdf").exists()


def test_detect_browser_paths_returns_existing_files() -> None:
    assert all(path.is_file() for path in detect_browser_paths())
