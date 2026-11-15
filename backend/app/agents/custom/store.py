"""Read and write custom agent files inside the connected folder, through SafePath.

Storage is the user's own folder (no database): ``<folder>/.maya/agents/<name>.md``. Every path
goes through ``FileContext.resolve`` (the SafePath rules plus MAYA's protected directories), so a
name or a symlink can never reach outside the folder. A new agent is created with exclusive
creation (never overwrites); an edit replaces only a file in that directory and keeps the
previous text next to it as ``<name>.md.bak``. Nothing is ever deleted: disabling an agent is an
``enabled: false`` edit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.agents.custom.definition import (
    AGENTS_DIR,
    MAX_FILE_BYTES,
    NAME_RE,
    CustomAgent,
    CustomAgentError,
    parse_definition,
    render_definition,
    validate_definition,
)
from app.files.access import FileAccessError, FileContext

if TYPE_CHECKING:
    from app.agents.capabilities import Capabilities

logger = logging.getLogger(__name__)

MAX_AGENTS = 50


@dataclass(frozen=True, slots=True)
class LoadedAgent:
    """One file in the agents folder: a valid agent, or why it is not usable."""

    file: str
    agent: CustomAgent | None = None
    error: str | None = None
    code: str | None = None

    def as_dict(self) -> dict[str, object]:
        if self.agent is not None:
            return {**self.agent.as_dict(), "file": self.file, "valid": True, "error": None}
        return {
            "name": Path(self.file).stem,
            "file": self.file,
            "valid": False,
            "error": self.error,
            "code": self.code,
            "kind": "custom",
            "enabled": False,
        }


class CustomAgentStore:
    """The custom agents of one user's connected folder."""

    def __init__(self, ctx: FileContext, caps: Capabilities | None = None) -> None:
        self._ctx = ctx
        self._caps = caps

    # ------------------------------------------------------------------ paths

    async def _dir(self, *, create: bool = False) -> Path | None:
        root = await self._ctx.root()
        directory = self._ctx.resolve(root, AGENTS_DIR)
        if create:
            await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        elif not await asyncio.to_thread(directory.is_dir):
            return None
        return directory

    async def _file(self, name: str, *, suffix: str = ".md") -> Path:
        if not NAME_RE.match(name):
            raise CustomAgentError("That is not a valid agent name.", "bad_name")
        root = await self._ctx.root()
        try:
            return self._ctx.resolve(root, f"{AGENTS_DIR}/{name}{suffix}")
        except FileAccessError as err:
            raise CustomAgentError(str(err), "bad_name") from None

    # ------------------------------------------------------------------ read

    async def list_all(self) -> list[LoadedAgent]:
        """Every ``*.md`` file in the agents folder, valid or not (invalid ones carry a reason)."""
        try:
            directory = await self._dir()
        except FileAccessError:
            return []
        if directory is None:
            return []

        def scan() -> list[tuple[str, str | None, str | None]]:
            rows: list[tuple[str, str | None, str | None]] = []
            for entry in sorted(os.scandir(directory), key=lambda e: e.name.lower())[:MAX_AGENTS]:
                if not entry.name.lower().endswith(".md"):
                    continue
                # A link is never followed: only regular files that are not links are read.
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    rows.append((entry.name, None, "Links are not read."))
                    continue
                if entry.stat().st_size > MAX_FILE_BYTES:
                    rows.append((entry.name, None, "The agent file is larger than 16 KB."))
                    continue
                try:
                    with open(entry.path, encoding="utf-8", errors="strict") as handle:
                        rows.append((entry.name, handle.read(), None))
                except (OSError, UnicodeDecodeError):
                    rows.append((entry.name, None, "The file cannot be read as UTF-8 text."))
            return rows

        out: list[LoadedAgent] = []
        seen: set[str] = set()
        for file_name, text, problem in await asyncio.to_thread(scan):
            relative = f"{AGENTS_DIR}/{file_name}"
            if text is None:
                out.append(LoadedAgent(relative, error=problem, code="unreadable"))
                continue
            try:
                agent = parse_definition(text)
                if f"{agent.name}.md" != file_name:
                    raise CustomAgentError(
                        f"The file name must be '{agent.name}.md' to match its name.",
                        "name_mismatch",
                    )
                validate_definition(agent, self._caps)
                if agent.name in seen:
                    raise CustomAgentError("Another file already defines this agent.", "duplicate")
                seen.add(agent.name)
                out.append(LoadedAgent(relative, agent=agent))
            except CustomAgentError as err:
                out.append(LoadedAgent(relative, error=str(err), code=err.code))
        return out

    async def get(self, name: str) -> CustomAgent | None:
        """A valid agent by name (enabled or not), else ``None``."""
        for item in await self.list_all():
            if item.agent is not None and item.agent.name == name:
                return item.agent
        return None

    async def enabled(self) -> list[CustomAgent]:
        return [i.agent for i in await self.list_all() if i.agent is not None and i.agent.enabled]

    # ------------------------------------------------------------------ write

    async def create(self, agent: CustomAgent) -> str:
        """Write a NEW agent file. Fails when one with this name exists. Returns its path."""
        validate_definition(agent, self._caps)
        await self._dir(create=True)
        target = await self._file(agent.name)
        data = render_definition(agent).encode("utf-8")

        def write() -> None:
            try:
                with open(target, "xb") as handle:
                    handle.write(data)
            except FileExistsError:
                raise CustomAgentError(
                    "An agent with this name already exists.", "exists"
                ) from None

        await asyncio.to_thread(write)
        await self._audit("custom_agent_create", agent.name)
        return f"{AGENTS_DIR}/{agent.name}.md"

    async def update(self, agent: CustomAgent) -> str:
        """Replace an EXISTING agent file (the previous text is kept as ``<name>.md.bak``)."""
        validate_definition(agent, self._caps)
        target = await self._file(agent.name)
        backup = await self._file(agent.name, suffix=".md.bak")
        data = render_definition(agent).encode("utf-8")

        def write() -> None:
            if not target.is_file() or target.is_symlink():
                raise CustomAgentError("There is no agent with this name to edit.", "not_found")
            shutil.copyfile(target, backup)
            temp = target.with_name(target.name + ".tmp")
            with open(temp, "wb") as handle:
                handle.write(data)
            os.replace(temp, target)

        await asyncio.to_thread(write)
        await self._audit("custom_agent_update", agent.name)
        return f"{AGENTS_DIR}/{agent.name}.md"

    async def set_enabled(self, name: str, enabled: bool) -> CustomAgent:
        agent = await self.get(name)
        if agent is None:
            raise CustomAgentError("There is no valid agent with this name.", "not_found")
        changed = CustomAgent(
            name=agent.name,
            description=agent.description,
            instructions=agent.instructions,
            capabilities=agent.capabilities,
            model=agent.model,
            enabled=enabled,
        )
        await self.update(changed)
        return changed

    async def _audit(self, tool: str, name: str) -> None:
        try:
            await self._ctx.services.audit(
                workspace_id=self._ctx.workspace_id,
                user_id=self._ctx.user_id,
                task_id=self._ctx.task_id,
                tool=tool,
                level="WRITE",
                detail={"agent": name},
            )
        except Exception as error:  # an audit failure never loses the user's agent file
            logger.warning("custom_agent_audit_failed error=%s", type(error).__name__)


_INVOKE = re.compile(
    r"^\s*(?:@|(?:use|run|ask|call)\s+(?:the\s+)?(?:agent\s+)?@?)([a-z][a-z0-9-]{1,38}[a-z0-9])"
    r"\b[\s,:;-]*(.*)$",
    re.IGNORECASE | re.DOTALL,
)


def invocation(instruction: str, names: set[str] | frozenset[str]) -> tuple[str, str] | None:
    """``(agent name, the rest of the request)`` when the user addresses a custom agent by name.

    Matches ``@name do X``, ``use name to X``, ``ask the agent name ...``. Only names in
    ``names`` match (a word that is merely at the start of a sentence never does).
    """
    match = _INVOKE.match(instruction or "")
    if match is None:
        return None
    name = match.group(1).lower()
    if name not in names:
        return None
    rest = match.group(2).strip()
    rest = re.sub(r"^(?:to|for)\s+", "", rest, flags=re.IGNORECASE)
    return name, rest or instruction.strip()


async def enabled_agents_for(workspace_id: UUID, user_id: UUID) -> list[CustomAgent]:
    """The user's enabled, valid custom agents; ``[]`` when no folder or file service exists."""
    from app.files import services as file_services
    from app.files.access import context_from

    try:
        ctx = context_from(file_services.current(), workspace_id, user_id)
        return await CustomAgentStore(ctx).enabled()
    except Exception as error:  # planning must never fail because of the agents folder
        logger.info("custom_agents_unavailable error=%s", type(error).__name__)
        return []


def called_step(instruction: str, agents: list[CustomAgent]) -> list[Any] | None:
    """A one-step plan when the user addressed an enabled custom agent by name, else ``None``."""
    from app.agents.state import SubTask

    called = invocation(instruction, {agent.name for agent in agents})
    if called is None:
        return None
    name, request = called
    return [
        SubTask(
            id="step_1",
            assigned_agent="file",
            description=request[:2000],
            dependencies=[],
            input_data={"executor": "custom", "custom_agent": name, "critical": True},
        )
    ]
