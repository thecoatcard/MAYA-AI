"""Shared conversion of read-only tool definitions to the three shapes the platform uses.

A definition is ``{"name", "description", "properties", "required"}`` with JSON-schema property
dicts (lower-case types). It becomes an MCP manifest entry (gateway), a Gemini Live function
declaration (voice, upper-case types) or a chat ``parameters`` object (see ``app/mcp/notion_tools``
and ``app/mcp/show_code``). Nothing here adds a permission: the tier lives in
``app/mcp/permissions.py``.
"""

from __future__ import annotations

from typing import Any


def mcp_manifest(defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The definitions as MCP ``tools/list`` entries for the gateway's built-in server."""
    return [
        {
            "name": d["name"],
            "description": d["description"],
            "inputSchema": {
                "type": "object",
                "properties": dict(d["properties"]),
                "required": list(d["required"]),
            },
        }
        for d in defs
    ]


def voice_declarations(defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The definitions as Gemini Live function declarations (``OBJECT`` / ``STRING`` types)."""

    def convert(prop: dict[str, Any]) -> dict[str, Any]:
        out = dict(prop)
        out["type"] = str(prop["type"]).upper()
        if isinstance(prop.get("items"), dict):
            out["items"] = convert(prop["items"])
        if isinstance(prop.get("properties"), dict):
            out["properties"] = {k: convert(v) for k, v in prop["properties"].items()}
        return out

    return [
        {
            "name": d["name"],
            "description": d["description"],
            "parameters": {
                "type": "OBJECT",
                "properties": {k: convert(v) for k, v in d["properties"].items()},
                "required": list(d["required"]),
            },
        }
        for d in defs
    ]
