"""HTML to PDF through the system Edge or Chrome, driven by Playwright (no browser download).

Safety model (the AI never writes HTML, but the page is still treated as hostile):

* JavaScript is disabled for the whole browser context.
* Every network request is aborted. The HTML is injected with ``set_content`` (not fetched),
  so there is no legitimate request: nothing is ever fetched.
* Service workers and downloads are blocked.

Concurrency model: the renderer owns one daemon thread that runs its own asyncio event loop
and the Playwright async API. It does not touch the application's event loop or its event-loop
policy (the app selects the selector loop on Windows for PostgreSQL, but Playwright needs
subprocess support, so the private loop is a proactor loop there). One browser is started on
first use and reused for every document; :func:`shutdown_browser_renderer` closes it.
:meth:`BrowserPdfRenderer.render` blocks the calling thread, :meth:`arender` awaits.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import shutil
import sys
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_CONCURRENT_RENDERS = 3
CHANNELS: tuple[str, ...] = ("msedge", "chrome")


class BrowserUnavailableError(RuntimeError):
    """No usable Edge or Chrome was found, or it failed to launch."""


class BrowserRenderError(RuntimeError):
    """The browser launched but rendering the PDF failed or timed out."""


@dataclass(frozen=True)
class BrowserRenderInfo:
    channel: str
    version: str


# A4 at 96 CSS px per inch. Page previews are slices of the HTML mirror at this height (an
# approximation: the slices do not follow the print page breaks).
PREVIEW_PAGE_WIDTH_PX = 794
PREVIEW_PAGE_HEIGHT_PX = 1123


@dataclass(frozen=True)
class ScreenshotResult:
    paths: list[Path]
    total_pages: int
    browser: BrowserRenderInfo


def _png_size(data: bytes) -> tuple[int, int]:
    """Width and height of a PNG from its IHDR chunk (no image library needed)."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise BrowserRenderError("The browser did not return a PNG image.")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def context_options() -> dict[str, Any]:
    """Playwright browser-context options. Exposed so tests can assert the lock-down."""
    return {
        "java_script_enabled": False,
        "accept_downloads": False,
        "service_workers": "block",
        "bypass_csp": False,
        "locale": "en-US",
        "viewport": {"width": 794, "height": 1123},
    }


def pdf_options() -> dict[str, Any]:
    return {"prefer_css_page_size": True, "print_background": True}


def detect_browser_paths() -> list[Path]:
    """Installed Edge/Chrome executables found in the standard locations (cheap, no launch)."""
    found: list[Path] = []
    roots = [os.environ.get(n) for n in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    windows = (
        "Microsoft/Edge/Application/msedge.exe",
        "Google/Chrome/Application/chrome.exe",
    )
    for root in roots:
        if root:
            found.extend(p for rel in windows if (p := Path(root) / rel).is_file())
    mac = (
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )
    found.extend(p for raw in mac if (p := Path(raw)).is_file())
    for name in (
        "microsoft-edge",
        "microsoft-edge-stable",
        "google-chrome",
        "google-chrome-stable",
    ):
        located = shutil.which(name)
        if located:
            found.append(Path(located))
    return found


def browser_available() -> bool:
    """True when an Edge or Chrome install is present (it may still fail to launch)."""
    return bool(detect_browser_paths())


def _new_private_loop() -> asyncio.AbstractEventLoop:
    """A fresh loop that supports subprocesses, independent of the global loop policy."""
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    return asyncio.new_event_loop()


class BrowserPdfRenderer:
    """Reusable headless-browser PDF renderer. Thread-safe; one browser per instance."""

    def __init__(
        self,
        *,
        channels: tuple[str, ...] = CHANNELS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.channels = channels
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # Touched only on the private loop thread:
        self._playwright: Any = None
        self._browser: Any = None
        self._info: BrowserRenderInfo | None = None
        self._launch_error: str | None = None
        self._gate: asyncio.Semaphore | None = None

    # ---- private loop -------------------------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = _new_private_loop()
                ready = threading.Event()

                def run() -> None:
                    asyncio.set_event_loop(loop)
                    loop.call_soon(ready.set)
                    loop.run_forever()

                thread = threading.Thread(target=run, name="maya-pdf-browser", daemon=True)
                thread.start()
                ready.wait(5)
                self._loop, self._thread = loop, thread
            return self._loop

    async def _ensure_browser(self) -> Any:
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        self._browser = None
        if self._launch_error is not None:
            raise BrowserUnavailableError(self._launch_error)
        try:
            from playwright.async_api import async_playwright
        except ImportError as err:  # pragma: no cover - the dependency is declared
            self._launch_error = f"Playwright is not installed: {err}"
            raise BrowserUnavailableError(self._launch_error) from err
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        failures: list[str] = []
        for channel in self.channels:
            try:
                browser = await self._playwright.chromium.launch(channel=channel, headless=True)
            except Exception as err:
                first_line = str(err).splitlines()[0] if str(err) else repr(err)
                failures.append(f"{channel}: {first_line}")
                continue
            self._browser = browser
            self._info = BrowserRenderInfo(channel=channel, version=str(browser.version))
            return browser
        self._launch_error = (
            "No usable Microsoft Edge or Google Chrome was found "
            f"({'; '.join(failures) or 'no channels tried'}). Install Edge or Chrome."
        )
        raise BrowserUnavailableError(self._launch_error)

    async def _render_async(
        self, html: str, output_path: Path, timeout_seconds: float | None = None
    ) -> BrowserRenderInfo:
        if self._gate is None:
            self._gate = asyncio.Semaphore(MAX_CONCURRENT_RENDERS)
        async with self._gate:
            browser = await self._ensure_browser()
            timeout_ms = int((timeout_seconds or self.timeout_seconds) * 1000)
            context = await browser.new_context(**context_options())
            try:
                context.set_default_timeout(timeout_ms)

                async def block_everything(route: Any) -> None:
                    await route.abort()

                # The document is injected with set_content, never fetched, so no request is
                # ever legitimate: every one (images, frames, fonts, fetches) is aborted.
                await context.route("**/*", block_everything)
                page = await context.new_page()
                await page.set_content(html, wait_until="load", timeout=timeout_ms)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                await page.pdf(path=str(output_path), **pdf_options())
            except Exception as err:
                raise BrowserRenderError(f"Browser PDF rendering failed: {err}") from err
            finally:
                try:
                    await context.close()
                except Exception:  # pragma: no cover - best effort
                    logger.debug("browser context close failed", exc_info=True)
        assert self._info is not None
        return self._info

    async def _screenshot_async(
        self, html: str, out_dir: Path, max_pages: int, timeout_seconds: float | None
    ) -> ScreenshotResult:
        if self._gate is None:
            self._gate = asyncio.Semaphore(MAX_CONCURRENT_RENDERS)
        async with self._gate:
            browser = await self._ensure_browser()
            timeout_ms = int((timeout_seconds or self.timeout_seconds) * 1000)
            context = await browser.new_context(**context_options())
            try:
                context.set_default_timeout(timeout_ms)

                async def block_everything(route: Any) -> None:
                    await route.abort()

                await context.route("**/*", block_everything)
                page = await context.new_page()
                await page.set_content(html, wait_until="load", timeout=timeout_ms)
                full = await page.screenshot(full_page=True, type="png", timeout=timeout_ms)
                _width, height = _png_size(full)
                total = max(1, -(-height // PREVIEW_PAGE_HEIGHT_PX))
                paths: list[Path] = []
                for index in range(min(total, max_pages)):
                    top = index * PREVIEW_PAGE_HEIGHT_PX
                    target = out_dir / f"page-{index + 1:03d}.png"
                    clip = {
                        "x": 0,
                        "y": top,
                        "width": PREVIEW_PAGE_WIDTH_PX,
                        "height": min(PREVIEW_PAGE_HEIGHT_PX, height - top),
                    }
                    data = await page.screenshot(
                        full_page=True, type="png", clip=clip, timeout=timeout_ms
                    )
                    target.write_bytes(data)
                    paths.append(target)
            except BrowserRenderError:
                raise
            except Exception as err:
                raise BrowserRenderError(f"Browser page preview failed: {err}") from err
            finally:
                try:
                    await context.close()
                except Exception:  # pragma: no cover - best effort
                    logger.debug("browser context close failed", exc_info=True)
        assert self._info is not None
        return ScreenshotResult(paths=paths, total_pages=total, browser=self._info)

    async def _close_async(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:  # pragma: no cover - best effort
            logger.debug("browser close failed", exc_info=True)
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:  # pragma: no cover - best effort
            logger.debug("playwright stop failed", exc_info=True)
        self._browser = None
        self._playwright = None

    # ---- public API ---------------------------------------------------------------------
    def _submit(
        self, html: str, output_path: Path | str, timeout_seconds: float | None = None
    ) -> Future[BrowserRenderInfo]:
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(
            self._render_async(html, Path(output_path), timeout_seconds), loop
        )

    def render(
        self, html: str, output_path: Path | str, *, timeout_seconds: float | None = None
    ) -> BrowserRenderInfo:
        """Render ``html`` to a PDF at ``output_path``. Blocks; safe to call from any thread.

        ``timeout_seconds`` overrides the instance default for this call (a long document
        needs more than a one-page report).
        """
        budget = timeout_seconds or self.timeout_seconds
        future = self._submit(html, output_path, timeout_seconds)
        try:
            return future.result(timeout=budget + 15)
        except FutureTimeout as err:
            future.cancel()
            raise BrowserRenderError(
                f"Browser PDF rendering timed out after {budget:.0f}s"
            ) from err

    async def arender(
        self, html: str, output_path: Path | str, *, timeout_seconds: float | None = None
    ) -> BrowserRenderInfo:
        """Awaitable :meth:`render`; never blocks the caller's event loop."""
        budget = timeout_seconds or self.timeout_seconds
        future = self._submit(html, output_path, timeout_seconds)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout=budget + 15)
        except TimeoutError as err:
            future.cancel()
            raise BrowserRenderError(
                f"Browser PDF rendering timed out after {budget:.0f}s"
            ) from err

    def warm_up(self) -> Future[bool]:
        """Start the browser in the background without waiting for it. The future resolves to
        True when a browser is running and to False when it could not start (it never raises).
        A failed start is remembered, so later renders fail fast and the caller falls back."""
        loop = self._ensure_loop()

        async def start() -> bool:
            try:
                await self._ensure_browser()
            except Exception as err:
                logger.info("browser warm-up failed; PDFs will use the fallback: %s", err)
                return False
            return True

        return asyncio.run_coroutine_threadsafe(start(), loop)

    def screenshot_pages(
        self,
        html: str,
        out_dir: Path | str,
        *,
        max_pages: int,
        timeout_seconds: float | None = None,
    ) -> ScreenshotResult:
        """Write up to ``max_pages`` PNG slices of ``html`` into ``out_dir``. Blocks; same
        lock-down as :meth:`render` (no JavaScript, every request aborted)."""
        budget = timeout_seconds or self.timeout_seconds
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(
            self._screenshot_async(html, Path(out_dir), max_pages, timeout_seconds), loop
        )
        try:
            return future.result(timeout=budget + 15)
        except FutureTimeout as err:
            future.cancel()
            raise BrowserRenderError(f"Page preview timed out after {budget:.0f}s") from err

    def close(self) -> None:
        """Close the browser and stop the private loop. The renderer can be reused after."""
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop = self._thread = None
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._close_async(), loop).result(timeout=15)
        except Exception:  # pragma: no cover - best effort
            logger.debug("browser renderer close failed", exc_info=True)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=5)
            if not loop.is_running():
                loop.close()
            self._launch_error = None
            self._gate = None


_renderer: BrowserPdfRenderer | None = None
_renderer_lock = threading.Lock()


def get_browser_renderer() -> BrowserPdfRenderer:
    """The process-wide renderer (created lazily; closed at shutdown or interpreter exit)."""
    global _renderer
    with _renderer_lock:
        if _renderer is None:
            _renderer = BrowserPdfRenderer()
            atexit.register(shutdown_browser_renderer)
        return _renderer


def start_browser_warm_up() -> None:
    """Start the shared browser in the background (called at application startup). Returns at
    once and never raises: without Edge or Chrome nothing starts, and a start failure is only
    logged, so PDF rendering keeps its existing fallback."""
    try:
        if not browser_available():
            return
        get_browser_renderer().warm_up()
    except Exception as err:  # the application must start regardless
        logger.warning("browser warm-up could not be started: %s", err)


def shutdown_browser_renderer() -> None:
    """Close the shared browser if one was started. Safe to call repeatedly."""
    global _renderer
    with _renderer_lock:
        renderer, _renderer = _renderer, None
    if renderer is not None:
        renderer.close()
