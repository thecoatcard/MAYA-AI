"""Who may call what: tiers for third-party tools, and the refusal of private browser targets.

Tiers reuse ``app.mcp.permissions``: a tool is READ only when the definition lists it as
read-only, a server hint (``destructiveHint``) can only raise it, and every other tool, known or
not, is APPROVAL_REQUIRED. The browser guard keeps a model-driven browser away from the user's
own machine and network (SSRF): localhost, private, link-local, metadata and non-http(s) targets
are refused before anything is sent to the server, and again when an approved call executes.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final
from urllib.parse import unquote, urlsplit

from app.mcp.permissions import PermissionDeniedError, ToolPermissionRegistry, ToolTier
from app.mcp_client.definitions import ServerDefinition

Resolver = Callable[[str], Awaitable[list[str]]]

NAME_PREFIX: Final = "mcp__"
DNS_TIMEOUT_SECONDS: Final = 3.0
_MAX_WALK_STRINGS: Final = 200
_MAX_WALK_DEPTH: Final = 4

_URL_KEYS: Final = frozenset({"url", "uri", "href", "link", "origin", "target_url", "page_url"})
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_HTTP_SCHEMES: Final = frozenset({"http", "https"})
# Schemes a browser would act on; a bare 'host:port' is not one of these.
_SPECIAL_SCHEMES: Final = frozenset(
    {"javascript", "data", "file", "about", "chrome", "blob", "view-source", "ftp", "ws", "wss"}
)
_INTERNAL_SUFFIXES: Final = (
    ".localhost",
    ".local",
    ".internal",
    ".localdomain",
    ".lan",
    ".home.arpa",
    ".intranet",
    ".corp",
)
_METADATA_NAMES: Final = frozenset({"metadata", "metadata.google.internal", "instance-data"})


def tool_model_name(server: str, tool: str) -> str:
    """The plain (untruncated) ``mcp__<server>__<tool>`` name; see ``schema.model_name``."""
    return f"{NAME_PREFIX}{server}__{tool}"


def build_registry(definitions: Mapping[str, ServerDefinition]) -> ToolPermissionRegistry:
    """A frozen registry holding READ for each listed read-only tool; every other name is unknown
    to it (and therefore APPROVAL_REQUIRED, see ``base_tier``)."""
    initial: dict[str, ToolTier] = {}
    for server, definition in definitions.items():
        for tool in definition.read_tools:
            initial[tool_model_name(server, tool)] = ToolTier.READ
    registry = ToolPermissionRegistry(initial)
    registry.freeze()
    return registry


def base_tier(
    registry: ToolPermissionRegistry,
    server: str,
    tool: str,
    hints: Mapping[str, Any] | None = None,
) -> ToolTier:
    """READ for a listed read-only tool, else APPROVAL_REQUIRED. A destructive hint raises it."""
    try:
        return registry.classify(tool_model_name(server, tool), dict(hints) if hints else None)
    except PermissionDeniedError:
        return ToolTier.APPROVAL_REQUIRED


# --------------------------------------------------------------------------------- URL guard


async def default_resolver(host: str) -> list[str]:
    """Addresses a name resolves to (empty when it does not resolve)."""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), DNS_TIMEOUT_SECONDS
        )
    except (OSError, TimeoutError):
        return []
    return sorted({str(info[4][0]).split("%")[0] for info in infos})


def _numeric_host(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """An IP address written in any form a browser accepts (dotted, short, decimal, hex, octal)."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if re.fullmatch(r"[0-9a-fA-FxX.]+", host) and any(c.isdigit() for c in host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except (OSError, ValueError):
            return None
    return None


def _not_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = [ip.ipv4_mapped, ip.sixtofour]
        if ip.teredo is not None:
            embedded.extend(ip.teredo)
        if any(e is not None and not e.is_global for e in embedded):
            return True
        if ip in ipaddress.ip_network("64:ff9b::/96"):  # NAT64: embeds an IPv4 address
            return not ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF).is_global
    return not ip.is_global or ip.is_multicast


def _host_name_problem(host: str) -> str | None:
    if not host:
        return "the address has no host"
    if host in _METADATA_NAMES or host.endswith(_INTERNAL_SUFFIXES) or host == "localhost":
        return "that is a local or internal address"
    if "." not in host and _numeric_host(host) is None:
        return "that is a local or internal address (a name without a domain)"
    return None


def _normalise_host(host: str) -> str:
    host = unquote(host).strip().strip("[]")
    host = host.translate({0x3002: ".", 0xFF0E: ".", 0xFF61: "."}).rstrip(".").lower()
    try:
        return host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError:
        return host


async def url_problem(raw: str, resolver: Resolver | None = None) -> str | None:
    """None when ``raw`` is a public http(s) address; otherwise the plain reason it is refused."""
    text = raw.strip()
    if not text or len(text) > 4096:
        return "the address is empty or too long"
    if any(ord(c) < 32 for c in text):
        return "the address contains control characters"
    match = _SCHEME.match(text)
    scheme = match.group(1).lower() if match else ""
    if "://" not in text:
        if scheme in _SPECIAL_SCHEMES:
            return f"'{scheme}:' addresses are not allowed"
        text = "http://" + text.lstrip("/")
        scheme = "http"
    if scheme not in _HTTP_SCHEMES:
        return f"'{scheme}:' addresses are not allowed (only http and https)"
    try:
        parts = urlsplit(text)
        host = _normalise_host(parts.hostname or "")
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        return "the address cannot be read"
    problem = _host_name_problem(host)
    if problem is not None:
        return problem
    ip = _numeric_host(host)
    if ip is not None:
        return "that is a private, local or reserved network address" if _not_public(ip) else None
    addresses = await (resolver or default_resolver)(host)
    for address in addresses:
        try:
            resolved = ipaddress.ip_address(address)
        except ValueError:
            return "the name resolved to an address that cannot be checked"
        if _not_public(resolved):
            return "that name points to a private, local or reserved network address"
    return None


def _walk_strings(value: object, key: str, depth: int, out: list[tuple[str, str]]) -> None:
    if len(out) >= _MAX_WALK_STRINGS or depth > _MAX_WALK_DEPTH:
        return
    if isinstance(value, str):
        out.append((key, value))
    elif isinstance(value, Mapping):
        for k, v in value.items():
            _walk_strings(v, str(k).lower(), depth + 1, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_strings(item, key, depth + 1, out)


async def check_arguments(
    definition: ServerDefinition,
    tool: str,
    arguments: Mapping[str, Any],
    resolver: Resolver | None = None,
) -> str | None:
    """For a browser server: the plain reason a URL in the arguments is refused, else None.

    A navigate-type tool must carry a ``url``. Any URL-like argument of any tool is inspected.
    """
    if not definition.url_guard:
        return None
    found: list[tuple[str, str]] = []
    _walk_strings(arguments, "", 0, found)
    targets = [
        value
        for key, value in found
        if key in _URL_KEYS or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value.strip())
    ]
    if tool in definition.navigate_tools and not targets:
        return "no address was given to open"
    for target in targets:
        problem = await url_problem(target, resolver)
        if problem is not None:
            return problem
    return None


def host_named_by_user(url: str, user_text: str) -> bool:
    """True when the address's host is written in the user's own message, not found by the model."""
    text = url.strip()
    if "://" not in text:
        text = "http://" + text
    try:
        host = _normalise_host(urlsplit(text).hostname or "")
    except ValueError:
        return False
    return bool(host) and host in (user_text or "").lower()


def effective_tier(
    definition: ServerDefinition,
    registry: ToolPermissionRegistry,
    tool: str,
    arguments: Mapping[str, Any],
    *,
    hints: Mapping[str, Any] | None,
    user_text: str,
) -> ToolTier:
    """The tier of one call. Opening a page is automatic only for an address the user wrote."""
    tier = base_tier(registry, definition.name, tool, hints)
    if tier is ToolTier.READ or tool not in definition.navigate_tools:
        return tier
    if isinstance(hints, Mapping) and hints.get("destructiveHint") is True:
        return tier
    url = arguments.get("url")
    if isinstance(url, str) and host_named_by_user(url, user_text):
        return ToolTier.READ
    return tier
