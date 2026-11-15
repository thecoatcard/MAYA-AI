"""Parse, render and validate a custom agent file (``<folder>/.maya/agents/<name>.md``).

The file is data, never code or policy. Shape::

    ---
    name: weekly-reporter
    description: Turns my notes into a weekly status report.
    capabilities: [web_search, read_files]
    model: lite
    enabled: true
    ---
    Free-text instructions (the body of the file).

Rules enforced here, for a file written by MAYA and for one the user (or anybody) dropped in the
folder alike:

* Only the keys above exist; any other key (``tools``, ``approval``, ``permissions`` ...) makes
  the file invalid instead of being ignored silently.
* ``capabilities`` come from a closed vocabulary (:data:`CAPABILITY_TOOLS`), every capability
  maps to READ-tier tools only, and each must be inside what the user already has
  (:func:`allowed_capabilities`). A custom agent can never gain a tool, a write, an external side
  effect or a way around approvals: the body of the file is only ever prompt text.
* ``model`` is one of the REST text aliases an agent may use.
* The parser is a small strict reader (no YAML tags, anchors or code), so a hostile file cannot
  do more than be rejected.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.agents.unified import ALIASES, PLANNER_FACING_ROLES
from app.gateway.registry import AGENT_ALIAS_CHOICES
from app.mcp.permissions import DEFAULT_TOOL_PERMISSIONS, ToolTier

if TYPE_CHECKING:
    from app.agents.capabilities import Capabilities

AGENTS_DIR = ".maya/agents"
MAX_FILE_BYTES = 16 * 1024
MAX_INSTRUCTION_CHARS = 6000
MAX_DESCRIPTION_CHARS = 240

# capability -> the tools it stands for. READ tier only, asserted at import time below.
CAPABILITY_TOOLS: dict[str, tuple[str, ...]] = {
    "web_search": ("web_search",),
    "read_files": ("file_list", "file_read"),
}
_ALLOWED_KEYS = frozenset({"name", "description", "capabilities", "model", "enabled"})
_RESERVED_NAMES = (
    frozenset(ALIASES)
    | frozenset(PLANNER_FACING_ROLES)
    | {"planner", "maya", "system", "custom", "all", "none"}
)
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
_BAD_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")

for _cap, _tools in CAPABILITY_TOOLS.items():  # a capability may never exceed READ
    for _tool in _tools:
        if DEFAULT_TOOL_PERMISSIONS.get(_tool) != ToolTier.READ:
            raise RuntimeError(f"custom capability {_cap} maps to a non-READ tool {_tool}")


class CustomAgentError(ValueError):
    """A definition that cannot be used. ``code`` is stable; ``str`` is plain words."""

    def __init__(self, message: str, code: str = "invalid_definition") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CustomAgent:
    name: str
    description: str
    instructions: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    model: str | None = None
    enabled: bool = True

    @property
    def tools(self) -> tuple[str, ...]:
        """The READ tools this agent stands for (derived, never stored in the file)."""
        return tuple(dict.fromkeys(t for c in self.capabilities for t in CAPABILITY_TOOLS[c]))

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "tools": list(self.tools),
            "model": self.model,
            "enabled": self.enabled,
            "instructions": self.instructions,
            "kind": "custom",
        }


def allowed_capabilities(caps: Capabilities | None = None) -> frozenset[str]:
    """Capabilities the user already has in this deployment (the ceiling for custom agents)."""
    from app.agents import capabilities as capability_module

    current = caps or capability_module.current()
    allowed = {"read_files"}
    if current.web_search:
        allowed.add("web_search")
    return frozenset(allowed)


def clean_text(value: str, limit: int) -> str:
    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    text = _BAD_CHARS.sub("", text).strip()
    if len(text) > limit:
        raise CustomAgentError(f"Text is longer than {limit} characters.", "too_long")
    return text


def _scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _parse_front(lines: list[str]) -> dict[str, object]:
    data: dict[str, object] = {}
    key: str | None = None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item:
            current = data.get(key) if key is not None else None
            if not isinstance(current, list):
                raise CustomAgentError("A list item has no list key before it.", "bad_frontmatter")
            current.append(_scalar(item.group(1)))
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if not match:
            raise CustomAgentError(f"Cannot read this line: {line[:60]!r}", "bad_frontmatter")
        key, rest = match.group(1).lower(), match.group(2).strip()
        if key in data:
            raise CustomAgentError(f"'{key}' is given twice.", "duplicate_key")
        if rest.startswith("["):
            if not rest.endswith("]"):
                raise CustomAgentError("A list must close with ']'.", "bad_frontmatter")
            inner = rest[1:-1].strip()
            data[key] = [_scalar(p) for p in inner.split(",")] if inner else []
        elif rest == "":
            data[key] = []
        else:
            data[key] = _scalar(rest)
    return data


def parse_definition(text: str) -> CustomAgent:
    """Read a definition file. Raises :class:`CustomAgentError`; does not check permissions."""
    if len(text.encode("utf-8", errors="replace")) > MAX_FILE_BYTES:
        raise CustomAgentError("The agent file is larger than 16 KB.", "too_large")
    lines = text.replace("\r\n", "\n").lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != "---":
        raise CustomAgentError("The file must start with a '---' header.", "bad_frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise CustomAgentError("The '---' header is not closed.", "bad_frontmatter") from None
    front = _parse_front(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    unknown = sorted(set(front) - _ALLOWED_KEYS)
    if unknown:
        raise CustomAgentError(
            f"Unknown setting(s) {unknown}. A custom agent cannot set tools, approvals or "
            "permissions; only name, description, capabilities, model, enabled.",
            "unknown_key",
        )
    name = str(front.get("name", "")).strip()
    description = clean_text(str(front.get("description", "")), MAX_DESCRIPTION_CHARS)
    raw_caps = front.get("capabilities", [])
    if isinstance(raw_caps, str):
        raw_caps = [raw_caps] if raw_caps else []
    if not isinstance(raw_caps, list):
        raise CustomAgentError("'capabilities' must be a list.", "bad_value")
    capabilities = tuple(dict.fromkeys(str(c).strip().lower() for c in raw_caps if str(c).strip()))
    model_raw = front.get("model")
    model = str(model_raw).strip().lower() if model_raw not in (None, "", []) else None
    enabled_raw = str(front.get("enabled", "true")).strip().lower()
    if enabled_raw not in ("true", "false"):
        raise CustomAgentError("'enabled' must be true or false.", "bad_value")
    return CustomAgent(
        name=name,
        description=description,
        instructions=clean_text(body, MAX_INSTRUCTION_CHARS),
        capabilities=capabilities,
        model=model,
        enabled=enabled_raw == "true",
    )


def validate_definition(agent: CustomAgent, caps: Capabilities | None = None) -> CustomAgent:
    """Check ``agent`` against the rules in the module docstring. Returns it, or raises."""
    if not NAME_RE.match(agent.name):
        raise CustomAgentError(
            "The name must be 3-40 characters: lowercase letters, digits and '-', starting with "
            "a letter.",
            "bad_name",
        )
    if agent.name in _RESERVED_NAMES:
        raise CustomAgentError(f"'{agent.name}' is a built-in agent name.", "reserved_name")
    if not agent.description:
        raise CustomAgentError("A description is required.", "missing_description")
    if len(agent.instructions) < 10:
        raise CustomAgentError("The instructions are required (at least 10 characters).", "empty")
    unknown = [c for c in agent.capabilities if c not in CAPABILITY_TOOLS]
    if unknown:
        raise CustomAgentError(
            f"Unknown capability {unknown}. Available: {sorted(CAPABILITY_TOOLS)}.",
            "unknown_capability",
        )
    allowed = allowed_capabilities(caps)
    beyond = [c for c in agent.capabilities if c not in allowed]
    if beyond:
        raise CustomAgentError(
            f"Capability {beyond} is not available to you in this deployment, so a custom agent "
            "cannot have it.",
            "capability_not_permitted",
        )
    if agent.model is not None and agent.model not in AGENT_ALIAS_CHOICES:
        raise CustomAgentError(f"Model must be one of {sorted(AGENT_ALIAS_CHOICES)}.", "bad_model")
    return agent


def render_definition(agent: CustomAgent) -> str:
    """The file text for ``agent`` (round-trips through :func:`parse_definition`)."""
    description = " ".join(agent.description.split()).replace('"', "'")
    head = [
        "---",
        f"name: {agent.name}",
        f'description: "{description}"',
        f"capabilities: [{', '.join(agent.capabilities)}]",
    ]
    if agent.model:
        head.append(f"model: {agent.model}")
    head.append(f"enabled: {'true' if agent.enabled else 'false'}")
    head.append("---")
    return "\n".join(head) + "\n" + agent.instructions.strip() + "\n"
