"""Safe page reading: ``fetch_page(url, max_chars)``.

Rules, all enforced here and not left to the caller:

* only ``http`` and ``https``, no credentials in the URL, only the usual web ports;
* the host is resolved once and every address must be a public one (no loopback, private,
  link-local, metadata, carrier-grade NAT or reserved range, also for IPv4-mapped IPv6); the
  connection then goes to that checked address, so a second DNS answer cannot redirect it
  (DNS rebinding); the same check runs on every redirect hop (at most 3);
* ``robots.txt`` is respected (cached per host); MAYA identifies itself in the User-Agent;
* 10 s per request, 2 MB at most, HTML or text only; at most 2 requests at once per host with a
  short pause between them.
"""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import httpx

from app.search.extract import extract_page, extract_pdf

logger = logging.getLogger(__name__)

USER_AGENT: Final = "MAYA-AI/0.1 (+https://maya.ai; dev@maya.ai)"
# Some sites refuse an unusual agent string. Page reading rotates between these browser-compatible
# strings, each carrying the MAYA-AI identifier with contact information.
USER_AGENTS: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 MAYA-AI/0.1 (https://maya.ai; dev@maya.ai)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.6 Safari/605.1.15 MAYA-AI/0.1 (https://maya.ai; dev@maya.ai)",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0 "
    "MAYA-AI/0.1 (https://maya.ai; dev@maya.ai)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0 MAYA-AI/0.1 (https://maya.ai; dev@maya.ai)",
)
_agent_cursor = itertools.count()


def next_user_agent() -> str:
    """The next agent string in rotation (per process)."""
    return USER_AGENTS[next(_agent_cursor) % len(USER_AGENTS)]


ROBOTS_TOKEN: Final = "MAYA-AI"
MAX_BYTES: Final = 2 * 1024 * 1024
MAX_REDIRECTS: Final = 3
REQUEST_TIMEOUT: Final = 10.0
ALLOWED_PORTS: Final = frozenset({80, 443, 8080, 8443})
ROBOTS_TTL_SECONDS: Final = 3600.0
_TEXT_TYPES: Final = ("text/html", "application/xhtml+xml", "text/plain")

Resolver = Callable[[str], Awaitable[list[str]]]


class UnsafeUrlError(Exception):
    """The URL points somewhere MAYA must not connect to."""


@dataclass(frozen=True, slots=True)
class PageResult:
    url: str
    ok: bool
    reason: str = ""
    final_url: str = ""
    title: str = ""
    published: str | None = None
    text: str = ""
    truncated: bool = False


async def system_resolver(host: str) -> list[str]:
    """Every address the system resolver gives for ``host``."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=0, proto=0)
    return sorted({str(info[4][0]) for info in infos})


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class PageFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        host_delay_seconds: float = 0.3,
        max_per_host: int = 2,
        clock: Callable[[], float] = time.monotonic,
        allow_pdf: bool = False,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        # ``allow_pdf`` (deep research only): a PDF is read as text with pypdf; every other check
        # (SSRF, robots, 2 MB, redirects) is the same. Off by default: the chat tool reads HTML.
        self._allow_pdf = allow_pdf
        self._timeout = timeout or httpx.Timeout(REQUEST_TIMEOUT)
        self._resolver = resolver
        self._transport = transport
        self._delay = host_delay_seconds
        self._max_per_host = max_per_host
        self._clock = clock
        self._gates: dict[str, asyncio.Semaphore] = {}
        self._last_start: dict[str, float] = {}
        self._pace = asyncio.Lock()
        self._robots: dict[str, tuple[float, robotparser.RobotFileParser | None]] = {}

    # ------------------------------------------------------------------ public

    async def fetch_page(self, url: str, max_chars: int = 4000) -> PageResult:
        """The title, date and main text of a public page, or why it was not read."""
        current = url.strip()
        try:
            for hop in range(MAX_REDIRECTS + 1):
                host, port, scheme = self._parts(current)
                if not await self._robots_allow(current):
                    return PageResult(url, False, "robots_disallowed", current)
                response = await self._request(current, host, port, scheme)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        return PageResult(url, False, "bad_redirect", current)
                    if hop == MAX_REDIRECTS:
                        return PageResult(url, False, "too_many_redirects", current)
                    current = urljoin(current, location)
                    continue
                return self._result(url, current, response, max_chars)
        except UnsafeUrlError as error:
            return PageResult(url, False, f"blocked: {error}", current)
        except httpx.TimeoutException:
            return PageResult(url, False, "timeout", current)
        except httpx.HTTPError:
            return PageResult(url, False, "network", current)
        return PageResult(url, False, "too_many_redirects", current)  # pragma: no cover

    # ------------------------------------------------------------------ safety

    @staticmethod
    def _parts(url: str) -> tuple[str, int, str]:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            raise UnsafeUrlError("only http and https links are read")
        if parts.username or parts.password:
            raise UnsafeUrlError("links with a login are not read")
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            raise UnsafeUrlError("no host")
        try:
            port = parts.port or (443 if scheme == "https" else 80)
        except ValueError as error:
            raise UnsafeUrlError("bad port") from error
        if port not in ALLOWED_PORTS:
            raise UnsafeUrlError("port not allowed")
        return host, port, scheme

    async def _safe_address(self, host: str) -> str:
        """One checked public address for ``host``; raises if any answer is not public."""
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            try:
                addresses = await self._resolver(host)
            except OSError as error:
                raise UnsafeUrlError("the host did not resolve") from error
        if not addresses:
            raise UnsafeUrlError("the host did not resolve")
        for address in addresses:
            try:
                public = _is_public(address)
            except ValueError as error:
                raise UnsafeUrlError("unreadable address") from error
            if not public:
                raise UnsafeUrlError("the address is not public")
        return addresses[0]

    # ------------------------------------------------------------------ requests

    async def _request(
        self, url: str, host: str, port: int, scheme: str, *, limit: int = MAX_BYTES
    ) -> _Response:
        address = await self._safe_address(host)
        gate = self._gates.setdefault(host, asyncio.Semaphore(self._max_per_host))
        async with gate:
            await self._wait_turn(host)
            return await self._send(url, host, port, scheme, address, limit)

    async def _wait_turn(self, host: str) -> None:
        async with self._pace:
            now = self._clock()
            start = max(now, self._last_start.get(host, 0.0) + self._delay)
            self._last_start[host] = start
        if start > now:
            await asyncio.sleep(start - now)

    async def _send(
        self, url: str, host: str, port: int, scheme: str, address: str, limit: int
    ) -> _Response:
        """GET ``url`` through the checked ``address`` (Host header and TLS name stay the host)."""
        parts = urlsplit(url)
        target_host = f"[{address}]" if ":" in address else address
        default = 443 if scheme == "https" else 80
        netloc = target_host if port == default else f"{target_host}:{port}"
        target = parts._replace(scheme=scheme, netloc=netloc).geturl()
        host_header = host if port == default else f"{host}:{port}"
        headers = {
            "User-Agent": next_user_agent(),
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9"
            + (",application/pdf;q=0.8" if self._allow_pdf else ""),
            "Host": host_header,
        }
        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=False, transport=self._transport
        ) as client:
            async with client.stream(
                "GET", target, headers=headers, extensions={"sni_hostname": host}
            ) as response:
                body = bytearray()
                truncated = False
                declared = response.headers.get("content-length", "")
                content_type = response.headers.get("content-type", "")
                is_redirect = response.status_code in (301, 302, 303, 307, 308)
                if is_redirect or not self._acceptable(content_type):
                    return _Response(response.status_code, response.headers, b"", False)
                if declared.isdigit() and int(declared) > limit:
                    truncated = True
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= limit:
                        truncated = True
                        break
                return _Response(
                    response.status_code, response.headers, bytes(body[:limit]), truncated
                )

    # ------------------------------------------------------------------ robots.txt

    async def _robots_allow(self, url: str) -> bool:
        parts = urlsplit(url)
        host, port, scheme = self._parts(url)
        origin = f"{scheme}://{parts.netloc.rsplit('@', 1)[-1]}"
        cached = self._robots.get(origin)
        if cached is None or self._clock() - cached[0] > ROBOTS_TTL_SECONDS:
            parser = await self._load_robots(origin, host, port, scheme)
            self._robots[origin] = (self._clock(), parser)
        else:
            parser = cached[1]
        if parser is None:
            return True
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        return parser.can_fetch(ROBOTS_TOKEN, path)

    async def _load_robots(
        self, origin: str, host: str, port: int, scheme: str
    ) -> robotparser.RobotFileParser | None:
        """The parsed robots.txt; ``None`` means everything is allowed (none, or unreachable)."""
        try:
            response = await self._request(
                f"{origin}/robots.txt", host, port, scheme, limit=256 * 1024
            )
        except UnsafeUrlError:
            raise
        except httpx.HTTPError:
            return None
        parser = robotparser.RobotFileParser()
        if response.status_code >= 500:
            # RFC 9309: an unavailable robots.txt means no crawling
            parser.parse(["User-agent: *", "Disallow: /"])
            return parser
        if response.status_code >= 400 or not response.body:
            return None
        parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
        return parser

    # ------------------------------------------------------------------ result

    def _acceptable(self, content_type: str) -> bool:
        return _readable_type(content_type) or (self._allow_pdf and _is_pdf(content_type))

    def _result(self, url: str, final_url: str, response: _Response, max_chars: int) -> PageResult:
        if response.status_code >= 400:
            return PageResult(url, False, f"http_{response.status_code}", final_url)
        content_type = response.headers.get("content-type", "")
        if not self._acceptable(content_type):
            return PageResult(url, False, "not_a_web_page", final_url)
        if _is_pdf(content_type):
            pdf = extract_pdf(response.body, max_chars=max_chars)
            if not pdf.text:
                return PageResult(url, False, "no_text", final_url, pdf.title, None, "", False)
            return PageResult(
                url, True, "", final_url, pdf.title, pdf.published, pdf.text, response.truncated
            )
        raw = _decode(response.body, content_type)
        if "html" not in content_type.lower() and "<html" not in raw[:2000].lower():
            text = " ".join(raw.split())[:max_chars]
            return PageResult(url, bool(text), "" if text else "empty", final_url, "", None, text)
        page = extract_page(raw, max_chars=max_chars)
        if not page.text:
            return PageResult(
                url, False, "no_text", final_url, page.title, page.published, "", response.truncated
            )
        return PageResult(
            url,
            True,
            "",
            final_url,
            page.title,
            page.published,
            page.text,
            response.truncated,
        )


@dataclass(frozen=True, slots=True)
class _Response:
    status_code: int
    headers: httpx.Headers
    body: bytes
    truncated: bool


def _readable_type(content_type: str) -> bool:
    kind = content_type.split(";", 1)[0].strip().lower()
    return not kind or kind in _TEXT_TYPES


def _is_pdf(content_type: str) -> bool:
    return content_type.split(";", 1)[0].strip().lower() == "application/pdf"


def _decode(body: bytes, content_type: str) -> str:
    charset = ""
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset":
            charset = value.strip("\"' ")
    for encoding in ([charset] if charset else []) + ["utf-8"]:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("cp1252", errors="replace")


_default_fetcher: PageFetcher | None = None


async def fetch_page(url: str, max_chars: int = 4000) -> PageResult:
    """``PageFetcher.fetch_page`` on the shared fetcher of this process."""
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = PageFetcher()
    return await _default_fetcher.fetch_page(url, max_chars)
