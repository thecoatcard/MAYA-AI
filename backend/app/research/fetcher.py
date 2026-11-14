"""Reading one public page for deep research.

Builds on ``app.search.fetch.PageFetcher``, which already enforces the safety rules (http and https
only, no credentials, public addresses only, every redirect re-checked with at most 3 hops, the
connection pinned to the checked address, ``robots.txt``, 2 MB, one request at a time per host with
a pause). This module adds what research needs on top: PDF text, longer timeouts (10 s connect and
20 s read), one attempt only (a blocked page is recorded, never retried), the reason a page was
skipped (paywall, login, bot check, robots, rate limit, empty or script-only page) and the
untrusted-data treatment of the text (marker envelope, regex scan and the injection screen).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Final, Protocol

import httpx

from app.decisions.screen import screen_instructions
from app.mcp.sanitizer import sanitize_tool_output, wrap_untrusted_input
from app.research.models import Skip, Source, site_of
from app.search.fetch import PageFetcher, PageResult

logger = logging.getLogger(__name__)

PAGE_CHARS: Final = 24_000
MIN_TEXT_CHARS: Final = 200
MODEL_CHARS: Final = 12_000  # the part of a page a model call reads
_GATE_TEXT_LIMIT: Final = 2_500  # a gate message sits on a short page; a long article is read
_PAYWALL: Final = (
    "subscribe to continue",
    "subscribe to read",
    "subscribers only",
    "for subscribers",
    "already a subscriber",
    "sign in to continue",
    "log in to continue",
    "login to continue",
    "sign in to read",
    "create a free account to",
    "register to continue",
    "become a member to",
)
_BOT: Final = (
    "verify you are human",
    "are you a robot",
    "captcha",
    "just a moment",
    "checking your browser",
    "access denied",
    "request blocked",
    "unusual traffic",
    "enable javascript and cookies",
)
_SCRIPT: Final = ("enable javascript", "javascript is required", "requires javascript")
_REASON_LABELS: Final = {
    "paywall": "paywall",
    "login": "login required",
    "blocked": "blocked (bot check)",
    "robots": "not allowed by robots.txt",
    "rate_limited": "rate limited",
    "empty": "no readable text",
    "script_only": "needs JavaScript",
    "not_text": "not a readable page",
    "timeout": "timed out",
    "network": "network error",
    "unsafe": "address not allowed",
    "error": "could not be read",
}


class PageReader(Protocol):
    async def fetch_page(self, url: str, max_chars: int = ...) -> PageResult: ...


def build_reader() -> PageFetcher:
    """The production reader: PDF allowed, 10 s connect and 20 s read, 2 pages per host."""
    return PageFetcher(
        allow_pdf=True,
        timeout=httpx.Timeout(20.0, connect=10.0),
        max_per_host=2,
        host_delay_seconds=0.5,
    )


def reason_label(code: str) -> str:
    return _REASON_LABELS.get(code, code)


def _has(text: str, needles: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(n in low for n in needles)


def classify_failure(result: PageResult) -> tuple[str, str]:
    """``(code, detail)`` for a page that was not read. Never retried."""
    reason = result.reason
    if reason == "robots_disallowed":
        return "robots", ""
    if reason.startswith("blocked"):
        return "unsafe", ""
    if reason in ("timeout", "network"):
        return reason, ""
    if reason == "http_401":
        return "login", reason
    if reason in ("http_402", "http_407"):
        return "paywall", reason
    if reason == "http_403":
        return "blocked", reason
    if reason == "http_429":
        return "rate_limited", reason
    if reason in ("no_text", "empty"):
        return "empty", ""
    if reason == "not_a_web_page":
        return "not_text", ""
    return "error", reason


def classify_text(text: str) -> tuple[str, str] | None:
    """A page that loaded but is a gate, a bot check or empty: ``(code, detail)``, else ``None``."""
    if len(text) < _GATE_TEXT_LIMIT:
        if _has(text, _BOT):
            return "blocked", "bot check page"
        if _has(text, _PAYWALL):
            return "paywall", "subscription or login wall"
        if _has(text, _SCRIPT):
            return "script_only", ""
    if len(text) < MIN_TEXT_CHARS:
        return "empty", "less than 200 characters of text"
    return None


class ResearchFetcher:
    def __init__(self, reader: PageReader | None = None, *, max_chars: int = PAGE_CHARS) -> None:
        self._reader: Any = reader or build_reader()
        self._max_chars = max_chars

    async def fetch(self, url: str) -> Source | Skip:
        """The page as a ``Source`` (text already wrapped and screened) or why it was skipped."""
        domain = site_of(url)
        try:
            result = await self._reader.fetch_page(url, self._max_chars)
        except Exception as error:  # noqa: BLE001 - one bad page never stops a run
            logger.warning("research_fetch_failed error=%s", type(error).__name__)
            return Skip(url, domain, "error", type(error).__name__)
        if not result.ok:
            code, detail = classify_failure(result)
            return Skip(url, domain, code, detail)
        gated = classify_text(result.text)
        if gated is not None:
            return Skip(url, domain, gated[0], gated[1])
        # Untrusted data: marker envelope plus the regex scan, then the model screen (advisory).
        safe = sanitize_tool_output(result.text, tool_name=f"web_page:{domain}")
        flagged = safe.has_injection_suspicions
        if not flagged:
            flagged = await screen_instructions(safe.clean_text, f"web page {domain}")
        return Source(
            url=url,
            final_url=result.final_url or url,
            title=(result.title or domain)[:300],
            domain=domain,
            published=result.published,
            text=safe.clean_text,
            wrapped=wrap_untrusted_input(
                safe.clean_text[:MODEL_CHARS],
                source=f"web_page:{domain}",
                timestamp=datetime.now(UTC).isoformat(),
            ),
            flagged=flagged,
        )
