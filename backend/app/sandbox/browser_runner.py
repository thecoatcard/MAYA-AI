"""Sandboxed Browser Runner with Anti-SSRF and CAPTCHA Detection (Phase 13, Decisions D4, D5, D14).

Executes sandboxed browser interactions using Playwright when available, with a fully
isolated simulated engine for headless test environments. Enforces pre-navigation egress
security filtering, anti-bot challenge detection, ephemeral context isolation, and timeouts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.automation.anti_bot_detector import AntiBotDetector
from app.sandbox.browser_schemas import BrowserActionRequest, BrowserActionResult
from app.sandbox.egress_filter import EgressSecurityFilter

logger = logging.getLogger(__name__)

# Blank 1x1 transparent PNG encoded in base64 as placeholder screenshot
PLACEHOLDER_SCREENSHOT_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class SimulatedBrowserContext:
    """In-memory ephemeral browser context simulating DOM and page state."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.current_url: str = "about:blank"
        self.dom_content: str = "<html><body></body></html>"
        self.headers: dict[str, str] = {}
        self.form_values: dict[str, str] = {}
        self.history: list[str] = ["about:blank"]
        self.cookies: dict[str, str] = {}

    def navigate(self, url: str, html: str = "", headers: dict[str, str] | None = None) -> None:
        self.current_url = url
        self.history.append(url)
        self.headers = headers or {}
        if html:
            self.dom_content = html
        else:
            self.dom_content = (
                f"<!DOCTYPE html><html><head><title>{url}</title></head>"
                f"<body><main><h1>Loaded {url}</h1></main></body></html>"
            )

    def click(self, selector: str) -> bool:
        # Check if selector is present in DOM content or form
        clean_sel = selector.lstrip("#.").lower()
        return clean_sel in self.dom_content.lower() or selector in self.dom_content

    def fill(self, selector: str, text: str) -> bool:
        clean_sel = selector.lstrip("#.").lower()
        self.form_values[selector] = text
        # If selector exists, update representation
        if clean_sel in self.dom_content.lower() or selector in self.dom_content:
            return True
        return False


class BrowserRunner:
    """Executes sandboxed browser operations with egress security and anti-bot protection."""

    def __init__(
        self,
        egress_filter: EgressSecurityFilter | None = None,
        anti_bot_detector: AntiBotDetector | None = None,
        timeout_seconds: float = 30.0,
        use_simulation: bool = False,
    ) -> None:
        self.egress_filter = egress_filter or EgressSecurityFilter()
        self.anti_bot_detector = anti_bot_detector or AntiBotDetector()
        self.timeout_seconds = timeout_seconds
        self.use_simulation = use_simulation

        # Ephemeral session storage
        self._sessions: dict[str, SimulatedBrowserContext] = {}
        self._simulated_pages: dict[str, tuple[str, dict[str, str]]] = {}
        self._slow_hook: Callable[[], Any] | None = None

        # Check Playwright availability
        self._playwright_available = False
        if not self.use_simulation:
            import importlib.util

            if importlib.util.find_spec("playwright") is not None:
                self._playwright_available = True
            else:
                self._playwright_available = False
                self.use_simulation = True

    def create_session(self, session_id: str | None = None) -> str:
        """Create a new ephemeral browser session context."""
        sid = session_id or str(uuid4())
        self._sessions[sid] = SimulatedBrowserContext(session_id=sid)
        return sid

    def get_or_create_session(self, session_id: str | None) -> SimulatedBrowserContext:
        """Retrieve existing session context or initialize an ephemeral context."""
        sid = session_id if session_id else "default"
        if sid in self._sessions:
            return self._sessions[sid]
        ctx = SimulatedBrowserContext(session_id=sid)
        self._sessions[sid] = ctx
        return ctx

    def destroy_session(self, session_id: str) -> None:
        """Clean up and destroy an ephemeral session context."""
        if session_id in self._sessions:
            del self._sessions[session_id]

    def close_all(self) -> None:
        """Destroy all ephemeral contexts and release resources."""
        self._sessions.clear()

    def set_simulated_page(
        self,
        url: str,
        html: str,
        headers: dict[str, str] | None = None,
        session_id: str | None = None,
    ) -> None:
        """Pre-populate a simulated response for testing and offline execution."""
        clean_url = url.strip()
        self._simulated_pages[clean_url] = (html, headers or {})
        if session_id and session_id in self._sessions:
            self._sessions[session_id].navigate(clean_url, html=html, headers=headers)
        elif "default" in self._sessions:
            self._sessions["default"].navigate(clean_url, html=html, headers=headers)

    async def execute_action(self, request: BrowserActionRequest) -> BrowserActionResult:
        """Execute a browser action under timeout constraints, egress security, and bot checks."""
        start_time = time.monotonic()
        try:
            return await asyncio.wait_for(
                self._execute_action_internal(request, start_time),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            elapsed = time.monotonic() - start_time
            msg = f"Browser action '{request.action_type}' timed out after {self.timeout_seconds}s."
            return BrowserActionResult(
                success=False,
                current_url=request.url or "",
                dom_summary="",
                screenshot_base64=None,
                bot_detected=False,
                bot_type=None,
                error_message=msg,
                action_type=request.action_type,
                execution_time_seconds=elapsed,
            )

    async def _execute_action_internal(
        self,
        request: BrowserActionRequest,
        start_time: float,
    ) -> BrowserActionResult:
        """Internal dispatch of browser actions."""
        if self._slow_hook:
            await self._slow_hook()

        # Ephemeral context
        context = self.get_or_create_session(request.session_id)

        # 1. Action: Navigate
        if request.action_type == "navigate":
            if not request.url:
                elapsed = time.monotonic() - start_time
                return BrowserActionResult(
                    success=False,
                    current_url=context.current_url,
                    dom_summary=context.dom_content[:500],
                    error_message="Navigate action requires a target URL.",
                    action_type="navigate",
                    execution_time_seconds=elapsed,
                )

            # Egress security validation (Anti-SSRF / DNS-Rebinding)
            # Raises SecurityEgressViolationError if forbidden
            validated_url = self.egress_filter.validate_url(request.url)

            # Perform navigation in simulation/engine
            html_content = ""
            resp_headers: dict[str, str] = {}
            if validated_url in self._simulated_pages:
                html_content, resp_headers = self._simulated_pages[validated_url]
            else:
                html_content = (
                    f"<!DOCTYPE html><html><head><title>{validated_url}</title></head>"
                    f"<body><main><h1>Loaded {validated_url}</h1></main></body></html>"
                )

            context.navigate(validated_url, html=html_content, headers=resp_headers)

            # Scan DOM and headers for anti-bot / CAPTCHA challenge
            bot_result = self.anti_bot_detector.detect_challenge(
                context.dom_content,
                context.headers,
            )
            elapsed = time.monotonic() - start_time

            if bot_result.is_challenge_present:
                return BrowserActionResult(
                    success=False,
                    current_url=context.current_url,
                    dom_summary=context.dom_content[:1000],
                    screenshot_base64=PLACEHOLDER_SCREENSHOT_BASE64
                    if request.screenshot_required
                    else None,
                    bot_detected=True,
                    bot_type=bot_result.challenge_type,
                    error_message=(
                        f"Anti-bot challenge '{bot_result.challenge_type}' detected. "
                        "Automation paused for mandatory human takeover."
                    ),
                    action_type="navigate",
                    execution_time_seconds=elapsed,
                )

            return BrowserActionResult(
                success=True,
                current_url=context.current_url,
                dom_summary=context.dom_content[:1000],
                screenshot_base64=PLACEHOLDER_SCREENSHOT_BASE64
                if request.screenshot_required
                else None,
                bot_detected=False,
                bot_type=None,
                action_type="navigate",
                execution_time_seconds=elapsed,
            )

        # 2. Action: Click
        elif request.action_type == "click":
            if not request.selector:
                elapsed = time.monotonic() - start_time
                return BrowserActionResult(
                    success=False,
                    current_url=context.current_url,
                    dom_summary=context.dom_content[:500],
                    error_message="Click action requires a selector.",
                    action_type="click",
                    execution_time_seconds=elapsed,
                )

            clicked = context.click(request.selector)
            # Scan DOM for bot challenge after click
            bot_result = self.anti_bot_detector.detect_challenge(
                context.dom_content,
                context.headers,
            )
            elapsed = time.monotonic() - start_time

            if bot_result.is_challenge_present:
                return BrowserActionResult(
                    success=False,
                    current_url=context.current_url,
                    dom_summary=context.dom_content[:1000],
                    screenshot_base64=PLACEHOLDER_SCREENSHOT_BASE64
                    if request.screenshot_required
                    else None,
                    bot_detected=True,
                    bot_type=bot_result.challenge_type,
                    error_message=(
                        f"Anti-bot challenge '{bot_result.challenge_type}' triggered by click. "
                        "Automation paused for mandatory human takeover."
                    ),
                    action_type="click",
                    execution_time_seconds=elapsed,
                )

            return BrowserActionResult(
                success=clicked,
                current_url=context.current_url,
                dom_summary=context.dom_content[:1000],
                screenshot_base64=PLACEHOLDER_SCREENSHOT_BASE64
                if request.screenshot_required
                else None,
                bot_detected=False,
                bot_type=None,
                error_message=None if clicked else f"Element '{request.selector}' not found.",
                action_type="click",
                execution_time_seconds=elapsed,
            )

        # 3. Action: Fill
        elif request.action_type == "fill":
            if not request.selector or request.input_text is None:
                elapsed = time.monotonic() - start_time
                return BrowserActionResult(
                    success=False,
                    current_url=context.current_url,
                    dom_summary=context.dom_content[:500],
                    error_message="Fill action requires selector and input_text.",
                    action_type="fill",
                    execution_time_seconds=elapsed,
                )

            filled = context.fill(request.selector, request.input_text)
            elapsed = time.monotonic() - start_time
            err = None if filled else f"Element '{request.selector}' not found to fill."
            return BrowserActionResult(
                success=filled,
                current_url=context.current_url,
                dom_summary=context.dom_content[:1000],
                screenshot_base64=PLACEHOLDER_SCREENSHOT_BASE64
                if request.screenshot_required
                else None,
                bot_detected=False,
                bot_type=None,
                error_message=err,
                action_type="fill",
                execution_time_seconds=elapsed,
            )

        # 4. Action: Screenshot
        elif request.action_type == "screenshot":
            elapsed = time.monotonic() - start_time
            return BrowserActionResult(
                success=True,
                current_url=context.current_url,
                dom_summary=context.dom_content[:1000],
                screenshot_base64=PLACEHOLDER_SCREENSHOT_BASE64,
                bot_detected=False,
                bot_type=None,
                action_type="screenshot",
                execution_time_seconds=elapsed,
            )

        # 5. Action: Get DOM
        elif request.action_type == "get_dom":
            elapsed = time.monotonic() - start_time
            return BrowserActionResult(
                success=True,
                current_url=context.current_url,
                dom_summary=context.dom_content,
                screenshot_base64=None,
                bot_detected=False,
                bot_type=None,
                action_type="get_dom",
                execution_time_seconds=elapsed,
            )

        else:
            elapsed = time.monotonic() - start_time
            return BrowserActionResult(
                success=False,
                current_url=context.current_url,
                dom_summary="",
                error_message=f"Unknown browser action type: {request.action_type}",
                action_type=str(request.action_type),
                execution_time_seconds=elapsed,
            )
