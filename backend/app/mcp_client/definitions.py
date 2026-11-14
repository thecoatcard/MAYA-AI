"""Server definitions: what may be started, with what, and which tools count as read-only.

A definition is data written by the owner (a JSON file) or shipped here (``playwright`` and
``github``, both disabled until the owner enables them). Nothing in a definition can loosen the
permission model: a tool is automatic only when it is on the definition's ``read_tools`` list,
and every other tool, known or not, needs the user's approval.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
ENV_NAME_PATTERN: Final = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
# Credentials a server may be given from the user's own connection (never from the app's env).
CREDENTIAL_PROVIDERS: Final = frozenset({"github"})
MAX_FILE_BYTES: Final = 64 * 1024
MAX_ARGS: Final = 40
MAX_ARG_CHARS: Final = 300
MAX_SERVERS: Final = 12

# Launchers that fetch a package by name: the package must carry an exact version.
_NODE_LAUNCHERS: Final = frozenset({"npx", "bunx", "pnpx"})
_NODE_RUNNERS: Final = frozenset({"npm", "pnpm", "yarn", "bun"})
_NODE_RUN_VERBS: Final = frozenset({"exec", "dlx", "x"})
_PY_LAUNCHERS: Final = frozenset({"uvx"})
_EXACT_NODE_SPEC: Final = re.compile(
    r"^(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+@\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$"
)
_EXACT_PY_SPEC: Final = re.compile(
    r"^[A-Za-z0-9._-]+(?:\[[A-Za-z0-9,._-]+\])?==\d+(?:\.\d+)*[A-Za-z0-9.+-]*$"
)
_EXACT_VERSION: Final = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")
_FLOATING_TAGS: Final = re.compile(r"(?:[@:=]|^)(?:latest|next|beta|canary|nightly)\b", re.I)


class ServerDefinition(BaseModel):
    """One MCP server MAYA may start as a child process (stdio only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    label: str = ""
    command: str
    args: tuple[str, ...] = ()
    # The exact version the owner pinned. For an npx-style launcher the package spec in ``args``
    # carries it (``@playwright/mcp@{version}`` is filled from here); for any other command it is
    # the version of the installed program and is required.
    version: str | None = None
    # Variables copied from MAYA's environment into the child, in addition to the SDK's small
    # default set (PATH and similar). Everything else is dropped.
    env_allow: tuple[str, ...] = ()
    # Child variable -> connection provider. The value is the signed-in user's own credential.
    credential_env: Mapping[str, str] = Field(default_factory=dict)
    enabled: bool = False
    # Tools that only read. Anything not listed (and every tool the server adds later) needs the
    # user's approval.
    read_tools: frozenset[str] = frozenset()
    # Tools that open a URL: automatic only for an address the user wrote in the message, and
    # always refused when the target is not a public address.
    navigate_tools: frozenset[str] = frozenset()
    # True when the server drives a browser: every URL-like argument is inspected.
    url_guard: bool = False
    # Words in the user's message that make this server relevant for the turn.
    keywords: tuple[str, ...] = ()
    builtin: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not NAME_PATTERN.match(value):
            raise ValueError("name must be 1-24 chars: a-z, 0-9 and '-', starting with a letter")
        return value

    @field_validator("command")
    @classmethod
    def _command(cls, value: str) -> str:
        # Started directly, never through a shell: a path may hold spaces, shell syntax is refused.
        if not value or len(value) > 260 or re.search(r"[;|&<>`$\"'\x00-\x1f]", value):
            raise ValueError("command must be one program path without shell characters")
        return value

    @field_validator("args")
    @classmethod
    def _args(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_ARGS or any(
            len(a) > MAX_ARG_CHARS or "\x00" in a or "\n" in a for a in value
        ):
            raise ValueError("args are too many or too long")
        return value

    @field_validator("env_allow")
    @classmethod
    def _env_allow(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for name in value:
            if not ENV_NAME_PATTERN.match(name) or name.startswith("MAYA_"):
                raise ValueError("env_allow holds plain variable names and never MAYA_* ones")
        return value

    @field_validator("credential_env")
    @classmethod
    def _credential_env(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        for var, provider in value.items():
            if not ENV_NAME_PATTERN.match(var) or var.startswith("MAYA_"):
                raise ValueError("credential_env keys are plain variable names")
            if provider not in CREDENTIAL_PROVIDERS:
                raise ValueError(f"unknown credential provider: {provider}")
        return dict(value)

    @property
    def title(self) -> str:
        return self.label or self.name

    def resolved_args(self) -> tuple[str, ...]:
        """The args with ``{version}`` filled from ``version`` (unchanged when there is none)."""
        if self.version is None:
            return self.args
        return tuple(a.replace("{version}", self.version) for a in self.args)

    def pin_problem(self) -> str | None:
        """Why this server may not be started, or None when its version is pinned exactly."""
        return pin_problem(self.command, self.resolved_args(), self.version)


def _program(command: str) -> str:
    base = re.split(r"[\\/]", command)[-1].lower()
    return re.sub(r"\.(cmd|exe|bat)$", "", base)


def _first_package(args: tuple[str, ...]) -> str | None:
    for arg in args:
        if not arg.startswith("-"):
            return arg
    return None


def pin_problem(command: str, args: tuple[str, ...], version: str | None) -> str | None:
    """None when the launch is pinned to an exact version; otherwise the plain reason.

    ``@latest``, dist-tags, ranges, a missing version and an unresolved ``{version}`` are refused.
    """
    joined = " ".join(args)
    if "{version}" in joined:
        return 'no version was set for this server (set "version" to an exact release)'
    if _FLOATING_TAGS.search(joined) or (version is not None and _FLOATING_TAGS.search(version)):
        return "a floating tag such as @latest is not allowed; pin an exact version"
    program = _program(command)
    if program in _NODE_LAUNCHERS:
        spec = _first_package(args)
    elif program in _NODE_RUNNERS and args[:1] and args[0] in _NODE_RUN_VERBS:
        spec = _first_package(args[1:])
    elif program in _PY_LAUNCHERS:
        spec = _first_package(args)
        if spec is None or not (_EXACT_PY_SPEC.match(spec) or _EXACT_NODE_SPEC.match(spec)):
            return "the package must be pinned to an exact version like name==1.2.3"
        return None
    else:
        if version is None or not _EXACT_VERSION.match(version):
            return 'set "version" to the exact installed version of this program (like 1.2.3)'
        return None
    if spec is None or not _EXACT_NODE_SPEC.match(spec):
        return "the package must be pinned to an exact version like name@1.2.3"
    return None


def _builtin_definitions() -> dict[str, ServerDefinition]:
    playwright = ServerDefinition(
        name="playwright",
        label="Playwright",
        command="npx",
        args=("-y", "@playwright/mcp@{version}", "--headless"),
        read_tools=frozenset(
            {
                "browser_snapshot",
                "browser_take_screenshot",
                "browser_console_messages",
                "browser_network_requests",
            }
        ),
        navigate_tools=frozenset({"browser_navigate"}),
        url_guard=True,
        keywords=(
            "playwright",
            "browser",
            "website",
            "web page",
            "webpage",
            "screenshot",
            "browse",
            "navigate",
        ),
        builtin=True,
    )
    github = ServerDefinition(
        name="github",
        label="GitHub",
        command="github-mcp-server",
        args=("stdio",),
        credential_env={"GITHUB_PERSONAL_ACCESS_TOKEN": "github"},
        read_tools=frozenset(
            {
                "get_me",
                "get_file_contents",
                "get_issue",
                "get_issue_comments",
                "issue_read",
                "list_issues",
                "search_issues",
                "get_pull_request",
                "get_pull_request_files",
                "get_pull_request_diff",
                "get_pull_request_comments",
                "get_pull_request_reviews",
                "get_pull_request_status",
                "pull_request_read",
                "list_pull_requests",
                "search_pull_requests",
                "list_commits",
                "get_commit",
                "list_branches",
                "list_tags",
                "get_tag",
                "search_code",
                "search_repositories",
                "search_users",
                "list_workflows",
                "list_workflow_runs",
                "get_workflow_run",
            }
        ),
        keywords=("github", "pull request", "issue", "repository", "repo", "commit", "branch"),
        builtin=True,
    )
    return {playwright.name: playwright, github.name: github}


BUILTIN_DEFINITIONS: Final[Mapping[str, ServerDefinition]] = _builtin_definitions()
# A file entry for a built-in server may set only these; the tiers, env and guards stay as shipped.
_BUILTIN_OVERRIDABLE: Final = frozenset({"name", "enabled", "version", "command", "args"})


class DefinitionSet(BaseModel):
    """The servers usable in this run, and why other entries were not accepted."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    servers: dict[str, ServerDefinition] = Field(default_factory=dict)
    problems: list[str] = Field(default_factory=list)

    def enabled(self) -> dict[str, ServerDefinition]:
        return {n: d for n, d in self.servers.items() if d.enabled}


def _merge_builtin(base: ServerDefinition, entry: Mapping[str, Any]) -> ServerDefinition:
    extra = set(entry) - _BUILTIN_OVERRIDABLE
    if extra:
        raise ValueError(f"cannot change {sorted(extra)} of a built-in server")
    data = base.model_dump()
    data.update({k: v for k, v in entry.items() if k != "name"})
    return ServerDefinition.model_validate(data)


def parse_definitions(raw: object) -> DefinitionSet:
    """Validate a parsed servers document. A bad entry is skipped with a reason, never raised."""
    result = DefinitionSet(servers=dict(BUILTIN_DEFINITIONS))
    entries = raw.get("servers") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        result.problems.append('the servers file must be a list or {"servers": [...]}')
        return result
    for index, entry in enumerate(entries[:MAX_SERVERS]):
        label = f"entry {index + 1}"
        if not isinstance(entry, dict):
            result.problems.append(f"{label}: not an object")
            continue
        name = str(entry.get("name") or "")
        label = f"server '{name[:24]}'"
        try:
            base = BUILTIN_DEFINITIONS.get(name)
            definition = (
                _merge_builtin(base, entry)
                if base is not None
                else ServerDefinition.model_validate(entry)
            )
        except (ValidationError, ValueError) as error:
            first = error.errors()[0]["msg"] if isinstance(error, ValidationError) else str(error)
            result.problems.append(f"{label}: {first}")
            continue
        problem = definition.pin_problem()
        if problem is not None and definition.enabled:
            result.problems.append(f"{label}: {problem}")
            # A built-in stays defined but disabled; a custom server is not defined at all.
            if definition.builtin:
                result.servers[name] = definition.model_copy(update={"enabled": False})
            continue
        result.servers[name] = definition
    return result


def load_definitions(path: str | Path | None) -> DefinitionSet:
    """The built-in definitions (disabled) plus the owner's file, when one is set."""
    if not path or not str(path).strip():
        return DefinitionSet(servers=dict(BUILTIN_DEFINITIONS))
    file = Path(str(path))
    try:
        if file.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file is larger than 64 KB")
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        # The message names the problem, never the file's content.
        logger.warning("mcp_servers_file_unusable error=%s", type(error).__name__)
        result = DefinitionSet(servers=dict(BUILTIN_DEFINITIONS))
        result.problems.append(f"servers file could not be read ({type(error).__name__})")
        return result
    return parse_definitions(raw)
