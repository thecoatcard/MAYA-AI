"""Schema normalizer and tool description poisoning defense for MCP tools (Decision D11)."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from app.gateway.schemas import ToolDefinition
from app.mcp.approvals import canonical_json
from app.mcp.sanitizer import clean_untrusted_text

logger = logging.getLogger(__name__)

TYPE_MAP: dict[str, str] = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
    "null": "NULL",
}

DISALLOWED_KEYWORDS: frozenset[str] = frozenset(
    {"$schema", "$id", "$comment", "$defs", "definitions", "additionalProperties", "title"}
)

SCRIPT_TAG_PATTERN = re.compile(r"<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>", re.I)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
SYSTEM_NOTE_PATTERN = re.compile(r"\[SYSTEM(?:\s+NOTE)?:\s*[^\]]+\]", re.I)


class SchemaPoisoningError(Exception):
    """Raised when an MCP server's schema hash does not match expected pinned hash."""


def sanitize_description(description: str) -> str:
    """Sanitize description text against prompt injection and HTML injection."""
    clean = clean_untrusted_text(description)
    clean = SCRIPT_TAG_PATTERN.sub("", clean)
    clean = SYSTEM_NOTE_PATTERN.sub("", clean)
    clean = HTML_TAG_PATTERN.sub("", clean)
    return clean.strip()


def normalize_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively convert JSON Schema to Google GenAI parameter declaration format."""
    normalized: dict[str, Any] = {}

    for k, v in schema.items():
        if k in DISALLOWED_KEYWORDS:
            continue

        if k == "type":
            if isinstance(v, list):
                # Union type (e.g. ["string", "null"])
                non_null_types = [t for t in v if t != "null"]
                primary_type = non_null_types[0] if non_null_types else "string"
                normalized["type"] = TYPE_MAP.get(str(primary_type).lower(), "STRING")
                if "null" in v:
                    normalized["nullable"] = True
            elif isinstance(v, str):
                normalized["type"] = TYPE_MAP.get(v.lower(), v.upper())
            else:
                normalized["type"] = "OBJECT"
        elif k == "properties" and isinstance(v, dict):
            normalized["properties"] = {
                prop_name: normalize_json_schema(prop_def)
                for prop_name, prop_def in v.items()
                if isinstance(prop_def, dict)
            }
        elif k == "items" and isinstance(v, dict):
            normalized["items"] = normalize_json_schema(v)
        elif k == "description" and isinstance(v, str):
            normalized["description"] = sanitize_description(v)
        elif k == "required" and isinstance(v, list):
            normalized["required"] = [str(item) for item in v]
        elif k == "enum" and isinstance(v, list):
            normalized["enum"] = [str(item) for item in v]
        else:
            normalized[k] = v

    if "type" not in normalized:
        normalized["type"] = "OBJECT"

    return normalized


def normalize_mcp_tool_to_genai(raw_tool: dict[str, Any]) -> ToolDefinition:
    """Normalize a raw MCP tool declaration into a Google GenAI ToolDefinition."""
    name = str(raw_tool.get("name", "")).strip()
    raw_desc = str(raw_tool.get("description", "")).strip()
    clean_desc = sanitize_description(raw_desc)

    input_schema = (
        raw_tool.get("inputSchema")
        or raw_tool.get("parameters")
        or {"type": "object", "properties": {}}
    )
    if isinstance(input_schema, dict):
        parameters = normalize_json_schema(input_schema)
    else:
        parameters = {"type": "OBJECT", "properties": {}}

    return ToolDefinition(
        name=name,
        description=clean_desc,
        parameters=parameters,
    )


def compute_tool_hash(tool_dict: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of a normalized tool definition (RFC 8785)."""
    canonical_repr = canonical_json(tool_dict)
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


def compute_server_schema_hash(tools: list[dict[str, Any]]) -> str:
    """Compute collective canonical SHA-256 hash across an entire tool manifest."""
    sorted_tools: list[dict[str, Any]] = []
    for t in tools:
        if isinstance(t, ToolDefinition):
            td = {"description": t.description, "name": t.name, "parameters": t.parameters}
        elif isinstance(t, dict):
            # If raw MCP tool, normalize first
            if "inputSchema" in t:
                norm = normalize_mcp_tool_to_genai(t)
                td = {
                    "description": str(t.get("description", "")),
                    "name": str(t.get("name", "")),
                    "parameters": norm.parameters,
                }
            else:
                td = {
                    "description": str(t.get("description", "")),
                    "name": str(t.get("name", "")),
                    "parameters": t.get("parameters", {}),
                }
        else:
            continue
        sorted_tools.append(td)

    # Sort tools deterministically by name
    sorted_tools.sort(key=lambda x: str(x.get("name", "")))
    canonical_repr = canonical_json(sorted_tools)
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


def verify_schema_hash(
    live_tools: list[dict[str, Any]],
    expected_hash: str,
    raise_on_mismatch: bool = False,
) -> bool:
    """Verify live discovered tools against expected schema hash to prevent poisoning."""
    actual_hash = compute_server_schema_hash(live_tools)
    matches = actual_hash.lower() == expected_hash.lower()
    if not matches and raise_on_mismatch:
        raise SchemaPoisoningError(
            f"Schema tampering or description poisoning detected! Expected hash '{expected_hash}', "
            f"observed live hash '{actual_hash}'."
        )
    return matches
