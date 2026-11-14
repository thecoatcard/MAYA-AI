"""GitHub Remote API MCP Connector with approval token gating (Phase 08).

READ methods (repository metadata, tree, file, commits, pull requests, issues) work with the
caller's own token, or unauthenticated for public repositories. Every request goes to the fixed
``api.github.com`` origin; a repository URL typed by a user is parsed into ``owner/name`` and never
used as a request URL.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import socket
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import quote, urlparse

import httpx

from app.mcp.permissions import PermissionDeniedError

logger = logging.getLogger(__name__)

GITHUB_API_HOST = "api.github.com"
GITHUB_API_URL = f"https://{GITHUB_API_HOST}"
# Hard caps for the repository READ methods (the toolkit applies tighter, per-call ones).
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024
# Shown to the user verbatim. Never names a token, an exception class or a raw error.
GITHUB_CONNECT_HINT = "Connect GitHub in Settings > Connections so I can see your repositories."
UNREACHABLE_MESSAGE = (
    "MAYA could not reach GitHub from this computer (network or DNS problem). Check your "
    "connection and try again."
)
PLACEHOLDER_MESSAGE = (
    "That repository name is a placeholder, not a real repository. Ask the user for the real "
    "owner/name or github.com URL, or list their repositories with github_list_repositories."
)
# One retry after a network failure, with a short backoff (never a loop).
RETRY_BACKOFF_SECONDS = 0.5
# Names the model has invented in the past; they are never sent to GitHub.
PLACEHOLDER_REPOS = frozenset(
    {
        "user/repo",
        "username/repo",
        "owner/name",
        "owner/repo",
        "org/repo",
        "your-username/your-repo",
        "your-username/repo",
        "your-org/your-repo",
        "<owner>/<repo>",
        "<owner>/<name>",
    }
)
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_URL_RE = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})")
# A profile URL: github.com/<login> and nothing after the login.
_PROFILE_RE = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/?(?=[\s?#]|$)",
    re.IGNORECASE,
)
_BINARY_EXTENSIONS = frozenset(
    "png jpg jpeg gif bmp ico webp svgz tif tiff pdf zip gz tgz bz2 xz 7z rar jar war class so dll "
    "dylib exe bin o a lib woff woff2 ttf otf eot mp3 mp4 mov avi mkv wav ogg flac webm pyc pyd "
    "wasm sqlite db psd ai dmg iso".split()
)


class GitHubRepoError(Exception):
    """A repository READ failed. ``str(error)`` is safe to show the user and the model: it holds
    a status class and guidance, never a token, header or response body."""

    def __init__(self, message: str, status: int | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        # A short technical code (for example "network", "dns", "timeout") for logs only.
        self.reason = reason


class _Unreachable(Exception):
    """An attempt failed before GitHub answered; carries the technical reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _network_reason(err: BaseException) -> str:
    """A technical code for a transport failure: ``timeout``, ``dns`` or ``network``."""
    if isinstance(err, httpx.TimeoutException):
        return "timeout"
    current: BaseException | None = err
    for _depth in range(6):
        if current is None:
            break
        if isinstance(current, socket.gaierror):
            return "dns"
        current = current.__cause__ or current.__context__
    return "network"


@dataclass(frozen=True)
class RepoRef:
    """A normalised repository reference; ``ref`` and ``path`` come from a /tree/ or /blob/ URL."""

    owner: str
    name: str
    ref: str | None = None
    path: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_repo_ref(value: str) -> RepoRef:
    """``owner/name`` or a github.com URL -> :class:`RepoRef`. Anything else is rejected.

    Only ``github.com`` and ``www.github.com`` over http(s) are accepted (no userinfo, no port, no
    look-alike host such as ``github.com.evil.test``). Requests are always built from the fixed
    api.github.com origin, never from the user's URL.
    """
    text = (value or "").strip()
    if not text or len(text) > 400 or any(c.isspace() or ord(c) < 32 for c in text):
        raise GitHubRepoError("Give the repository as owner/name or a https://github.com/ URL.")
    ref: str | None = None
    path = ""
    if "://" in text or text.lower().startswith(("github.com/", "www.github.com/")):
        parsed = urlparse(text if "://" in text else f"https://{text}")
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise GitHubRepoError("Only plain https://github.com/ repository URLs are supported.")
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if host not in ("github.com", "www.github.com") or port not in (None, 80, 443):
            raise GitHubRepoError(
                "Only github.com repositories are supported (this host is not allowed)."
            )
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            raise GitHubRepoError("The URL must point at a repository: github.com/owner/name.")
        owner, name = parts[0], parts[1]
        if len(parts) >= 4 and parts[2] in ("tree", "blob"):
            ref, path = parts[3], "/".join(parts[4:])
    else:
        parts = text.strip("/").split("/")
        if len(parts) != 2:
            raise GitHubRepoError("Give the repository as owner/name or a https://github.com/ URL.")
        owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if not _OWNER_RE.match(owner) or not _NAME_RE.match(name) or name in (".", ".."):
        raise GitHubRepoError("That is not a valid GitHub owner/name.")
    if f"{owner}/{name}".lower() in PLACEHOLDER_REPOS:
        raise GitHubRepoError(PLACEHOLDER_MESSAGE)
    return RepoRef(owner, name, ref, path)


def find_profile_login(text: str) -> str | None:
    """The login of the first github.com PROFILE URL in ``text`` (``github.com/<login>``, no
    repository part), else None. A repository URL is never a profile."""
    for match in _PROFILE_RE.finditer(text or ""):
        login = match.group(1)
        if _OWNER_RE.match(login):
            return login
    return None


def find_repo_urls(text: str) -> list[RepoRef]:
    """Every github.com repository URL mentioned in ``text`` (deduplicated, in order)."""
    seen: dict[str, RepoRef] = {}
    for match in _URL_RE.finditer(text or ""):
        try:
            ref = parse_repo_ref(match.group(0))
        except GitHubRepoError:
            continue
        seen.setdefault(ref.full_name.lower(), ref)
    return list(seen.values())


def _safe_path(path: str) -> str:
    """A repository-relative path made safe to splice into an API URL (no traversal)."""
    cleaned = (path or "").strip().strip("/")
    if "\\" in cleaned or "\x00" in cleaned or any(s in (".", "..") for s in cleaned.split("/")):
        raise GitHubRepoError("That path is not allowed.")
    return quote(cleaned, safe="/")


class GitHubConnector:
    """GitHub API client connector enforcing READ queries and APPROVAL_REQUIRED mutations."""

    def __init__(
        self,
        token: str | None,
        base_url: str = GITHUB_API_URL,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # ``token`` None: unauthenticated, public repositories only, 60 requests per hour.
        self.token = token or None
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self.rate_limit_remaining: int | None = None
        self._default_branches: dict[str, str] = {}

    @property
    def authenticated(self) -> bool:
        return self.token is not None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Maya-AI-MCP-Connector",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def get_issue(self, repo: str, issue_number: int) -> dict[str, Any]:
        """Fetch issue details from GitHub (READ tier)."""
        url = f"{self.base_url}/repos/{repo}/issues/{issue_number}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers(), timeout=15.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    async def list_repositories(
        self, limit: int = 30, owner: str | None = None
    ) -> list[dict[str, Any]]:
        """Repositories, most recently PUSHED first (READ tier).

        ``owner`` None: the authenticated user's own repositories (needs a connection). ``owner``
        a GitHub login: that account's public repositories (no connection needed).
        """
        per_page = max(1, min(limit, 100))
        if owner is None:
            if not self.authenticated:
                raise GitHubRepoError(GITHUB_CONNECT_HINT)
            path = "/user/repos"
            params: dict[str, str | int] = {
                "per_page": per_page,
                "sort": "pushed",
                "direction": "desc",
                "affiliation": "owner,collaborator,organization_member",
            }
            what = "Your repository list"
        else:
            if not _OWNER_RE.match(owner):
                raise GitHubRepoError("That is not a valid GitHub user name.")
            path = f"/users/{owner}/repos"
            params = {"per_page": per_page, "sort": "pushed", "direction": "desc", "type": "owner"}
            what = f"The GitHub account '{owner}'"
        data = await self._get(path, params, what=what)
        if not isinstance(data, list):
            raise GitHubRepoError("GitHub returned an unexpected answer for the repositories.")
        return [r for r in data if isinstance(r, dict)]

    async def search_issues(self, query: str) -> list[dict[str, Any]]:
        """Search issues across repositories (READ tier)."""
        url = f"{self.base_url}/search/issues"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers(), params={"q": query}, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
            return cast(list[dict[str, Any]], data.get("items", []))

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str,
        has_valid_approval_token: bool = False,
    ) -> dict[str, Any]:
        """Open a pull request on GitHub (APPROVAL_REQUIRED tier)."""
        if not has_valid_approval_token:
            raise PermissionDeniedError(
                "Execution of APPROVAL_REQUIRED tool 'github_create_pull_request' "
                "forbidden without valid approval token",
                tool_name="github_create_pull_request",
            )

        url = f"{self.base_url}/repos/{repo}/pulls"
        payload = {"title": title, "head": head, "base": base, "body": body}
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=self._headers(), json=payload, timeout=20.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    # ------------------------------------------------------------------ repository READ methods

    def _note_rate_limit(self, resp: httpx.Response) -> None:
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining is not None and remaining.isdigit():
            self.rate_limit_remaining = int(remaining)

    def _fail(self, resp: httpx.Response, what: str) -> GitHubRepoError:
        """Map an error response to a precise, secret-free message."""
        status = resp.status_code
        self._note_rate_limit(resp)
        remaining = resp.headers.get("x-ratelimit-remaining")
        if status == 404:
            if self.token:
                msg = (
                    f"{what} was not found: it is private or your GitHub token has no access to "
                    "it (or it does not exist)."
                )
            else:
                msg = (
                    f"{what} was not found. It may be private: without a GitHub connection only "
                    "public repositories can be read. Connect GitHub in Settings > Connections."
                )
        elif status == 401:
            msg = (
                "GitHub rejected the saved token (invalid, expired or revoked). Reconnect GitHub "
                "in Settings > Connections."
            )
        elif status in (403, 429) and (
            remaining == "0" or status == 429 or resp.headers.get("retry-after")
        ):
            reset = resp.headers.get("x-ratelimit-reset", "")
            when = f" (resets at unix time {reset})" if reset.isdigit() else ""
            who = (
                "The GitHub API rate limit for your token is used up"
                if self.token
                else "The unauthenticated GitHub rate limit (60 requests per hour) is used up; "
                "connect GitHub in Settings > Connections for a higher limit"
            )
            msg = f"{who}{when}. Try again later."
        elif status == 403:
            msg = (
                "GitHub refused access (403): the token lacks permission for this repository "
                "(for example SSO authorisation or a missing read scope), or it is blocked."
            )
        elif status == 409:
            msg = "The repository is empty or has no commits yet."
        elif status == 422:
            msg = "GitHub could not process the request (for example an unknown branch or ref)."
        else:
            msg = f"GitHub answered with an error (HTTP {status}). Try again shortly."
        return GitHubRepoError(msg, status)

    async def _get(
        self, path: str, params: dict[str, str | int] | None = None, *, what: str = "The item"
    ) -> Any:
        """GET one api.github.com resource and return its JSON. Never leaves api.github.com.

        A network failure is retried once after a short backoff, then reported with the plain
        ``UNREACHABLE_MESSAGE``; the technical reason is kept on the error and in the log only.
        """
        if urlparse(self.base_url).hostname != GITHUB_API_HOST:
            raise GitHubRepoError("Repository reads are only allowed against api.github.com.")
        try:
            return await self._get_once(path, params, what=what)
        except _Unreachable:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)
        try:
            return await self._get_once(path, params, what=what)
        except _Unreachable as err:
            logger.warning("github_unreachable reason=%s attempts=2", err.reason)
            raise GitHubRepoError(UNREACHABLE_MESSAGE, reason=err.reason) from None

    async def _get_once(self, path: str, params: dict[str, str | int] | None, *, what: str) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(transport=self._transport, follow_redirects=False) as c:
                for _hop in range(3):
                    async with c.stream(
                        "GET", url, headers=self._headers(), params=params, timeout=20.0
                    ) as resp:
                        if resp.status_code in (301, 302, 307, 308):
                            location = resp.headers.get("location", "")
                            if urlparse(location).hostname != GITHUB_API_HOST:
                                raise GitHubRepoError("GitHub redirected somewhere unexpected.")
                            url, params = location, None
                            continue
                        if resp.status_code >= 400:
                            await resp.aread()
                            raise self._fail(resp, what)
                        self._note_rate_limit(resp)
                        body = bytearray()
                        async for chunk in resp.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_RESPONSE_BYTES:
                                raise GitHubRepoError("GitHub's answer is too large to read.")
                        return json.loads(bytes(body))
                raise GitHubRepoError("GitHub redirected too many times.")
        except httpx.HTTPError as err:
            raise _Unreachable(_network_reason(err)) from None
        except ValueError:
            raise GitHubRepoError("GitHub returned an unreadable answer.") from None

    @staticmethod
    def _repo_path(repo: RepoRef) -> str:
        return f"/repos/{repo.owner}/{repo.name}"

    async def get_repo(self, repo: RepoRef) -> dict[str, Any]:
        """Repository metadata plus its language breakdown (READ tier)."""
        data = await self._get(self._repo_path(repo), what=f"Repository {repo.full_name}")
        if not isinstance(data, dict):
            raise GitHubRepoError("GitHub returned an unexpected answer for the repository.")
        branch = data.get("default_branch")
        if isinstance(branch, str):
            self._default_branches[repo.full_name.lower()] = branch
        try:
            languages = await self._get(f"{self._repo_path(repo)}/languages", what="Languages")
        except GitHubRepoError:
            languages = {}
        data["languages"] = languages if isinstance(languages, dict) else {}
        return data

    async def default_branch(self, repo: RepoRef) -> str:
        key = repo.full_name.lower()
        if key not in self._default_branches:
            await self.get_repo(repo)
        return self._default_branches.get(key, "HEAD")

    async def list_tree(self, repo: RepoRef, branch: str | None = None) -> dict[str, Any]:
        """The recursive git tree of a branch: ``{"branch", "truncated", "entries"}`` (READ)."""
        ref = branch or repo.ref or await self.default_branch(repo)
        data = await self._get(
            f"{self._repo_path(repo)}/git/trees/{quote(ref, safe='')}",
            {"recursive": 1},
            what=f"Branch '{ref}' of {repo.full_name}",
        )
        if not isinstance(data, dict):
            raise GitHubRepoError("GitHub returned an unexpected tree.")
        entries = [
            {"path": e.get("path"), "type": e.get("type"), "size": e.get("size")}
            for e in data.get("tree", [])
            if isinstance(e, dict) and isinstance(e.get("path"), str)
        ]
        return {"branch": ref, "truncated": bool(data.get("truncated")), "entries": entries}

    async def read_file(
        self, repo: RepoRef, path: str, branch: str | None = None
    ) -> dict[str, Any]:
        """One file's decoded text (READ). Never returns bytes of a binary or oversized file.

        Result: ``{"path", "size", "text", "binary", "too_large"}``; ``text`` is None when the
        file is binary or larger than ``MAX_FILE_BYTES``.
        """
        safe = _safe_path(path)
        if not safe:
            raise GitHubRepoError("A file path is required.")
        params: dict[str, str | int] = {}
        if branch or repo.ref:
            params["ref"] = str(branch or repo.ref)
        data = await self._get(
            f"{self._repo_path(repo)}/contents/{safe}",
            params or None,
            what=f"File '{path}' in {repo.full_name}",
        )
        if isinstance(data, list):
            raise GitHubRepoError(f"'{path}' is a directory: use github_list_tree to list it.")
        if not isinstance(data, dict) or data.get("type") != "file":
            raise GitHubRepoError(f"'{path}' is not a regular file (a symlink or submodule).")
        size = int(data.get("size") or 0)
        name = str(data.get("path") or path)

        def refused(*, binary: bool = False, too_large: bool = False) -> dict[str, Any]:
            return {
                "path": name,
                "size": size,
                "text": None,
                "binary": binary,
                "too_large": too_large,
            }

        if "." in name and name.rsplit(".", 1)[-1].lower() in _BINARY_EXTENSIONS:
            return refused(binary=True)
        if size > MAX_FILE_BYTES:
            return refused(too_large=True)
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            raise GitHubRepoError(f"GitHub did not return the content of '{path}'.")
        try:
            raw = base64.b64decode(data["content"])
        except (binascii.Error, ValueError):
            raise GitHubRepoError(f"The content of '{path}' could not be decoded.") from None
        if b"\x00" in raw[:8000]:
            return refused(binary=True)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return refused(binary=True)
        return {"path": name, "size": size, "text": text, "binary": False, "too_large": False}

    async def list_commits(
        self,
        repo: RepoRef,
        limit: int = 20,
        branch: str | None = None,
        path: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recent commits, newest first (READ)."""
        params: dict[str, str | int] = {"per_page": max(1, min(limit, 50))}
        if branch or repo.ref:
            params["sha"] = str(branch or repo.ref)
        if path:
            params["path"] = _safe_path(path)
        data = await self._get(
            f"{self._repo_path(repo)}/commits", params, what=f"Commits of {repo.full_name}"
        )
        return cast(list[dict[str, Any]], data if isinstance(data, list) else [])

    async def list_pull_requests(
        self, repo: RepoRef, state: str = "open", limit: int = 20
    ) -> list[dict[str, Any]]:
        """Pull requests (READ)."""
        params: dict[str, str | int] = {
            "state": state if state in ("open", "closed", "all") else "open",
            "per_page": max(1, min(limit, 50)),
        }
        data = await self._get(
            f"{self._repo_path(repo)}/pulls", params, what=f"Pull requests of {repo.full_name}"
        )
        return cast(list[dict[str, Any]], data if isinstance(data, list) else [])

    async def list_issues(
        self, repo: RepoRef, state: str = "open", limit: int = 20
    ) -> list[dict[str, Any]]:
        """Issues only (pull requests are filtered out) (READ)."""
        params: dict[str, str | int] = {
            "state": state if state in ("open", "closed", "all") else "open",
            "per_page": max(1, min(limit * 2, 100)),
        }
        data = await self._get(
            f"{self._repo_path(repo)}/issues", params, what=f"Issues of {repo.full_name}"
        )
        items = [i for i in (data if isinstance(data, list) else []) if "pull_request" not in i]
        return cast(list[dict[str, Any]], items[: max(1, min(limit, 50))])
