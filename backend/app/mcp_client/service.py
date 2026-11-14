"""The MCP client service: one child process per server (per user for credentialed servers).

Lazy start on first use, per-call timeout, per-turn call budget, output cap, bounded restart
after a crash, clean shutdown. It knows nothing about chat or approvals: whoever calls
``call`` has already passed MAYA's permission gate (see ``chat.py``). It does enforce, on every
path, the things that never depend on who asked: the server must be enabled and pinned, the tool
must be one the server listed, browser URLs must be public, and the output is capped.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from app.core.provider_errors import failure_cause
from app.gateway.schemas import ToolDefinition
from app.mcp.permissions import ToolTier
from app.mcp_client.definitions import DefinitionSet, ServerDefinition
from app.mcp_client.policy import (
    Resolver,
    base_tier,
    build_registry,
    check_arguments,
    effective_tier,
)
from app.mcp_client.runner import RawResult, RunnerError, ServerRunner, ToolInfo, child_environment
from app.mcp_client.schema import tool_definition

logger = logging.getLogger(__name__)

MAX_ARGUMENT_CHARS: Final = 8000
COOLDOWN_SECONDS: Final = 60.0
_CAUSE_TEXT: Final[dict[str, str]] = {
    "timeout": "the tool server took too long",
    "network": "the connection to the tool server closed",
}


class McpClientError(Exception):
    """A plain, safe-to-show reason a tool server could not be used."""


class McpRefused(McpClientError):
    """MAYA refused the call itself (a private browser address, a bad argument)."""


@dataclass(frozen=True, slots=True)
class Identity:
    workspace_id: UUID | None = None
    user_id: UUID | None = None


CredentialResolver = Callable[[str, Identity], Awaitable[str | None]]


RunnerFactory = Callable[[ServerDefinition, dict[str, str]], ServerRunner]


@dataclass(frozen=True, slots=True)
class McpClientSettings:
    call_timeout_seconds: float = 30.0
    start_timeout_seconds: float = 60.0
    max_output_chars: int = 20000
    max_calls_per_turn: int = 6
    max_tools_per_turn: int = 12
    max_restarts: int = 2
    max_processes: int = 4


class CallBudget:
    """Calls left in one reply. ``take`` is the only way to spend one."""

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self._used = 0

    def take(self) -> bool:
        if self._used >= self.max_calls:
            return False
        self._used += 1
        return True


@dataclass(frozen=True, slots=True)
class ToolEntry:
    server: str
    tool: str
    model_name: str
    declaration: ToolDefinition
    hints: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CallResult:
    text: str
    is_error: bool
    truncated: bool


@dataclass
class _Slot:
    key: tuple[str, str]
    runner: ServerRunner | None = None
    entries: list[ToolEntry] = field(default_factory=list)
    fingerprint: str | None = None
    changed: bool = False
    failures: int = 0
    blocked_until: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _fingerprint(tools: list[ToolInfo]) -> str:
    material = json.dumps(
        sorted((t.name, t.input_schema) for t in tools), sort_keys=True, default=str
    )
    return hashlib.sha256(material.encode()).hexdigest()


class McpClientService:
    def __init__(
        self,
        definitions: DefinitionSet,
        settings: McpClientSettings | None = None,
        *,
        credentials: CredentialResolver | None = None,
        environ: Mapping[str, str] | None = None,
        resolver: Resolver | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        runner_factory: RunnerFactory | None = None,
    ) -> None:
        self._definitions = definitions
        self.settings = settings or McpClientSettings()
        self._credentials = credentials
        self._environ: Mapping[str, str] = environ if environ is not None else os.environ
        self._resolver = resolver
        self._monotonic = monotonic
        self._factory = runner_factory or self._default_factory
        self._slots: dict[tuple[str, str], _Slot] = {}
        self._registry = build_registry(definitions.servers)
        self._closed = False

    # ------------------------------------------------------------------------ definitions

    @property
    def problems(self) -> list[str]:
        return list(self._definitions.problems)

    def enabled_servers(self) -> dict[str, ServerDefinition]:
        return {
            name: d
            for name, d in self._definitions.servers.items()
            if d.enabled and d.pin_problem() is None
        }

    def relevant_servers(self, text: str) -> list[ServerDefinition]:
        """Enabled servers a user's message is about (by name or keyword). No process is started."""
        lowered = (text or "").lower()
        return [
            d
            for d in self.enabled_servers().values()
            if d.name in lowered or any(k in lowered for k in d.keywords)
        ]

    def definition(self, server: str) -> ServerDefinition | None:
        return self.enabled_servers().get(server)

    # ------------------------------------------------------------------------ permissions

    def tier(
        self,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        hints: Mapping[str, Any] | None,
        user_text: str,
    ) -> ToolTier:
        """The tier of one call. Unknown server or tool: APPROVAL_REQUIRED."""
        definition = self.definition(server)
        if definition is None:
            return ToolTier.APPROVAL_REQUIRED
        return effective_tier(
            definition, self._registry, tool, arguments, hints=hints, user_text=user_text
        )

    async def inspect(self, server: str, tool: str, arguments: Mapping[str, Any]) -> str | None:
        """The plain reason a browser address in the arguments is refused, else None."""
        definition = self.definition(server)
        if definition is None:
            return None
        return await check_arguments(definition, tool, arguments, self._resolver)

    def static_tier(self, server: str, tool: str) -> ToolTier:
        return base_tier(self._registry, server, tool)

    # ------------------------------------------------------------------------ processes

    def _default_factory(self, definition: ServerDefinition, env: dict[str, str]) -> ServerRunner:
        return ServerRunner(
            command=definition.command,
            args=definition.resolved_args(),
            env=env,
            start_timeout=self.settings.start_timeout_seconds,
            max_output_chars=self.settings.max_output_chars,
        )

    def _slot(self, definition: ServerDefinition, identity: Identity) -> _Slot:
        who = (
            f"{identity.workspace_id}:{identity.user_id}" if definition.credential_env else "shared"
        )
        key = (definition.name, who)
        slot = self._slots.get(key)
        if slot is None:
            slot = self._slots[key] = _Slot(key=key)
        return slot

    def _running_count(self) -> int:
        return sum(1 for s in self._slots.values() if s.runner is not None and s.runner.running)

    async def _injected(self, definition: ServerDefinition, identity: Identity) -> dict[str, str]:
        injected: dict[str, str] = {}
        for variable, provider in definition.credential_env.items():
            token = await self._credentials(provider, identity) if self._credentials else None
            if not token:
                raise McpClientError(
                    f"{provider.title()} is not connected. Open Settings > Connections, add your "
                    f"{provider.title()} token and press Test connection."
                )
            injected[variable] = token
        return injected

    async def _ensure(self, definition: ServerDefinition, identity: Identity) -> _Slot:
        """A running server for this caller, started now if it is not. Holds the slot's lock."""
        if self._closed:
            raise McpClientError("tool servers are shutting down")
        slot = self._slot(definition, identity)
        async with slot.lock:
            if slot.changed:
                raise McpClientError(
                    f"The tools of {definition.title} changed since they were first used. "
                    "Restart MAYA after reviewing the server."
                )
            if slot.runner is not None and slot.runner.running:
                return slot
            if self._monotonic() < slot.blocked_until:
                raise McpClientError(
                    f"{definition.title} keeps stopping; it is paused for a minute. "
                    "Try again later."
                )
            if self._running_count() >= self.settings.max_processes:
                raise McpClientError("too many tool servers are running; try again later")
            problem = definition.pin_problem()
            if problem is not None:
                raise McpClientError(f"{definition.title} is not pinned: {problem}")
            injected = await self._injected(definition, identity)
            env = child_environment(self._environ, definition.env_allow, injected)
            runner = self._factory(definition, env)
            try:
                await runner.start()
                tools = await runner.list_tools(self.settings.start_timeout_seconds)
            except RunnerError as error:
                await runner.stop()
                self._note_failure(slot)
                raise McpClientError(f"{definition.title}: {error}") from None
            fingerprint = _fingerprint(tools)
            if slot.fingerprint is not None and slot.fingerprint != fingerprint:
                slot.changed = True
                await runner.stop()
                logger.warning("mcp_client_tools_changed server=%s", definition.name)
                raise McpClientError(
                    f"The tools of {definition.title} changed since they were first used. "
                    "Restart MAYA after reviewing the server."
                )
            slot.fingerprint = fingerprint
            slot.runner = runner
            slot.entries = self._entries(definition, tools)
            logger.info("mcp_client_started server=%s tools=%d", definition.name, len(tools))
            return slot

    def _note_failure(self, slot: _Slot) -> None:
        slot.failures += 1
        if slot.failures > self.settings.max_restarts:
            slot.blocked_until = self._monotonic() + COOLDOWN_SECONDS
            slot.failures = 0

    @staticmethod
    def _entries(definition: ServerDefinition, tools: list[ToolInfo]) -> list[ToolEntry]:
        taken: set[str] = set()
        entries: list[ToolEntry] = []
        for tool in tools:
            built = tool_definition(
                definition.name,
                definition.title,
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                },
                taken,
            )
            if built is None:
                continue
            declaration, name = built
            taken.add(name)
            entries.append(
                ToolEntry(
                    server=definition.name,
                    tool=tool.name,
                    model_name=name,
                    declaration=declaration,
                    hints=tool.hints,
                )
            )
        return entries

    # ------------------------------------------------------------------------ public use

    async def discover(self, server: str, identity: Identity) -> list[ToolEntry]:
        """The tools of one server (starts it on first use). Raises ``McpClientError``."""
        definition = self.definition(server)
        if definition is None:
            raise McpClientError(f"{server} is not enabled")
        slot = await self._ensure(definition, identity)
        return list(slot.entries)

    async def call(
        self,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
        identity: Identity,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> CallResult:
        """Run one tool. The caller has passed the permission gate; this still refuses a private
        browser address, an unlisted tool and oversized arguments. Raises ``McpClientError``."""
        definition = self.definition(server)
        if definition is None:
            raise McpClientError(f"{server} is not enabled")
        try:
            size = len(json.dumps(arguments, default=str))
        except (TypeError, ValueError):
            raise McpRefused("the arguments cannot be sent (not plain data)") from None
        if size > MAX_ARGUMENT_CHARS:
            raise McpRefused("the arguments are too large")
        problem = await check_arguments(definition, tool, arguments, self._resolver)
        if problem is not None:
            raise McpRefused(f"Refused: {problem}.")
        slot = await self._ensure(definition, identity)
        if tool not in {e.tool for e in slot.entries}:
            raise McpClientError(f"{definition.title} has no tool named {tool[:60]!r}")
        runner = slot.runner
        assert runner is not None
        limit = timeout if timeout is not None else self.settings.call_timeout_seconds
        try:
            raw: RawResult = await runner.call_tool(tool, arguments, limit)
        except RunnerError as error:
            if error.crashed or error.timed_out:
                self._note_failure(slot)
            raise McpClientError(f"{definition.title}: {error}") from None
        except Exception as error:  # noqa: BLE001 - never leak a raw error text
            cause = _CAUSE_TEXT.get(failure_cause(error), "the tool server failed")
            raise McpClientError(f"{definition.title}: {cause}") from None
        slot.failures = 0
        return CallResult(text=raw.text, is_error=raw.is_error, truncated=raw.truncated)

    def new_budget(self) -> CallBudget:
        return CallBudget(self.settings.max_calls_per_turn)

    async def shutdown(self) -> None:
        """Stop every child. Safe to call twice."""
        self._closed = True
        for slot in list(self._slots.values()):
            if slot.runner is not None:
                await slot.runner.stop()
                slot.runner = None

    def status(self) -> list[dict[str, Any]]:
        """Servers, whether on, whether running, tool counts. No secrets, no arguments."""
        rows: list[dict[str, Any]] = []
        for name, definition in sorted(self._definitions.servers.items()):
            slots = [s for s in self._slots.values() if s.key[0] == name]
            running = [s for s in slots if s.runner is not None and s.runner.running]
            rows.append(
                {
                    "name": name,
                    "label": definition.title,
                    "builtin": definition.builtin,
                    "enabled": definition.enabled and definition.pin_problem() is None,
                    "configured_enabled": definition.enabled,
                    "running": bool(running),
                    "tool_count": max((len(s.entries) for s in running), default=0),
                    "pinned": definition.pin_problem() is None,
                }
            )
        return rows


# ---------------------------------------------------------------------------- process-wide

_current: McpClientService | None = None


def set_current(service: McpClientService | None) -> None:
    """The service of this app (set at startup, cleared at shutdown)."""
    global _current
    _current = service


def current() -> McpClientService | None:
    return _current
