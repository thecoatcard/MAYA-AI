"""Turn a third-party tool into something the model gateway accepts, and name it.

A server's JSON Schema can be arbitrarily large, recursive or use keywords the Gemini function
declaration does not accept. ``convert_schema`` keeps a small, safe subset, resolves local
``$ref`` s with a cycle guard, caps depth, width and size, and never raises: a schema it cannot
reduce within the caps gives ``None`` and the tool is simply not offered.

Descriptions come from the server and are untrusted (tool poisoning): they are cleaned, capped
and replaced by a neutral line when they look like instructions.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final

from app.gateway.schemas import ToolDefinition
from app.mcp.sanitizer import INJECTION_SIGNATURES

MAX_NAME_CHARS: Final = 64  # the model's function-name limit
MAX_DEPTH: Final = 5
MAX_PROPERTIES: Final = 40
MAX_ENUM: Final = 50
MAX_SCHEMA_CHARS: Final = 6000
MAX_DESCRIPTION: Final = 300
MAX_TOOL_DESCRIPTION: Final = 500
NAME_PREFIX: Final = "mcp__"

_TYPES: Final = frozenset({"string", "number", "integer", "boolean", "array", "object"})
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")
_BAD_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


def clean_description(text: object, limit: int = MAX_DESCRIPTION) -> str:
    """Single-line, control-free, capped text from an untrusted description."""
    if not isinstance(text, str):
        return ""
    cleaned = _CONTROL.sub("", text)
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit]


def looks_like_instruction(text: str) -> bool:
    """True when a description matches a prompt-injection signature (the same ones as outputs)."""
    return any(pattern.search(text) for pattern, _label in INJECTION_SIGNATURES)


def model_name(server: str, tool: str, taken: set[str] | None = None) -> str:
    """A stable ``mcp__<server>__<tool>`` name of at most 64 characters.

    A name that is already clean and short is used as is. Otherwise the tool part is cut and a
    short hash of the REAL tool name is appended, so the same tool always gets the same name and
    two different tools never share one (``taken`` is checked as well).
    """
    clean = _BAD_NAME_CHARS.sub("_", tool)
    plain = f"{NAME_PREFIX}{server}__{clean}"
    if clean == tool and len(plain) <= MAX_NAME_CHARS and (taken is None or plain not in taken):
        return plain
    digest = hashlib.sha256(f"{server}\x00{tool}".encode()).hexdigest()[:8]
    head = f"{NAME_PREFIX}{server}__"
    room = MAX_NAME_CHARS - len(head) - 9
    name = f"{head}{clean[: max(room, 1)]}_{digest}"
    return name[:MAX_NAME_CHARS]


# ----------------------------------------------------------------------------- schema reduce


class _Reducer:
    def __init__(self, root: dict[str, Any]) -> None:
        self.root = root

    def resolve(self, ref: str) -> dict[str, Any] | None:
        if not ref.startswith("#/"):
            return None
        node: Any = self.root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node if isinstance(node, dict) else None

    def reduce(self, node: Any, depth: int, seen: frozenset[str]) -> dict[str, Any]:
        if not isinstance(node, dict):
            return {"type": "string"}
        if "$ref" in node and isinstance(node["$ref"], str):
            ref = node["$ref"]
            target = None if ref in seen else self.resolve(ref)
            if target is None:
                return {"type": "object"} if ref in seen else {"type": "string"}
            merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
            return self.reduce(merged, depth, seen | {ref})
        node = self._flatten_combiners(node, depth, seen)
        nullable = bool(node.get("nullable"))
        kind = node.get("type")
        if isinstance(kind, list):
            non_null = [k for k in kind if k != "null"]
            nullable = nullable or len(non_null) != len(kind)
            kind = non_null[0] if non_null else None
        if "const" in node and "enum" not in node:
            node = {**node, "enum": [node["const"]]}
        if kind is None:
            kind = "object" if "properties" in node else "array" if "items" in node else "string"
        if kind not in _TYPES:
            kind = "string"
        out: dict[str, Any] = {"type": kind}
        description = clean_description(node.get("description"))
        if nullable:
            out["nullable"] = True
        enum = node.get("enum")
        if isinstance(enum, list) and enum:
            if all(isinstance(v, str) for v in enum) and kind == "string":
                out["enum"] = [str(v)[:100] for v in enum[:MAX_ENUM]]
            else:  # Gemini accepts string enums only: say the options in words instead
                options = ", ".join(str(v)[:30] for v in enum[:10])
                description = f"{description} Allowed values: {options}.".strip()
        if kind == "object":
            self._object(node, out, depth, seen)
        elif kind == "array":
            items = node.get("items")
            if depth >= MAX_DEPTH or not isinstance(items, dict):
                out["items"] = {"type": "string"}
            else:
                out["items"] = self.reduce(items, depth + 1, seen)
        elif kind in ("number", "integer"):
            for key in ("minimum", "maximum"):
                if isinstance(node.get(key), (int, float)) and not isinstance(node[key], bool):
                    out[key] = node[key]
        if description:
            out["description"] = description[:MAX_DESCRIPTION]
        return out

    def _flatten_combiners(
        self, node: dict[str, Any], depth: int, seen: frozenset[str]
    ) -> dict[str, Any]:
        """allOf is merged; anyOf/oneOf keep the first branch that is not null (and say so)."""
        merged = {k: v for k, v in node.items() if k not in ("allOf", "anyOf", "oneOf")}
        for part in node.get("allOf") or []:
            sub = self.reduce(part, depth, seen) if isinstance(part, dict) else {}
            props = {**(merged.get("properties") or {}), **(sub.get("properties") or {})}
            required = sorted(set(merged.get("required") or []) | set(sub.get("required") or []))
            merged = {**sub, **merged}
            if props:
                merged["properties"] = props
            if required:
                merged["required"] = required
        for key in ("anyOf", "oneOf"):
            branches = [b for b in (node.get(key) or []) if isinstance(b, dict)]
            if not branches:
                continue
            real = [b for b in branches if b.get("type") != "null"]
            if len(real) != len(branches):
                merged["nullable"] = True
            if real:
                first = self.reduce(real[0], depth, seen)
                for k, v in first.items():
                    merged.setdefault(k, v)
        return merged

    def _object(
        self, node: dict[str, Any], out: dict[str, Any], depth: int, seen: frozenset[str]
    ) -> None:
        props = node.get("properties")
        if not isinstance(props, dict) or depth >= MAX_DEPTH:
            out["properties"] = {}
            return
        reduced: dict[str, Any] = {}
        for name, sub in list(props.items())[:MAX_PROPERTIES]:
            if isinstance(name, str) and 0 < len(name) <= 64:
                reduced[name] = self.reduce(sub, depth + 1, seen)
        out["properties"] = reduced
        required = [r for r in (node.get("required") or []) if isinstance(r, str) and r in reduced]
        if required:
            out["required"] = required


def _size(value: object) -> int:
    return len(json.dumps(value, separators=(",", ":")))


def convert_schema(schema: object) -> dict[str, Any] | None:
    """The Gemini-subset parameters object for a tool's input schema, or None to not offer it."""
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}
    reducer = _Reducer(schema)
    try:
        out = reducer.reduce(schema, 0, frozenset())
    except RecursionError:
        return None
    if out.get("type") != "object":
        return None  # a tool's arguments are always an object
    out.setdefault("properties", {})
    out.pop("nullable", None)
    props: dict[str, Any] = out["properties"]
    required: list[str] = list(out.get("required", []))
    # Over the size cap: drop optional properties from the end, then give up.
    while _size(out) > MAX_SCHEMA_CHARS:
        optional = [name for name in props if name not in required]
        if not optional:
            return None
        del props[optional[-1]]
    return out


def tool_definition(
    server: str, server_title: str, tool: dict[str, Any], taken: set[str]
) -> tuple[ToolDefinition, str] | None:
    """The declaration offered to the model for one discovered tool, with its model name.

    ``tool`` carries ``name``, ``description`` and ``input_schema``. Returns None when the tool
    cannot be offered safely (no name, schema over the caps).
    """
    real = tool.get("name")
    if not isinstance(real, str) or not real or len(real) > 128:
        return None
    parameters = convert_schema(tool.get("input_schema"))
    if parameters is None:
        return None
    name = model_name(server, real, taken)
    raw = clean_description(tool.get("description"), MAX_TOOL_DESCRIPTION)
    description = (
        f"{raw}"
        if raw and not looks_like_instruction(raw)
        else f"The {real} tool of {server_title}."
    )
    description = f"[{server_title} tool] {description}"[:MAX_TOOL_DESCRIPTION]
    return ToolDefinition(name=name, description=description, parameters=parameters), name
