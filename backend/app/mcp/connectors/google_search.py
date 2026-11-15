"""Google Search MCP connector: Gemini Search Grounding through the Model Gateway.

The connector is a thin adapter. It holds no provider client: every grounded call goes through
``ModelGateway.generate_text(..., enable_google_search=True, **search_call_kwargs("research"))``,
so it gets the API-key pool (affinity and instant failover), retries, the agent model alias and
timeout, cost tracking and metrics exactly like every other agent call.

Failures keep their identity. A 429 / 503 / timeout is reported as ``GoogleSearchError`` with a
``reason`` (``rate_limited``, ``unavailable``, ``timeout``, ``error``) and the provider status; the
exception is chained to the provider error so the task worker's rate-limit detection (which walks
``__cause__``) still pauses and retries the step. An empty answer is retried once through the
gateway before the search fails with reason ``no_content``. Nothing is invented: no canned
findings, and a citation is only ever a URL that the grounding metadata returned.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.core.provider_errors import provider_error_info
from app.mcp.limiter import VendorRateLimiter
from app.mcp.sanitizer import SanitizationResult, sanitize_tool_output

logger = logging.getLogger(__name__)

MAX_SOURCES = 20
SOURCES_HEADING = "Sources:"

_SEARCH_PROMPT = (
    "Perform a thorough web search using Google Search for: {query}\n"
    "Synthesize the latest factual information, official documentation, code examples, and "
    "technical specifications into a clear, detailed report."
)
_EMPTY_RESPONSE_RETRY_PROMPT = (
    "Try the search again using a concise answer. Use Google Search grounding to find "
    "reliable sources that directly address the request. Summarize only supported facts, "
    "and include the relevant years when available. Request: {query}"
)

REASON_RATE_LIMITED = "rate_limited"
REASON_UNAVAILABLE = "unavailable"
REASON_TIMEOUT = "timeout"
REASON_NO_CONTENT = "no_content"
REASON_ERROR = "error"
# Explicit outcome when the search alias and its fallback are both exhausted (quota or 5xx).
OUTCOME_SEARCH_UNAVAILABLE = "search_unavailable"

_REASON_TEXT = {
    REASON_RATE_LIMITED: (
        "Google Search grounding is rate limited on all tried keys; try again in a minute"
    ),
    REASON_UNAVAILABLE: (
        "Google Search grounding is temporarily unavailable (the provider answered 503); "
        "try again in a minute"
    ),
    REASON_TIMEOUT: "Google Search grounding timed out; try again",
    REASON_NO_CONTENT: "Google Search returned no content",
    REASON_ERROR: "Google Search grounding failed",
}


class GoogleSearchError(RuntimeError):
    """Grounded search failed. ``reason`` and ``status`` say how, without provider text."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = REASON_NO_CONTENT,
        status: int | None = None,
        error_class: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.status = status
        self.error_class = error_class

    @property
    def search_unavailable(self) -> bool:
        """True when grounded search could not run at all (quota or provider unavailable)."""
        return self.reason in (REASON_RATE_LIMITED, REASON_UNAVAILABLE)

    @property
    def outcome(self) -> str:
        return OUTCOME_SEARCH_UNAVAILABLE if self.search_unavailable else self.reason


@dataclass(frozen=True, slots=True)
class GoogleSearchResult:
    """Structured result of a Google Search grounding operation."""

    query: str
    text: str
    citations: list[dict[str, str]]
    sanitized_result: SanitizationResult


def format_sources(citations: list[dict[str, str]], limit: int = MAX_SOURCES) -> str:
    """The deterministic ``Sources:`` list (numbered ``title - url``), or ``""`` for none."""
    seen: set[str] = set()
    lines: list[str] = []
    for citation in citations:
        url = str(citation.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        title = " ".join(str(citation.get("title") or url).split())[:150]
        lines.append(f"{len(lines) + 1}. {title} - {url}")
        if len(lines) >= limit:
            break
    return f"{SOURCES_HEADING}\n" + "\n".join(lines) if lines else ""


_SOURCE_LINE = re.compile(r"^\s*\d{1,3}\.\s+(?P<title>.*?)\s+-\s+(?P<url>https?://\S+)\s*$")


def parse_sources(text: str) -> list[dict[str, str]]:
    """Citations back from the ``Sources:`` block that :func:`format_sources` appended."""
    marker = text.rfind(f"\n{SOURCES_HEADING}\n")
    if marker < 0:
        return []
    out: list[dict[str, str]] = []
    for line in text[marker:].splitlines():
        match = _SOURCE_LINE.match(line)
        if match:
            out.append(
                {
                    "title": match.group("title").strip() or match.group("url"),
                    "url": match.group("url"),
                    "snippet": "",
                }
            )
    return out


def with_sources(text: str, citations: list[dict[str, str]]) -> str:
    """``text`` followed by the numbered sources (unchanged when there are none)."""
    block = format_sources(citations)
    return f"{text.rstrip()}\n\n{block}" if block else text


class GoogleSearchConnector:
    """Google Search connector powered by Gemini web search grounding via the Model Gateway."""

    def __init__(
        self,
        gateway: Any | None = None,
        rate_limiter: VendorRateLimiter | None = None,
    ) -> None:
        self.gateway = gateway
        self.rate_limiter = rate_limiter or VendorRateLimiter()

    async def search(
        self,
        query: str,
        count: int = 5,
        *,
        workspace_id: Any | None = None,
        task_id: Any | None = None,
    ) -> GoogleSearchResult:
        """Run one grounded search, collect its citations and sanitize the text."""
        clean_query = query.strip()
        if self.gateway is None:
            raise GoogleSearchError(
                "Google Search has no model gateway: configure the Gemini API key",
                reason=REASON_ERROR,
            )
        # Imported here: ``app.agents`` imports the task package, which imports this module.
        from app.agents.model_policy import search_call_kwargs

        await self.rate_limiter.acquire("google_search")

        text = ""
        citations: list[dict[str, str]] = []
        for attempt, prompt in enumerate(
            (
                _SEARCH_PROMPT.format(query=clean_query),
                _EMPTY_RESPONSE_RETRY_PROMPT.format(query=clean_query),
            )
        ):
            try:
                response = await self.gateway.generate_text(
                    **search_call_kwargs("research"),
                    contents=[prompt],
                    temperature=0.2,
                    enable_google_search=True,
                    workspace_id=workspace_id,
                    task_id=task_id,
                )
            except Exception as err:
                raise self._classified(err) from err
            text = (getattr(response, "text", "") or "").strip()
            if text:
                # Only the response that supplied content contributes citations.
                citations = [
                    {
                        "title": str(c.get("title") or c["url"]),
                        "url": str(c["url"]),
                        "snippet": "",
                    }
                    for c in (getattr(response, "citations", None) or [])
                    if c.get("url")
                ]
                break
            if attempt == 0:
                logger.info("google_search_empty_retry query_length=%d", len(clean_query))

        if not text:
            # No canned findings or invented citations: an empty search is an error.
            raise GoogleSearchError(
                f"{_REASON_TEXT[REASON_NO_CONTENT]} for '{clean_query[:80]}'",
                reason=REASON_NO_CONTENT,
            )

        sanitized = sanitize_tool_output(text, tool_name="google_search")
        return GoogleSearchResult(
            query=clean_query,
            text=text,
            citations=citations,
            sanitized_result=sanitized,
        )

    @staticmethod
    def _classified(err: Exception) -> GoogleSearchError:
        """A ``GoogleSearchError`` that keeps the provider error class and status."""
        error_class, status = provider_error_info(err)
        if status in (429, 408) or error_class == "RESOURCE_EXHAUSTED":
            reason = REASON_RATE_LIMITED
        elif status is not None and 500 <= status < 600:
            reason = REASON_UNAVAILABLE
        elif isinstance(err, TimeoutError) or "timeout" in error_class.lower():
            reason = REASON_TIMEOUT
        else:
            reason = REASON_ERROR
        # Class and status only: provider text can echo the prompt, and keys never reach here.
        logger.warning(
            "google_search_grounding_failed error_class=%s status=%s reason=%s",
            error_class,
            status if status is not None else "none",
            reason,
        )
        return GoogleSearchError(
            _REASON_TEXT[reason], reason=reason, status=status, error_class=error_class
        )
