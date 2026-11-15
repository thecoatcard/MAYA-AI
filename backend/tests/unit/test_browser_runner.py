"""Unit tests for Sandboxed Browser Runner (Phase 13, Decisions D4, D5, D14)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from app.automation.anti_bot_detector import AntiBotDetector
from app.sandbox.browser_runner import BrowserRunner
from app.sandbox.browser_schemas import BrowserActionRequest
from app.sandbox.egress_filter import EgressSecurityFilter, SecurityEgressViolationError


@pytest.fixture
def mock_resolver() -> Callable[[str], list[str]]:
    def _resolver(host: str) -> list[str]:
        if host in {"example.com", "public.com", "safe.org"}:
            return ["93.184.216.34"]
        if host == "metadata.cloud":
            return ["169.254.169.254"]
        if host == "rebind.attacker.com":
            return ["127.0.0.1"]
        return ["93.184.216.34"]

    return _resolver


@pytest.fixture
def browser_runner(mock_resolver: Callable[[str], list[str]]) -> BrowserRunner:
    egress_filter = EgressSecurityFilter(dns_resolver=mock_resolver)
    anti_bot = AntiBotDetector()
    return BrowserRunner(
        egress_filter=egress_filter,
        anti_bot_detector=anti_bot,
        timeout_seconds=5.0,
        use_simulation=True,
    )


class TestBrowserRunner:
    @pytest.mark.asyncio
    async def test_navigate_allowed_url_succeeds(self, browser_runner: BrowserRunner) -> None:
        browser_runner.set_simulated_page(
            "https://example.com/welcome",
            html="<html><body><h1>Welcome to Maya</h1></body></html>",
        )
        req = BrowserActionRequest(
            action_type="navigate",
            url="https://example.com/welcome",
        )
        result = await browser_runner.execute_action(req)
        assert result.success is True
        assert result.current_url == "https://example.com/welcome"
        assert "Welcome to Maya" in result.dom_summary
        assert result.bot_detected is False
        assert result.screenshot_base64 is not None

    @pytest.mark.asyncio
    async def test_navigate_blocked_by_egress_security(self, browser_runner: BrowserRunner) -> None:
        blocked_urls = [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8000/admin",
            "http://10.0.0.1/internal",
            "http://metadata.cloud/credentials",
            "http://rebind.attacker.com/",
            "file:///etc/shadow",
        ]
        for url in blocked_urls:
            req = BrowserActionRequest(action_type="navigate", url=url)
            result = await browser_runner.execute_action(req)
            assert result.success is False
            assert result.error_message is not None
            assert any(
                term in result.error_message.lower() for term in ("blocked", "prohibited", "egress")
            )

    @pytest.mark.asyncio
    async def test_execute_action_catches_egress_violation_error(self) -> None:
        class AlwaysRejectFilter(EgressSecurityFilter):
            def validate_url(self, url: str) -> str:
                raise SecurityEgressViolationError(f"Rejected domain in test: {url}")

        runner = BrowserRunner(
            egress_filter=AlwaysRejectFilter(),
            anti_bot_detector=AntiBotDetector(),
            timeout_seconds=5.0,
            use_simulation=True,
        )
        req = BrowserActionRequest(action_type="navigate", url="https://example.com")
        result = await runner.execute_action(req)
        assert result.success is False
        assert result.error_message is not None
        assert "Rejected domain in test" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_action_catches_slow_hook_runtime_error(
        self, browser_runner: BrowserRunner
    ) -> None:
        async def failing_hook() -> None:
            raise RuntimeError("Hook failure in test")

        browser_runner._slow_hook = failing_hook
        req = BrowserActionRequest(action_type="navigate", url="https://example.com")
        result = await browser_runner.execute_action(req)
        assert result.success is False
        assert result.error_message is not None
        assert "Hook failure in test" in result.error_message

    @pytest.mark.asyncio
    async def test_navigate_detects_captcha_and_triggers_takeover(
        self, browser_runner: BrowserRunner
    ) -> None:
        browser_runner.set_simulated_page(
            "https://safe.org/login",
            html="""
            <html>
            <body>
                <div class="cf-turnstile" data-sitekey="0x4AAAAAAABcd"></div>
                <p>Verifying browser...</p>
            </body>
            </html>
            """,
        )
        req = BrowserActionRequest(
            action_type="navigate",
            url="https://safe.org/login",
        )
        result = await browser_runner.execute_action(req)
        assert result.success is False
        assert result.bot_detected is True
        assert result.bot_type == "cloudflare_turnstile"
        assert "human takeover" in (result.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_click_and_fill_actions(self, browser_runner: BrowserRunner) -> None:
        session_id = browser_runner.create_session()
        browser_runner.set_simulated_page(
            "https://public.com/form",
            html="""
            <html>
            <body>
                <input id="search-box" type="text" value=""/>
                <button id="search-btn">Search</button>
            </body>
            </html>
            """,
            session_id=session_id,
        )

        # First navigate
        await browser_runner.execute_action(
            BrowserActionRequest(
                action_type="navigate",
                url="https://public.com/form",
                session_id=session_id,
            )
        )

        # Fill input
        fill_res = await browser_runner.execute_action(
            BrowserActionRequest(
                action_type="fill",
                selector="#search-box",
                input_text="Maya AI",
                session_id=session_id,
            )
        )
        assert fill_res.success is True

        # Click button
        click_res = await browser_runner.execute_action(
            BrowserActionRequest(
                action_type="click",
                selector="#search-btn",
                session_id=session_id,
            )
        )
        assert click_res.success is True

        # Get DOM
        dom_res = await browser_runner.execute_action(
            BrowserActionRequest(
                action_type="get_dom",
                session_id=session_id,
            )
        )
        assert dom_res.success is True
        assert "search-box" in dom_res.dom_summary

    @pytest.mark.asyncio
    async def test_session_cleanup_and_ephemeral_destruction(
        self, browser_runner: BrowserRunner
    ) -> None:
        session_id = browser_runner.create_session()
        browser_runner.set_simulated_page(
            "https://public.com/secret",
            html="<html><body>Secret Context Data</body></html>",
            session_id=session_id,
        )
        await browser_runner.execute_action(
            BrowserActionRequest(
                action_type="navigate",
                url="https://public.com/secret",
                session_id=session_id,
            )
        )

        # Destroy context
        browser_runner.destroy_session(session_id)

        # Querying closed session creates fresh or fails
        dom_res = await browser_runner.execute_action(
            BrowserActionRequest(
                action_type="get_dom",
                session_id=session_id,
            )
        )
        # Should not retain old secret page
        assert "Secret Context Data" not in dom_res.dom_summary

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self, mock_resolver: Callable[[str], list[str]]) -> None:
        # Create runner with tiny timeout
        fast_runner = BrowserRunner(
            egress_filter=EgressSecurityFilter(dns_resolver=mock_resolver),
            anti_bot_detector=AntiBotDetector(),
            timeout_seconds=0.01,
            use_simulation=True,
        )

        async def slow_action() -> None:
            await asyncio.sleep(0.5)

        fast_runner._slow_hook = slow_action  # Hook for triggering timeout in simulation

        res = await fast_runner.execute_action(
            BrowserActionRequest(
                action_type="navigate",
                url="https://public.com/test",
            )
        )
        assert res.success is False
        assert "timed out" in (res.error_message or "").lower()
