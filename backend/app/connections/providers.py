"""Provider definitions: input normalisation and the live "test connection" check.

A provider is a small description plus an async ``check``. Adding Google (or any other service)
means adding one ``ProviderSpec`` to ``PROVIDERS``; storage, encryption, the API and the web form
need no change. All outgoing hosts are fixed here, never taken from user input.

Every failure becomes a ``TestResult`` with a stable ``code`` and a plain sentence that says what
to do. No message contains the token, a URL with a token, or the provider's raw response body.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

import httpx

NOTION_API = "https://api.notion.com/v1"
# /databases/{id}/query is stable in this version (newer versions query per data source).
NOTION_VERSION = "2022-06-28"
GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
USER_AGENT = "Maya-AI-MCP-Connector"
_TIMEOUT = httpx.Timeout(10.0)

HttpFactory = Callable[[], httpx.AsyncClient]


def default_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)


class ConnectionInputError(ValueError):
    """The value the user typed cannot be used (``code`` is stable, ``message`` is for people)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class TestResult:
    """Outcome of one check. ``ok`` is only true when the provider accepted the credentials."""

    __test__ = False  # not a pytest class

    ok: bool
    code: str
    message: str
    account: str | None = None
    scopes: tuple[str, ...] | None = None
    database_title: str | None = None
    # Mailbox only: one (label, ok, plain sentence) per half (incoming, outgoing).
    steps: tuple[tuple[str, bool, str], ...] | None = None


@dataclass(frozen=True, slots=True)
class Credentials:
    """What a provider check needs. ``token`` is kept out of ``repr`` on purpose."""

    token: str = field(repr=False)
    database_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    key: str
    label: str
    has_database_id: bool
    help_url: str
    normalize_token: Callable[[str], str]
    check: Callable[[Credentials, HttpFactory], Awaitable[TestResult]]
    # "token": one pasted token (GitHub, Notion). "mailbox": address + app password + hosts, kept
    # in the same sealed document and tested by ``app.connections.mailbox``.
    kind: str = "token"


# ------------------------------------------------------------------------------ normalisation

_BEARER = re.compile(r"^(?:bearer|token)\s+", re.IGNORECASE)
_ID_AT_END = re.compile(
    r"([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})$"
)
_UUIDISH = re.compile(r"^[0-9a-fA-F-]{32,36}$")


def normalize_token(raw: str, *, label: str) -> str:
    """Strip whitespace and a pasted ``Bearer `` prefix; reject what can never be a token."""
    token = _BEARER.sub("", (raw or "").strip()).strip()
    if not token:
        raise ConnectionInputError("token_missing", f"Paste your {label} token first.")
    if re.search(r"\s", token) or not token.isascii() or len(token) > 512:
        raise ConnectionInputError(
            "token_format",
            f"That does not look like a {label} token (it contains spaces or odd characters). "
            "Copy the token again without anything around it.",
        )
    if len(token) < 20:
        raise ConnectionInputError(
            "token_format", f"That is too short to be a {label} token. Copy the whole token."
        )
    return token


def normalize_notion_database_id(raw: str) -> str:
    """The 32-hex database id from a Notion database URL or a plain id (hyphens optional)."""
    text = (raw or "").strip()
    if not text:
        raise ConnectionInputError("database_id_missing", "Enter the Notion database URL or id.")
    if text.lower().startswith(("http://", "https://")):
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        if host != "notion.so" and not host.endswith((".notion.so", ".notion.site")):
            raise ConnectionInputError(
                "database_id_format", "That link is not a Notion link. Copy it from Notion."
            )
        segment = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
        match = _ID_AT_END.search(segment)
    elif _UUIDISH.match(text):
        match = _ID_AT_END.search(text)
    else:
        match = None
    if match is None:
        raise ConnectionInputError(
            "database_id_format",
            "That is not a Notion database id. Open the database as a full page, copy its link "
            "(the part before ?v= holds a 32-character id) and paste the link or the id.",
        )
    return match.group(1).replace("-", "").lower()


# ----------------------------------------------------------------------------------- results


def _network(provider: str, exc: Exception) -> TestResult:
    if isinstance(exc, httpx.TimeoutException):
        return TestResult(
            False, "timeout", f"{provider} did not answer in time. Try again in a moment."
        )
    return TestResult(
        False,
        "network",
        f"MAYA could not reach {provider} (network problem). Check the server's internet "
        "access and try again.",
    )


def _rate_limited(provider: str, resp: httpx.Response) -> TestResult:
    wait = resp.headers.get("retry-after")
    tail = (
        f" Try again in about {wait} seconds." if wait and wait.isdigit() else " Try again shortly."
    )
    return TestResult(False, "rate_limited", f"{provider} is rate limiting requests.{tail}")


def _unexpected(provider: str, status: int) -> TestResult:
    return TestResult(
        False, "provider_error", f"{provider} answered with an unexpected error (HTTP {status})."
    )


# -------------------------------------------------------------------------------------- GitHub


def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": USER_AGENT,
    }


async def check_github(creds: Credentials, http: HttpFactory) -> TestResult:
    try:
        async with http() as client:
            resp = await client.get(f"{GITHUB_API}/user", headers=github_headers(creds.token))
    except httpx.HTTPError as exc:
        return _network("GitHub", exc)
    code = resp.status_code
    if code == 200:
        try:
            login = str(resp.json().get("login") or "")
        except ValueError:
            login = ""
        header = resp.headers.get("x-oauth-scopes")
        scopes = tuple(s.strip() for s in header.split(",") if s.strip()) if header else ()
        if header is None:
            note = " (fine-grained token: access is set per repository on GitHub)"
        elif scopes:
            note = f" with scopes: {', '.join(scopes)}"
        else:
            note = " (no scopes: public data only)"
        return TestResult(
            True,
            "ok",
            f"Connected to GitHub as {login or 'your account'}{note}.",
            account=login or None,
            scopes=scopes if header is not None else None,
        )
    if code == 401:
        return TestResult(
            False,
            "token_rejected",
            "GitHub rejected this token (401). It may be wrong, expired or revoked: create a "
            "new token on GitHub and paste it again.",
        )
    if code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
        return _rate_limited("GitHub", resp)
    if code == 403:
        return TestResult(
            False,
            "token_forbidden",
            "GitHub refused this token (403). If your organisation uses SSO, authorise the "
            "token for it, or give the token access to your account.",
        )
    if code == 429:
        return _rate_limited("GitHub", resp)
    return _unexpected("GitHub", code)


# -------------------------------------------------------------------------------------- Notion


def notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def _notion_error_code(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return ""
    return str(data.get("code") or "") if isinstance(data, dict) else ""


def _notion_title(data: object) -> str | None:
    if isinstance(data, dict):
        parts = data.get("title")
        if isinstance(parts, list):
            text = "".join(str(p.get("plain_text", "")) for p in parts if isinstance(p, dict))
            return text.strip() or None
    return None


# Plain sentences shared by the connection test and the Notion read tools (chat and voice), so a
# user sees the same fix wherever the failure shows up.
NOTION_DATABASE_NOT_SHARED = (
    "Notion cannot see this database. Open it in Notion, click the ... menu, choose "
    "Connections and add your integration (Share the database with it), then test again. "
    "If the id is right, check that it is a database and not a normal page."
)
NOTION_PAGE_NOT_SHARED = (
    "Notion cannot see this page. Open it in Notion, click the ... menu, choose Connections "
    "and add your integration (Share the page with it), then try again."
)
NOTION_NO_READ_PERMISSION = (
    "The integration has no permission to read this database. Give it the Read content "
    "capability and share the database with it."
)


def _notion_token_failure(resp: httpx.Response) -> TestResult:
    code = resp.status_code
    if code == 401:
        return TestResult(
            False,
            "token_rejected",
            "Notion rejected this token (401). Copy the Internal Integration Secret again from "
            "notion.so/my-integrations.",
        )
    if code == 429:
        return _rate_limited("Notion", resp)
    return _unexpected("Notion", code)


async def check_notion(creds: Credentials, http: HttpFactory) -> TestResult:
    headers = notion_headers(creds.token)
    try:
        async with http() as client:
            me = await client.get(f"{NOTION_API}/users/me", headers=headers)
            if me.status_code != 200:
                return _notion_token_failure(me)
            try:
                bot = me.json()
            except ValueError:
                bot = {}
            name = (
                str(bot.get("name") or "your Notion integration")
                if isinstance(bot, dict)
                else "your Notion integration"
            )
            if not creds.database_id:
                return TestResult(
                    True,
                    "ok_no_database",
                    f"The token works (integration: {name}). Add the database id of your tasks "
                    "database to finish.",
                    account=name,
                )
            try:
                db_id = normalize_notion_database_id(creds.database_id)
            except ConnectionInputError as err:
                return TestResult(False, err.code, err.message, account=name)
            db = await client.get(f"{NOTION_API}/databases/{db_id}", headers=headers)
    except httpx.HTTPError as exc:
        return _network("Notion", exc)
    code = db.status_code
    if code == 200:
        try:
            title = _notion_title(db.json())
        except ValueError:
            title = None
        shown = f'"{title}"' if title else "the database"
        return TestResult(
            True,
            "ok",
            f"Connected to Notion (integration: {name}); can read {shown}.",
            account=name,
            database_title=title,
        )
    detail = _notion_error_code(db)
    if code == 404 or detail == "object_not_found":
        return TestResult(
            False,
            "database_not_shared",
            NOTION_DATABASE_NOT_SHARED,
            account=name,
        )
    if code == 400:
        return TestResult(
            False,
            "database_id_invalid",
            "Notion says this id is not a database (it may be a page). Open the tasks database "
            "as a full page and copy its link or id.",
            account=name,
        )
    if code == 401:
        return _notion_token_failure(db)
    if code == 403:
        return TestResult(
            False,
            "token_forbidden",
            NOTION_NO_READ_PERMISSION,
            account=name,
        )
    if code == 429:
        return _rate_limited("Notion", db)
    return _unexpected("Notion", code)


def notion_request_failure(resp: httpx.Response, *, target: str = "database") -> TestResult:
    """A failed Notion read made by a chat or voice tool, in the same words as the connection
    test. ``target`` is what was asked for: ``"database"`` or ``"page"``. Never carries the token
    or the response body."""
    code = resp.status_code
    detail = _notion_error_code(resp)
    if code == 404 or detail == "object_not_found":
        message = NOTION_PAGE_NOT_SHARED if target == "page" else NOTION_DATABASE_NOT_SHARED
        return TestResult(False, "not_shared", message)
    if code == 403:
        return TestResult(False, "token_forbidden", NOTION_NO_READ_PERMISSION)
    if code == 400:
        return TestResult(
            False,
            "bad_request",
            f"Notion rejected the request (400). Check the {target} id or link and try again.",
        )
    return _notion_token_failure(resp)


async def _mailbox_never_http(creds: Credentials, http: HttpFactory) -> TestResult:
    # The mailbox is tested over IMAP/SMTP by ConnectionService, never through this HTTP hook.
    return TestResult(False, "not_http", "The mailbox is tested over IMAP and SMTP.")


def _normalize_mail_password(raw: str) -> str:
    from app.connections.mailbox import normalize_app_password

    return normalize_app_password(raw)


PROVIDERS: dict[str, ProviderSpec] = {
    "mail": ProviderSpec(
        key="mail",
        label="Mailbox",
        has_database_id=False,
        help_url="https://myaccount.google.com/apppasswords",
        normalize_token=_normalize_mail_password,
        check=_mailbox_never_http,
        kind="mailbox",
    ),
    "github": ProviderSpec(
        key="github",
        label="GitHub",
        has_database_id=False,
        help_url="https://github.com/settings/tokens",
        normalize_token=lambda raw: normalize_token(raw, label="GitHub"),
        check=check_github,
    ),
    "notion": ProviderSpec(
        key="notion",
        label="Notion",
        has_database_id=True,
        help_url="https://www.notion.so/my-integrations",
        normalize_token=lambda raw: normalize_token(raw, label="Notion"),
        check=check_notion,
    ),
}
