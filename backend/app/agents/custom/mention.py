"""``@agent-name <request>`` in chat: resolved BEFORE any other routing, never sent to GitHub.

A leading ``@name`` always means the user's custom agent (a file in their folder under
``.maya/agents``). This module holds the pure parts (parsing, wording checks, the plain texts) and
the read-only lookups: which agent exists, and where the target file ("main.py") is. The target is
looked up in this order by the caller: an attached file, a file of THIS conversation, then a file
in the connected folder (SafePath rules, read only).
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.agents.custom.definition import NAME_RE, CustomAgent
from app.agents.custom.store import CustomAgentStore
from app.files.access import FileAccessError, FileContext

MAX_CANDIDATES: Final = 5
_SCAN_DEPTH: Final = 4
_SCAN_LIMIT: Final = 2000
_SKIP_DIRS: Final = frozenset({"node_modules", "__pycache__", "venv", ".venv", "dist", "build"})

_MENTION = re.compile(
    r"^\s*@([a-z][a-z0-9-]{1,38}[a-z0-9])\b[\s,:;-]*(.*)$", re.IGNORECASE | re.DOTALL
)
# A file name with an extension, as a whole word (quotes allowed): main.py, "my app.js".
_FILE_NAME = re.compile(
    r"[\"'`]([\w][\w .()-]{0,80}\.[A-Za-z0-9]{1,6})[\"'`]"
    r"|(?<![\w/\\.@-])([\w][\w.()-]{0,80}\.[A-Za-z0-9]{1,6})(?![\w-])"
)
_CODE_EXT: Final = frozenset(
    "py js jsx ts tsx java kt go rs rb php c h cpp cc hpp cs swift sql sh bash ps1 html css "
    "json yaml yml toml md txt ipynb vue svelte dart lua r scala".split()
)
_GITHUB_WORDS = re.compile(
    r"\b(?:github|git\s*hub|repo|repos|repository|repositories|pull\s+request|commit|commits)\b",
    re.IGNORECASE,
)
_OWNER_REPO = re.compile(r"(?<![\w./-])[A-Za-z0-9][\w.-]{0,38}/[A-Za-z0-9][\w.-]{0,99}(?![\w/])")
_REVIEW_WORDS = re.compile(
    r"\b(?:review|audit|analy[sz]e|inspect|critique|check|improve|explain|fix|refactor|"
    r"karo|kar\s*do|dekho|batao)\b",
    re.IGNORECASE,
)

CREATE_HINT: Final = (
    'Create it with: "Mere liye ek agent banao jo meri Python files ka code review kare", '
    "or connect your folder in Settings > Files."
)


@dataclass(frozen=True, slots=True)
class Mention:
    name: str
    request: str  # the text after the name (the whole message when nothing follows)


def parse_mention(text: str) -> Mention | None:
    """``@name rest`` at the start of the message, else ``None``. The name must be well-formed."""
    match = _MENTION.match(text or "")
    if match is None:
        return None
    name = match.group(1).lower()
    if not NAME_RE.match(name):
        return None
    return Mention(name, match.group(2).strip() or (text or "").strip())


def target_file_name(request: str) -> str | None:
    """The first code-like file name in ``request`` ("main.py"), else ``None``."""
    for match in _FILE_NAME.finditer(request or ""):
        name = (match.group(1) or match.group(2)).strip()
        if name.rpartition(".")[2].lower() in _CODE_EXT:
            return name
    return None


def has_repo_wording(text: str) -> bool:
    """True when the message really is about a GitHub repository (URL, owner/repo, github/repo
    wording). A bare file name is not."""
    value = text or ""
    if "github.com" in value.lower() or _GITHUB_WORDS.search(value):
        return True
    # owner/repo, but "src/main.py" (a path ending in a code extension) is a file, not a repo
    return any(
        "." not in found or found.rpartition(".")[2].lower() not in _CODE_EXT
        for found in _OWNER_REPO.findall(value)
    )


def is_code_file_review(text: str) -> bool:
    """A review/look-at request for a named code file with no GitHub wording: never a GitHub job."""
    value = text or ""
    return (
        target_file_name(value) is not None
        and _REVIEW_WORDS.search(value) is not None
        and not has_repo_wording(value)
    )


# ------------------------------------------------------------------ plain texts


def missing_agent_text(name: str, available: list[str]) -> str:
    base = f"There is no agent named {name} in your working folder (.maya/agents). {CREATE_HINT}"
    if available:
        return f"{base} Agents I can see: {', '.join(available[:10])}."
    return base


def agent_unusable_text(agent: CustomAgent) -> str:
    return f"The agent {agent.name} is switched off. Enable it in Agents, then ask again."


# ------------------------------------------------------------------ lookups


async def list_agent_names(ctx: FileContext) -> tuple[list[str], CustomAgentStore]:
    store = CustomAgentStore(ctx)
    try:
        names = [a.name for a in await store.enabled()]
    except Exception:  # noqa: BLE001 - an unreadable folder just means no agents
        names = []
    return names, store


def _walk(root: Path, wanted: str, *, same_type: bool = False) -> list[str]:
    out: list[str] = []
    base = str(root)
    seen = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        depth = current[len(base) :].count(os.sep)
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        if depth >= _SCAN_DEPTH:
            dirs[:] = []
        for name in sorted(files):
            seen += 1
            if seen > _SCAN_LIMIT:
                return out
            lower = name.lower()
            if same_type:
                hit = lower.endswith("." + wanted.lower()) and not lower.startswith(".")
            else:
                hit = lower == wanted.lower()
            if hit:
                out.append(os.path.relpath(os.path.join(current, name), base).replace(os.sep, "/"))
    return out


async def folder_candidates(ctx: FileContext, file_name: str) -> list[str]:
    """Folder-relative paths of files named ``file_name`` (case-insensitive); [] without a
    usable folder."""
    try:
        root = await ctx.root()
    except FileAccessError:
        return []
    return (await asyncio.to_thread(_walk, root, file_name))[:MAX_CANDIDATES]


async def read_folder_file(ctx: FileContext, relative: str, max_chars: int) -> tuple[str, str]:
    """``(name, text)`` of one folder file through SafePath, read only; ``("", "")`` on failure."""
    try:
        source = await ctx.folder_source(relative)
        view = await ctx.read(source, max_chars=max_chars)
    except Exception:  # noqa: BLE001 - not readable: treated as not found
        return "", ""
    return (source.relative or source.name, view.text) if view.readable else ("", "")


async def folder_same_type(ctx: FileContext, extension: str) -> list[str]:
    """Other files with the same extension (``py``) in the folder: real question candidates."""
    try:
        root = await ctx.root()
    except FileAccessError:
        return []
    found = await asyncio.to_thread(_walk, root, extension, same_type=True)
    return found[:MAX_CANDIDATES]
